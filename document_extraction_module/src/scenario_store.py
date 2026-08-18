from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import ExtractionConfig, ScenarioTemplateWrite


BUILTIN_METADATA = {
    "procurement-compliance": {
        "name": "采购合规审查",
        "description": "从采购、招投标、评审和中标材料中抽取项目、参与方、金额、制度与风险关系。",
        "category": "采购管理",
    },
    "contract-key-terms": {
        "name": "合同关键要素",
        "description": "识别合同主体、金额、日期、履约义务、付款与违约条款，形成可核对的合同要素图谱。",
        "category": "合同管理",
    },
    "supplier-risk": {
        "name": "供应商风险画像",
        "description": "从工商、处罚、失信和内部名单材料中抽取供应商关联方、处罚与风险记录。",
        "category": "供应商管理",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScenarioStore:
    """SQLite-backed scenario template catalogue with immutable IDs and versions."""

    def __init__(self, database_path: Path, config_root: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_root = Path(config_root)
        self._initialize()
        self._seed_builtins()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '通用',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    built_in INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _seed_builtins(self) -> None:
        now = _utc_now()
        with self._connect() as connection:
            for scenario_id, metadata in BUILTIN_METADATA.items():
                config_path = self.config_root / f"{scenario_id}.json"
                if not config_path.exists():
                    continue
                config = ExtractionConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
                config = config.model_copy(update={"scenario_name": metadata["name"]})
                connection.execute(
                    """
                    INSERT OR IGNORE INTO scenario_templates
                        (id, name, description, category, enabled, built_in, version,
                         config_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, 1, 1, ?, ?, ?)
                    """,
                    (
                        scenario_id,
                        metadata["name"],
                        metadata["description"],
                        metadata["category"],
                        config.model_dump_json(exclude={"api_key"}),
                        now,
                        now,
                    ),
                )

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM scenario_templates"
        parameters: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY built_in DESC, category, updated_at DESC, name"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._public(row) for row in rows]

    def get(self, scenario_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scenario_templates WHERE id = ?", (scenario_id,)
            ).fetchone()
        if row is None:
            raise KeyError(scenario_id)
        return self._public(row)

    def get_many(self, scenario_ids: Iterable[str], *, require_enabled: bool = True) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(value).strip() for value in scenario_ids if str(value).strip()))
        templates = [self.get(scenario_id) for scenario_id in unique_ids]
        if require_enabled:
            disabled = [item["name"] for item in templates if not item["enabled"]]
            if disabled:
                raise ValueError(f"以下场景模板已停用：{'、'.join(disabled)}")
        return templates

    def create(self, payload: ScenarioTemplateWrite) -> dict[str, Any]:
        scenario_id = f"scenario-{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        config = payload.config.model_copy(update={"scenario_name": payload.name})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scenario_templates
                    (id, name, description, category, enabled, built_in, version,
                     config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, ?)
                """,
                (
                    scenario_id,
                    payload.name,
                    payload.description,
                    payload.category,
                    int(payload.enabled),
                    config.model_dump_json(exclude={"api_key"}),
                    now,
                    now,
                ),
            )
        return self.get(scenario_id)

    def update(self, scenario_id: str, payload: ScenarioTemplateWrite) -> dict[str, Any]:
        current = self.get(scenario_id)
        config = payload.config.model_copy(update={"scenario_name": payload.name})
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scenario_templates
                SET name = ?, description = ?, category = ?, enabled = ?,
                    version = version + 1, config_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.name,
                    payload.description,
                    payload.category,
                    int(payload.enabled),
                    config.model_dump_json(exclude={"api_key"}),
                    _utc_now(),
                    scenario_id,
                ),
            )
        if not cursor.rowcount:
            raise KeyError(scenario_id)
        updated = self.get(scenario_id)
        updated["previous_version"] = current["version"]
        return updated

    def duplicate(self, scenario_id: str, *, name: str | None = None) -> dict[str, Any]:
        source = self.get(scenario_id)
        payload = ScenarioTemplateWrite.model_validate(
            {
                "name": (name or f"{source['name']}（副本）").strip(),
                "description": source["description"],
                "category": source["category"],
                "enabled": True,
                "config": source["config"],
            }
        )
        return self.create(payload)

    def delete(self, scenario_id: str) -> None:
        current = self.get(scenario_id)
        if current["built_in"]:
            raise PermissionError("内置模板不能删除，可以将其停用或复制后修改")
        with self._connect() as connection:
            connection.execute("DELETE FROM scenario_templates WHERE id = ?", (scenario_id,))

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "enabled": bool(row["enabled"]),
            "built_in": bool(row["built_in"]),
            "version": int(row["version"]),
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
