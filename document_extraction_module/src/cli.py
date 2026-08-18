from __future__ import annotations

import argparse
import json
from pathlib import Path

from .exporters import to_csv_text, to_json_text
from .extractor import DocumentExtractionService
from .models import ExtractionConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantica 文档实体关系抽取")
    parser.add_argument("document", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()

    config = ExtractionConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    result = DocumentExtractionService().extract(args.document, args.document.name, config)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "semantica-document-extraction.json").write_text(
        to_json_text(result), encoding="utf-8"
    )
    (args.output / "semantica-document-extraction.csv").write_text(
        to_csv_text(result), encoding="utf-8"
    )
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
