"""The `Docsmind` facade. This is what `from docsmind import Docsmind` gives you.

It resolves config into concrete adapters (lazily, so `pip install docsmind`
alone doesn't require anthropic/openai/fastapi to be installed), and exposes
a small surface: ingest, chat, compare, tables.

Everything here delegates to `ingestion/`, `retrieval/`, and `agent/` — this
file should stay thin. It's the wiring, not the logic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from docsmind.config import DocsmindConfig
from docsmind.domain.agent import AgentResult
from docsmind.domain.changes import ChangeSet
from docsmind.domain.documents import DocumentVersion
from docsmind.domain.tables import AgentTable


class Docsmind:
    """Synchronous facade over the async core, for quickstart ergonomics.

    Use `AsyncDocsmind` (or call the `a*` methods) directly if you're already
    in an event loop, e.g. inside FastAPI.
    """

    def __init__(self, config: DocsmindConfig | None = None) -> None:
        self.config = config or DocsmindConfig()
        self._async = AsyncDocsmind(self.config)

    def ingest(self, path: str | Path, *, collection: str, version: str | None = None) -> DocumentVersion:
        return _run(self._async.aingest(path, collection=collection, version=version))

    def chat(
        self,
        question: str,
        *,
        collection: str | None = None,
        conversation_id: UUID | None = None,
    ) -> str:
        """Ask the Mind. Pass the previous `conversation_id` (see `AsyncDocsmind.achat_result`)
        to continue a conversation."""
        return _run(self._async.achat(question, collection=collection, conversation_id=conversation_id))

    def chat_result(
        self,
        question: str,
        *,
        collection: str | None = None,
        conversation_id: UUID | None = None,
    ) -> AgentResult:
        """Like `chat`, but returns the full result (citations, conversation id)."""
        return _run(self._async.achat_result(question, collection=collection, conversation_id=conversation_id))

    def compare(self, version_a: UUID, version_b: UUID) -> ChangeSet:
        return _run(self._async.acompare(version_a, version_b))

    def get_table(self, table_id: UUID) -> AgentTable:
        return _run(self._async.aget_table(table_id))


class AsyncDocsmind:
    """The real implementation. `Docsmind` above just runs this via asyncio."""

    def __init__(self, config: DocsmindConfig) -> None:
        self.config = config
        # Adapters are resolved lazily on first use, not at __init__ time,
        # so importing docsmind never requires optional SDKs to be installed.
        self._llm = None
        self._embedder = None
        self._vector_store = None
        self._blob_store = None

    async def aingest(self, path: str | Path, *, collection: str, version: str | None = None) -> DocumentVersion:
        from docsmind.ingestion.pipeline import IngestionPipeline

        pipeline = IngestionPipeline(config=self.config)
        return await pipeline.run(Path(path), collection=collection, version_label=version)

    async def achat(
        self,
        question: str,
        *,
        collection: str | None = None,
        conversation_id: UUID | None = None,
    ) -> str:
        return (
            await self.achat_result(question, collection=collection, conversation_id=conversation_id)
        ).answer

    async def achat_result(
        self,
        question: str,
        *,
        collection: str | None = None,
        conversation_id: UUID | None = None,
    ) -> AgentResult:
        """Like `achat`, but returns the full result (citations, conversation id, usage)."""
        from docsmind.agent.agent import DocsmindAgent

        agent = DocsmindAgent(config=self.config)
        result = await agent.run(question, collection=collection, conversation_id=conversation_id)
        # Let the background memory write finish before the caller's event loop goes away.
        await agent.drain()
        return result

    async def acompare(self, version_a: UUID, version_b: UUID) -> ChangeSet:
        from docsmind.agent.subagents.change_analysis.subagent import ChangeAnalysisSubagent

        subagent = ChangeAnalysisSubagent(config=self.config)
        return await subagent.compare(version_a, version_b)

    async def aget_table(self, table_id: UUID) -> AgentTable:
        from docsmind.adapters.storage.postgres.tables import PostgresTableRepository

        repo = PostgresTableRepository(config=self.config)
        return await repo.get(table_id)


def _run(coro):
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Docsmind() sync methods can't be called from inside a running event loop. "
        "Use AsyncDocsmind and `await` the a*() methods instead (e.g. in FastAPI)."
    )