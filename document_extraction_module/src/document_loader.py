from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from semantica.parse import DocumentParser


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".html", ".htm", ".txt", ".text", ".md", ".markdown"}


class DocumentLoader:
    """Use Semantica parsers and add lightweight Markdown support."""

    def __init__(self) -> None:
        self.parser = DocumentParser()

    def load(self, file_path: Path) -> Tuple[str, Dict[str, Any]]:
        suffix = file_path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文件格式：{suffix}")

        if suffix in {".md", ".markdown"}:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            metadata: Dict[str, Any] = {"format": "markdown", "size": len(text)}
        else:
            parsed = self.parser.parse_document(
                file_path,
                extract_tables=True,
                extract_images=False,
            )
            text = str(parsed.get("full_text") or parsed.get("text") or "")
            metadata = self._json_safe(parsed.get("metadata") or {})
            metadata["format"] = suffix.lstrip(".")

        text = text.replace("\x00", "").strip()
        if not text:
            raise ValueError("文档已解析，但没有获得可抽取的文本；扫描版 PDF 需要先做 OCR")
        return text, metadata

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_safe(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
