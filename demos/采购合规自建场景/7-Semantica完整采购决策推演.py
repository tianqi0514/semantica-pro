#!/usr/bin/env python3
"""
Semantica 采购合规审查：从配置、计算到决策推演的完整 Python 演示。

本文件故意不做成一个“只能跑通但看不懂”的短示例，而是把真实实施中的边界写清楚：

1. 项目代码读取业务数据和内控配置；
2. 项目代码把金额、采购方式、供应商风险转成 Semantica Reasoner 能识别的事实；
3. Semantica Reasoner 按 IF ... AND ... THEN ... 规则做前向链式推理；
4. 项目代码把推理结果转成硬阻断、复核项和可比较分数；
5. 对多个候选方案重复上述过程，形成“如果改成 X，会怎样”的推演；
6. Semantica ContextGraph 保存业务对象、证据、规则、方案和最终决策的关系；
7. 输出推理迹线、历史前例、决策上下文和可导入/留档的 JSON。

重要边界：
- ContextGraph 和 Reasoner 是 Semantica 原生能力。
- 本文件中的 JSON 业务格式、金额分档、评分公式、硬阻断顺序是演示项目自己的实现。
- 候选方案是输入文件预先给定的；Semantica 并不会自动生成采购方案。
- 这是企业内控演示，不构成法律或采购专业意见。

本地运行（已安装项目依赖时）：
    python "demos/采购合规自建场景/7-Semantica完整采购决策推演.py"

复用当前已构建的 Docker 镜像运行（在 semantica 仓库根目录执行）：
    docker run --rm \
      -v "$PWD/demos/采购合规自建场景:/demo" \
      -w /app --entrypoint python semantica-knowledge-explorer:latest \
      "/demo/7-Semantica完整采购决策推演.py" \
      --config "/demo/7-采购合规决策配置.json" \
      --input "/demo/7-采购业务输入.json" \
      --output-dir "/demo/7-python推演输出"
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

# ===== Semantica 原生 API =====
# ContextGraph：构建内存上下文图、记录决策、查前例、遍历决策上下文。
# Reasoner：把字符串事实按 IF/AND/THEN 规则做前向链式推理。
from semantica.context import ContextGraph
from semantica.reasoning import Reasoner


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "7-采购合规决策配置.json"
DEFAULT_INPUT = SCRIPT_DIR / "7-采购业务输入.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "7-python推演输出"


def load_json(path: Path) -> dict[str, Any]:
    """读取业务配置或业务输入。

    这是“业务接入层”，不是 Semantica 限定的接口。
    真实实施可在这里改成 SQLAlchemy/pandas 查数据库，或请求 ERP/SRM API。
    """
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def policy_by_type(config: dict[str, Any], policy_type: str) -> dict[str, Any]:
    """按业务类型取某条政策配置。"""
    for policy in config["policies"]:
        if policy["policy_type"] == policy_type:
            return policy
    raise ValueError(f"缺少政策配置: {policy_type}")


def validate_input(config: dict[str, Any], business_input: dict[str, Any]) -> None:
    """在进入图谱和推理前做最小数据质量检查。

    Semantica Reasoner 的变量绑定是字符串模式匹配。为了避免逗号和括号破坏
    fact(arg1, arg2) 这种演示语法，本示例对会进入事实的 ID/枚举值做限制。
    """huay't    atom_pattern = re.compile(r"^[^(),\s]+$")

    def require_atom(label: str, value: Any) -> None:
        if not isinstance(value, str) or not atom_pattern.match(value):
            raise ValueError(f"{label} 必须是不含空格、逗号和括号的字符串：{value!r}")

    case = business_input["case"]
    require_atom("case.id", case["id"])
    if case["amount_cny"] <= 0:
        raise ValueError("case.amount_cny 必须大于 0")

    method_codes = set(config["codebook"]["procurement_methods"])
    risk_codes = set(config["codebook"]["supplier_risk_levels"])
    supplier_ids: set[str] = set()
    plan_ids: set[str] = set()

    for supplier in business_input["suppliers"]:
        require_atom("supplier.id", supplier["id"])
        require_atom("supplier.risk_level", supplier["risk_level"])
        if supplier["risk_level"] not in risk_codes:
            raise ValueError(f"未配置的供应商风险等级：{supplier['risk_level']}")
        if supplier["id"] in supplier_ids:
            raise ValueError(f"供应商 ID 重复：{supplier['id']}")
        supplier_ids.add(supplier["id"])

    for plan in business_input["candidate_plans"]:
        require_atom("plan.id", plan["id"])
        require_atom("plan.procurement_method", plan["procurement_method"])
        if plan["id"] in plan_ids:
            raise ValueError(f"方案 ID 重复：{plan['id']}")
        if plan["procurement_method"] not in method_codes:
            raise ValueError(f"未配置的采购方式：{plan['procurement_method']}")
        if plan["supplier_id"] not in supplier_ids:
            raise ValueError(f"方案 {plan['id']} 引用了不存在的供应商：{plan['supplier_id']}")
        if plan["quoted_price_cny"] <= 0 or plan["estimated_delivery_days"] <= 0:
            raise ValueError(f"方案 {plan['id']} 的价格和交付天数必须大于 0")
        plan_ids.add(plan["id"])

    weights = config["scoring"]["weights"]
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("评分权重之和必须等于 1")


def build_context_graph(
    config: dict[str, Any], business_input: dict[str, Any]
) -> ContextGraph:
    """用 Semantica ContextGraph 构建本次决策的业务上下文。

    这一步存的是“这次决策涉及谁、什么项目、哪份证据和哪条规则”。
    它不会自动把节点属性变成 Reasoner 事实；后面会显式调用
    normalize_plan_to_facts() 完成两者之间的桥接。
    """
    graph = ContextGraph(
        # 关闭文本实体/关系自动抽取，保证本演示只出现我们明确导入的数据。
        extract_entities=False,
        extract_relationships=False,
        # 本演示不依赖可选的节点嵌入和社区发现，降低运行环境要求。
        advanced_analytics=False,
    )

    case = business_input["case"]
    graph.add_node(
        case["id"],
        "procurement_case",
        content=case["title"],
        amount_cny=case["amount_cny"],
        currency=case["currency"],
    )
    graph.add_node(
        case["department_id"],
        "department",
        content=case["department_name"],
    )
    graph.add_node(
        case["project_id"],
        "project",
        content=case["project_name"],
    )
    graph.add_edge(case["id"], case["department_id"], "requested_by")
    graph.add_edge(case["id"], case["project_id"], "belongs_to_project")

    # 证据节点：留下内容、类型和来源，让结果可追溯。
    for evidence in business_input["evidence"]:
        graph.add_node(
            evidence["id"],
            "evidence",
            content=evidence["title"],
            evidence_type=evidence["type"],
            detail=evidence["content"],
            source_system=evidence["source_system"],
        )

    graph.add_edge(case["id"], "EVIDENCE-BUDGET-001", "supported_by")

    # 供应商与它当日的风险证据。
    for supplier in business_input["suppliers"]:
        graph.add_node(
            supplier["id"],
            "supplier",
            content=supplier["name"],
            risk_level=supplier["risk_level"],
            risk_reason=supplier["risk_reason"],
            risk_source=supplier["risk_source"],
            risk_as_of=supplier["risk_as_of"],
        )
        graph.add_edge(
            supplier["id"],
            supplier["risk_evidence_id"],
            "risk_supported_by",
        )

    # 政策与可执行规则都进图。这样做是为了决策留痕，
    # 真正执行规则的仍是后面的 Reasoner。
    for policy in config["policies"]:
        graph.add_node(
            policy["id"],
            "policy",
            content=policy["name"],
            policy_type=policy["policy_type"],
            meaning=policy["meaning"],
        )

    for rule in config["reasoning_rules"]:
        graph.add_node(
            rule["id"],
            "reasoning_rule",
            content=rule["name"],
            expression=rule["expression"],
            business_meaning=rule["business_meaning"],
        )
        graph.add_edge(rule["id"], rule["policy_id"], "implements_policy")

    # 候选方案不是 Semantica 生成的，而是业务人员或上游算法给定的可选行动。
    for plan in business_input["candidate_plans"]:
        graph.add_node(
            plan["id"],
            "candidate_plan",
            content=plan["name"],
            procurement_method=plan["procurement_method"],
            quoted_price_cny=plan["quoted_price_cny"],
            estimated_delivery_days=plan["estimated_delivery_days"],
            change_description=plan["change_description"],
        )
        graph.add_edge(plan["id"], case["id"], "plan_for")
        graph.add_edge(plan["id"], plan["supplier_id"], "uses_supplier")

    return graph


def normalize_plan_to_facts(
    config: dict[str, Any],
    business_input: dict[str, Any],
    plan: dict[str, Any],
) -> list[str]:
    """把原始业务字段转换为 Reasoner 使用的字符串事实。

    这是整个项目最关键的“业务适配层”：
    - Python 在这里做金额 >= 阈值的数值比较；
    - Python 在这里查出方案所用供应商的 risk_level；
    - Reasoner 只接收已经规范化后的符号事实。

    因此，不能把 case_amount_band 误解为 Semantica 自动懂了金额。
    """
    case = business_input["case"]
    suppliers = {item["id"]: item for item in business_input["suppliers"]}
    supplier = suppliers[plan["supplier_id"]]
    method_policy = policy_by_type(config, "procurement_method")

    amount_band = (
        "open_tender_required"
        if case["amount_cny"] >= method_policy["open_tender_threshold_cny"]
        else "below_open_tender_threshold"
    )

    return [
        f"plan_for_case({plan['id']}, {case['id']})",
        f"case_amount_band({case['id']}, {amount_band})",
        f"candidate_method({plan['id']}, {plan['procurement_method']})",
        f"candidate_supplier({plan['id']}, {plan['supplier_id']})",
        f"supplier_risk({plan['supplier_id']}, {supplier['risk_level']})",
    ]


def run_semantica_reasoning(
    config: dict[str, Any], facts: list[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """用 Semantica Reasoner 对一个候选方案做前向链式推理。

    每个方案都新建一个 Reasoner，因为 Reasoner 会把添加和推导出的事实
    留在 self.facts 工作内存中。如果所有方案共用同一实例，前一方案的事实会污染后一方案。
    """
    reasoner = Reasoner(max_iterations=20)

    # add_rule() 是 Semantica API。解析后再补上业务规则 ID/名称，
    # 便于输出“哪条规则 + 哪些前提 -> 哪个结论”。
    for rule_config in config["reasoning_rules"]:
        rule = reasoner.add_rule(rule_config["expression"])
        rule.rule_id = rule_config["id"]
        rule.name = rule_config["name"]

    for fact in facts:
        reasoner.add_fact(fact)

    # forward_chain() 返回 InferenceResult，比 infer_facts() 只返回结论更适合演示解释链。
    inference_results = reasoner.forward_chain()
    inferred_facts = [item.conclusion for item in inference_results]
    trace = [
        {
            "conclusion": item.conclusion,
            "rule_id": item.rule_used.rule_id if item.rule_used else None,
            "rule_name": item.rule_used.name if item.rule_used else None,
            "premises": item.premises,
            "confidence": item.confidence,
        }
        for item in inference_results
    ]
    return inferred_facts, trace


def predicate_arguments(fact: str, predicate: str) -> list[str] | None:
    """从 violation(PLAN-A, reason) 中取出参数。仅服务于本演示的受控语法。"""
    prefix = f"{predicate}("
    if not fact.startswith(prefix) or not fact.endswith(")"):
        return None
    return [part.strip() for part in fact[len(prefix) : -1].split(",")]


def collect_plan_result(
    plan: dict[str, Any],
    input_facts: list[str],
    inferred_facts: list[str],
    trace: list[dict[str, Any]],
    scoring_config: dict[str, Any],
) -> dict[str, Any]:
    """把 Semantica 推导事实整理成业务人员能读的方案结果。"""
    violations: set[str] = set()
    manual_reviews: set[str] = set()
    passes_core_rules = False

    for fact in inferred_facts:
        args = predicate_arguments(fact, "violation")
        if args and len(args) == 2 and args[0] == plan["id"]:
            violations.add(args[1])

        args = predicate_arguments(fact, "requires_manual_review")
        if args and len(args) == 2 and args[0] == plan["id"]:
            manual_reviews.add(args[1])

        args = predicate_arguments(fact, "passes_core_rules")
        if args and len(args) == 1 and args[0] == plan["id"]:
            passes_core_rules = True

    hard_reasons = set(scoring_config["hard_block_reasons"])
    blocking_reasons = sorted(violations & hard_reasons)
    hard_blocked = bool(blocking_reasons)

    if hard_blocked:
        status = "硬性阻断"
    elif violations:
        status = "需人工复核"
    elif passes_core_rules:
        status = "通过核心规则"
    else:
        # 这个分支很重要：“没推导出违规”不等于“完整合规”。
        status = "未命中已配置规则"

    compliance_score = max(
        0.0,
        100.0
        - sum(
            scoring_config["violation_deductions"].get(reason, 0)
            for reason in violations
        ),
    )

    return {
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "procurement_method": plan["procurement_method"],
        "supplier_id": plan["supplier_id"],
        "quoted_price_cny": plan["quoted_price_cny"],
        "estimated_delivery_days": plan["estimated_delivery_days"],
        "input_facts": input_facts,
        "inferred_facts": inferred_facts,
        "inference_trace": trace,
        "violations": sorted(violations),
        "manual_reviews": sorted(manual_reviews),
        "blocking_reasons": blocking_reasons,
        "hard_blocked": hard_blocked,
        "status": status,
        "scores": {
            "compliance": round(compliance_score, 2)
        },
    }


def calculate_comparable_scores(
    plan_results: list[dict[str, Any]], scoring_config: dict[str, Any]
) -> None:
    """给各方案补齐价格、交付和综合分。

    这是业务计算，不是 Semantica 内置算法。
    - 价格分 = 最低报价 / 本方案报价 * 100
    - 交付分 = 最短天数 / 本方案天数 * 100
    - 综合分 = 合规分*65% + 价格分*20% + 交付分*15%

    硬性阻断不会因为价格低就被抵消；选择方案时会先做阻断过滤。
    """
    min_price = min(item["quoted_price_cny"] for item in plan_results)
    min_days = min(item["estimated_delivery_days"] for item in plan_results)
    weights = scoring_config["weights"]

    for result in plan_results:
        price_score = min_price / result["quoted_price_cny"] * 100.0
        delivery_score = min_days / result["estimated_delivery_days"] * 100.0
        result["scores"]["price"] = round(price_score, 2)
        result["scores"]["delivery"] = round(delivery_score, 2)
        result["scores"]["total"] = round(
            result["scores"]["compliance"] * weights["compliance"]
            + price_score * weights["price"]
            + delivery_score * weights["delivery"],
            2,
        )


def choose_recommended_plan(plan_results: list[dict[str, Any]]) -> dict[str, Any]:
    """按“硬约束优先”的业务顺序选出推荐方案。

    1. 先排除 hard_blocked；
    2. 如果存在完全没有违规的方案，只在这些方案中比分；
    3. 如果没有无违规方案，才在未硬阻断方案中比分；
    4. 如果所有方案都被硬阻断，仍返回分数最高者，但明确标记无可直接执行方案。
    """
    non_blocked = [item for item in plan_results if not item["hard_blocked"]]
    clean = [item for item in non_blocked if not item["violations"]]

    if clean:
        pool = clean
        selection_basis = "在无违规且未硬阻断的方案中选综合分最高者"
        executable = True
    elif non_blocked:
        pool = non_blocked
        selection_basis = "无完全通过方案；在未硬阻断方案中选分数最高者，仍须人工复核"
        executable = False
    else:
        pool = plan_results
        selection_basis = "所有方案均被硬阻断；仅返回分数最高的对比项，不得直接执行"
        executable = False

    winner = max(pool, key=lambda item: item["scores"]["total"])
    return {
        "selected_plan_id": winner["plan_id"],
        "selected_plan_name": winner["plan_name"],
        "executable_under_demo_rules": executable,
        "selection_basis": selection_basis,
        "selected_total_score": winner["scores"]["total"],
    }


def record_historical_decisions(
    graph: ContextGraph, business_input: dict[str, Any]
) -> list[str]:
    """用 Semantica record_decision() 先写入两条演示历史决策。"""
    decision_ids: list[str] = []
    for historical in business_input["historical_decisions"]:
        decision_ids.append(
            graph.record_decision(
                category=historical["category"],
                scenario=historical["scenario"],
                reasoning=historical["reasoning"],
                outcome=historical["outcome"],
                confidence=historical["confidence"],
                entities=historical["entities"],
                decision_maker="historical_import",
                metadata={"source": "demo_history"},
            )
        )
    return decision_ids


def build_precedent_query(
    config: dict[str, Any],
    business_input: dict[str, Any],
    selected_result: dict[str, Any],
) -> str:
    """生成当前决策场景文本。空格是为适配当前源码的 split() 分词。"""
    method_name = config["codebook"]["procurement_methods"][
        selected_result["procurement_method"]
    ]
    suppliers = {item["id"]: item for item in business_input["suppliers"]}
    risk_name = config["codebook"]["supplier_risk_levels"][
        suppliers[selected_result["supplier_id"]]["risk_level"]
    ]
    return (
        f"采购 合规 审查 金额 {business_input['case']['amount_cny']} "
        f"{method_name} 供应商 {risk_name}"
    )


def compact_precedents(precedents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留业务核对需要的前例字段。"""
    return [
        {
            "decision_id": item["decision"]["id"],
            "scenario": item["decision"]["scenario"],
            "outcome": item["decision"]["outcome"],
            "similarity": round(item["similarity"], 4),
            "content_similarity": round(item["content_similarity"], 4),
            "structural_similarity": round(item["structural_similarity"], 4),
        }
        for item in precedents
    ]


