"""Pure helpers: citation handling, keyword query building, JSON extraction,
context pruning, tool argument validation and scope enforcement."""

from __future__ import annotations

from uuid import uuid4

import pytest

from docsmind.agent.catalog import Catalog
from docsmind.agent.loop import ELIDED, prune_messages
from docsmind.agent.sources import SourceRegistry, finalize_citations, strip_markers
from docsmind.agent.tools import ToolContext, build_registry
from docsmind.agent.util import parse_json_object
from docsmind.domain.agent import Message, ToolCall
from docsmind.domain.errors import ToolInputError
from docsmind.retrieval.keyword import build_tsquery, snippet_around


def _registry_with(n: int) -> SourceRegistry:
    registry = SourceRegistry()
    for i in range(n):
        registry.add(
            chunk_id=uuid4(), version_id=uuid4(), title=f"Doc {i}", collection="c", label="v1",
            page_start=i + 1, page_end=i + 1, snippet="text",
        )
    return registry


def test_finalize_citations_drops_unknown_handles_and_normalizes_groups():
    registry = _registry_with(2)
    text, cited = finalize_citations("A [S1, S2]. B [S9]. C [S2].", registry)
    assert text == "A [S1][S2]. B . C [S2]."
    assert [c.handle for c in cited] == ["S1", "S2"]


def test_finalize_citations_renumbers_by_first_appearance():
    registry = _registry_with(5)
    text, cited = finalize_citations("Late [S4]. Early [S2][S4]. Then [S5].", registry)
    assert text == "Late [S1]. Early [S2][S1]. Then [S3]."
    assert [c.handle for c in cited] == ["S1", "S2", "S3"]
    assert [c.document_title for c in cited] == ["Doc 3", "Doc 1", "Doc 4"]


def test_source_registry_dedupes_chunks():
    registry = SourceRegistry()
    kwargs = {
        "chunk_id": uuid4(), "version_id": uuid4(), "title": "T", "collection": "c", "label": "v",
        "page_start": 1, "page_end": 1, "snippet": "x",
    }
    assert registry.add(**kwargs).handle == registry.add(**kwargs).handle == "S1"
    assert len(registry) == 1


def test_strip_markers_for_history_replay():
    assert strip_markers("Ten years [S1][S3]. Logs 90 days [S2, S4].") == "Ten years. Logs 90 days."


def test_build_tsquery_never_contains_syntax():
    query = build_tsquery("data:* & !drop | (x)")
    assert query is not None
    assert all(ch not in query.replace(" | ", "") for ch in "&!():*'")
    assert build_tsquery("!!! ???") is None


def test_snippet_centres_on_match():
    content = "x" * 2000 + " needle " + "y" * 2000
    snippet = snippet_around(content, ["needle"], width=200)
    assert "needle" in snippet and len(snippet) < 260


def test_parse_json_object_tolerates_prose_and_fences():
    assert parse_json_object('Sure!\n```json\n{"route": "complex", "x": {"a": "}"}}\n```') == {
        "route": "complex",
        "x": {"a": "}"},
    }
    assert parse_json_object("no json here") is None
    assert parse_json_object('{"broken": ') is None


def test_prune_elides_oldest_tool_results_but_protects_recent_rounds():
    def round_(i: int) -> list[Message]:
        call = ToolCall(id=f"c{i}", name="search", arguments={})
        return [
            Message(role="assistant", tool_calls=[call]),
            Message(role="tool", tool_call_id=f"c{i}", content="word " * 500),
        ]

    messages = [Message(role="user", content="q")]
    for i in range(4):
        messages += round_(i)
    pruned = prune_messages(messages, budget_tokens=1500)
    tool_contents = [m.content for m in pruned if m.role == "tool"]
    assert tool_contents[0] == ELIDED
    assert tool_contents[-1] != ELIDED and tool_contents[-2] != ELIDED  # last two rounds intact
    assert prune_messages(messages, budget_tokens=10**6) is messages


@pytest.fixture
def tool_ctx(config, repo):
    catalog = Catalog.from_rows(repo.rows)
    return ToolContext(repo=repo, catalog=catalog, sources=SourceRegistry(), config=config, emit=lambda e: None)


async def _call(tool_ctx, name, args):
    return await build_registry().execute(ToolCall(id="1", name=name, arguments=args), tool_ctx)


async def test_search_returns_numbered_sources(tool_ctx):
    out = await _call(tool_ctx, "search", {"query": "retention periods invoices"})
    assert not out.is_error
    assert "[S1]" in out.content and "Retention Policy" in out.content and "p.2" in out.content


async def test_search_respects_document_scope(tool_ctx, repo):
    out = await _call(tool_ctx, "search", {"query": "policy", "documents": ["D2"]})
    assert "Retention Policy" not in out.content
    assert repo.search_calls[-1]["version_ids"] == [repo.v2]


async def test_unknown_document_handle_is_an_error_the_model_can_fix(tool_ctx):
    out = await _call(tool_ctx, "read_pages", {"document": "D9", "start_page": 1})
    assert out.is_error and "Valid documents: D1, D2" in out.content


async def test_invalid_arguments_are_reported_not_raised(tool_ctx):
    out = await _call(tool_ctx, "read_pages", {"document": "D1", "start_page": 0})
    assert out.is_error and "start_page" in out.content


async def test_read_pages_limits_range(tool_ctx):
    out = await _call(tool_ctx, "read_pages", {"document": "D1", "start_page": 1, "end_page": 3})
    assert not out.is_error and "[S1]" in out.content and "[S3]" in out.content
    out = await _call(tool_ctx, "read_pages", {"document": "D1", "start_page": 1, "end_page": 9})
    assert out.is_error


async def test_malformed_tool_arguments_from_model(tool_ctx):
    call = ToolCall(id="1", name="search", arguments={}, parse_error="arguments were not valid JSON")
    out = await build_registry().execute(call, tool_ctx)
    assert out.is_error and "not valid JSON" in out.content


async def test_outline_and_missing_outline(tool_ctx):
    out = await _call(tool_ctx, "get_outline", {"document": "D1"})
    assert "Retention periods — p.2" in out.content
    out = await _call(tool_ctx, "get_outline", {"document": "D2"})
    assert "no table of contents" in out.content


async def test_delegate_unavailable_inside_subagents(tool_ctx):
    out = await _call(tool_ctx, "delegate", {"subagent": "researcher", "task": "Find retention periods."})
    assert out.is_error


def test_catalog_resolve_error_lists_valid_handles(repo):
    catalog = Catalog.from_rows(repo.rows)
    with pytest.raises(ToolInputError, match="D1, D2"):
        catalog.resolve("D7")
    assert catalog.resolve("d1").title == "Retention Policy"
