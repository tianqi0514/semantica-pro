#!/usr/bin/env python3
"""Verify that the procurement compliance demo is ready for presentation."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def get_json(path: str, query: dict[str, str] | None = None):
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def check(label: str, condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(f"{label}：{detail}")
    print(f"  ✓ {label}：{detail}")


def main() -> int:
    print("验证演示环境：")
    try:
        health = get_json("/api/health")
        check("服务健康", health.get("status") == "ok", health.get("status", "unknown"))

        stats = get_json("/api/graph/stats")
        check("图谱规模", stats.get("node_count", 0) >= 30, f"{stats.get('node_count')} 个节点 / {stats.get('edge_count')} 条关系")

        decisions = get_json("/api/decisions")
        decision_ids = {item.get("decision_id") for item in decisions}
        check("决策案例", {"DEC-2025-042", "DEC-2025-017", "DEC-2024-088"}.issubset(decision_ids), "高风险、合规、历史先例各 1 个")

        compliance = get_json("/api/decisions/DEC-2025-042/compliance")
        check("违规命中", compliance.get("compliant") is False and len(compliance.get("violations", [])) == 4, "命中 4 条制度规则")

        path = get_json(
            "/api/graph/path",
            {"source": "DEC-2025-042", "target": "EMP-LI-MING", "algorithm": "bfs", "directed": "false"},
        )
        check("关联路径", bool(path.get("path")), "审查决策可追溯到采购经办人")

        semantic = get_json(
            "/api/graph/semantic-neighborhood",
            {"node_id": "DEC-2025-042", "top_k": "5", "min_similarity": "0.50"},
        )
        check("语义邻域", len(semantic.get("neighbors", [])) >= 1, "预置向量可用于相似对象发现")

        bounds = get_json("/api/temporal/bounds")
        check("时间范围", bool(bounds.get("min")) and bool(bounds.get("max")), f"{bounds.get('min')} → {bounds.get('max')}")

        schemes = get_json("/api/vocabulary/schemes")
        check("SKOS 术语表", any("采购合规" in item.get("label", "") for item in schemes), "采购合规审查术语表已加载")

        registry = get_json("/api/ontology/registry")
        check("OWL 本体", any("采购合规" in item.get("name", "") for item in registry), "采购合规审查本体已注册")

    except (AssertionError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  ✗ 验证失败：{exc}", file=sys.stderr)
        return 1

    print("验证通过，可以开始演示。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
