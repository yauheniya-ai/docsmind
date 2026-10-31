"""Shared fakes for the agent tests: a scripted LLM, an in-memory repository."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar
from uuid import UUID, uuid4

from docsmind.domain.agent import LLMResponse, Message, SystemBlock, ToolCall, ToolSpec, Usage


class ScriptedLLM:
    """Dispatches on which role is calling (by its system prompt) and plays
    back queued responses for that role. Records every call for assertions."""

    ROLES: ClassVar[dict[str, str]] = {
        "You are the router": "router",
        "You are the planner": "planner",
        "You are the Mind": "executor",
        "You are a subagent": "subagent",
        "You decide what a document assistant should remember": "memory",
        "You maintain the running summary": "summarizer",
    }

    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.queues: dict[str, list[LLMResponse | Callable[..., LLMResponse]]] = {}
        self.calls: list[dict] = []

    def queue(self, role: str, *responses: LLMResponse | Callable[..., LLMResponse]) -> None:
        self.queues.setdefault(role, []).extend(responses)

    def role_of(self, system: list[SystemBlock]) -> str:
        head = system[0].text if system else ""
        for prefix, role in self.ROLES.items():
            if head.startswith(prefix):
                return role
        return "unknown"

    async def complete(
        self,
        messages: list[Message],
        *,
        system: list[SystemBlock],
        tools: list[ToolSpec] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        on_text=None,
    ) -> LLMResponse:
        role = self.role_of(system)
        self.calls.append(
            {"role": role, "messages": list(messages), "system": system, "tools": tools, "tool_choice": tool_choice}
        )
        queue = self.queues.get(role)
        if not queue:
            raise AssertionError(f"No scripted response left for role '{role}'")
        item = queue.pop(0)
        response = item(messages) if callable(item) else item
        if on_text and response.text:
            await on_text(response.text)
        return response

    def calls_for(self, role: str) -> list[dict]:
        return [c for c in self.calls if c["role"] == role]


def text(value: str, **kw) -> LLMResponse:
    return LLMResponse(text=value, stop_reason="end", usage=Usage(input_tokens=10, output_tokens=5), **kw)


def tool_use(*calls: tuple[str, dict], text_before: str = "") -> LLMResponse:
    return LLMResponse(
        text=text_before,
        tool_calls=[ToolCall(id=f"call_{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        stop_reason="tool_use",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


class FakeRepo:
    """Two documents, one chunk per page, with just enough query behaviour."""

    def __init__(self) -> None:
        self.v1, self.v2 = uuid4(), uuid4()
        self.rows = [
            {
                "id": self.v1, "document_id": uuid4(), "title": "Retention Policy",
                "collection": "policies", "label": "2024", "page_count": 3, "word_count": 900,
            },
            {
                "id": self.v2, "document_id": uuid4(), "title": "Security Policy",
                "collection": "policies", "label": "2025", "page_count": 2, "word_count": 500,
            },
        ]
        self.pages: dict[tuple[UUID, int], dict] = {}
        texts = {
            (self.v1, 1): "Overview of the retention policy. Scope and purpose.",
            (self.v1, 2): "Section 4.2 Retention periods: invoices are kept for ten years. Logs for 90 days.",
            (self.v1, 3): "Appendix: definitions of data subject and controller.",
            (self.v2, 1): "Security controls: encryption at rest is mandatory.",
            (self.v2, 2): "Incident response must start within 24 hours.",
        }
        for (v, p), content in texts.items():
            self.pages[(v, p)] = {"chunk_id": uuid4(), "version_id": v, "page_start": p, "page_end": p, "content": content}
        self.search_calls: list[dict] = []

    async def list_catalog(self, version_ids=None):
        return [r for r in self.rows if version_ids is None or r["id"] in version_ids]

    async def keyword_search(self, tsquery, *, version_ids=None, limit=8):
        self.search_calls.append({"tsquery": tsquery, "version_ids": version_ids})
        terms = [t.strip() for t in tsquery.split("|")]
        hits = []
        for row in self.pages.values():
            if version_ids is not None and row["version_id"] not in version_ids:
                continue
            score = sum(t in row["content"].lower() for t in terms)
            if score:
                hits.append({**row, "rank": score})
        hits.sort(key=lambda r: -r["rank"])
        return hits[:limit]

    async def grep_chunks(self, needle, *, version_ids=None, limit=12):
        return [
            r for r in self.pages.values()
            if needle.lower() in r["content"].lower() and (version_ids is None or r["version_id"] in version_ids)
        ][:limit]

    async def get_chunks_for_pages(self, version_id, start_page, end_page):
        return [
            r for (v, p), r in sorted(self.pages.items(), key=lambda kv: kv[0][1])
            if v == version_id and start_page <= p <= end_page
        ]

    async def get_sections(self, version_id):
        if version_id != self.v1:
            return []
        return [
            {"path": "01", "number": "1", "heading": "Overview", "page_start": 1, "ordinal": 0},
            {"path": "01.01", "number": "1.1", "heading": "Scope", "page_start": 1, "ordinal": 1},
            {"path": "02", "number": "2", "heading": "Retention periods", "page_start": 2, "ordinal": 2},
        ]
