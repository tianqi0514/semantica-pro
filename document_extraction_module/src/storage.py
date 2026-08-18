from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .exporters import to_csv_text, to_json_text


_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        path.mkdir(parents=False, exist_ok=False)
        return job_id, path

    def path_for(self, job_id: str) -> Path:
        if not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError("无效的任务编号")
        path = (self.root / job_id).resolve()
        if path.parent != self.root:
            raise ValueError("无效的任务路径")
        return path

    def save_result(self, job_id: str, result: Dict[str, Any]) -> None:
        path = self.path_for(job_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "result.json").write_text(to_json_text(result), encoding="utf-8")
        (path / "semantica-import.csv").write_text(to_csv_text(result), encoding="utf-8")

    def save_status(self, job_id: str, status: Dict[str, Any]) -> None:
        """Persist progress atomically so polling never observes partial JSON."""
        path = self.path_for(job_id)
        path.mkdir(parents=True, exist_ok=True)
        status_path = path / "status.json"
        temporary_path = path / "status.json.tmp"
        temporary_path.write_text(to_json_text(status), encoding="utf-8")
        temporary_path.replace(status_path)

    def load_status(self, job_id: str) -> Dict[str, Any]:
        status_path = self.path_for(job_id) / "status.json"
        if not status_path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(status_path.read_text(encoding="utf-8"))

    def list_statuses(self, *, active_only: bool = False, limit: int = 20) -> list[Dict[str, Any]]:
        statuses: list[Dict[str, Any]] = []
        active_states = {"queued", "processing"}
        for job_path in self.root.iterdir():
            if not job_path.is_dir() or not _JOB_ID_RE.fullmatch(job_path.name):
                continue
            try:
                status = self.load_status(job_path.name)
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                continue
            if active_only and status.get("status") not in active_states:
                continue
            statuses.append(status)
        statuses.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return statuses[: max(1, min(limit, 100))]

    def recover_incomplete_jobs(self) -> int:
        """Mark jobs abandoned by a service restart instead of leaving them spinning forever."""
        recovered = 0
        now = datetime.now(timezone.utc).isoformat()
        for status in self.list_statuses(limit=100):
            if status.get("status") not in {"queued", "processing"}:
                continue
            for item in status.get("items", []):
                if item.get("status") in {"queued", "processing"}:
                    item.update(
                        {
                            "status": "failed",
                            "error": "抽取服务曾重启，此文件未完成，请重新提交",
                            "completed_at": now,
                        }
                    )
            status.update(
                {
                    "status": "failed",
                    "phase": "interrupted",
                    "completed": int(status.get("total", len(status.get("items", [])))),
                    "failed": sum(1 for item in status.get("items", []) if item.get("status") == "failed"),
                    "percent": 100,
                    "current_index": None,
                    "current_file": None,
                    "updated_at": now,
                    "completed_at": now,
                    "result_ready": False,
                    "message": "抽取服务重启导致任务中断，请重新提交文件",
                }
            )
            self.save_status(status["job_id"], status)
            recovered += 1
        return recovered

    def load_result(self, job_id: str) -> Dict[str, Any]:
        result_path = self.path_for(job_id) / "result.json"
        if not result_path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(result_path.read_text(encoding="utf-8"))

    def export_path(self, job_id: str, export_format: str) -> Path:
        filename = "result.json" if export_format == "json" else "semantica-import.csv"
        path = self.path_for(job_id) / filename
        if not path.exists():
            raise FileNotFoundError(job_id)
        return path
