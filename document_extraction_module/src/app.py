from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr

from .batch import combine_results
from .document_loader import SUPPORTED_SUFFIXES
from .extractor import DocumentExtractionService
from .models import ExtractionConfig, LLMSettingsUpdate
from .settings_store import LLMSettingsStore
from .storage import JobStore


MODULE_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(MODULE_ROOT / ".env")

STATIC_ROOT = MODULE_ROOT / "static"
SCENARIO_ROOT = MODULE_ROOT / "config"
JOB_ROOT = Path(os.getenv("DOCUMENT_EXTRACT_JOB_ROOT", MODULE_ROOT / "data" / "jobs"))
SETTINGS_DB = Path(os.getenv("DOCUMENT_EXTRACT_SETTINGS_DB", JOB_ROOT.parent / "settings.db"))
SETTINGS_KEY = Path(os.getenv("DOCUMENT_EXTRACT_SETTINGS_KEY_FILE", SETTINGS_DB.with_name(".settings.key")))
MAX_UPLOAD_BYTES = int(os.getenv("DOCUMENT_EXTRACT_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_BATCH_FILES = int(os.getenv("DOCUMENT_EXTRACT_MAX_BATCH_FILES", "20"))
ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "DOCUMENT_EXTRACT_ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if value.strip()
]

app = FastAPI(title="Semantica 文档知识抽取", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

store = JobStore(JOB_ROOT)
settings_store = LLMSettingsStore(SETTINGS_DB, SETTINGS_KEY)
service = DocumentExtractionService()
store.recover_incomplete_jobs()

_STAGE_PROGRESS = {
    "waiting": 0.0,
    "loading_document": 0.02,
    "splitting_text": 0.04,
    "checking_model": 0.06,
    "preparing_extractor": 0.06,
    "extracting_entities": 0.10,
    "extracting_relations": 0.55,
    "merging_results": 0.92,
    "chunk_completed": 1.0,
    "chunk_failed": 1.0,
    "document_completed": 1.0,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_progress(status: dict[str, Any]) -> None:
    items = status.get("items", [])
    total = int(status.get("total", len(items)))
    completed_items = [item for item in items if item.get("status") in {"completed", "failed"}]
    succeeded = sum(1 for item in items if item.get("status") == "completed")
    failed = sum(1 for item in items if item.get("status") == "failed")
    progress_units = float(len(completed_items))
    current = next((item for item in items if item.get("status") == "processing"), None)
    if current is not None:
        chunks_total = int(current.get("chunks_total") or 0)
        chunks_completed = int(current.get("chunks_completed") or 0)
        stage_fraction = _STAGE_PROGRESS.get(str(current.get("stage", "waiting")), 0.0)
        if chunks_total:
            file_progress = chunks_completed / chunks_total
            if chunks_completed < chunks_total and current.get("stage") not in {"chunk_completed", "chunk_failed"}:
                file_progress = (chunks_completed + stage_fraction) / chunks_total
            progress_units += min(0.99, file_progress)
        else:
            progress_units += min(0.05, stage_fraction)
    status.update(
        {
            "completed": len(completed_items),
            "succeeded": succeeded,
            "failed": failed,
            "percent": 100 if total and len(completed_items) == total else round(progress_units / max(total, 1) * 100),
            "current_index": current.get("index") if current else None,
            "current_file": current.get("source_name") if current else None,
            "current_stage": current.get("stage") if current else None,
            "updated_at": _utc_now(),
        }
    )


def _public_progress(status: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(status, ensure_ascii=False))
    try:
        started = datetime.fromisoformat(payload["started_at"])
        ended = datetime.fromisoformat(payload.get("completed_at") or _utc_now())
        payload["elapsed_seconds"] = max(0, round((ended - started).total_seconds()))
    except (KeyError, TypeError, ValueError):
        payload["elapsed_seconds"] = 0
    now = datetime.now(timezone.utc)
    for item in payload.get("items", []):
        try:
            stage_started = datetime.fromisoformat(item["stage_started_at"])
            stage_ended = datetime.fromisoformat(item.get("completed_at")) if item.get("completed_at") else now
            item["stage_elapsed_seconds"] = max(0, round((stage_ended - stage_started).total_seconds()))
        except (KeyError, TypeError, ValueError):
            item["stage_elapsed_seconds"] = 0
    return payload


async def _run_batch_extraction(
    job_id: str,
    prepared_files: list[tuple[int, str, Path]],
    config: ExtractionConfig,
) -> None:
    status = store.load_status(job_id)
    completed_results: list[tuple[str, dict[str, Any]]] = []
    try:
        status.update({"status": "processing", "phase": "extracting"})
        _update_progress(status)
        store.save_status(job_id, status)

        for index, source_name, source_path in prepared_files:
            item = next(candidate for candidate in status["items"] if candidate["index"] == index)
            item.update(
                {
                    "status": "processing",
                    "started_at": _utc_now(),
                    "stage": "waiting",
                    "stage_started_at": _utc_now(),
                    "chunks_total": 0,
                    "chunks_completed": 0,
                }
            )
            _update_progress(status)
            store.save_status(job_id, status)

            def report_file_progress(progress: dict[str, Any]) -> None:
                if progress.get("stage") and progress["stage"] != item.get("stage"):
                    item["stage_started_at"] = _utc_now()
                item.update(progress)
                _update_progress(status)
                store.save_status(job_id, status)

            try:
                result = await asyncio.to_thread(
                    service.extract,
                    source_path,
                    source_name,
                    config,
                    report_file_progress,
                )
                completed_results.append((source_name, result))
                item.update(
                    {
                        "status": "completed",
                        "statistics": result["statistics"],
                        "completed_at": _utc_now(),
                    }
                )
            except Exception as exc:
                item.update(
                    {
                        "status": "failed",
                        "error": service._safe_error(exc),
                        "completed_at": _utc_now(),
                    }
                )
            _update_progress(status)
            store.save_status(job_id, status)

        status["phase"] = "finalizing"
        _update_progress(status)
        store.save_status(job_id, status)
        combined = combine_results(completed_results, status["items"])
        combined["job_id"] = job_id
        store.save_result(job_id, combined)
        failed = int(combined["batch"]["failed"])
        final_status = "failed" if not completed_results else "partial" if failed else "completed"
        status.update(
            {
                "status": final_status,
                "phase": "completed",
                "completed_at": _utc_now(),
                "result_ready": True,
            }
        )
        _update_progress(status)
        store.save_status(job_id, status)
    except Exception as exc:
        now = _utc_now()
        for item in status.get("items", []):
            if item.get("status") in {"queued", "processing"}:
                item.update(
                    {
                        "status": "failed",
                        "error": "任务执行异常，未完成此文件",
                        "completed_at": now,
                    }
                )
        status.update(
            {
                "status": "failed",
                "phase": "failed",
                "completed_at": now,
                "result_ready": False,
                "message": service._safe_error(exc),
            }
        )
        _update_progress(status)
        store.save_status(job_id, status)


def _parse_config(config_json: str) -> ExtractionConfig:
    try:
        return ExtractionConfig.model_validate_json(config_json)
    except Exception as exc:
        # Do not echo raw validation input: legacy clients may include an API key.
        raise HTTPException(status_code=422, detail="抽取配置无效，请检查字段格式和取值范围") from exc


def _apply_saved_llm_settings(config: ExtractionConfig) -> ExtractionConfig:
    if config.method != "llm":
        return config
    runtime_settings = settings_store.runtime_settings()
    request_api_key = config.api_key.get_secret_value() if config.api_key else None
    key = request_api_key or runtime_settings["api_key"]
    if not key:
        raise HTTPException(
            status_code=422,
            detail="Kimi K3 API Key 尚未配置，请先在模型配置中保存密钥",
        )
    return config.model_copy(
        update={
            "provider": runtime_settings["provider"],
            "model": runtime_settings["model"],
            "base_url": runtime_settings["base_url"],
            "api_key": SecretStr(key),
        }
    )


def _test_llm_connection(config: ExtractionConfig | None = None) -> dict[str, Any]:
    runtime = settings_store.runtime_settings()
    api_key = (
        config.api_key.get_secret_value()
        if config is not None and config.api_key is not None
        else runtime.get("api_key")
    )
    base_url = str((config.base_url if config else None) or runtime["base_url"]).rstrip("/")
    model = str((config.model if config else None) or runtime["model"])
    if not api_key:
        raise RuntimeError("Kimi K3 API Key 尚未配置")

    request = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            region = "中国站应使用 https://api.moonshot.cn/v1；国际站应使用 https://api.moonshot.ai/v1"
            raise RuntimeError(f"Kimi 鉴权失败（HTTP 401），API Key 与接口区域可能不匹配。{region}") from exc
        if exc.code == 403:
            raise RuntimeError("Kimi 拒绝访问（HTTP 403），请检查账号权限和模型权限") from exc
        if exc.code == 429:
            raise RuntimeError("Kimi 请求受限（HTTP 429），请检查余额或稍后重试") from exc
        raise RuntimeError(f"Kimi 连接测试失败（HTTP {exc.code}）") from exc
    except Exception as exc:
        raise RuntimeError(f"无法连接 Kimi 模型服务：{type(exc).__name__}") from exc

    model_ids = [item.get("id") for item in payload.get("data", []) if isinstance(item, dict)]
    if model not in model_ids:
        raise RuntimeError(f"连接成功，但当前账号的模型列表中没有 {model}")
    return {
        "status": "ok",
        "base_url": base_url,
        "model": model,
        "model_available": True,
        "model_count": len(model_ids),
    }


async def _ensure_llm_connection(config: ExtractionConfig) -> None:
    if config.method != "llm":
        return
    try:
        await asyncio.to_thread(_test_llm_connection, config)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_ROOT / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "module": "document-extraction",
        "supported_formats": sorted(SUPPORTED_SUFFIXES),
        "llm": settings_store.public_settings(),
    }


@app.get("/api/settings/llm")
async def get_llm_settings() -> dict:
    return settings_store.public_settings()


@app.put("/api/settings/llm")
async def update_llm_settings(payload: LLMSettingsUpdate) -> dict:
    api_key = payload.api_key.get_secret_value().strip() if payload.api_key else None
    return settings_store.update(
        provider=payload.provider,
        display_name=payload.display_name.strip(),
        model=payload.model.strip(),
        base_url=payload.base_url,
        api_key=api_key,
        clear_api_key=payload.clear_api_key,
    )


@app.post("/api/settings/llm/test")
async def test_llm_settings() -> dict:
    try:
        return await asyncio.to_thread(_test_llm_connection)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/scenarios/procurement-compliance")
async def procurement_scenario() -> dict:
    path = SCENARIO_ROOT / "procurement-compliance.json"
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/extractions")
async def create_extraction(
    file: UploadFile = File(...),
    config_json: str = Form(...),
) -> dict:
    source_name = Path(file.filename or "document.txt").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"不支持的文件格式：{suffix}")

    config = _apply_saved_llm_settings(_parse_config(config_json))
    await _ensure_llm_connection(config)

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 限制",
        )

    job_id, job_path = store.create()
    source_path = job_path / f"source{suffix}"
    source_path.write_bytes(content)

    try:
        result = await asyncio.to_thread(service.extract, source_path, source_name, config)
        result["job_id"] = job_id
        store.save_result(job_id, result)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"job_id": job_id, "status": "completed", "result": result}


