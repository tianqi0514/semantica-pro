from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict


CSV_FIELDS = [
    "kind",
    "id",
    "type",
    "content",
    "source",
    "target",
    "weight",
    "confidence",
    "source_file",
    "document_id",
    "valid_from",
    "valid_until",
    "temporal_confidence",
    "evidence",
]


def to_json_text(result: Dict[str, Any]) -> str:
    """The result already follows Explorer's entities/relationships import shape."""
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def to_csv_text(result: Dict[str, Any]) -> str:
    """Build one UTF-8 CSV that Explorer can import in a single upload."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()

    for entity in result.get("entities", []):
        metadata = entity.get("metadata", {}) or {}
        writer.writerow(
            {
                "kind": "node",
                "id": entity.get("id", ""),
                "type": entity.get("type", "entity"),
                "content": entity.get("text", ""),
                "confidence": entity.get("confidence", ""),
                "source_file": metadata.get("source_file", ""),
                "document_id": metadata.get("document_id", ""),
                "evidence": json.dumps(metadata.get("evidence", []), ensure_ascii=False),
            }
        )

    for relation in result.get("relationships", []):
        metadata = relation.get("metadata", {}) or {}
        writer.writerow(
            {
                "kind": "edge",
                "id": relation.get("id", ""),
                "type": relation.get("type", "related_to"),
                "source": relation.get("source", ""),
                "target": relation.get("target", ""),
                "weight": relation.get("weight", 1.0),
                "confidence": metadata.get("confidence", ""),
                "source_file": metadata.get("source_file", ""),
                "document_id": metadata.get("document_id", ""),
                "valid_from": relation.get("valid_from") or metadata.get("valid_from", ""),
                "valid_until": relation.get("valid_until") or metadata.get("valid_until", ""),
                "temporal_confidence": metadata.get("temporal_confidence", ""),
                "evidence": json.dumps(metadata.get("evidence", []), ensure_ascii=False),
            }
        )

    return "\ufeff" + output.getvalue()
