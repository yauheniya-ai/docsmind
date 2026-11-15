"""The Change Analyzer as an agent on a persistent table: reuse, no repeated scans, reconciliation,
archive, table tools, the model-call limit, documents missing from the register."""

from __future__ import annotations

import json

from fakes import text, tool_use
from test_change_analyzer import (
    ENCRYPTION,
    ENCRYPTION_PROOF,
    INVOICES,
    INVOICES_PROOF,
    VERSIONS,
    WORDING,
    VersionsRepo,
    scan_text,
    script,
    verifier,
)

from docsmind.agent.catalog import Catalog
from docsmind.agent.subagents.change_analysis.analyzer import ChangeAnalyzer
from docsmind.agent.subagents.specs import SUBAGENTS


def _tool_results(llm, call_index: int) -> str:
    """The tool results the agent's `call_index`-th model call was shown last, one block per tool."""
    results = []
    for message in reversed(llm.calls_for("agent")[call_index]["messages"]):
        if message.role != "tool":
            break
        results.append(message.content)
    return "\n---\n".join(reversed(results))


async def _first_run(agent, llm, repo, *rows, verifiers=None):
    script(llm, scan=scan_text(*rows), verifiers=verifiers or {})
    return await agent(repo).compare(repo.v2, repo.v1)