@app.post("/api/extractions/batch", status_code=202)
async def create_batch_extraction(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    config_json: str = Form(...),
) -> dict:
    if not files:
        raise HTTPException(status_code=422, detail="请至少上传一个文件")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=422, detail=f"一次最多上传 {MAX_BATCH_FILES} 个文件")

    config = _apply_saved_llm_settings(_parse_config(config_json))
    await _ensure_llm_connection(config)
    job_id, job_path = store.create()
    source_root = job_path / "sources"
    source_root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    prepared_files: list[tuple[int, str, Path]] = []

    for index, upload in enumerate(files, start=1):
        source_name = Path(upload.filename or f"document-{index}.txt").name
        suffix = Path(source_name).suffix.lower()
        item: dict[str, Any] = {"index": index, "source_name": source_name, "status": "queued"}
        try:
            if suffix not in SUPPORTED_SUFFIXES:
                raise ValueError(f"不支持的文件格式：{suffix or '无扩展名'}")
            content = await upload.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 限制")
            source_path = source_root / f"source-{index:03d}{suffix}"
            source_path.write_bytes(content)
            prepared_files.append((index, source_name, source_path))
        except Exception as exc:
            item.update(
                {
                    "status": "failed",
                    "error": service._safe_error(exc),
                    "completed_at": _utc_now(),
                }
            )
        finally:
            await upload.close()
        items.append(item)

    now = _utc_now()
    status: dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "phase": "queued",
        "total": len(items),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "percent": 0,
        "current_index": None,
        "current_file": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "result_ready": False,
        "method": config.method,
        "model": config.model if config.method == "llm" else None,
        "items": items,
    }
    _update_progress(status)
    store.save_status(job_id, status)
    background_tasks.add_task(_run_batch_extraction, job_id, prepared_files, config)
    return {
        "job_id": job_id,
        "status": "queued",
        "progress_url": f"/api/extractions/{job_id}/status",
        "progress": _public_progress(status),
    }


@app.get("/api/extraction-jobs/active")
async def get_active_extraction_jobs() -> dict:
    return {"jobs": [_public_progress(item) for item in store.list_statuses(active_only=True)]}


@app.get("/api/extractions/{job_id}/status")
async def get_extraction_status(job_id: str) -> dict:
    try:
        return _public_progress(store.load_status(job_id))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="抽取任务不存在") from exc


@app.get("/api/extractions/{job_id}")
async def get_extraction(job_id: str) -> dict:
    try:
        return store.load_result(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="抽取任务不存在") from exc


@app.get("/api/extractions/{job_id}/export/{export_format}")
async def export_extraction(job_id: str, export_format: str) -> FileResponse:
    if export_format not in {"json", "csv"}:
        raise HTTPException(status_code=422, detail="只支持 json 或 csv")
    try:
        path = store.export_path(job_id, export_format)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="抽取任务不存在") from exc
    media_type = "application/json" if export_format == "json" else "text/csv"
    download_name = f"semantica-document-extraction.{export_format}"
    return FileResponse(path, media_type=media_type, filename=download_name)
