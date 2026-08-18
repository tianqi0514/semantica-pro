from __future__ import annotations

import copy
import json
from typing import Any


def _append_unique(values: list[Any], additions: list[Any]) -> None:
    seen = {json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in values}
    for value in additions:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            values.append(value)
            seen.add(marker)


def _merge_metadata(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    source_files = target.setdefault("source_files", [])
    document_ids = target.setdefault("document_ids", [])
    for metadata in (target, incoming):
        source_file = metadata.get("source_file")
        document_id = metadata.get("document_id")
        if source_file and source_file not in source_files:
            source_files.append(source_file)
        if document_id and document_id not in document_ids:
            document_ids.append(document_id)
        for value in metadata.get("source_files", []):
            if value not in source_files:
                source_files.append(value)
        for value in metadata.get("document_ids", []):
            if value not in document_ids:
                document_ids.append(value)

    evidence = target.setdefault("evidence", [])
    _append_unique(evidence, incoming.get("evidence", []))
    scenario_refs = target.setdefault("scenario_refs", [])
    _append_unique(scenario_refs, incoming.get("scenario_refs", []))


def tag_result_scenario(result: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Attach an immutable template snapshot to every extracted fact for auditability."""

    scenario_ref = {
        "id": scenario["id"],
        "name": scenario["name"],
        "version": int(scenario["version"]),
    }
    run = result.setdefault("run", {})
    run.update(
        {
            "scenario": scenario["name"],
            "scenario_id": scenario["id"],
            "scenario_version": int(scenario["version"]),
        }
    )
    for document in result.get("documents", []):
        refs = document.setdefault("scenario_refs", [])
        _append_unique(refs, [scenario_ref])
    for collection_name in ("entities", "relationships"):
        for item in result.get(collection_name, []):
            metadata = item.setdefault("metadata", {})
            refs = metadata.setdefault("scenario_refs", [])
            _append_unique(refs, [scenario_ref])
    return result


def combine_results(
    completed: list[tuple[str, dict[str, Any]]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Combine per-document extraction output into one importable graph payload."""

    document_map: dict[str, dict[str, Any]] = {}
    entity_map: dict[str, dict[str, Any]] = {}
    relationship_map: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    chunks = 0
    chunks_processed = 0

    for source_name, result in completed:
        run = result.get("run", {})
        scenario_name = str(run.get("scenario", "未命名场景"))
        for document in result.get("documents", []):
            document_id = str(document.get("id") or f"{source_name}:{len(document_map)}")
            current_document = document_map.get(document_id)
            if current_document is None:
                document_map[document_id] = copy.deepcopy(document)
            else:
                refs = current_document.setdefault("scenario_refs", [])
                _append_unique(refs, document.get("scenario_refs", []))
        stats = result.get("statistics", {})
        chunks += int(stats.get("chunks", 0))
        chunks_processed += int(stats.get("chunks_processed", 0))
        warnings.extend(
            f"{source_name} · {scenario_name}：{warning}" for warning in result.get("warnings", [])
        )

        for entity in result.get("entities", []):
            entity_id = entity["id"]
            current = entity_map.get(entity_id)
            if current is None:
                current = copy.deepcopy(entity)
                metadata = current.setdefault("metadata", {})
                _merge_metadata(metadata, {})
                entity_map[entity_id] = current
            else:
                current["confidence"] = max(
                    float(current.get("confidence", 0.0)),
                    float(entity.get("confidence", 0.0)),
                )
                _merge_metadata(current.setdefault("metadata", {}), entity.get("metadata", {}) or {})

        for relationship in result.get("relationships", []):
            relationship_id = relationship["id"]
            current = relationship_map.get(relationship_id)
            if current is None:
                current = copy.deepcopy(relationship)
                metadata = current.setdefault("metadata", {})
                _merge_metadata(metadata, {})
                relationship_map[relationship_id] = current
            else:
                current["weight"] = max(
                    float(current.get("weight", 0.0)),
                    float(relationship.get("weight", 0.0)),
                )
                current_metadata = current.setdefault("metadata", {})
                incoming_metadata = relationship.get("metadata", {}) or {}
                current_metadata["confidence"] = max(
                    float(current_metadata.get("confidence", 0.0)),
                    float(incoming_metadata.get("confidence", 0.0)),
                )
                _merge_metadata(current_metadata, incoming_metadata)

    failed = sum(1 for item in items if item.get("status") == "failed")
    succeeded = len(completed)
    # Use the upload index rather than the display filename so that two distinct
    # files named, for example, ``合同.pdf`` are still counted independently.
    all_files = {
        int(item["file_index"])
        for item in items
        if item.get("file_index") is not None
    }
    successful_files = {
        int(item["file_index"])
        for item in items
        if item.get("file_index") is not None and item.get("status") == "completed"
    }
    # Legacy callers did not provide file_index. Preserve their established
    # source-name counting behavior.
    if not all_files:
        all_files = {
            str(item.get("source_name", ""))
            for item in items
            if item.get("source_name")
        }
        successful_files = {source_name for source_name, _ in completed}
    scenario_refs: dict[str, dict[str, Any]] = {}
    for item in items:
        scenario_id = item.get("scenario_id")
        if not scenario_id:
            continue
        ref = scenario_refs.setdefault(
            str(scenario_id),
            {
                "id": str(scenario_id),
                "name": str(item.get("scenario_name") or scenario_id),
                "version": int(item.get("scenario_version") or 1),
                "runs_succeeded": 0,
                "runs_failed": 0,
            },
        )
        if item.get("status") == "completed":
            ref["runs_succeeded"] += 1
        elif item.get("status") == "failed":
            ref["runs_failed"] += 1

    for ref in scenario_refs.values():
        scenario_id = ref["id"]
        ref["entities"] = sum(
            1
            for entity in entity_map.values()
            if any(item.get("id") == scenario_id for item in entity.get("metadata", {}).get("scenario_refs", []))
        )
        ref["relationships"] = sum(
            1
            for relationship in relationship_map.values()
            if any(item.get("id") == scenario_id for item in relationship.get("metadata", {}).get("scenario_refs", []))
        )

    first_run = copy.deepcopy(completed[0][1].get("run", {})) if completed else {}
    first_run.update(
        {
            "batch": True,
            "scenario": "多场景联合抽取" if len(scenario_refs) > 1 else first_run.get("scenario", ""),
            "scenario_ids": list(scenario_refs),
            "scenario_names": [item["name"] for item in scenario_refs.values()],
            "files_total": len(all_files),
            "files_succeeded": len(successful_files),
            "files_failed": len(all_files - successful_files),
            "template_runs_total": len(items),
            "template_runs_succeeded": succeeded,
            "template_runs_failed": failed,
        }
    )
    if len(scenario_refs) > 1:
        first_run.pop("scenario_id", None)
        first_run.pop("scenario_version", None)
    return {
        "schema_version": "1.0",
        "run": first_run,
        "batch": {
            "total": len(items),
            "succeeded": succeeded,
            "failed": failed,
            "files_total": len(all_files),
            "templates_total": len(scenario_refs),
            "items": items,
        },
        "scenarios": list(scenario_refs.values()),
        "documents": list(document_map.values()),
        "entities": sorted(entity_map.values(), key=lambda item: (item.get("type", ""), item.get("text", ""))),
        "relationships": sorted(
            relationship_map.values(),
            key=lambda item: (item.get("type", ""), item.get("source", ""), item.get("target", "")),
        ),
        "statistics": {
            "documents": len(successful_files),
            "documents_failed": len(all_files - successful_files),
            "template_runs": succeeded,
            "template_runs_failed": failed,
            "chunks": chunks,
            "chunks_processed": chunks_processed,
            "entities": len(entity_map),
            "relationships": len(relationship_map),
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }
