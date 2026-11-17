"""The Mind answering from a real database: a scripted LLM calls the real `search` tool, which
queries Postgres, and the answer's citation points at the ingested page."""

from __future__ import annotations

from fakes import ScriptedLLM, text, tool_use

from docsmind.adapters.storage.memory import (
    InMemoryChangeStore,
    InMemoryConversationStore,
    InMemoryMemoryStore,
)
from docsmind.adapters.storage.postgres.repository import PostgresRepository
from docsmind.agent.agent import DocsmindAgent
from docsmind.ingestion.pipeline import IngestionPipeline


async def test_answer_cites_a_passage_found_by_the_search_tool(db_config, make_pdf):
    version = await IngestionPipeline(db_config).run(
        make_pdf("Invoices must be retained for ten years."), collection="finance"
    )
    llm = ScriptedLLM()
    llm.queue("router", text('{"route": "simple"}'))
    llm.queue(
        "executor",
        tool_use(("search", {"query": "invoice retention"})),
        text("Invoices are kept for ten years [S1]."),
    )
    llm.queue("memory", text("{}"))
    agent = DocsmindAgent(
        db_config.model_copy(update={"agent_memory_enabled": False}),
        llm=llm,
        fast_llm=llm,
        repository=PostgresRepository(db_config),
        conversations=InMemoryConversationStore(),
        memory=InMemoryMemoryStore(),
        changes=InMemoryChangeStore(),
    )

    events = [e async for e in agent.stream("How long are invoices kept?", version_ids=[version.id])]

    answer = next(e for e in events if e.type == "answer").data
    assert "ten years" in answer["answer"]
    assert [c["version_id"] for c in answer["citations"]] == [str(version.id)]
    assert answer["citations"][0]["page_start"] == 1
