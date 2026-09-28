"""Splits a section's accumulated text into overlapping `Chunk`s.

Uses a word-count approximation of tokens (~0.75 words/token for English is
the usual rule of thumb, so we budget slightly fewer words than the token
target) rather than a real tokenizer. This avoids pulling in `tiktoken` (which
also means the count won't exactly match what any specific LLM API bills you
for), but keeps the core dependency footprint small. Swap in `tiktoken` here
if you need exact token accounting.
"""

from __future__ import annotations

from uuid import UUID

from docsmind.domain.documents import Chunk, ChunkType


def chunk_section_text(
    text: str,
    *,
    version_id: UUID,
    section_id: UUID,
    page_start: int | None,
    page_end: int | None,
    chunk_size_tokens: int = 700,
    chunk_overlap_tokens: int = 100,
) -> list[Chunk]:
    """One section's text -> one or more overlapping text Chunks.

    A section shorter than `chunk_size_tokens` becomes a single chunk — most
    are, in practice; this only kicks in for long sections without
    sub-headings.
    """
    words = text.split()
    if not words:
        return []

    words_per_chunk = int(chunk_size_tokens * 0.75)
    overlap_words = int(chunk_overlap_tokens * 0.75)
    stride = max(words_per_chunk - overlap_words, 1)

    chunks: list[Chunk] = []
    start = 0
    while start < len(words):
        end = min(start + words_per_chunk, len(words))
        chunk_text = " ".join(words[start:end])
        chunks.append(
            Chunk(
                version_id=version_id,
                section_id=section_id,
                chunk_type=ChunkType.TEXT,
                content=chunk_text,
                page_start=page_start,
                page_end=page_end,
            )
        )
        if end == len(words):
            break
        start += stride

    return chunks
