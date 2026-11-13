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
from uuid import UUID

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog
from docsmind.agent.harness.context import ContextBuilder, TurnState
from docsmind.agent.harness.memory import RECALL_LIMIT, MemoryWriter
from docsmind.agent.harness.summarizer import Summarizer
from docsmind.agent.loop import run_tool_loop
from docsmind.agent.orchestrator import Orchestrator
from docsmind.agent.planner import Planner
from docsmind.agent.sources import SourceRegistry, finalize_citations
from docsmind.agent.subagents.change_analysis.analyzer import ChangeAnalyzer
from docsmind.agent.subagents.change_analysis.env import RunEnv
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
from docsmind.domain.changes import ChangeResult, ChangeRow
from docsmind.domain.errors import (
    AgentError,
    ConversationNotFoundError,
    LLMConfigurationError,
    ToolInputError,
)
from docsmind.domain.ports import LLM, ChangeStore, ConversationStore, MemoryStore

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 8_000
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
        changes: ChangeStore | None = None,
        vision=None,
        scan_llm: LLM | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self._llm = llm
        self._fast_llm = fast_llm
        self._scan_llm = scan_llm
        self._repo = repository
        self._conversations = conversations
        self._memory = memory
        self._changes = changes
        self._vision = vision
        self._query_embedder = None
        self.tools = tools or build_registry()
        self.main_tools = self.tools.for_executor()
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
    def scan_llm(self) -> LLM:
        """The long-context model of the Change Analyzer's scan (defaults to the main model)."""
        if self._scan_llm is None:
            if self.config.change_scan_model and self.config.change_scan_model != self.config.llm_model:
                from docsmind.adapters.llm import create_llm

                self._scan_llm = create_llm(self.config, model=self.config.change_scan_model)
            else:
                self._scan_llm = self.llm
        return self._scan_llm

    @property
    def vision(self):
        """The vision model for `page_vision`, or None when its API key is not configured."""
        if self._vision is None:
            try:
                from docsmind.adapters.vision import create_captioner
                from docsmind.domain.errors import EnrichmentConfigurationError

                self._vision = create_captioner(self.config)
            except (EnrichmentConfigurationError, ImportError):
                self._vision = False
        return None if self._vision is False else self._vision

    @property
    def changes(self) -> ChangeStore:
        if self._changes is None:
            from docsmind.adapters.storage.postgres.change_store import PostgresChangeStore

            self._changes = PostgresChangeStore(self.config)
        return self._changes

    @property
    def repo(self):
        if self._repo is None:
            from docsmind.adapters.storage.postgres.repository import PostgresRepository

            self._repo = PostgresRepository(self.config)
        return self._repo

    @property
    def query_embedder(self):
        if self._query_embedder is None:
            try:
                from docsmind.adapters.embeddings import create_embedder
                from docsmind.domain.errors import EnrichmentConfigurationError

                self._query_embedder = create_embedder(self.config)
            except (EnrichmentConfigurationError, ImportError):
                # Search still works without embeddings (keyword-only fallback).
                self._query_embedder = False
        return None if self._query_embedder is False else self._query_embedder

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
        stage_models = {
            "main": self.config.llm_model,
            "fast": fast,
            "scan": self.config.change_scan_model or self.config.llm_model,
        }
        register = {
            "label": "Document register",
            "text": "The register (documents you may use):\n<one line per document: handle, title, pages, words ...>",
            "cached": True,
        }
        memory = {
            "label": "Memory",
            "text": (
                "<memory>\nNotes kept from earlier conversations. They can be outdated or wrong; "
                "verify against the documents before relying on them.\n- <note> ...\n</memory>"
            ),
            "cached": False,
        }
        summary = {
            "label": "Conversation summary",
            "text": "<conversation_summary>\n<running summary of older turns>\n</conversation_summary>",
            "cached": False,
        }
        interpreted = {
            "label": "Interpreted question",
            "text": "<interpreted_question>\n<the router's standalone rewrite>\n</interpreted_question>",
            "cached": False,
        }
        plan = {
            "label": "Plan",
            "text": (
                "<plan>\nFollow this plan; adapt it if the evidence demands.\n1. <step> ...\n</plan>"
            ),
            "cached": False,
        }
        return {
            "agent": {
                "name": "Mind",
                "role": "Orchestrator",
                "system_prompt": prompts.EXECUTOR,
                "context": [register, memory, summary, interpreted, plan],
                "user_prompt": (
                    "The user's latest message, sent after the recent conversation history "
                    "(older turns are folded into the summary above)."
                ),
                "description": (
                    "The main chat agent. Routes each question, plans the complex ones, searches and reads "
                    "your documents with tools, delegates large or parallel work to subagents, and answers "
                    "with citations that are verified against what it actually read."
                ),
                "model": self.config.llm_model,
                "tools": self.main_tools.info(),
            },
            "stages": [
                {
                    "name": "router",
                    "role": "Routing",
                    "system_prompt": prompts.ROUTER,
                    "context": [register, memory, summary],
                    "user_prompt": "The user's latest message, after the last two exchanges of history.",
                    "description": (
                        "Decides whether a question is simple or complex, and rewrites follow-ups into "
                        "standalone questions. One quick model call, no tools."
                    ),
                    "model": fast,
                },
                {
                    "name": "planner",
                    "role": "Planning",
                    "system_prompt": prompts.PLANNER,
                    "context": [register, memory, summary, interpreted],
                    "user_prompt": "The standalone question produced by the router.",
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
                    "system_prompt": spec.prompt,
                    "context": [register],
                    "user_prompt": (
                        "The two document handles (current version first) from the Mind's `delegate` call, plus "
                        "optional notes. Each stage gets its own fresh context: no history, memory or plan."
                    ),
                    "description": spec.description,
                    "model": self.config.llm_model,
                    "tools": self.tools.info(spec.tools),
                    "stages": [
                        {
                            "name": stage.name,
                            "role": stage.role,
                            "system_prompt": stage.prompt,
                            "context": [register] if stage.name in ("changelog", "verify") else [],
                            "user_prompt": stage.user_prompt,
                            "description": stage.description,
                            "model": stage_models[stage.model],
                            "budget": stage.budget,
                            "tools": self.tools.info(stage.tools),
                        }
                        for stage in spec.stages
                    ],
                }
                for spec in SUBAGENTS.values()
            ],
            "limits": {
                "max_steps": self.config.agent_max_steps,
                "verify_max_steps": self.config.change_verify_max_steps,
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

    async def compare(
        self,
        current: UUID,
        previous: UUID,
        *,
        notes: str = "",
        on_event: Emit | None = None,
    ) -> ChangeResult:
        """Run the Change Analyzer on two ingested versions, without a chat. The versions check
        may swap them if their labels say the second is the newer one."""
        rows = await self.repo.list_catalog([current, previous])
        catalog = Catalog.from_rows(rows)
        docs = [catalog.by_version(current), catalog.by_version(previous)]
        if docs[0] is None or docs[1] is None or current == previous:
            raise AgentError("Pass two different, ingested document versions.")
        env = self._make_env(on_event or (lambda _event: None), SUBAGENTS["change_analyzer"].tools)
        analyzer = ChangeAnalyzer(env, catalog)
        message = await analyzer.compare(docs[0], docs[1], notes)
        if analyzer.analysis_id is None:
            return ChangeResult(message=message)
        analysis = await self.changes.get_analysis(analyzer.analysis_id)
        return ChangeResult(message=message, analysis=analysis, rows=await self.changes.list_rows(analyzer.analysis_id))

    async def verify_rows(self, analysis_id: UUID, row_ids: list[UUID], *, notes: str = "") -> list[ChangeRow]:
        """Run the Change Analyzer's verification on rows of a stored table, without a chat: what the
        "Verify" button of the table does. Any row can be checked; a row that cannot be proven stays
        in the table, unverified, with the reason. -> the rows as they are afterwards."""
        analysis = await self.changes.get_analysis(analysis_id)
        if analysis is None:
            raise AgentError("That change analysis does not exist.")
        rows = await self.repo.list_catalog([analysis.current_version_id, analysis.previous_version_id])
        catalog = Catalog.from_rows(rows)
        env = self._make_env(lambda _event: None, SUBAGENTS["change_analyzer"].tools)
        analyzer = ChangeAnalyzer(env, catalog)
        await analyzer.verify_existing(analysis_id, [str(r) for r in row_ids])
        out = [await self.changes.get_row(r) for r in row_ids]
        return [r for r in out if r is not None]

    def _make_env(self, emit: Emit, tool_names: tuple[str, ...]) -> RunEnv:
        return RunEnv(
            config=self.config,
            llm=self.llm,
            fast_llm=self.fast_llm,
            scan_llm=self.scan_llm,
            repo=self.repo,
            changes=self.changes,
            tools=self.tools.subset(tool_names),
            emit=emit,
            vision=self.vision,
            embedder=self.query_embedder,
        )

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

        # Change analyses started during this turn are remembered on the assistant message, so the
        # UI can reopen their tables when the conversation is loaded again.
        analyses: list[str] = []
        send = emit

        def emit(event: AgentEvent) -> None:
            if event.type == "analysis_start" and event.data.get("id") not in analyses:
                analyses.append(event.data["id"])
            send(event)


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
            delegate=self._make_delegate(catalog, sources, usage, emit),
            embedder=self.query_embedder,
            changes=self.changes,
        )
        bundle = self.builder.executor(state, self.main_tools.specs())
        result = await run_tool_loop(
            llm=llm,
            bundle=bundle,
            registry=self.main_tools,
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
        if analyses:
            route_meta["analyses"] = analyses
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

    def _make_delegate(self, catalog: Catalog, sources: SourceRegistry, usage: Usage, emit: Emit):
        async def delegate(name: str, task: str, documents: list[str] | None, extra: dict) -> str:
            if name not in SUBAGENTS:
                raise ToolInputError(f"Unknown subagent '{name}'.")
            env = self._make_env(emit, SUBAGENTS[name].tools)
            analyzer = ChangeAnalyzer(env, catalog)
            try:
                if extra.get("analysis_id"):
                    try:
                        analysis_id = UUID(str(extra["analysis_id"]).strip())
                    except ValueError as exc:
                        raise ToolInputError("analysis_id is not a valid id.") from exc
                    return await analyzer.verify_existing(analysis_id, extra.get("row_ids") or [], task)
                docs = catalog.resolve_many(documents)
                if len(docs) != 2:
                    raise ToolInputError("Pass exactly two documents: [current, previous].")
                return await analyzer.compare(docs[0], docs[1], task)
            finally:
                usage.add(env.usage)

        return delegate

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
