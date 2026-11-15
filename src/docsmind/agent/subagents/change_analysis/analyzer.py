"""The Change Analyzer: an agent that works on one persistent change table per pair of documents.

The agent (prompt: `prompts.CHANGE_ANALYZER_AGENT`) decides the order of work itself. This class is its
toolkit and its state: each `op_*` method is what one tool does (see `agent/tools/change_table.py`), and
`run` starts the agent loop. What must hold whatever the agent decides is enforced here, in code:

- a scan writes only candidate rows (staging); a candidate becomes part of the table only through
  verification, or, if it is not actionable (editorial, relocated, non-normative), through the scan itself;
- a rescan is first matched with the existing rows (active and archived), so it adds only what is new;
- an active row is never removed because a scan did not find it again; rejected and deleted rows are
  archived with the reason, never destroyed;
- a new scan of a pair that already has one is refused unless the user asked for it;
- every write goes through `ChangeRow.checked()`.

A is always the current version and B the previous one (the Mind passes [current, previous]; the versions
check can swap them when the labels say otherwise).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.harness.context import PromptBundle
from docsmind.agent.loop import run_tool_loop
from docsmind.agent.subagents.change_analysis.changelog import find_changelog, match_entries
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.agent.subagents.change_analysis.matching import (
    neighbours,
    parse_handle,
    row_handle,
    similarity,
)
from docsmind.agent.subagents.change_analysis.prep import (
    Prepared,
    ScanPart,
    plan_parts,
    prepare,
    prepare_changelog,
)
from docsmind.agent.subagents.change_analysis.report import build_report, clause_name, counts
from docsmind.agent.subagents.change_analysis.rows import row_to_json
from docsmind.agent.subagents.change_analysis.scan import ScanFailed, scan
from docsmind.agent.subagents.change_analysis.verify import DocText, Verdict, squash, verify_row
from docsmind.agent.subagents.specs import CHANGE_ANALYZER
from docsmind.agent.tools.base import ToolContext
from docsmind.domain.agent import AgentEvent, Message, SystemBlock, Usage
from docsmind.domain.changes import (
    CLAIM_FIELDS,
    ChangeAnalysis,
    ChangeRow,
    ChangeRun,
    Nature,
)
from docsmind.domain.errors import ToolInputError
from docsmind.pricing import cost_breakdown, cost_by_tool

log = logging.getLogger(__name__)

MAX_GAP_CHECKS = 20
MAX_VERIFY_PER_CALL = 60
OUTLINE_ENTRIES = 40
FIRST_PAGE_CHARS = 1_500
LIST_ROWS = 60  # rows get_table shows at once
VERDICTS = ("versions", "unrelated", "changelog")
DUPLICATE_SCORE = 0.6  # similarity above which add_row warns about an existing row


def _doc_snapshot(doc: CatalogDoc) -> dict:
    """What is kept on the analysis so it can be shown when the document itself has been deleted."""
    return {"title": doc.title, "collection": doc.collection, "label": doc.label, "page_count": doc.page_count}


def _doc_info(doc: CatalogDoc) -> dict:
    return {"handle": doc.handle, "title": doc.title, "label": doc.label, "version_id": str(doc.version_id)}


def _label(entry: dict) -> str:
    """A changelog entry as one line, in full: the report never cuts it short."""
    text = f"{entry['clause']}: {entry['text']}" if entry["clause"] else entry["text"]
    return " ".join(text.split())


def _short(text: str, n: int = 110) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


class _CountedLLM:
    """The agent's own model, counted live (calls and tokens) so the request-wide call limit sees every turn."""

    def __init__(self, llm, env: RunEnv) -> None:
        self._llm = llm
        self._env = env
        self.model = llm.model

    async def complete(self, *args, **kwargs):
        response = await self._llm.complete(*args, **kwargs)
        self._env.model_calls += 1
        self._env.tool_steps += 1
        self._env._count(self.model, response.usage, "change_analyzer")
        return response