def write_json(path: Path, data: Any) -> None:
    """将可审计结果写成人可读 JSON。"""
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def print_summary(
    business_input: dict[str, Any],
    plan_results: list[dict[str, Any]],
    recommendation: dict[str, Any],
    precedents: list[dict[str, Any]],
    graph_stats: dict[str, Any],
    result_path: Path,
    graph_path: Path,
) -> None:
    """在终端给出一份简明的执行摘要。"""
    print("\n" + "=" * 72)
    print("Semantica 采购合规决策推演结果")
    print("=" * 72)
    case = business_input["case"]
    print(f"采购事项 : {case['id']} / {case['title']}")
    print(f"采购金额 : {case['amount_cny']:,.0f} 元")
    print("\n方案对比：")
    for result in plan_results:
        violations = ", ".join(result["violations"]) or "无"
        print(
            f"  {result['plan_id']}: {result['plan_name']} | "
            f"状态={result['status']} | 违规={violations} | "
            f"综合分={result['scores']['total']:.2f}"
        )

    print(f"\n推荐方案 : {recommendation['selected_plan_id']} / "
          f"{recommendation['selected_plan_name']}")
    print(f"选择根据 : {recommendation['selection_basis']}")
    print(f"匹配前例 : {len(precedents)} 条")
    print(
        f"图谱结果 : {graph_stats['node_count']} 个节点 / "
        f"{graph_stats['edge_count']} 条边"
    )
    print(f"详细结果 : {result_path}")
    print(f"图谱快照 : {graph_path}")


