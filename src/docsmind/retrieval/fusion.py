"""Reciprocal rank fusion: merge ranked result lists (keyword + vector) into one.

Scores from full-text rank and cosine distance aren't comparable, but ranks
are: each list contributes 1 / (k + rank) per item, so something near the top
of *both* lists beats something that tops only one. k=60 is the standard value.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Any

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[dict[str, Any]]],
    *,
    key: str = "chunk_id",
    limit: int = 10,
    k: int = RRF_K,
) -> list[dict[str, Any]]:
    scores: dict[Hashable, float] = {}
    rows: dict[Hashable, dict[str, Any]] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            ident = row[key]
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank)
            rows.setdefault(ident, row)
    ordered = sorted(scores, key=lambda ident: scores[ident], reverse=True)
    return [{**rows[ident], "score": scores[ident]} for ident in ordered[:limit]]