class ChangeAnalyzer:
    def __init__(self, env: RunEnv, catalog: Catalog) -> None:
        self.env = env
        self.catalog = catalog
        self.analysis: ChangeAnalysis | None = None
        self.a_doc: CatalogDoc | None = None  # current version
        self.b_doc: CatalogDoc | None = None  # previous version (or the changelog of A)
        self.pair: tuple[CatalogDoc, CatalogDoc] | None = None  # as the caller gave them: [current, previous]
        self.prep: Prepared | None = None
        self.changelog_mode = False
        self._a_text: DocText | None = None
        self._b_text: DocText | None = None
        self.verify_stats = {"attempted": 0, "verified": 0, "removed": 0, "errors": 0}
        self.sub_id = ""
        self.request = ""
        self._rescanned = False  # a scoped or repeated scan was used as a research aid in this request
        self._existing: ChangeAnalysis | None = None  # the analysis as it was when this request started
        self._started = time.monotonic()
        self._lock = asyncio.Lock()  # row ordinals are assigned on insert: one insert at a time

    # -- events ------------------------------------------------------------------------

    @property
    def analysis_id(self) -> UUID | None:
        """The analysis this run worked on (None when it stopped before one was needed)."""
        return self.analysis.id if self.analysis else None

    def _emit(self, type: str, **data) -> None:
        self.env.emit(AgentEvent(type=type, data=data))

    def _status(self, phase: str, text: str) -> None:
        self._emit("analysis_status", id=str(self.analysis_id) if self.analysis else None, phase=phase, text=text)

    def _guard(self) -> None:
        """Every costly tool starts here: the request-wide cap on model calls."""
        if self.env.budget_exhausted:
            raise ToolInputError(
                f"The model-call limit of this request ({self.env.model_call_limit}) is reached. Stop now and tell "
                "the user what is still open (pending candidates, unverified rows)."
            )

    # -- the agent -----------------------------------------------------------------------------

    async def run(
        self,
        request: str,
        *,
        documents: tuple[CatalogDoc, CatalogDoc] | None = None,
        analysis_id: UUID | None = None,
    ) -> str:
        """Start the agent on `request`, for a pair of documents ([current, previous]) or an existing
        analysis. -> the text handed back to the Mind."""
        self.request = request.strip()
        self.sub_id = uuid4().hex[:8]
        self._started = time.monotonic()
        task = self.request or "Compare the documents."
        self._emit("subagent_start", id=self.sub_id, name="change_analyzer", task=task[:200])
        ok = False
        try:
            text = await self._run(documents, analysis_id)
            ok = True
            return text
        finally:
            self._emit("subagent_end", id=self.sub_id, name="change_analyzer", ok=ok)

    async def _run(self, documents: tuple[CatalogDoc, CatalogDoc] | None, analysis_id: UUID | None) -> str:
        env, cfg = self.env, self.env.config
        env.model_call_limit = cfg.change_request_max_model_calls
        self.pair = documents
        await self._load_existing(documents, analysis_id)
        self._existing = self.analysis
        if self.analysis is not None:
            self._announce()

        registry = env.tools.subset(CHANGE_ANALYZER.tools).configured(cfg)
        bundle = PromptBundle(
            system=[
                SystemBlock(text=CHANGE_ANALYZER.prompt),
                SystemBlock(text=f"The register (documents you may use):\n{self.catalog.render()}", cache=True),
            ],
            messages=[Message(role="user", content=await self._opening_message())],
            tools=registry.specs(),
        )
        ctx = ToolContext(
            repo=env.repo,
            catalog=self.catalog,
            sources=env.sources,
            config=cfg,
            emit=env.emit,
            delegate=None,  # no recursion
            embedder=env.embedder,
            changes=env.changes,
            vision=env.vision,
            cache={"change_analyzer": self},
        )
        usage = Usage()
        outcome = "done"
        steps = 0
        text = ""
        try:
            result = await run_tool_loop(
                llm=_CountedLLM(env.llm, env),
                bundle=bundle,
                registry=registry,
                ctx=ctx,
                usage=usage,
                emit=env.emit,
                max_steps=cfg.change_agent_max_steps,
                max_tokens=cfg.agent_max_output_tokens * 2,
                context_budget_tokens=cfg.agent_context_budget_tokens,
                stream_text=False,
                event_tag={"subagent": self.sub_id},
            )
            text, steps = result.text, result.steps
            if result.exhausted:
                outcome = "step limit reached"
        except asyncio.CancelledError:
            outcome = "stopped"
            await self._finish("stopped")
            raise
        except Exception:
            outcome = "failed"
            await self._finish("failed")
            raise
        finally:
            await self._record_run(steps, outcome)

        await self._ensure_report_current()
        if self.analysis is not None:
            if self.analysis.status == "running":
                await self._finish("done" if self.analysis.report else "stopped")
            else:
                self._emit("analysis_done", id=str(self.analysis.id), status=self.analysis.status, counts=await self._counts())
        status = await self._status_line()
        return f"{status}\n\n{text}".strip() if status else text

    async def _ensure_report_current(self) -> None:
        """The report always reflects the table: if rows changed (or there is no report yet), it is rewritten
        here, whatever the agent did. Skipped only when the request ran out of model calls."""
        if self.analysis is None or self.env.budget_exhausted:
            return
        try:
            analysis = await self.env.changes.get_analysis(self.analysis.id)
            if analysis is None or not await self.env.changes.list_rows(analysis.id):
                return
            if analysis.report and not analysis.report_stale:
                return
            await self.op_write_report()
        except Exception:
            log.exception("Could not bring the report of analysis %s up to date", self.analysis.id)

    async def _opening_message(self) -> str:
        lines = []
        if self.pair is not None:
            a, b = self.pair
            lines.append(f"Documents given: {a.handle} · {a.title} ({a.label}) and {b.handle} · {b.title} ({b.label}).")
            lines.append("The first is meant to be the newer (current) version; check_versions confirms it.")
        lines.append(await self._state_text())
        if self.analysis is not None:
            lines.append(
                f"This pair was already analyzed on {self.analysis.created_at:%Y-%m-%d}. If the request asks to compare "
                "or analyze it again, do not redo anything: say first that it was analyzed on that date and give the "
                "counts, then ask what the user wants to do with it. If the request asks for specific work, do that."
            )
        lines += ["", "<user_request>", self.request or "Compare the two documents.", "</user_request>"]
        return "\n".join(lines)

    async def _state_text(self) -> str:
        analysis = self.analysis
        if analysis is None:
            return "State: there is no change table for these documents yet. Start with check_versions."
        rows = await self.env.changes.list_rows(analysis.id, status=None)
        active = [r for r in rows if r.status == "active"]
        pending = [r for r in rows if r.status == "candidate"]
        archived = [r for r in rows if r.status == "archived"]
        changelog = analysis.changelog or {}
        if "found" in changelog:
            changelog_text = "The changelog was searched: " + ("found." if changelog.get("found") else "none found.")
        else:
            changelog_text = "The changelog has not been searched yet."
        if not analysis.report:
            report_text = "No report yet."
        else:
            report_text = "A report exists. It is rewritten by code whenever the table changes: you do not need to."
        scan_meta = analysis.meta.get("scan") or {}
        scan_text = (
            f"A scan was already done ({scan_meta.get('at') or 'earlier'})."
            if self._has_scan(analysis, rows)
            else "No scan has been done yet."
        )
        return "\n".join(
            [
                (
                    f"State: change table {analysis.id}, created {analysis.created_at:%Y-%m-%d %H:%M} UTC, "
                    f"status {analysis.status}, mode {analysis.mode}."
                ),
                (
                    f"{len(active)} active rows ({sum(r.actionable for r in active)} actionable, "
                    f"{sum(r.verified for r in active)} verified), {len(pending)} candidates waiting for "
                    f"verification, {len(archived)} archived."
                ),
                scan_text,
                changelog_text,
                report_text,
            ]
        )

    @staticmethod
    def _has_scan(analysis: ChangeAnalysis, rows: list[ChangeRow]) -> bool:
        return bool(analysis.meta.get("scan")) or any(r.origin == "scan" for r in rows)

    async def _counts(self) -> dict:
        assert self.analysis is not None
        return counts(await self.env.changes.list_rows(self.analysis.id))

    async def _status_line(self) -> str:
        if self.analysis is None:
            return ""
        analysis = await self.env.changes.get_analysis(self.analysis.id) or self.analysis
        rows = await self.env.changes.list_rows(analysis.id, status=None)
        active = [r for r in rows if r.status == "active"]
        pending = sum(r.status == "candidate" for r in rows)
        counts_text = (
            f"{len(active)} changes, {sum(r.actionable for r in active)} compliance-relevant, "
            f"{sum(r.verified for r in active)} verified"
            + (f", {pending} pending verification" if pending else "")
        )
        before = self._existing
        if before is None:
            line = f"Change analysis {analysis.id}: {counts_text}. The table and the report are open next to the chat."
        else:
            date = f"{before.created_at:%Y-%m-%d}"
            if analysis.table_revision == before.table_revision and analysis.report == before.report:
                line = (
                    f"ALREADY ANALYZED: this pair was analyzed on {date}. That analysis was reused, nothing was "
                    f"recomputed or changed. Change analysis {analysis.id}: {counts_text}. The table and the report "
                    "are open next to the chat."
                )
            else:
                line = (
                    f"UPDATED: this pair was analyzed on {date}; this request worked on that table. "
                    f"Change analysis {analysis.id}: {counts_text}. The table and the report are open next to the chat."
                )
        if analysis.verdict_note:
            line += f" Note: {analysis.verdict_note}"
        return line

    async def _record_run(self, steps: int, outcome: str) -> None:
        if self.analysis is None:
            return
        env = self.env
        try:
            await env.changes.add_run(
                ChangeRun(
                    analysis_id=self.analysis.id,
                    request=self.request[:2_000],
                    kind=self._run_kind(),  # type: ignore[arg-type]
                    scope="",
                    usage={
                        "cost": {
                            **cost_breakdown(env.usage_by_model, env.config.model_prices),
                            "tools": cost_by_tool(env.usage_by_tool_model, env.config.model_prices),
                        },
                        "model_calls": env.model_calls,
                        "seconds": int(time.monotonic() - self._started),
                    },
                    steps=steps,
                    outcome=outcome,
                )
            )
        except Exception:
            log.exception("Could not record the run of analysis %s", self.analysis.id)

    def _run_kind(self) -> str:
        text = self.request.lower()
        if self.analysis is not None and self.analysis.meta.get("created_by_run") == self.sub_id:
            return "compare"
        if self._rescanned:
            return "rescan"
        if "verif" in text:
            return "verify"
        if "?" in text:
            return "question"
        return "edit"

    # -- finding the analysis ---------------------------------------------------------------------

    async def _load_existing(self, documents: tuple[CatalogDoc, CatalogDoc] | None, analysis_id: UUID | None) -> None:
        changes = self.env.changes
        analysis: ChangeAnalysis | None = None
        if analysis_id is not None:
            analysis = await changes.get_analysis(analysis_id)
            if analysis is None:
                raise ToolInputError("That change analysis does not exist.")
        elif documents is not None:
            first, second = documents
            if first.content_hash and second.content_hash:
                analysis = await changes.find_analysis(first.content_hash, second.content_hash)
                if analysis is None:  # uploaded the other way round: the same pair
                    analysis = await changes.find_analysis(second.content_hash, first.content_hash)
        if analysis is None:
            return
        self.analysis = analysis
        self.a_doc = self.catalog.by_version(analysis.current_version_id) if analysis.current_version_id else None
        self.b_doc = self.catalog.by_version(analysis.previous_version_id) if analysis.previous_version_id else None
        if self.a_doc is None or self.b_doc is None:
            # The documents may be linked but outside the chat's selection: look them up.
            ids = [v for v in (analysis.current_version_id, analysis.previous_version_id) if v]
            if ids:
                found = Catalog.from_rows(await self.env.repo.list_catalog(ids))
                if self.a_doc is None and analysis.current_version_id:
                    self.a_doc = found.by_version(analysis.current_version_id)
                if self.b_doc is None and analysis.previous_version_id:
                    self.b_doc = found.by_version(analysis.previous_version_id)

    def _announce(self) -> None:
        analysis = self.analysis
        assert analysis is not None
        current, previous = self.a_doc, self.b_doc
        self._emit(
            "analysis_start",
            id=str(analysis.id),
            current=_doc_info(current) if current else dict(analysis.current_doc),
            previous=_doc_info(previous) if previous else dict(analysis.previous_doc),
            mode=analysis.mode,
            diff_ratio=analysis.diff_ratio,
            public=analysis.public,
            note=analysis.verdict_note,
            existing=True,
            reverify=True,  # the table is stored: the panel keeps showing it while this run works on it
        )

    def _need_analysis(self) -> ChangeAnalysis:
        if self.analysis is None:
            raise ToolInputError("There is no change table yet. Call check_versions first.")
        return self.analysis

    def _need_docs(self) -> tuple[CatalogDoc, CatalogDoc]:
        self._need_analysis()
        if self.a_doc is None or self.b_doc is None:
            raise ToolInputError(
                "A document of this analysis is not in the register (it may have been deleted). The table and the "
                "report are kept; ask the user to ingest the same file again to continue with the documents."
            )
        return self.a_doc, self.b_doc

    async def _ensure_texts(self) -> tuple[DocText, DocText, Prepared]:
        """The two texts and their diff, loaded once per request."""
        if self.prep is not None and self._a_text is not None and self._b_text is not None:
            return self._a_text, self._b_text, self.prep
        analysis = self._need_analysis()
        a, b = self._need_docs()
        raw_a, raw_b = await asyncio.gather(
            self.env.repo.get_full_text(a.version_id), self.env.repo.get_full_text(b.version_id)
        )
        for doc, raw in ((a, raw_a), (b, raw_b)):
            if not (raw or "").strip():
                raise ToolInputError(f"{doc.handle} has no extracted text (it may be a scan), so it cannot be compared.")
        self.changelog_mode = analysis.mode == "changelog"
        cfg = self.env.config
        if self.changelog_mode:
            self.prep = await asyncio.to_thread(prepare_changelog, raw_a, raw_b, pages_a=a.page_count, pages_b=b.page_count)
        else:
            self.prep = await asyncio.to_thread(
                prepare, raw_a, raw_b, threshold=cfg.change_diff_threshold, pages_a=a.page_count, pages_b=b.page_count
            )
        self._a_text = DocText(a, await asyncio.to_thread(squash, raw_a))
        self._b_text = DocText(b, await asyncio.to_thread(squash, raw_b))
        return self._a_text, self._b_text, self.prep

    # -- tool: check_versions ----------------------------------------------------------------------

    async def op_check_versions(self) -> str:
        if self.analysis is not None:
            return await self._existing_text()
        if self.pair is None:
            raise ToolInputError("No documents were given for a new comparison.")
        self._guard()
        env, cfg = self.env, self.env.config
        first, second = self.pair
        self._status("prepare", "Reading and comparing the texts")
        raw_first, raw_second = await asyncio.gather(
            env.repo.get_full_text(first.version_id), env.repo.get_full_text(second.version_id)
        )
        for doc, raw in ((first, raw_first), (second, raw_second)):
            if not (raw or "").strip():
                return f"{doc.handle} has no extracted text (it may be a scan), so it cannot be compared. No table was made."
        prep = await asyncio.to_thread(
            prepare, raw_first, raw_second, threshold=cfg.change_diff_threshold, pages_a=first.page_count, pages_b=second.page_count
        )
        if prep.identical:
            return (
                f"{first.handle} and {second.handle} are identical once headers, footers and page numbers are removed: "
                "there is nothing to compare. No table was made."
            )

        self._status("check", "Checking that these are versions of one document")
        decision = await self._decide(first, second, prep, "")
        if decision["verdict"] == "unrelated":
            return (
                decision["explanation"]
                or f"{first.handle} and {second.handle} do not look like two versions of one document."
            ) + " No table was made."

        a_doc, b_doc, a_raw, b_raw = first, second, raw_first, raw_second
        if decision["newer"] == "B":
            a_doc, b_doc, a_raw, b_raw = second, first, raw_second, raw_first
        changelog_mode = decision["verdict"] == "changelog"
        if changelog_mode:
            prep = await asyncio.to_thread(prepare_changelog, a_raw, b_raw, pages_a=a_doc.page_count, pages_b=b_doc.page_count)
        elif a_doc is not first:
            prep = await asyncio.to_thread(
                prepare, a_raw, b_raw, threshold=cfg.change_diff_threshold, pages_a=a_doc.page_count, pages_b=b_doc.page_count
            )
        mode = "changelog" if changelog_mode else prep.mode

        # One analysis per pair: one that appeared meanwhile for the same hashes is reused.
        existing = None
        if a_doc.content_hash and b_doc.content_hash:
            existing = await env.changes.find_analysis(a_doc.content_hash, b_doc.content_hash)
        if existing is not None:
            self.analysis, self.a_doc, self.b_doc = existing, a_doc, b_doc
            self._announce()
            return await self._existing_text()

        analysis = ChangeAnalysis(
            current_version_id=a_doc.version_id,
            previous_version_id=b_doc.version_id,
            current_hash=a_doc.content_hash,
            previous_hash=b_doc.content_hash,
            current_doc=_doc_snapshot(a_doc),
            previous_doc=_doc_snapshot(b_doc),
            mode=mode,  # type: ignore[arg-type]
            diff_ratio=None if changelog_mode else prep.ratio,
            public=decision["public"],
            verdict_note=decision["explanation"],
            meta={"query": decision["query"], "created_by_run": self.sub_id},
        )
        await env.changes.create_analysis(analysis)
        self.analysis, self.a_doc, self.b_doc = analysis, a_doc, b_doc
        self.prep, self.changelog_mode = prep, changelog_mode
        self._a_text = DocText(a_doc, await asyncio.to_thread(squash, a_raw))
        self._b_text = DocText(b_doc, await asyncio.to_thread(squash, b_raw))
        self._emit(
            "analysis_start",
            id=str(analysis.id),
            current=_doc_info(a_doc),
            previous=_doc_info(b_doc),
            mode=mode,
            diff_ratio=analysis.diff_ratio,
            public=analysis.public,
            note=analysis.verdict_note,
        )
        if changelog_mode:
            kind = "B is the changelog of A (mode changelog)"
        else:
            how = "the current version plus a diff" if mode == "diff" else "both versions in full"
            kind = f"two versions of one document, diff ratio {prep.ratio:.0%}, so a scan would use {how}"
        return (
            f"Versions confirmed: A (current) = {a_doc.handle} · {a_doc.title} ({a_doc.label}); B = {b_doc.handle} · "
            f"{b_doc.title} ({b_doc.label}); {kind}. The documents are "
            f"{'public' if analysis.public else 'treated as internal (no web search)'}. "
            f"Change table {analysis.id} created, empty. {analysis.verdict_note}"
        ).strip()

    async def _existing_text(self) -> str:
        analysis = self._need_analysis()
        ratio = f", diff ratio {analysis.diff_ratio:.0%}" if analysis.diff_ratio is not None else ""
        return (
            "A change table for this pair exists already (no model call needed). A (current) = "
            f"{analysis.current_doc.get('title', '')} ({analysis.current_doc.get('label', '')}); B = "
            f"{analysis.previous_doc.get('title', '')} ({analysis.previous_doc.get('label', '')}); mode {analysis.mode}{ratio}; "
            f"{'public' if analysis.public else 'internal'} documents.\n" + await self._state_text()
        )

    async def _decide(self, first: CatalogDoc, second: CatalogDoc, prep: Prepared, notes: str) -> dict:
        glances = await asyncio.gather(self._glance(first), self._glance(second))
        parts = []
        for tag, doc, glance in (("document_a", first, glances[0]), ("document_b", second, glances[1])):
            parts.append(f'<{tag} handle="{doc.handle}" title="{doc.title}" label="{doc.label}">\n{glance}\n</{tag}>')
        parts.append(f"<diff_ratio>{prep.ratio:.2f}</diff_ratio>")
        if notes:
            parts.append(f"<focus>\n{notes}\n</focus>")
        answer = (
            await self.env.json_call(
                self.env.fast_llm, prompts.DECIDER, "\n".join(parts), max_tokens=700, tool="check_versions"
            )
            or {}
        )
        verdict = str(answer.get("verdict") or "versions").strip().lower()
        return {
            "verdict": verdict if verdict in VERDICTS else "versions",
            "newer": "B" if str(answer.get("newer") or "A").strip().upper().startswith("B") else "A",
            "public": bool(answer.get("public")),
            "query": " ".join(str(answer.get("query") or "").split())[:200],
            "explanation": " ".join(str(answer.get("explanation") or "").split())[:600],
        }

    async def _glance(self, doc: CatalogDoc) -> str:
        repo = self.env.repo
        sections = await repo.get_sections(doc.version_id)
        outline = "\n".join(
            " ".join(f"{s.get('number') or ''} {s.get('heading') or ''}".split()) for s in sections[:OUTLINE_ENTRIES]
        )
        chunks = await repo.get_chunks_for_pages(doc.version_id, 1, 1)
        first_page = " ".join(" ".join((c.get("content") or "") for c in chunks).split())[:FIRST_PAGE_CHARS]
        return f"<outline>\n{outline or '(none)'}\n</outline>\n<first_page>\n{first_page or '(no text)'}\n</first_page>"

    # -- tool: scan_documents ------------------------------------------------------------------------

    async def op_scan(self, *, mode: str = "auto", focus: str = "") -> str:
        """The first pass that builds the foundation of the table. Run once per pair: it is refused when a scan
        finished. A scan that failed or crashed midway is continued from the rows saved so far."""
        analysis = self._need_analysis()
        self._need_docs()
        rows = await self.env.changes.list_rows(analysis.id, status=None)
        scan_meta = dict(analysis.meta.get("scan") or {})
        scan_rows = [r for r in rows if r.origin == "scan"]
        started = bool(scan_meta) or bool(scan_rows)
        # an analysis scanned before the marker existed has no "complete" key: it finished
        complete = bool(scan_meta.get("complete", True)) if started else False
        if started and complete:
            when = scan_meta.get("at") or f"{analysis.created_at:%Y-%m-%d}"
            active = [r for r in rows if r.status == "active"]
            return (
                f"Not scanned again: this pair was analyzed on {when}; its results are in the table ({len(active)} active "
                f"rows, {sum(r.verified for r in active)} verified, {sum(r.status == 'candidate' for r in rows)} pending). "
                "The scan is the internal first pass that built the foundation of the table, so the user keeps getting this "
                "same table. Improve it by research and verification: examine a chapter in depth, verify a row, check the "
                "changelog, add a row."
            )
        resuming = started
        if resuming:
            mode = str(scan_meta.get("requested_mode") or mode)
            focus = str(scan_meta.get("focus") or focus)
            self._rescanned = True
        done_parts = {int(i) for i in scan_meta.get("done_parts") or []}

        self._guard()
        _a_text, _b_text, prep = await self._ensure_texts()
        cfg = self.env.config
        if mode in ("diff", "full") and not self.changelog_mode:
            prep = dataclasses.replace(prep, mode=mode)  # type: ignore[arg-type]
        parts = plan_parts(prep, split_tokens=cfg.change_scan_split_tokens, max_input_tokens=cfg.change_scan_max_input_tokens)
        if self.changelog_mode:
            self._whole_changelog(prep, parts)
        used_mode = "changelog" if self.changelog_mode else prep.mode

        async def save_progress(complete_now: bool, stats: dict | None = None) -> None:
            nonlocal analysis
            meta = {
                **analysis.meta,
                "scan": {
                    **(analysis.meta.get("scan") or {}),
                    "at": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
                    "complete": complete_now,
                    "done_parts": sorted(done_parts),
                    "requested_mode": mode,
                    "focus": focus,
                    "mode": used_mode,
                    "parts": len(parts),
                    **({k: stats[k] for k in ("skipped", "retries", "rows")} if stats else {}),
                },
            }
            analysis = analysis.model_copy(update={"meta": meta})
            self.analysis = analysis
            await self.env.changes.update_analysis(analysis.id, meta=meta)

        await save_progress(False)  # written before the first model call: a crash leaves the scan marked unfinished
        self._status("scan", "Continuing the scan" if resuming else "Scanning for changes")

        async def stage(row: ChangeRow) -> None:
            await self._add_row(row.checked(), status="candidate", origin="scan")

        async def part_done(index: int) -> None:
            done_parts.add(index)
            await save_progress(False)

        failure = ""
        try:
            stats = await scan(
                self.env,
                prep,
                parts,
                changelog_mode=self.changelog_mode,
                notes=focus,
                on_row=stage,
                on_status=lambda t: self._status("scan", t),
                resume_rows=scan_rows,
                skip_parts=frozenset(done_parts),
                on_part_done=part_done,
            )
        except ScanFailed as exc:
            failure = str(exc)
            stats = {"parts": len(parts), "skipped": 0, "retries": 0, "rows": 0}

        candidates = [r for r in await self.env.changes.list_rows(analysis.id, status="candidate") if r.origin == "scan"]
        if failure:
            await save_progress(False, stats)
            return (
                f"The scan stopped midway: {failure} {len(candidates)} candidates are saved and "
                f"{len(done_parts)} of {len(parts)} part(s) are done. Call scan_documents again to continue from where it "
                "stopped (it is given the rows found so far)."
            )

        # The scan finished: settle what needs no verification.
        pending: list[ChangeRow] = []
        promoted = 0
        for row in candidates:
            if row.actionable:
                pending.append(row)
                continue
            updated = await self.env.changes.update_row(row.id, status="active")
            promoted += 1
            if updated is not None:
                self._emit("change_row_update", row=row_to_json(updated))
        await save_progress(True, stats)

        head = "Scan finished after continuing an interrupted one" if resuming else "Scan done"
        lines = [f"{head} ({len(parts)} part(s), {used_mode} mode): {len(candidates)} candidates."]
        lines.append(
            f"- {len(pending)} actionable candidates are waiting for verification"
            + (": " + ", ".join(row_handle(r) for r in pending[:40]) if pending else "")
            + ". Call verify_findings on them; none of them is in the table yet."
        )
        lines.append(f"- {promoted} editorial, relocated or non-normative candidates were added to the table (not verified).")
        return "\n".join(lines)

    @staticmethod
    def _whole_changelog(prep: Prepared, parts: list[ScanPart]) -> None:
        """Every part of the document comes with the whole changelog."""
        for part in parts:
            part.b_lo, part.b_hi = 0, len(prep.b)
            part.tokens = sum(prep._line_tokens_a[part.lo : part.hi]) + prep.tokens_b

    async def _add_row(self, row: ChangeRow, *, status: str, origin: str, verified: bool | None = None) -> ChangeRow:
        async with self._lock:
            return await self._insert_row(row, status=status, origin=origin, verified=verified)

    async def _insert_row(self, row: ChangeRow, *, status: str, origin: str, verified: bool | None = None) -> ChangeRow:
        """Store a row (the caller holds the lock: ordinals are assigned on insert)."""
        analysis = self._need_analysis()
        update: dict = {"analysis_id": analysis.id, "status": status, "origin": origin}
        if verified is not None:
            update["verified"] = verified
        stored = await self.env.changes.add_row(row.model_copy(update=update))
        self._emit("change_row", row=row_to_json(stored))
        return stored

    # -- tool: find_changelog -----------------------------------------------------------------------

    async def op_find_changelog(self, query: str | None = None) -> str:
        analysis = self._need_analysis()
        a, b = self._need_docs()
        if self.changelog_mode or analysis.mode == "changelog":
            return "B is already the changelog of A: the scan was built from it. Nothing to search."
        self._guard()
        self._status("changelog", "Looking for a changelog")
        result = await find_changelog(
            self.env,
            a,
            b,
            public=bool(analysis.public),
            query=query or str(analysis.meta.get("query") or ""),
            notes="",
            emit=self.env.emit,
            tag={"subagent": self.sub_id},
        )
        rows = await self.env.changes.list_rows(analysis.id)
        result["unmatched"] = []
        if result.get("found"):
            relevant = [e for e in result["entries"] if e.get("compliance_relevant")]
            if relevant:
                matches = await match_entries(self.env, relevant, rows)
                result["matched"] = sum(1 for v in matches.values() if v is not None)
                result["unmatched"] = [_label(relevant[i]) for i, rid in matches.items() if rid is None]
        self.analysis = analysis.model_copy(update={"changelog": result})
        await self.env.changes.update_analysis(analysis.id, changelog=result)
        if not result.get("found"):
            where = "in the documents and online" if analysis.public else "in the documents (internal: no web search)"
            return f"No changelog found {where}." + (f" ({result['error']})" if result.get("error") else "")
        entries = result["entries"]
        lines = [
            f"Changelog found: {result.get('source') or 'source unknown'}. {result.get('summary') or ''}".strip(),
            f"{len(entries)} entries, {sum(1 for e in entries if e.get('compliance_relevant'))} compliance-relevant.",
        ]
        if result["unmatched"]:
            lines.append(f"{len(result['unmatched'])} compliance-relevant entries have no row in the table:")
            lines += [f"- {_short(label, 160)}" for label in result["unmatched"][:25]]
            lines.append("Call verify_findings with changelog_gaps=true to check them against the documents.")
        else:
            lines.append("Every compliance-relevant entry is covered by a row.")
        return "\n".join(lines)

    # -- tool: verify_findings -------------------------------------------------------------------------

    async def op_verify(self, rows: list[str] | None, which: str | None, changelog_gaps: bool) -> str:
        analysis = self._need_analysis()
        a_text, b_text, _prep = await self._ensure_texts()
        self._guard()
        all_rows = await self.env.changes.list_rows(analysis.id, status=None)
        targets: list[ChangeRow] = []
        problems: list[str] = []
        if rows:
            by_ordinal = {r.ordinal: r for r in all_rows}
            for handle in rows:
                n = parse_handle(handle)
                row = by_ordinal.get(n) if n is not None else None
                if row is None:
                    problems.append(f"{handle}: no such row")
                elif row.status == "archived":
                    problems.append(f"{row_handle(row)} is archived (restore it first)")
                else:
                    targets.append(row)
        elif which == "pending":
            targets = [r for r in all_rows if r.status == "candidate" and r.actionable]
        elif which == "unverified":
            targets = [r for r in all_rows if r.status == "active" and r.actionable and not r.verified]
        elif not changelog_gaps:
            raise ToolInputError('Say what to verify: rows=["R3", ...], or which="pending" (candidates) or "unverified".')

        lines: list[str] = []
        if targets:
            if len(targets) > MAX_VERIFY_PER_CALL:
                lines.append(f"Only the first {MAX_VERIFY_PER_CALL} of {len(targets)} rows are verified in this call; call again for the rest.")
                targets = targets[:MAX_VERIFY_PER_CALL]
            if self.changelog_mode:
                kept = []
                for row in targets:
                    if row.nature == Nature.REMOVED:
                        await self._note(row, "A removal cannot be proven from a changelog alone.")
                        lines.append(f"{row_handle(row)}: a removal cannot be proven from a changelog alone")
                    else:
                        kept.append(row)
                targets = kept
            outcomes = await self._verify_all(targets, a_text, b_text, "")
            lines += [f"{row_handle(row)} {_short(clause_name(row), 60)}: {text}" for row, text in outcomes]
        if changelog_gaps:
            lines += await self._changelog_gaps(a_text, b_text)
        lines += [f"Skipped {p}" for p in problems]
        if not lines:
            return "Nothing to verify."
        verified = sum(1 for line in lines if ": verified" in line)
        return f"Verification done ({verified} verified).\n" + "\n".join(lines)

    async def _verify_all(
        self, targets: list[ChangeRow], a: DocText, b: DocText, notes: str
    ) -> list[tuple[ChangeRow, str]]:
        semaphore = asyncio.Semaphore(self.env.config.change_verify_parallel)
        finished = 0
        self._status("verify", f"Verifying {len(targets)} rows against the text")
        results: list[tuple[ChangeRow, str]] = []
        public = bool(self.analysis and self.analysis.public)

        async def one(row: ChangeRow) -> None:
            nonlocal finished
            async with semaphore:
                if self.env.budget_exhausted:
                    results.append((row, "not checked: the model-call limit of this request is reached"))
                    return
                verdict = await verify_row(
                    self.env, row=row, a=a, b=b, changelog_mode=self.changelog_mode, notes=notes, public=public
                )
            results.append((row, await self._apply(row, verdict)))
            finished += 1
            self._status("verify", f"Verified {finished} of {len(targets)} rows")

        await asyncio.gather(*(one(r) for r in targets))
        order = {r.id: i for i, r in enumerate(targets)}
        return sorted(results, key=lambda item: order[item[0].id])

    async def _apply(self, row: ChangeRow, verdict: Verdict) -> str:
        """Store a verdict. -> one line for the agent.

        A candidate that is proven joins the table; one that is not is archived with the reason. An active
        row that cannot be proven stays (unverified, with a note): only verification of a candidate or a
        person removes anything from view."""
        changes = self.env.changes
        self.verify_stats["attempted"] += 1
        if verdict.status == "verified":
            self.verify_stats["verified"] += 1
            fields: dict = {"verified": True, "proof": verdict.proof, "verification_note": None}
            if row.status == "candidate":
                fields["status"] = "active"
            updated = await changes.update_row(row.id, **fields)
            pages = ", ".join(sorted({f"{'A' if p.doc == 'current' else 'B'} p.{p.page}" for p in verdict.proof if p.page}))
            outcome = f"verified{' (' + pages + ')' if pages else ''}"
        elif verdict.status == "not_found" and row.status == "candidate":
            self.verify_stats["removed"] += 1
            log.info("Archived %s (%s): %s", clause_name(row), row.nature.value, verdict.note)
            await changes.archive_row(row.id, f"No proof in the text: {verdict.note}")
            self._emit("change_row_removed", id=str(row.id), analysis_id=str(row.analysis_id))
            return f"no proof in the text, archived ({_short(verdict.note, 100)})"
        else:
            if verdict.status == "error":
                self.verify_stats["errors"] += 1
                note = verdict.note
            else:
                note = f"Not verified: {verdict.note}"
            updated = await changes.update_row(row.id, verified=False, proof=[], verification_note=note)
            outcome = f"not verified ({_short(note, 100)})"
        if updated is not None:
            self._emit("change_row_update", row=row_to_json(updated))
        return outcome

    async def _note(self, row: ChangeRow, note: str) -> None:
        updated = await self.env.changes.update_row(row.id, verification_note=note)
        if updated is not None:
            self._emit("change_row_update", row=row_to_json(updated))

    async def _changelog_gaps(self, a: DocText, b: DocText) -> list[str]:
        """Compliance-relevant changelog entries that no row describes: look at both versions."""
        analysis = self._need_analysis()
        changelog = dict(analysis.changelog or {})
        if not changelog.get("found"):
            return ["No changelog is stored: call find_changelog first."]
        unmatched = [str(label) for label in changelog.get("unmatched") or []]
        relevant = {_label(e): e for e in changelog.get("entries") or [] if e.get("compliance_relevant")}
        entries = [relevant[label] for label in unmatched if label in relevant]
        if not entries:
            return ["Every compliance-relevant changelog entry is covered by a row."]
        checked = entries[:MAX_GAP_CHECKS]
        changelog["unchecked"] = [_label(e) for e in entries[MAX_GAP_CHECKS:]]
        changelog["added"], changelog["claimed_not_found"] = [], []
        self._status("changelog", f"Checking {len(checked)} changelog entries the table does not cover")
        semaphore = asyncio.Semaphore(self.env.config.change_verify_parallel)
        lines: list[str] = []
        public = bool(analysis.public)

        async def one(entry: dict) -> None:
            label = _label(entry)
            async with semaphore:
                if self.env.budget_exhausted:
                    changelog["unchecked"].append(label)
                    return
                verdict = await verify_row(
                    self.env,
                    row=None,
                    entry=f"{entry['clause']}\n{entry['text']}".strip(),
                    a=a,
                    b=b,
                    changelog_mode=False,
                    public=public,
                )
            if verdict.status == "verified" and verdict.row is not None:
                row = verdict.row.model_copy(update={"verified": True, "proof": verdict.proof}).checked()
                stored = await self._add_row(row, status="active", origin="changelog", verified=True)
                changelog["added"].append(clause_name(stored))
                lines.append(f"changelog entry '{_short(label, 70)}' is real: added as {row_handle(stored)} (verified)")
            elif verdict.status == "not_found":
                changelog["claimed_not_found"].append(label)
                lines.append(f"changelog entry '{_short(label, 70)}': claimed, no change found in the text")
            else:
                changelog["unchecked"].append(label)
                lines.append(f"changelog entry '{_short(label, 70)}': could not be checked")

        await asyncio.gather(*(one(e) for e in checked))
        self.analysis = analysis.model_copy(update={"changelog": changelog})
        await self.env.changes.update_analysis(analysis.id, changelog=changelog)
        return lines

    # -- tool: write_report ----------------------------------------------------------------------------

    async def op_write_report(self, focus: str = "", *, reuse_effort: bool = False) -> str:
        """Write the report from the active rows. `reuse_effort`: a rewrite after an edit (no analysis work was
        done) keeps the effort and cost figures of the analysis instead of counting only the rewrite."""
        analysis = self._need_analysis()
        self._guard()
        self._status("report", "Writing the report")
        env, cfg = self.env, self.env.config
        analysis = await env.changes.get_analysis(analysis.id) or analysis
        rows = await env.changes.list_rows(analysis.id)
        if not rows:
            return "The table has no rows: there is nothing to report on."
        scan_meta = dict(analysis.meta.get("scan") or {})
        previous = analysis.meta if reuse_effort and "cost" in analysis.meta else {}
        stats = {
            "diff_ratio": analysis.diff_ratio or 0.0,
            "threshold": cfg.change_diff_threshold,
            "scan": {
                "parts": scan_meta.get("parts", 0),
                "skipped": scan_meta.get("skipped", 0),
                "retries": scan_meta.get("retries", 0),
            },
            "verify_attempted": previous.get("verify_attempted", self.verify_stats["attempted"]),
            "verify_removed": previous.get("verify_removed", self.verify_stats["removed"]),
            "verify_errors": previous.get("verify_errors", self.verify_stats["errors"]),
            "verify_retries": previous.get("verify_retries", env.verify_retries),
            "model_calls": previous.get("model_calls", env.model_calls),
            "tool_steps": previous.get("tool_steps", env.tool_steps),
            "input_tokens": previous.get("input_tokens", env.usage.input_tokens),
            "output_tokens": previous.get("output_tokens", env.usage.output_tokens),
            "cost": previous.get("cost")
            or {
                **cost_breakdown(env.usage_by_model, cfg.model_prices),
                "tools": cost_by_tool(env.usage_by_tool_model, cfg.model_prices),
            },
            "vision_reads": previous.get("vision_reads", env.vision_reads),
            "seconds": previous.get("seconds", int(time.monotonic() - self._started)),
        }
        report = await build_report(
            env,
            analysis=analysis,
            rows=rows,
            changelog=dict(analysis.changelog or {}),
            stats=stats,
            changelog_mode=analysis.mode == "changelog",
        )
        meta = {
            **analysis.meta,
            **{k: v for k, v in stats.items() if k != "scan"},  # the scan's own record (complete, parts done) stays
            "parts": scan_meta.get("parts", 0),
            "current_title": analysis.current_doc.get("title", ""),
            "previous_title": analysis.previous_doc.get("title", ""),
            "models": self._models_by_stage(),
        }
        await env.changes.update_analysis(analysis.id, status="done", report=report, meta=meta)
        self.analysis = await env.changes.get_analysis(analysis.id) or analysis
        c = counts(rows)
        self._emit("analysis_done", id=str(analysis.id), status="done", counts=c)
        return f"Report written for {c['total']} rows ({c['actionable']} compliance-relevant, {c['verified']} verified)."

    def _models_by_stage(self) -> dict[str, str]:
        """Which model did which job (for the Info sheet of the Excel export)."""
        env = self.env
        return {
            "versions check and changelog matching": env.fast_llm.model,
            "scan": env.scan_llm.model,
            "verification, changelog search and report": env.llm.model,
            "page vision": env.config.resolved_vision_model,
            "search embeddings": env.config.embedding_model,
        }

    async def _finish(self, status: str, note: str = "") -> None:
        if self.analysis is None:
            return
        try:
            fields: dict = {"status": status}
            if note:
                fields["verdict_note"] = note
            await self.env.changes.update_analysis(self.analysis.id, **fields)
            self.analysis = await self.env.changes.get_analysis(self.analysis.id) or self.analysis
            self._emit("analysis_done", id=str(self.analysis.id), status=status, counts=await self._counts())
        except Exception:
            log.exception("Could not record the end of analysis %s", self.analysis.id)

    # -- tools: the table -------------------------------------------------------------------------------

    async def _row_by_handle(self, handle: str) -> ChangeRow:
        analysis = self._need_analysis()
        n = parse_handle(handle)
        if n is not None:
            for row in await self.env.changes.list_rows(analysis.id, status=None):
                if row.ordinal == n:
                    return row
        raise ToolInputError(f"There is no row '{handle}' in this table. Use get_table to list the rows (R1, R2, ...).")

    @staticmethod
    def _row_line(row: ChangeRow) -> str:
        mark = "?" if row.status == "candidate" else ("✓" if row.verified else "·")
        return (
            f"{row_handle(row)} | {row.nature.value} | {row.binding.value} | {mark} | "
            f"{_short(row.current_clause or row.previous_clause, 60)} | {_short(row.change_description)}"
        )

    async def op_get_table(
        self,
        *,
        status: str = "active",
        nature: str | None = None,
        actionable_only: bool = False,
        verified: bool | None = None,
        clause_contains: str | None = None,
        offset: int = 0,
        limit: int = LIST_ROWS,
    ) -> str:
        analysis = self._need_analysis()
        rows = await self.env.changes.list_rows(analysis.id, status=None)
        total = {s: sum(r.status == s for r in rows) for s in ("active", "candidate", "archived")}
        active = [r for r in rows if r.status == "active"]
        shown = [r for r in rows if r.status == status]
        if nature:
            shown = [r for r in shown if r.nature.value == nature]
        if actionable_only:
            shown = [r for r in shown if r.actionable]
        if verified is not None:
            shown = [r for r in shown if r.verified == verified]
        if clause_contains:
            needle = clause_contains.lower()
            shown = [r for r in shown if needle in f"{r.current_clause} {r.previous_clause} {r.change_description}".lower()]
        page = shown[offset : offset + limit]
        by_nature = ", ".join(f"{n.value} {sum(r.nature == n for r in active)}" for n in Nature)
        head = (
            f"Table {analysis.id}: {total['active']} active ({by_nature}; {sum(r.actionable for r in active)} actionable, "
            f"{sum(r.verified for r in active)} verified), {total['candidate']} candidates, {total['archived']} archived. "
            f"Showing {len(page)} of {len(shown)} {status} rows matching"
            + (f" (from {offset})" if offset else "")
            + ".\nFormat: handle | nature | binding | verified (✓ yes, · no, ? pending) | clause | description"
        )
        if not page:
            return head + "\n(no rows)"
        more = f"\n[More: call again with offset={offset + limit}]" if offset + limit < len(shown) else ""
        return head + "\n" + "\n".join(self._row_line(r) for r in page) + more

    async def op_get_row(self, handle: str) -> str:
        row = await self._row_by_handle(handle)
        proof = "\n".join(
            f"  - {'A' if p.doc == 'current' else 'B'}{' p.' + str(p.page) if p.page else ''}: \"{_short(p.text, 300)}\""
            for p in row.proof
        )
        lines = [
            f"{row_handle(row)} ({row.status}, origin {row.origin}, {'verified' if row.verified else 'not verified'}"
            + (f", confirmed by {row.confirmations} later scan(s)" if row.confirmations else "")
            + ")",
            f"Current clause (A): {row.current_clause}",
            f"Previous clause (B): {row.previous_clause or '-'}",
            f"Nature: {row.nature.value}; binding: {row.binding.value}; category: {row.category or '-'}; actionable: {row.actionable}",
            f"Change: {row.change_description}",
            f"Compliance requirement: {row.compliance_requirement}",
            f"Applicability scope: {row.applicability_scope or '-'}",
        ]
        if row.verification_note:
            lines.append(f"Verification note: {row.verification_note}")
        if row.archive_reason:
            lines.append(f"Archived: {row.archive_reason}")
        if proof:
            lines.append("Proof:\n" + proof)
        return "\n".join(lines)

    async def op_add_row(self, fields: dict, *, allow_duplicate: bool = False) -> str:
        analysis = self._need_analysis()
        row = ChangeRow(analysis_id=analysis.id, **fields).checked()
        if not (row.current_clause.strip() or row.previous_clause.strip()):
            raise ToolInputError("A row needs a current or a previous clause.")
        async with self._lock:  # the duplicate check and the insert are one step: parallel calls must not race
            if not allow_duplicate:
                known = await self.env.changes.list_rows(analysis.id, status=None)
                known = [r for r in known if r.status in ("active", "archived")]
                similar = [r for r in neighbours(row, known, 3) if similarity(row, r) >= DUPLICATE_SCORE]
                if similar:
                    lines = [
                        f"{row_handle(r)} ({r.status}): {_short(r.current_clause)} | {_short(r.change_description)}"
                        for r in similar
                    ]
                    return (
                        "Not added: similar rows exist already:\n" + "\n".join(lines) + "\n"
                        "If this is really a different finding, call add_row again with allow_duplicate=true. "
                        "To bring back an archived row use restore_row."
                    )
            stored = await self._insert_row(row, status="active", origin="agent")
        note = "" if stored.actionable else " (it is not actionable: editorial or non-normative rows are not verified in depth)"
        return f"Added {row_handle(stored)} to the table, not verified. Call verify_findings on it{note}."

    async def op_update_row(self, handle: str, fields: dict) -> str:
        row = await self._row_by_handle(handle)
        if row.status == "archived":
            raise ToolInputError(f"{row_handle(row)} is archived; restore it before editing.")
        merged = row.model_copy(update=fields).checked()
        changed = {k: getattr(merged, k) for k in fields}
        changed.update(binding=merged.binding, compliance_requirement=merged.compliance_requirement)
        reset = row.verified and any(getattr(merged, k) != getattr(row, k) for k in CLAIM_FIELDS)
        if reset:
            changed.update(verified=False, proof=[], verification_note="Edited after verification: the proof no longer applies.")
        updated = await self.env.changes.update_row(row.id, **changed)
        if updated is not None:
            self._emit("change_row_update", row=row_to_json(updated))
        return f"Updated {row_handle(row)}." + (" It was verified before: the verification was cleared, verify it again." if reset else "")

    async def op_archive_row(self, handle: str, reason: str) -> str:
        row = await self._row_by_handle(handle)
        if row.status == "archived":
            return f"{row_handle(row)} is archived already."
        await self.env.changes.archive_row(row.id, reason)
        self._emit("change_row_removed", id=str(row.id), analysis_id=str(row.analysis_id))
        return f"Archived {row_handle(row)} ({reason}). It can be restored."

    async def op_restore_row(self, handle: str) -> str:
        row = await self._row_by_handle(handle)
        if row.status != "archived":
            return f"{row_handle(row)} is not archived."
        restored = await self.env.changes.restore_row(row.id)
        if restored is not None:
            self._emit("change_row", row=row_to_json(restored))
        return f"Restored {row_handle(row)} to the table."

    # -- verify rows of an existing table: the UI's "Verify" button ---------------------------------------

    async def verify_existing(self, analysis_id: UUID, row_ids: list[str], notes: str = "") -> str:
        env = self.env
        analysis = await env.changes.get_analysis(analysis_id)
        if analysis is None:
            raise ToolInputError("That change analysis does not exist.")
        self.analysis = analysis
        self.a_doc = self._catalog_doc(analysis.current_version_id)
        self.b_doc = self._catalog_doc(analysis.previous_version_id)
        rows: list[ChangeRow] = []
        for raw in row_ids:
            try:
                row = await env.changes.get_row(UUID(str(raw)))
            except ValueError:
                row = None
            if row is not None and row.analysis_id == analysis_id and row.status != "archived":
                rows.append(row)
        if not rows:
            raise ToolInputError("None of those rows belong to that analysis.")

        self._announce()
        a_text, b_text, _ = await self._ensure_texts()
        try:
            outcomes = await self._verify_all(rows, a_text, b_text, notes)
        finally:
            self._emit("analysis_done", id=str(analysis_id), status=analysis.status, counts=None)
        verified = sum(1 for _, text in outcomes if text.startswith("verified"))
        lines = [f"Checked {len(rows)} row(s) of analysis {analysis_id}: {verified} verified."]
        lines += [f"- {clause_name(row)} ({row.nature.value}): {text}" for row, text in outcomes]
        return "\n".join(lines)

    def _catalog_doc(self, version_id: UUID | None) -> CatalogDoc:
        doc = self.catalog.by_version(version_id) if version_id else None
        if doc is None:
            raise ToolInputError(
                "A document of that analysis is not in the register (it may have been deleted). "
                "Ingest the same file again to continue; the table and report are kept."
            )
        return doc