def run_demo(config_path: Path, input_path: Path, output_dir: Path) -> dict[str, Any]:
    """串起从输入到留档的完整业务流程。"""
    print("[1/8] 读取业务配置和采购数据")
    config = load_json(config_path)
    business_input = load_json(input_path)
    validate_input(config, business_input)

    print("[2/8] 用 ContextGraph 构建采购事项、证据、政策和候选方案")
    graph = build_context_graph(config, business_input)

    print("[3/8] 将每个候选方案转换为事实，调用 Reasoner 做链式推理")
    plan_results: list[dict[str, Any]] = []
    for plan in business_input["candidate_plans"]:
        facts = normalize_plan_to_facts(config, business_input, plan)
        inferred_facts, trace = run_semantica_reasoning(config, facts)
        plan_results.append(
            collect_plan_result(
                plan,
                facts,
                inferred_facts,
                trace,
                config["scoring"],
            )
        )

    print("[4/8] 用业务公式计算合规、价格、交付和综合分")
    calculate_comparable_scores(plan_results, config["scoring"])
    recommendation = choose_recommended_plan(plan_results)
    selected_result = next(
        item
        for item in plan_results
        if item["plan_id"] == recommendation["selected_plan_id"]
    )

    print("[5/8] 记入演示历史决策，再用 ContextGraph 查找相似前例")
    historical_decision_ids = record_historical_decisions(graph, business_input)
    precedent_query = build_precedent_query(config, business_input, selected_result)
    precedent_config = config["precedent_search"]
    precedents_raw = graph.find_precedents_by_scenario(
        scenario=precedent_query,
        category="procurement_compliance",
        limit=precedent_config["limit"],
        similarity_threshold=precedent_config["similarity_threshold"],
        # 当前 ContextGraph 这个方法的实现仍是词集合相似度；
        # 不把它误表述为这个演示真正调用了向量模型。
        use_semantic_search=False,
    )
    precedents = compact_precedents(precedents_raw)

    print("[6/8] 用 ContextGraph.record_decision() 写入最终决策与决策依据")
    fired_rule_ids = sorted(
        {
            trace_item["rule_id"]
            for trace_item in selected_result["inference_trace"]
            if trace_item["rule_id"]
        }
    )
    reasoning_text = (
        f"候选方案共{len(plan_results)}个；"
        f"推荐{selected_result['plan_id']}；"
        f"核心规则状态为{selected_result['status']}；"
        f"综合分{selected_result['scores']['total']}；"
        f"匹配历史前例{len(precedents)}条。"
    )
    # confidence 在这里是演示项目用“综合分/100”定义的决策信心度，
    # 它不是 Semantica 统计学意义上校准过的概率。
    decision_confidence = min(1.0, selected_result["scores"]["total"] / 100.0)
    final_decision_id = graph.record_decision(
        category="procurement_compliance",
        scenario=precedent_query,
        reasoning=reasoning_text,
        outcome=f"推荐执行 {selected_result['plan_id']}: {selected_result['plan_name']}",
        confidence=decision_confidence,
        entities=[
            business_input["case"]["id"],
            selected_result["plan_id"],
            selected_result["supplier_id"],
        ],
        decision_maker="procurement_compliance_demo",
        metadata={
            "selected_plan_id": selected_result["plan_id"],
            "selected_score": selected_result["scores"]["total"],
            "executable_under_demo_rules": recommendation[
                "executable_under_demo_rules"
            ],
        },
    )

    # record_decision() 会自动创建 decision -> entity 的 involves 边。
    # 下面再显式连到政策和本方案实际命中的规则，形成可追溯决策依据。
    for policy in config["policies"]:
        graph.add_edge(final_decision_id, policy["id"], "governed_by")
    for rule_id in fired_rule_ids:
        graph.add_edge(final_decision_id, rule_id, "evaluated_by")

    print("[7/8] 从最终决策节点向外遍历2跳，生成决策上下文")
    decision_context = graph.get_neighbors(
        final_decision_id,
        hops=2,
        include_distance_metadata=True,
    )

    print("[8/8] 导出完整推演结果和 ContextGraph JSON 快照")
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "采购合规推演结果.json"
    graph_path = output_dir / "采购合规上下文图.json"

    graph_stats = graph.stats()
    output = {
        "demo_name": config["demo_name"],
        "source_files": {
            "config": str(config_path),
            "business_input": str(input_path),
        },
        "case": deepcopy(business_input["case"]),
        "implementation_boundary": {
            "semantica_native": [
                "ContextGraph.add_node/add_edge 构建上下文图",
                "Reasoner.add_fact/add_rule/forward_chain 执行符号规则推理",
                "ContextGraph.record_decision 记录决策",
                "ContextGraph.find_precedents_by_scenario 查找前例",
                "ContextGraph.get_neighbors 遍历决策上下文",
                "ContextGraph.save_to_file 导出图快照",
            ],
            "demo_business_adapter": [
                "JSON 业务数据和配置格式",
                "将金额、方式、风险字段转成 Reasoner 事实",
                "候选方案的生成与输入",
                "硬阻断、扣分、价格分、交付分和选择顺序",
                "终端摘要和本业务结果 JSON 格式",
            ],
        },
        "plan_simulations": plan_results,
        "recommendation": recommendation,
        "precedent_search": {
            "query": precedent_query,
            "implementation_note": precedent_config["implementation_note"],
            "historical_decision_ids": historical_decision_ids,
            "matches": precedents,
        },
        "final_decision": {
            "decision_id": final_decision_id,
            "reasoning": reasoning_text,
            "confidence": round(decision_confidence, 4),
            "confidence_note": "演示定义为综合分/100，不是校准概率",
            "fired_rule_ids": fired_rule_ids,
            "context_within_2_hops": decision_context,
        },
        "graph_stats": graph_stats,
    }

    write_json(result_path, output)
    graph.save_to_file(str(graph_path))
    print_summary(
        business_input,
        plan_results,
        recommendation,
        precedents,
        graph_stats,
        result_path,
        graph_path,
    )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantica 采购合规审查与多方案决策推演"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"业务配置 JSON，默认：{DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"采购业务输入 JSON，默认：{DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录，默认：{DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run_demo(
        config_path=arguments.config.resolve(),
        input_path=arguments.input.resolve(),
        output_dir=arguments.output_dir.resolve(),
    )
