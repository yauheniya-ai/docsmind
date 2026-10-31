"""The Mind: ties the harness, orchestrator, planner, executor and subagents
into one conversational turn.

    question -> [harness: history, summary, memory, register]
             -> orchestrator (route) -> [planner] -> executor <-> tools / subagents
             -> answer (citations verified) -> persisted -> memory notes (background)

`stream()` yields `AgentEvent`s as the turn progresses (the HTTP layer turns
them into Server-Sent Events); `run()` collects them into a final result for
the CLI and Python API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from docsmind.agent.catalog import Catalog
from docsmind.agent.harness.context import ContextBuilder, TurnState
from docsmind.agent.harness.memory import RECALL_LIMIT, MemoryWriter
from docsmind.agent.harness.summarizer import Summarizer
from docsmind.agent.loop import run_tool_loop
from docsmind.agent.orchestrator import Orchestrator
from docsmind.agent.planner import Planner
from docsmind.agent.sources import SourceRegistry, finalize_citations
from docsmind.agent.subagents.specs import SUBAGENTS
from docsmind.agent.tools import ToolContext, ToolRegistry, build_registry
from docsmind.agent.tools.base import Emit
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import (
    AgentEvent,
    AgentResult,
    Conversation,
    StoredMessage,
    Usage,
)
from docsmind.domain.errors import (
    AgentError,
    ConversationNotFoundError,
    LLMConfigurationError,
)
from docsmind.domain.ports import LLM, ConversationStore, MemoryStore

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 8_000
MAX_SUBAGENT_REPORT_CHARS = 4_000
FALLBACK_ANSWER = (
    "I ran out of steps before I could finish. Try narrowing the question or selecting a single document."
)


class DocsmindAgent:
    def __init__(
        self,
        config: DocsmindConfig,
        *,
        llm: LLM | None = None,
        fast_llm: LLM | None = None,
        repository=None,
        conversations: ConversationStore | None = None,
        memory: MemoryStore | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self._llm = llm
        self._fast_llm = fast_llm
        self._repo = repository
        self._conversations = conversations
        self._memory = memory
        self.tools = tools or build_registry()
        self.builder = ContextBuilder(config)
        self._background: set[asyncio.Task] = set()

    # -- lazily-resolved collaborators (so importing never needs SDKs/DB) --------

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            from docsmind.adapters.llm import create_llm

            self._llm = create_llm(self.config)
        return self._llm

    @property
    def fast_llm(self) -> LLM:
        if self._fast_llm is None:
            if self.config.agent_fast_model and self.config.agent_fast_model != self.config.llm_model:
                from docsmind.adapters.llm import create_llm

                self._fast_llm = create_llm(self.config, model=self.config.agent_fast_model)
            else:
                self._fast_llm = self.llm
        return self._fast_llm

    @property
    def repo(self):
        if self._repo is None:
            from docsmind.adapters.storage.postgres.repository import PostgresRepository

            self._repo = PostgresRepository(self.config)
        return self._repo

    @property
    def conversations(self) -> ConversationStore:
        if self._conversations is None:
            from docsmind.adapters.storage.postgres.agent_store import PostgresConversationStore

            self._conversations = PostgresConversationStore(self.config)
        return self._conversations

    @property
    def memory(self) -> MemoryStore:
        if self._memory is None:
            from docsmind.adapters.storage.postgres.agent_store import PostgresMemoryStore

            self._memory = PostgresMemoryStore(self.config)
        return self._memory

    # -- public API ----------------------------------------------------------------

    def describe(self) -> dict:
        """Static description of the agent and its subagents, for the UI."""
        fast = self.config.agent_fast_model or self.config.llm_model
        return {
            "agent": {
                "name": "Mind",
                "role": "Orchestrator",
                "description": (
                    "The main chat agent. Routes each question, plans the complex ones, searches and reads "
                    "your documents with tools, delegates large or parallel work to subagents, and answers "
                    "with citations that are verified against what it actually read."
                ),
                "model": self.config.llm_model,
                "tools": self.tools.info(),
            },
            "stages": [
                {
                    "name": "router",
                    "role": "Routing",
                    "description": (
                        "Decides whether a question is simple or complex, and rewrites follow-ups into "
                        "standalone questions. One quick model call, no tools."
                    ),
                    "model": fast,
                },
                {
                    "name": "planner",
                    "role": "Planning",
                    "description": (
                        "For complex questions, drafts a short step-by-step plan before the Mind starts "
                        "working. One model call, no tools: a stage of the Mind, not a subagent."
                    ),
                    "model": fast,
                },
            ],
            "harness": {
                "router_model": fast,
                "summarizer_model": fast,
                "memory_model": fast,
                "memory_enabled": self.config.agent_memory_enabled,
            },
            "subagents": [
                {
                    "name": spec.name,
                    "role": spec.role,
                    "description": spec.description,
                    "model": self.config.llm_model,
                    "tools": self.tools.info(spec.tools),
                }
                for spec in SUBAGENTS.values()
            ],
            "limits": {
                "max_steps": self.config.agent_max_steps,
                "subagent_max_steps": self.config.agent_subagent_max_steps,
            },
        }

    async def stream(
        self,
        question: str,
        *,
        conversation_id: UUID | None = None,
        version_ids: list[UUID] | None = None,
        collection: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"The question is too long (max {MAX_QUESTION_CHARS} characters).")

        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        task = asyncio.create_task(
            self._guarded_turn(queue.put_nowait, question, conversation_id, version_ids, collection)
        )
        task.add_done_callback(lambda _t: queue.put_nowait(None))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # Consumer went away (client disconnect) or finished: stop any work left.
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def run(
        self,
        question: str,
        *,
        conversation_id: UUID | None = None,
        version_ids: list[UUID] | None = None,
        collection: str | None = None,
        on_event: Emit | None = None,
    ) -> AgentResult:
        result: AgentResult | None = None
        async for event in self.stream(
            question, conversation_id=conversation_id, version_ids=version_ids, collection=collection
        ):
            if on_event:
                on_event(event)
            if event.type == "answer":
                result = AgentResult(**event.data)
            elif event.type == "error":
                raise AgentError(event.data.get("message", "The Mind failed."))
        await self.drain()
        if result is None:
            raise AgentError("The Mind finished without an answer.")
        return result

    async def drain(self, timeout: float = 15.0) -> None:
        """Wait for background work (memory notes). Call before shutting the
        event loop down; the server doesn't need to."""
        if self._background:
            await asyncio.wait(list(self._background), timeout=timeout)

    # -- one turn ----------------------------------------------------------------------

    async def _guarded_turn(self, emit, question, conversation_id, version_ids, collection) -> None:
        try:
            await self._turn(emit, question, conversation_id, version_ids, collection)
        except asyncio.CancelledError:
            raise
        except LLMConfigurationError as exc:
            emit(AgentEvent(type="error", data={"code": "llm_not_configured", "message": str(exc)}))
        except ConversationNotFoundError as exc:
            emit(AgentEvent(type="error", data={"code": "conversation_not_found", "message": str(exc)}))
        except Exception as exc:
            log.exception("Agent turn failed")
            emit(
                AgentEvent(
                    type="error",
                    data={
                        "code": "internal",
                        "message": f"The Mind could not finish this answer ({type(exc).__name__}). Please try again.",
                    },
                )
            )

    async def _turn(
        self,
        emit: Emit,
        question: str,
        conversation_id: UUID | None,
        version_ids: list[UUID] | None,
        collection: str | None,
    ) -> None:
        started = time.monotonic()
        usage = Usage()
        llm, fast = self.llm, self.fast_llm  # fail fast on missing keys, before any DB work

        # --- harness: gather everything the prompts are built from ---------------
        conversation: Conversation | None = None
        history: list[StoredMessage] = []
        if conversation_id is not None:
            conversation = await self.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(f"Conversation {conversation_id} does not exist.")
            history = await self.conversations.messages(conversation_id)
            conversation = await Summarizer(fast, self.config).maybe_compress(
                conversation, history, self.conversations
            )

        rows = await self.repo.list_catalog(version_ids)
        if collection:
            rows = [r for r in rows if r["collection"] == collection]
        catalog = Catalog.from_rows(rows)

        memory_items = []
        if self.config.agent_memory_enabled:
            try:
                memory_items = await self.memory.recall(version_ids=catalog.version_ids, limit=RECALL_LIMIT)
            except Exception:
                log.exception("Memory recall failed; continuing without it")

        state = TurnState(
            catalog=catalog,
            question=question,
            history=history[conversation.summarized_upto :] if conversation else [],
            summary=conversation.summary if conversation else None,
            memory=memory_items,
        )

        # --- orchestrator -> planner -----------------------------------------------
        decision = await Orchestrator(fast, self.builder).route(state, usage)
        state.interpreted_question = decision.question
        emit(
            AgentEvent(
                type="route",
                data={
                    "route": decision.route,
                    "question": decision.question,
                    "documents": len(catalog),
                    # Lets the UI show titles instead of D1/D2 in the activity trace.
                    "catalog": [{"handle": d.handle, "title": d.title, "label": d.label} for d in catalog.docs],
                },
            )
        )
        if decision.route == "complex":
            state.plan = await Planner(fast, self.builder).plan(state, usage)
            if state.plan:
                emit(AgentEvent(type="plan", data={"steps": state.plan.steps}))

        # --- executor ----------------------------------------------------------------
        sources = SourceRegistry()
        tool_ctx = ToolContext(
            repo=self.repo,
            catalog=catalog,
            sources=sources,
            config=self.config,
            emit=emit,
            delegate=self._make_delegate(llm, catalog, sources, usage, emit),
        )
        bundle = self.builder.executor(state, self.tools.specs())
        result = await run_tool_loop(
            llm=llm,
            bundle=bundle,
            registry=self.tools,
            ctx=tool_ctx,
            usage=usage,
            emit=emit,
            max_steps=self.config.agent_max_steps,
            max_tokens=self.config.agent_max_output_tokens,
            context_budget_tokens=self.config.agent_context_budget_tokens,
            stream_text=True,
        )

        answer, citations = finalize_citations(result.text or FALLBACK_ANSWER, sources)

        # --- persist ------------------------------------------------------------------
        route_meta = {
            "route": decision.route,
            "plan": state.plan.steps if state.plan else [],
            "steps": result.steps,
            "usage": usage.model_dump(),
        }
        citation_dicts = [c.model_dump(mode="json") for c in citations]
        if conversation is None:
            conversation = await self.conversations.create(question[:80])
        await self.conversations.append(
            conversation.id,
            [
                StoredMessage(role="user", content=question),
                StoredMessage(role="assistant", content=answer, citations=citation_dicts, meta=route_meta),
            ],
        )

        emit(
            AgentEvent(
                type="answer",
                data={
                    "answer": answer,
                    "citations": citation_dicts,
                    "conversation_id": str(conversation.id),
                    "route": decision.route,
                    "plan": route_meta["plan"],
                    "usage": usage.model_dump(),
                },
            )
        )

        # --- save notes (background, best effort) -------------------------------------
        if self.config.agent_memory_enabled and result.text:
            self._spawn(
                MemoryWriter(fast, self.memory).remember(
                    conversation_id=conversation.id,
                    question=question,
                    answer=answer,
                    sources=citations,
                    catalog=catalog,
                )
            )

        emit(AgentEvent(type="done", data={"duration_ms": int((time.monotonic() - started) * 1000)}))

    # -- subagents -------------------------------------------------------------------------

    def _make_delegate(self, llm: LLM, catalog: Catalog, sources: SourceRegistry, usage: Usage, emit: Emit):
        async def delegate(name: str, task: str, documents: list[str] | None) -> str:
            spec = SUBAGENTS[name]
            sub_id = uuid4().hex[:8]
            # Same handles as the parent (D1, D3...), restricted to what was asked for.
            sub_catalog = Catalog(catalog.resolve_many(documents))
            sub_ctx = ToolContext(
                repo=self.repo,
                catalog=sub_catalog,
                sources=sources,  # shared: [S#] handles stay valid for the parent
                config=self.config,
                emit=emit,
                delegate=None,  # no recursion
            )
            registry = self.tools.subset(spec.tools)
            bundle = self.builder.subagent(spec, task, sub_catalog, registry.specs())

            emit(AgentEvent(type="subagent_start", data={"id": sub_id, "name": spec.name, "task": task}))
            ok = False
            try:
                result = await run_tool_loop(
                    llm=llm,
                    bundle=bundle,
                    registry=registry,
                    ctx=sub_ctx,
                    usage=usage,
                    emit=emit,
                    max_steps=spec.max_steps or self.config.agent_subagent_max_steps,
                    max_tokens=self.config.agent_max_output_tokens,
                    context_budget_tokens=self.config.agent_context_budget_tokens,
                    stream_text=False,
                    event_tag={"subagent": sub_id},
                )
                ok = bool(result.text)
                report = result.text or "The subagent finished without a report."
                if result.exhausted:
                    report += "\n(The subagent hit its step limit; the report may be incomplete.)"
                return report[:MAX_SUBAGENT_REPORT_CHARS]
            finally:
                emit(AgentEvent(type="subagent_end", data={"id": sub_id, "name": spec.name, "ok": ok}))

        return delegate

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
