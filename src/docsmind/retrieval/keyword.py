"""Helpers for keyword retrieval. Kept free of I/O so they're trivially testable."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
MAX_QUERY_TERMS = 12


def query_terms(text: str) -> list[str]:
    """Lower-cased unique alphanumeric terms, in order of appearance.

    Splitting on everything else (dots, hyphens, quotes, operators) means the
    result can never contain tsquery syntax, so user/model input can't break
    the query."""
    seen: dict[str, None] = {}
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) >= 2:
            seen.setdefault(token, None)
    return list(seen)[:MAX_QUERY_TERMS]


def build_tsquery(text: str) -> str | None:
    """OR of the query's terms, as a `to_tsquery` expression.

    OR (not AND) because questions are phrased as sentences — requiring every
    word to appear would match nothing. Ranking (`ts_rank_cd`) pushes chunks
    containing more of the terms, closer together, to the top. Stop words are
    dropped by Postgres itself."""
    terms = query_terms(text)
    return " | ".join(terms) if terms else None


def snippet_around(content: str, terms: list[str], width: int = 700) -> str:
    """A window of `content` centred on the first matching term."""
    content = content.strip()
    if len(content) <= width:
        return content
    lowered = content.lower()
    positions = [p for p in (lowered.find(t) for t in terms) if p >= 0]
    centre = min(positions) if positions else 0
    start = max(0, centre - width // 4)
    end = min(len(content), start + width)
    start = max(0, end - width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}"
