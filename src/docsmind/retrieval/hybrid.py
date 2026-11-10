"""Search that uses meaning as well as keywords, when embeddings exist.

Keyword (Postgres full-text) search always works. If the documents in scope
have been embedded (an on-demand step, see `enrichment/`), the query is also
embedded and the vector results are fused with the keyword results by
reciprocal rank fusion. Any failure on the vector side (no key, network,
provider error) silently falls back to keyword-only: search must not break.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from docsmind.retrieval.fusion import reciprocal_rank_fusion

log = logging.getLogger(__name__)


async def hybrid_rows(
    repo: Any,
    embedder: Any,
    query: str,
    tsquery: str | None,
    *,
    version_ids: list[UUID],
    limit: int,
) -> tuple[list[dict], str]:
    """-> (rows, mode) where mode is "hybrid" or "keyword"."""
    pool = max(limit * 3, 20)
    keyword = await repo.keyword_search(tsquery, version_ids=version_ids, limit=pool) if tsquery else []
    if embedder is None:
        return keyword[:limit], "keyword"
    try:
        if await repo.count_embedded(embedder.model_name, version_ids) == 0:
            return keyword[:limit], "keyword"
        [vector] = await embedder.embed([query])
        semantic = await repo.vector_search(
            vector, model=embedder.model_name, version_ids=version_ids, limit=pool
        )
    except Exception as exc:  # noqa: BLE001 - degrade to keyword search
        log.warning("vector search unavailable, using keyword only: %s", exc)
        return keyword[:limit], "keyword"
    return reciprocal_rank_fusion([keyword, semantic], limit=limit), "hybrid"
