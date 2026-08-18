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


def combine_results(
    completed: list[tuple[str, dict[str, Any]]],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Combine per-document extraction output into one importable graph payload."""

    documents: list[dict[str, Any]] = []
    entity_map: dict[str, dict[str, Any]] = {}
    relationship_map: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    chunks = 0
    chunks_processed = 0

    for source_name, result in completed:
        documents.extend(copy.deepcopy(result.get("documents", [])))
        stats = result.get("statistics", {})
        chunks += int(stats.get("chunks", 0))
        chunks_processed += int(stats.get("chunks_processed", 0))
        warnings.extend(f"{source_name}：{warning}" for warning in result.get("warnings", []))

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
    first_run = copy.deepcopy(completed[0][1].get("run", {})) if completed else {}
    first_run.update(
        {
            "batch": True,
            "files_total": len(items),
            "files_succeeded": succeeded,
            "files_failed": failed,
        }
    )
    return {
        "schema_version": "1.0",
        "run": first_run,
        "batch": {
            "total": len(items),
            "succeeded": succeeded,
            "failed": failed,
            "items": items,
        },
        "documents": documents,
        "entities": sorted(entity_map.values(), key=lambda item: (item.get("type", ""), item.get("text", ""))),
        "relationships": sorted(
            relationship_map.values(),
            key=lambda item: (item.get("type", ""), item.get("source", ""), item.get("target", "")),
        ),
        "statistics": {
            "documents": succeeded,
            "documents_failed": failed,
            "chunks": chunks,
            "chunks_processed": chunks_processed,
            "entities": len(entity_map),
            "relationships": len(relationship_map),
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }
