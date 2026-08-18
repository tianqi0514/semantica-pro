from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    text: str

    @property
    def chunk_id(self) -> str:
        return f"chunk-{self.index:04d}"


def split_text(text: str, chunk_size: int, overlap: int) -> List[TextChunk]:
    """Split text near paragraph/sentence boundaries while preserving offsets."""
    if not text:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: List[TextChunk] = []
    start = 0
    text_length = len(text)
    boundaries = ("\n\n", "\n", "。", "！", "？", ";", "；", ". ")

    while start < text_length:
        hard_end = min(text_length, start + chunk_size)
        end = hard_end
        if hard_end < text_length:
            search_from = start + max(1, chunk_size // 2)
            best_boundary = -1
            best_width = 0
            window = text[search_from:hard_end]
            for boundary in boundaries:
                position = window.rfind(boundary)
                if position >= 0:
                    absolute = search_from + position
                    if absolute > best_boundary:
                        best_boundary = absolute
                        best_width = len(boundary)
            if best_boundary > start:
                end = best_boundary + best_width

        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append(TextChunk(len(chunks), start, end, chunk_text))

        if end >= text_length:
            break
        next_start = max(start + 1, end - overlap)
        start = next_start

    return chunks
