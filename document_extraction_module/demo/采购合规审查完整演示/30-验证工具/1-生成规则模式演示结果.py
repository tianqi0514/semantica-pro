"""离线复现采购合规演示结果。

这个脚本不调用 Kimi，也不伪造结果。它直接使用当前仓库的 Semantica
文档抽取服务，按“3 份文件 × 3 个场景模板”执行规则模式抽取，然后调用
线上服务相同的合并器和导出器生成 JSON、CSV。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
DEMO_ROOT = SCRIPT_PATH.parents[1]
REPOSITORY_ROOT = SCRIPT_PATH.parents[4]
MODULE_ROOT = REPOSITORY_ROOT / "document_extraction_module"
sys.path.insert(0, str(REPOSITORY_ROOT))

from document_extraction_module.src.batch import combine_results, tag_result_scenario
from document_extraction_module.src.exporters import to_csv_text, to_json_text
from document_extraction_module.src.extractor import DocumentExtractionService
from document_extraction_module.src.models import ExtractionConfig


DOCUMENTS = [
    DEMO_ROOT / "10-上传材料" / "1-采购立项与评审记录.txt",
    DEMO_ROOT / "10-上传材料" / "2-供应商尽调与风险核查报告.txt",
    DEMO_ROOT / "10-上传材料" / "3-采购合同关键条款确认稿.txt",
]

SCENARIOS = [
    ("procurement-compliance", "采购合规审查"),
    ("supplier-risk", "供应商风险画像"),
    ("contract-key-terms", "合同关键要素"),
]


def load_regex_config(scenario_id: str) -> ExtractionConfig:
    """读取产品内置模板，只把执行方式切换为可复现的规则模式。"""

    config_path = MODULE_ROOT / "config" / f"{scenario_id}.json"
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_config["method"] = "regex"
    return ExtractionConfig.model_validate(raw_config)


def main() -> None:
    service = DocumentExtractionService()
    completed_results = []
    task_items = []
    task_index = 0

    for file_index, document_path in enumerate(DOCUMENTS, start=1):
        for scenario_id, scenario_name in SCENARIOS:
            task_index += 1
            config = load_regex_config(scenario_id)
            result = service.extract(document_path, document_path.name, config)

            # 产品后端会在每个事实上附上提交任务时的模板标识和版本。
            tag_result_scenario(
                result,
                {"id": scenario_id, "name": scenario_name, "version": 1},
            )
            completed_results.append((document_path.name, result))
            task_items.append(
                {
                    "index": task_index,
                    "file_index": file_index,
                    "source_name": document_path.name,
                    "scenario_id": scenario_id,
                    "scenario_name": scenario_name,
                    "scenario_version": 1,
                    "status": "completed",
                    "statistics": result["statistics"],
                }
            )

    combined = combine_results(completed_results, task_items)
    combined["job_id"] = "offline-procurement-demo"

    output_root = DEMO_ROOT / "40-实际输出"
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "1-规则模式联合抽取结果.json"
    csv_path = output_root / "2-规则模式联合抽取结果.csv"
    json_path.write_text(to_json_text(combined), encoding="utf-8")
    csv_path.write_text(to_csv_text(combined), encoding="utf-8")

    summary = {
        "documents": combined["statistics"]["documents"],
        "template_runs": combined["statistics"]["template_runs"],
        "entities": combined["statistics"]["entities"],
        "relationships": combined["statistics"]["relationships"],
        "warnings": combined["statistics"]["warnings"],
        "scenarios": combined["scenarios"],
        "json": str(json_path),
        "csv": str(csv_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