async def test_the_same_pair_is_not_scanned_again_and_costs_no_model_call(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    script(llm, scan=scan_text(INVOICES, WORDING), verifiers={"Invoice retention": INVOICES_PROOF})
    first = await chat.compare(repo.v2, repo.v1)
    before = len(llm.calls_for("agent"))

    llm.queue("agent", tool_use(("check_versions", {}), ("scan_documents", {})), text("It was done already."))
    second = await chat.compare(repo.v2, repo.v1, notes="Check it again")

    assert second.analysis.id == first.analysis.id and len(await changes.list_analyses()) == 1  # one analysis per pair
    assert "ALREADY ANALYZED" in second.message and "nothing was recomputed" in second.message  # not presented as new
    assert "ALREADY ANALYZED" not in first.message
    assert [r.id for r in second.rows] == [r.id for r in first.rows]
    assert len(llm.calls_for("scanner")) == len(llm.calls_for("decider")) == len(llm.calls_for("verifier")) == 1
    opening = llm.calls_for("agent")[before]["messages"][0].content
    assert "change table" in opening and "A scan was already done" in opening and "Check it again" in opening
    assert "already analyzed on" in opening and "do not redo anything" in opening
    assert "Not scanned again" in _tool_results(llm, before + 1) and "internal first pass" in _tool_results(llm, before + 1)


async def test_the_pair_is_found_when_given_the_other_way_round(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    script(llm, scan=scan_text(INVOICES))
    first = await chat.compare(repo.v2, repo.v1)
    llm.queue("agent", tool_use(("check_versions", {})), text("Known."))
    again = await chat.compare(repo.v1, repo.v2)  # oldest first this time
    assert again.analysis.id == first.analysis.id and len(llm.calls_for("decider")) == 1








async def test_table_tools_list_add_edit_archive_and_restore(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    first = await _first_run(agent, llm, repo, INVOICES, WORDING, verifiers={"Invoice retention": INVOICES_PROOF})
    new_row = {"current_clause": "Audit logs", "nature": "new", "compliance_requirement": "Keep audit logs", "binding": "normative"}
    before = len(llm.calls_for("agent"))
    llm.queue(
        "agent",
        tool_use(("get_table", {"actionable_only": True}), ("get_row", {"row": "R1"})),
        tool_use(("add_row", new_row), ("add_row", {**new_row, "change_description": "Same clause again"})),
        tool_use(("update_row", {"row": "R1", "compliance_requirement": "Keep invoices for twelve years"})),
        tool_use(("archive_row", {"row": "R2", "reason": "Not relevant"}), ("get_row", {"row": "R99"})),
        tool_use(("restore_row", {"row": "R2"})),
        text("Done."),  # the agent never calls write_report: code rewrites the report because rows changed
    )
    llm.queue("reporter", text("## Substantive changes\nInvoice retention and a new audit-log requirement."))
    result = await chat.compare(repo.v2, repo.v1, notes="Tidy the table")

    listing = _tool_results(llm, before + 1)
    assert "R1 | modified | normative | ✓ | Invoice retention" in listing and "Verbatim" not in listing
    assert "Proof:" in listing and "Invoices are kept for ten years." in listing  # get_row shows the proof
    added = _tool_results(llm, before + 2)
    assert "Added R3" in added and "Not added: similar rows exist already" in added  # the duplicate is refused
    assert "verification was cleared" in _tool_results(llm, before + 3)
    last = _tool_results(llm, before + 4)
    assert "Archived R2" in last and "no row 'R99'" in last
    assert "Restored R2" in _tool_results(llm, before + 5)

    rows = {r.current_clause: r for r in result.rows}
    assert set(rows) == {"Invoice retention", "Staff devices wording", "Audit logs"}
    assert not rows["Invoice retention"].verified and rows["Audit logs"].origin == "agent" and not rows["Audit logs"].verified
    # The report always reflects the table: it was rewritten after the changes, with no call by the agent.
    assert not result.analysis.report_stale and result.analysis.report_revision == result.analysis.table_revision
    assert "new audit-log requirement" in result.analysis.report and first.analysis.id == result.analysis.id
    assert len(llm.calls_for("reporter")) == 2  # the first comparison, and this rewrite


async def test_write_report_clears_the_outdated_flag(agent, llm, changes):
    repo = VersionsRepo()
    first = await _first_run(agent, llm, repo, WORDING)
    await changes.update_row(first.rows[0].id, category="wording")
    assert (await changes.get_analysis(first.analysis.id)).report_stale
    llm.queue("agent", tool_use(("write_report", {})), text("Rewritten."))
    llm.queue("reporter", text("## Substantive changes\nNone."))
    await agent(repo).compare(repo.v2, repo.v1, notes="Rewrite the report")
    assert not (await changes.get_analysis(first.analysis.id)).report_stale


async def test_the_model_call_limit_stops_the_costly_tools_and_says_what_is_open(config, llm, changes):
    from docsmind.adapters.storage.memory import InMemoryConversationStore, InMemoryMemoryStore
    from docsmind.agent.agent import DocsmindAgent

    repo = VersionsRepo()
    limited = DocsmindAgent(
        config.model_copy(update={"change_request_max_model_calls": 5}),
        llm=llm, fast_llm=llm, scan_llm=llm, repository=repo,
        conversations=InMemoryConversationStore(), memory=InMemoryMemoryStore(), changes=changes,
    )  # fmt: skip
    script(llm, scan=scan_text(INVOICES, ENCRYPTION), verifiers={"Invoice retention": INVOICES_PROOF}, plan=False)
    llm.queue(
        "agent",
        tool_use(("check_versions", {})),
        tool_use(("scan_documents", {})),
        tool_use(("verify_findings", {"which": "pending"})),
        tool_use(("write_report", {})),
        text("Stopped: the limit was reached."),
    )
    result = await limited.compare(repo.v2, repo.v1)
    # agent turns count too: 1 turn + versions check + 1 turn + scan + 1 turn = 5 calls, then every costly tool refuses
    assert "model-call limit of this request (5) is reached" in _tool_results(llm, 3)
    assert "model-call limit of this request (5) is reached" in _tool_results(llm, 4)
    assert len(llm.calls_for("verifier")) == 0  # no verification started past the limit
    assert len(await changes.list_rows(result.analysis.id, status="candidate")) == 2  # kept, pending
    assert "2 pending verification" in result.message


async def test_a_document_missing_from_the_register_leaves_table_and_report_usable(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    first = await _first_run(agent, llm, repo, WORDING)
    await changes.update_analysis(first.analysis.id, previous_version_id=None)  # the previous document was deleted

    catalog = Catalog.from_rows(await repo.list_catalog([repo.v2]))
    env = chat._make_env(lambda _e: None, SUBAGENTS["change_analyzer"].tools)
    llm.queue("agent", tool_use(("get_table", {}), ("scan_documents", {"rescan": True})), text("I cannot scan now."))
    out = await ChangeAnalyzer(env, catalog).run("Scan again", analysis_id=first.analysis.id)

    results = _tool_results(llm, len(llm.calls_for("agent")) - 1)
    assert "Staff devices wording" in results  # the table still reads
    assert "not in the register" in results and "ingest the same file again" in results.lower().replace("ask the user to ", "")
    assert "I cannot scan now." in out






async def test_a_report_is_rewritten_after_edits_outside_the_chat_and_downloads_are_current(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    first = await _first_run(agent, llm, repo, INVOICES, WORDING, verifiers={"Invoice retention": INVOICES_PROOF})
    revision = (await changes.get_analysis(first.analysis.id)).table_revision

    await changes.update_row(first.rows[0].id, category="finance")  # a person edits a row in the panel
    stale = await changes.get_analysis(first.analysis.id)
    assert stale.report_stale and stale.table_revision == revision + 1

    llm.queue("reporter", text("## Substantive changes\nRewritten after the edit."))
    refreshed = await chat.refresh_report(first.analysis.id)
    assert not refreshed.report_stale and "Rewritten after the edit." in refreshed.report
    assert refreshed.meta["cost"] == stale.meta["cost"]  # a rewrite keeps the effort figures of the analysis

    again = await chat.refresh_report(first.analysis.id)  # a current report is left alone: no model call
    assert again.report == refreshed.report and len(llm.calls_for("reporter")) == 2


async def test_only_report_relevant_changes_make_the_report_outdated(changes):
    from uuid import uuid4

    from docsmind.domain.changes import ChangeAnalysis, ChangeRow

    analysis = ChangeAnalysis(current_version_id=uuid4(), previous_version_id=uuid4())
    await changes.create_analysis(analysis)
    row = await changes.add_row(ChangeRow(analysis_id=analysis.id, current_clause="4.1 Backups"))
    await changes.update_analysis(analysis.id, report="# Report")
    await changes.update_row(row.id, confirmations=1)  # a rescan confirmed the row
    await changes.update_row(row.id, approved=True)  # a person ticked it
    assert not (await changes.get_analysis(analysis.id)).report_stale
    await changes.update_row(row.id, change_description="Now stricter.")
    assert (await changes.get_analysis(analysis.id)).report_stale


async def test_a_scan_that_failed_midway_is_continued_from_the_rows_saved_so_far(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    cut = json.dumps(INVOICES) + '\n{"current_clause": "Backup enc'  # one row, then the output breaks
    llm.queue("decider", text(json.dumps(VERSIONS)))
    llm.queue("scanner", text(cut), text("garbage"), text("garbage"), text("garbage"))  # every attempt fails
    llm.queue("agent", tool_use(("check_versions", {})), tool_use(("scan_documents", {})), text("The scan stopped."))
    first = await chat.compare(repo.v2, repo.v1)
    meta = (await changes.get_analysis(first.analysis.id)).meta["scan"]
    assert meta["complete"] is False  # marked unfinished
    assert "The scan stopped midway" in _tool_results(llm, 2) and "Call scan_documents again" in _tool_results(llm, 2)
    saved = await changes.list_rows(first.analysis.id, status="candidate")
    assert [r.current_clause for r in saved] == ["Invoice retention"]  # what was found is kept, pending

    # A new request calls it again: the scanner is given the rows so far and finishes the job.
    llm.queue("agent", tool_use(("scan_documents", {})), tool_use(("verify_findings", {"which": "pending"})), text("Done."))
    llm.queue("scanner", text(scan_text(ENCRYPTION)))  # only what was missing
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Backup encryption": ENCRYPTION_PROOF}))
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Backup encryption": ENCRYPTION_PROOF}))
    llm.queue("reporter", text("## Substantive changes\nBoth."))
    second = await chat.compare(repo.v2, repo.v1, notes="Continue")
    resumed = llm.calls_for("scanner")[4]["messages"][0].content
    assert "<rows_so_far>" in resumed and "Invoice retention" in resumed
    assert sorted(r.current_clause for r in second.rows) == ["Backup encryption", "Invoice retention"]
    assert all(r.verified for r in second.rows)
    assert (await changes.get_analysis(first.analysis.id)).meta["scan"]["complete"] is True
    assert len(llm.calls_for("scanner")) == 5 and len(llm.calls_for("decider")) == 1

    # Now it is finished: another scan is refused.
    llm.queue("agent", tool_use(("scan_documents", {})), text("Nothing to scan."))
    await chat.compare(repo.v2, repo.v1, notes="Again")
    assert "Not scanned again" in _tool_results(llm, len(llm.calls_for("agent")) - 1)
    assert len(llm.calls_for("scanner")) == 5


async def test_a_crash_before_the_scan_finished_leaves_it_marked_unfinished(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    script(llm, scan=scan_text(WORDING), plan=False)
    llm.queue("agent", tool_use(("check_versions", {})), text("Checked."))
    first = await chat.compare(repo.v2, repo.v1)
    analysis = await changes.get_analysis(first.analysis.id)
    # simulate a process that died while scanning: the marker is written before the first model call
    await changes.update_analysis(analysis.id, meta={**analysis.meta, "scan": {"complete": False, "done_parts": []}})
    llm.queue("agent", tool_use(("scan_documents", {})), text("ok"))
    llm.queue("reporter", text("## Substantive changes\nx"))
    await chat.compare(repo.v2, repo.v1, notes="Continue")
    assert (await changes.get_analysis(analysis.id)).meta["scan"]["complete"] is True
    assert len(llm.calls_for("scanner")) == 1
