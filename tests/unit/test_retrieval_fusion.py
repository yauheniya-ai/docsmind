from docsmind.retrieval.fusion import reciprocal_rank_fusion


def test_rrf_merges_and_reranks_shared_hits() -> None:
    keyword = [
        {"chunk_id": "a", "content": "kw-a"},
        {"chunk_id": "b", "content": "kw-b"},
    ]
    vector = [
        {"chunk_id": "b", "content": "vec-b"},
        {"chunk_id": "c", "content": "vec-c"},
    ]
    rows = reciprocal_rank_fusion([keyword, vector], limit=3)
    assert [r["chunk_id"] for r in rows] == ["b", "a", "c"]


def test_rrf_respects_limit() -> None:
    rows = reciprocal_rank_fusion(
        [[{"chunk_id": str(i), "content": "x"} for i in range(10)]],
        limit=4,
    )
    assert len(rows) == 4
