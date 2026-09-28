"""Chunk one page's text for vector search.

The rule, deliberately simple: most PDF pages are ~3,000 characters of text,
which is already a reasonable RAG chunk size — so most pages become exactly
one chunk. A minority of pages are much denser (dense two-column layouts,
small print, appendix tables) and get split into multiple ~3,000-4,000
character pieces with a small overlap, purely so a passage that straddles a
split point isn't cut off mid-sentence with no context on either side.

This replaces an earlier, considerably more complicated approach: chunking
within a heuristically-detected section tree. That doesn't generalize across
the huge variety of real-world PDFs, and chunks are only used for "find the
relevant passage" vector search — they were never the source of truth for
document structure (see `domain/documents.py`'s `Section` docstring). Page
boundaries are a property of the file itself, not a guess.
"""

from __future__ import annotations

from uuid import UUID

from docsmind.domain.documents import Chunk, ChunkType

DEFAULT_TARGET_CHARS = 3_500  # most pages fit in one chunk under this
DEFAULT_MAX_CHARS = 4_000  # only split a page once it exceeds this
DEFAULT_OVERLAP_CHARS = 100  # ~50 chars of shared context on each side of a split


def chunk_page_text(
    text: str,
    *,
    version_id: UUID,
    page_number: int,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        # The common case — one page, one chunk, no splitting at all.
        return [
            Chunk(
                version_id=version_id,
                chunk_type=ChunkType.TEXT,
                content=text,
                page_start=page_number,
                page_end=page_number,
            )
        ]

    # A dense page: slide a target_chars-wide window across it, moving
    # forward by (target_chars - overlap_chars) each step so consecutive
    # pieces share `overlap_chars` characters of context.
    stride = max(target_chars - overlap_chars, 1)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        piece = text[start:end]
        chunks.append(
            Chunk(
                version_id=version_id,
                chunk_type=ChunkType.TEXT,
                content=piece,
                page_start=page_number,
                page_end=page_number,
            )
        )
        if end == len(text):
            break
        start += stride

    return chunks