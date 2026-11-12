"""The Change Analyzer: a code-driven pipeline, with models used inside the steps.

    texts -> prepare (normalize, diff, ratio)            code
          -> versions check (are these versions? which is newer? public?)   small model call
          -> scan (+ changelog search in parallel)       one long-context call; a tool loop
          -> verify every compliance-relevant row        a small tool loop per row, in parallel
          -> match changelog entries, check the gaps     small model call; tool loops
          -> report                                      one model call, plus code-built sections

The Mind only calls `delegate`; everything above happens here, in order, in code. A is always the
current version and B the previous one (the Mind passes [current, previous]; the versions check can
swap them when the labels say otherwise).
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID, uuid4

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.subagents.change_analysis.changelog import find_changelog, match_entries
from docsmind.agent.subagents.change_analysis.env import RunEnv
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
from docsmind.domain.agent import AgentEvent
from docsmind.domain.changes import ChangeAnalysis, ChangeRow, Nature
from docsmind.domain.errors import ToolInputError
from docsmind.pricing import cost_breakdown

log = logging.getLogger(__name__)

MAX_GAP_CHECKS = 20
OUTLINE_ENTRIES = 40
FIRST_PAGE_CHARS = 1_500
VERDICTS = ("versions", "unrelated", "changelog")


def _doc_info(doc: CatalogDoc) -> dict:
    return {"handle": doc.handle, "title": doc.title, "label": doc.label, "version_id": str(doc.version_id)}


def _label(entry: dict) -> str:
    """A changelog entry as one line, in full: the report never cuts it short."""
    text = f"{entry['clause']}: {entry['text']}" if entry["clause"] else entry["text"]
    return " ".join(text.split())


class ChangeAnalyzer:
    def __init__(self, env: RunEnv, catalog: Catalog) -> None:
        self.env = env
        self.catalog = catalog
        self.rows: dict[UUID, ChangeRow] = {}  # this run's rows, in table order
        self.verify_stats = {"attempted": 0, "verified": 0, "removed": 0, "errors": 0}
        self._analysis_id: UUID | None = None
        self._lock = asyncio.Lock()  # row ordinals are assigned on insert: one insert at a time

    # -- events ------------------------------------------------------------------------

    @property
    def analysis_id(self) -> UUID | None:
        """The analysis this run created (None when it stopped before one was needed)."""
        return self._analysis_id

    def _emit(self, type: str, **data) -> None:
        self.env.emit(AgentEvent(type=type, data=data))

    def _status(self, phase: str, text: str) -> None:
        self._emit("analysis_status", id=str(self._analysis_id) if self._analysis_id else None, phase=phase, text=text)

    # -- compare two documents ---------------------------------------------------------------

    async def compare(self, first: CatalogDoc, second: CatalogDoc, notes: str = "") -> str:
        """The whole pipeline. -> the text handed back to the Mind."""
        sub_id = uuid4().hex[:8]
        self._emit(
            "subagent_start",
            id=sub_id,
            name="change_analyzer",
            task=f"Compare {first.handle} ({first.label}) with {second.handle} ({second.label})",
        )
        ok = False
        try:
            text = await self._compare(first, second, notes, sub_id)
            ok = True
            return text
        finally:
            self._emit("subagent_end", id=sub_id, name="change_analyzer", ok=ok)

    async def _compare(self, first: CatalogDoc, second: CatalogDoc, notes: str, sub_id: str) -> str:
        env, cfg = self.env, self.env.config
        started = time.monotonic()

        self._status("prepare", "Reading and comparing the texts")
        raw_first, raw_second = await asyncio.gather(
            env.repo.get_full_text(first.version_id), env.repo.get_full_text(second.version_id)
        )
        for doc, raw in ((first, raw_first), (second, raw_second)):
            if not (raw or "").strip():
                return f"{doc.handle} has no extracted text (it may be a scan), so it cannot be compared."
        prep = await asyncio.to_thread(
            prepare,
            raw_first,
            raw_second,
            threshold=cfg.change_diff_threshold,
            pages_a=first.page_count,
            pages_b=second.page_count,
        )
        if prep.identical:
            return (
                f"{first.handle} and {second.handle} are identical once headers, footers and page numbers "
                "are removed: there is nothing to compare."
            )

        self._status("check", "Checking that these are versions of one document")
        decision = await self._decide(first, second, prep, notes)
        if decision["verdict"] == "unrelated":
            return decision["explanation"] or (
                f"{first.handle} and {second.handle} do not look like two versions of one document, so I did not compare them."
            )

        # Orientation: A is the current version (or, for a changelog, the document itself).
        a_doc, b_doc, a_raw, b_raw = first, second, raw_first, raw_second
        if decision["newer"] == "B":
            a_doc, b_doc, a_raw, b_raw = second, first, raw_second, raw_first
        changelog_mode = decision["verdict"] == "changelog"
        if changelog_mode:
            prep = await asyncio.to_thread(
                prepare_changelog, a_raw, b_raw, pages_a=a_doc.page_count, pages_b=b_doc.page_count
            )
        elif a_doc is not first:
            prep = await asyncio.to_thread(
                prepare, a_raw, b_raw, threshold=cfg.change_diff_threshold, pages_a=a_doc.page_count, pages_b=b_doc.page_count
            )
        mode = "changelog" if changelog_mode else prep.mode

        analysis = ChangeAnalysis(
            current_version_id=a_doc.version_id,
            previous_version_id=b_doc.version_id,
            mode=mode,  # type: ignore[arg-type]
            diff_ratio=None if changelog_mode else prep.ratio,
            public=decision["public"],
            verdict_note=decision["explanation"],
        )
        await env.changes.create_analysis(analysis)
        self._analysis_id = analysis.id
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

        changelog_task: asyncio.Task | None = None
        try:
            parts = plan_parts(
                prep, split_tokens=cfg.change_scan_split_tokens, max_input_tokens=cfg.change_scan_max_input_tokens
            )
            if changelog_mode:
                self._whole_changelog(prep, parts)

            a_text = DocText(a_doc, await asyncio.to_thread(squash, a_raw))
            b_text = DocText(b_doc, await asyncio.to_thread(squash, b_raw))

            # Scan and changelog search run at the same time, each in its own small context.
            if not changelog_mode:
                changelog_task = asyncio.create_task(
                    find_changelog(
                        env,
                        a_doc,
                        b_doc,
                        public=bool(analysis.public),
                        query=decision["query"],
                        notes=notes,
                        emit=env.emit,
                        tag={"subagent": sub_id},
                    )
                )
            self._status("scan", "Scanning for changes")
            scan_failure: str | None = None
            try:
                scan_stats = await scan(
                    env,
                    prep,
                    parts,
                    changelog_mode=changelog_mode,
                    notes=notes,
                    on_row=self._store_row,
                    on_status=lambda t: self._status("scan", t),
                )
            except ScanFailed as exc:
                scan_failure = str(exc)
                scan_stats = {"parts": len(parts), "skipped": 0, "retries": 0, "rows": len(self.rows)}
            changelog: dict = {"found": False, "entries": []}
            if changelog_task is not None:
                if scan_failure:
                    changelog_task.cancel()
                else:
                    changelog = await changelog_task
            if scan_failure:
                await self._finish(analysis.id, "failed", note=scan_failure)
                return (
                    f"{scan_failure} {len(self.rows)} candidate rows were stored (analysis {analysis.id}), "
                    "but they are not verified. Try again."
                )

            # Verify the compliance-relevant rows against the text.
            targets = [r for r in self.rows.values() if r.actionable]
            if changelog_mode:
                for row in [r for r in targets if r.nature == Nature.REMOVED]:
                    await self._note(row, "A removal cannot be proven from a changelog alone.")
                targets = [r for r in targets if r.nature != Nature.REMOVED]
            await self._verify_all(targets, a_text, b_text, changelog_mode, notes, remove_unproven=True)

            # Changelog entries that no row explains: look at both versions.
            if changelog.get("found"):
                await self._changelog_gaps(changelog, a_text, b_text, notes)
            await env.changes.update_analysis(analysis.id, changelog=changelog)

            rows = list(self.rows.values())
            stats = {
                "diff_ratio": 0.0 if changelog_mode else prep.ratio,
                "threshold": cfg.change_diff_threshold,
                "scan": scan_stats,
                "verify_attempted": self.verify_stats["attempted"],
                "verify_removed": self.verify_stats["removed"],
                "verify_errors": self.verify_stats["errors"],
                "verify_retries": env.verify_retries,
                "model_calls": env.model_calls,
                "tool_steps": env.tool_steps,
                "input_tokens": env.usage.input_tokens,
                "output_tokens": env.usage.output_tokens,
                "cost": cost_breakdown(env.usage_by_model),
                "vision_reads": env.vision_reads,
                "seconds": int(time.monotonic() - started),
            }
            self._status("report", "Writing the report")
            report = await build_report(
                env,
                analysis=analysis,
                rows=rows,
                changelog=changelog,
                stats=stats,
                changelog_mode=changelog_mode,
            )
            meta = {**stats, "parts": len(parts)}
            await env.changes.update_analysis(analysis.id, status="done", report=report, meta=meta)
            self._emit("analysis_done", id=str(analysis.id), status="done", counts=counts(rows))
        except asyncio.CancelledError:
            await self._finish(analysis.id, "stopped")
            raise
        except Exception:
            await self._finish(analysis.id, "failed")
            raise
        finally:
            if changelog_task is not None and not changelog_task.done():
                changelog_task.cancel()

        return self._summary(analysis, a_doc, b_doc, mode, rows, report)

    # -- verify rows of an existing table ---------------------------------------------------------------

    async def verify_existing(self, analysis_id: UUID, row_ids: list[str], notes: str = "") -> str:
        env = self.env
        analysis = await env.changes.get_analysis(analysis_id)
        if analysis is None:
            raise ToolInputError("That change analysis does not exist.")
        a_doc = self.catalog_doc(analysis.current_version_id)
        b_doc = self.catalog_doc(analysis.previous_version_id)
        rows: list[ChangeRow] = []
        for raw in row_ids:
            try:
                row = await env.changes.get_row(UUID(str(raw)))
            except ValueError:
                row = None
            if row is not None and row.analysis_id == analysis_id:
                rows.append(row)
        if not rows:
            raise ToolInputError("None of those rows belong to that analysis.")

        self._analysis_id = analysis_id
        self._emit(
            "analysis_start",
            id=str(analysis_id),
            current=_doc_info(a_doc),
            previous=_doc_info(b_doc),
            mode=analysis.mode,
            diff_ratio=analysis.diff_ratio,
            public=analysis.public,
            note=analysis.verdict_note,
            reverify=True,
        )
        a_raw, b_raw = await asyncio.gather(
            env.repo.get_full_text(a_doc.version_id), env.repo.get_full_text(b_doc.version_id)
        )
        a_text = DocText(a_doc, await asyncio.to_thread(squash, a_raw or ""))
        b_text = DocText(b_doc, await asyncio.to_thread(squash, b_raw or ""))
        changelog_mode = analysis.mode == "changelog"
        for row in rows:
            self.rows[row.id] = row
        try:
            await self._verify_all(rows, a_text, b_text, changelog_mode, notes, remove_unproven=False)
        finally:
            self._emit("analysis_done", id=str(analysis_id), status=analysis.status, counts=None)
        verified = [r for r in self.rows.values() if r.verified]
        lines = [f"Checked {len(rows)} row(s) of analysis {analysis_id}: {len(verified)} verified."]
        for row in self.rows.values():
            state = "verified" if row.verified else f"not verified ({row.verification_note or 'no proof'})"
            lines.append(f"- {clause_name(row)} ({row.nature.value}): {state}")
        return "\n".join(lines)

    def catalog_doc(self, version_id: UUID) -> CatalogDoc:
        doc = self.catalog.by_version(version_id)
        if doc is None:
            raise ToolInputError("The documents of that analysis are not in the current selection.")
        return doc

    # -- steps ----------------------------------------------------------------------------------------

    async def _decide(self, first: CatalogDoc, second: CatalogDoc, prep: Prepared, notes: str) -> dict:
        glances = await asyncio.gather(self._glance(first), self._glance(second))
        parts = []
        for tag, doc, glance in (("document_a", first, glances[0]), ("document_b", second, glances[1])):
            parts.append(
                f'<{tag} handle="{doc.handle}" title="{doc.title}" label="{doc.label}">\n{glance}\n</{tag}>'
            )
        parts.append(f"<diff_ratio>{prep.ratio:.2f}</diff_ratio>")
        if notes:
            parts.append(f"<focus>\n{notes}\n</focus>")
        answer = await self.env.json_call(self.env.fast_llm, prompts.DECIDER, "\n".join(parts), max_tokens=700) or {}
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

    @staticmethod
    def _whole_changelog(prep: Prepared, parts: list[ScanPart]) -> None:
        """Every part of the document comes with the whole changelog."""
        for part in parts:
            part.b_lo, part.b_hi = 0, len(prep.b)
            part.tokens = sum(prep._line_tokens_a[part.lo : part.hi]) + prep.tokens_b

    async def _store_row(self, row: ChangeRow) -> None:
        row = row.model_copy(update={"analysis_id": self._analysis_id})
        async with self._lock:
            stored = await self.env.changes.add_row(row)
        self.rows[stored.id] = stored
        self._emit("change_row", row=row_to_json(stored))

    async def _note(self, row: ChangeRow, note: str) -> None:
        updated = await self.env.changes.update_row(row.id, verification_note=note)
        if updated is not None:
            self.rows[row.id] = updated
            self._emit("change_row_update", row=row_to_json(updated))

    async def _verify_all(
        self,
        targets: list[ChangeRow],
        a: DocText,
        b: DocText,
        changelog_mode: bool,
        notes: str,
        *,
        remove_unproven: bool,
    ) -> None:
        if not targets:
            return
        semaphore = asyncio.Semaphore(self.env.config.change_verify_parallel)
        finished = 0
        self._status("verify", f"Verifying {len(targets)} rows against the text")

        async def one(row: ChangeRow) -> None:
            nonlocal finished
            async with semaphore:
                verdict = await verify_row(self.env, row=row, a=a, b=b, changelog_mode=changelog_mode, notes=notes)
            await self._apply(row, verdict, remove_unproven=remove_unproven)
            finished += 1
            self._status("verify", f"Verified {finished} of {len(targets)} rows")

        await asyncio.gather(*(one(r) for r in targets))

    async def _apply(self, row: ChangeRow, verdict: Verdict, *, remove_unproven: bool) -> None:
        changes = self.env.changes
        self.verify_stats["attempted"] += 1
        if verdict.status == "verified":
            self.verify_stats["verified"] += 1
            updated = await changes.update_row(row.id, verified=True, proof=verdict.proof, verification_note=None)
        elif verdict.status == "not_found" and remove_unproven:
            self.verify_stats["removed"] += 1
            # No proof in the text: the change does not exist. It is dropped, not reported or stored.
            log.info("Dropped %s (%s): %s", clause_name(row), row.nature.value, verdict.note)
            await changes.delete_row(row.id)
            self.rows.pop(row.id, None)
            self._emit("change_row_removed", id=str(row.id), analysis_id=str(row.analysis_id))
            return
        else:
            if verdict.status == "error":
                self.verify_stats["errors"] += 1
                note = verdict.note
            else:
                note = f"Not verified: {verdict.note}"
            updated = await changes.update_row(row.id, verified=False, proof=[], verification_note=note)
        if updated is not None:
            self.rows[row.id] = updated
            self._emit("change_row_update", row=row_to_json(updated))

    async def _changelog_gaps(self, changelog: dict, a: DocText, b: DocText, notes: str) -> None:
        """Compliance-relevant changelog entries that no table row describes."""
        entries = changelog.get("entries") or []
        relevant = [e for e in entries if e.get("compliance_relevant")]
        changelog["added"], changelog["claimed_not_found"], changelog["unchecked"] = [], [], []
        if not relevant:
            return
        self._status("changelog", "Matching the changelog to the table")
        matches = await match_entries(self.env, relevant, list(self.rows.values()))
        unmatched = [relevant[i] for i, row_id in matches.items() if row_id is None]
        changelog["matched"] = len(relevant) - len(unmatched)
        if not unmatched:
            return

        checked = unmatched[:MAX_GAP_CHECKS]
        changelog["unchecked"] = [_label(e) for e in unmatched[MAX_GAP_CHECKS:]]
        self._status("changelog", f"Checking {len(checked)} changelog entries the table does not cover")
        semaphore = asyncio.Semaphore(self.env.config.change_verify_parallel)

        async def one(entry: dict) -> None:
            label = _label(entry)
            async with semaphore:
                verdict = await verify_row(
                    self.env,
                    row=None,
                    entry=f"{entry['clause']}\n{entry['text']}".strip(),
                    a=a,
                    b=b,
                    changelog_mode=False,
                    notes=notes,
                )
            if verdict.status == "verified" and verdict.row is not None:
                row = verdict.row.model_copy(update={"verified": True, "proof": verdict.proof, "origin": "changelog"}).checked()
                await self._store_row(row)
                changelog["added"].append(clause_name(row))
            elif verdict.status == "not_found":
                changelog["claimed_not_found"].append(label)
            else:
                changelog["unchecked"].append(label)

        await asyncio.gather(*(one(e) for e in checked))

    async def _finish(self, analysis_id: UUID, status: str, note: str = "") -> None:
        try:
            fields: dict = {"status": status}
            if note:
                fields["verdict_note"] = note
            await self.env.changes.update_analysis(analysis_id, **fields)
            self._emit("analysis_done", id=str(analysis_id), status=status, counts=counts(list(self.rows.values())))
        except Exception:
            log.exception("Could not record the end of analysis %s", analysis_id)

    def _summary(self, analysis: ChangeAnalysis, a: CatalogDoc, b: CatalogDoc, mode: str, rows: list[ChangeRow], report: str) -> str:
        c = counts(rows)
        head = [
            f"Change analysis {analysis.id} is complete and shown next to the chat (the table and the full report).",
            f"A (current) = {a.handle} · {a.title} ({a.label}); B (previous) = {b.handle} · {b.title} ({b.label}); mode: {mode}.",
            (
                f"{c['total']} changes: {c['new']} new, {c['modified']} modified, {c['removed']} removed, "
                f"{c['editorial']} editorial, {c['relocated']} relocated; "
                f"{c['actionable']} compliance-relevant, {c['verified']} verified."
            ),
        ]
        text = "\n".join(head) + "\n\n" + report
        limit = self.env.config.change_report_max_chars
        return text if len(text) <= limit else text[: limit - 1] + "…"
