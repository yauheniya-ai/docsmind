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
    llm.queue("gate", text('{"duplicate_of": null, "reason": "new"}'))  # the first add_row: nothing covers it
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
    assert "Added R3" in added and "Not added: R3 (active) already covers this" in added  # the repeat is refused
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
    script(llm, scan=scan_text(INVOICES, ENCRYPTION, WORDING), verifiers={"Invoice retention": INVOICES_PROOF}, plan=False)
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
    assert "Report written" in _tool_results(llm, 4)  # the report is never refused by the limit
    assert len(llm.calls_for("verifier")) == 0  # no verification started past the limit
    assert len(await changes.list_rows(result.analysis.id, status="candidate")) == 2  # kept, pending
    assert "2 pending verification" in result.message
    assert result.analysis.report and "## Methodology" in result.analysis.report  # written although the limit was hit


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
    # the figures cover the whole history: the first request, plus the rewrite (one more model call)
    assert refreshed.meta["requests"] == 2 and refreshed.meta["model_calls"] > stale.meta["model_calls"]

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


async def test_the_methodology_covers_every_request_not_only_the_last(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    first = await _first_run(agent, llm, repo, INVOICES, verifiers={"Invoice retention": INVOICES_PROOF})
    calls_first = first.analysis.meta["model_calls"]
    assert "requests on this analysis" not in first.analysis.report  # a single request: no "all requests" wording

    llm.queue("agent", tool_use(("update_row", {"row": "R1", "category": "finance"})), text("Edited."))
    llm.queue("reporter", text("## Substantive changes\nAfter the edit."))
    second = await chat.compare(repo.v2, repo.v1, notes="Tag the invoice row")
    meta = second.analysis.meta
    assert meta["requests"] == 2 and meta["model_calls"] > calls_first  # this request and the first one
    assert "Effort of all 2 requests on this analysis" in second.analysis.report
    assert meta["cost"]["tools"] and any(t["tool"] == "scan_documents" for t in meta["cost"]["tools"])  # the first request's tools
    assert "Scan: 1 candidate rows" in second.analysis.report  # counted from the table, not from the last call


def test_plain_text_shows_documents_as_a_and_b_and_drops_source_markers():
    from docsmind.agent.subagents.change_analysis.rows import plain_text

    assert plain_text("Found in D2 pp.119-120, Appendix E [S1] and D1 p.4 [S1, S11].", "D2", "D1") == (
        "Found in A p.119-120, Appendix E and B p.4."
    )
    assert plain_text("See D3 for details [S2]", "D2", "D1") == "See D3 for details"  # other documents stay as they are


async def test_a_changelog_found_is_written_for_people_and_a_given_url_is_read_first(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    finder = {
        "found": True,
        "source": "D2 pp.2-3, Appendix E [S1]",
        "summary": "The changelog [S4] lists changes of D2 against D1.",
        "entries": [{"clause": "D2 p.2: 2", "kind": "new", "text": "Backups encrypted [S1]", "compliance_relevant": True}],
    }
    llm.queue("decider", text(json.dumps({**VERSIONS, "public": True})))
    llm.queue("scanner", text(scan_text()))
    llm.queue("finder", text(json.dumps(finder)))
    llm.queue(
        "agent",
        tool_use(("check_versions", {})),
        tool_use(("find_changelog", {"url": "https://publisher.example/changes"})),
        text("Done."),
    )
    result = await chat.compare(repo.v2, repo.v1)

    task = llm.calls_for("finder")[0]["messages"][0].content
    assert "<changelog_url>https://publisher.example/changes</changelog_url>" in task
    assert "web_search is not available" in task  # no search key: the DOI or URL of the documents is the way
    stored = result.analysis.changelog
    assert stored["source"] == "A p.2-3, Appendix E"  # clickable in the report, no register handle
    assert stored["summary"] == "The changelog lists changes of A against B."
    assert stored["entries"][0]["clause"] == "A p.2: 2" and stored["entries"][0]["text"] == "Backups encrypted"


def _row(clause: str, nature: str, **kw):
    from docsmind.domain.changes import Binding, ChangeRow, Nature

    binding = kw.pop("binding", Binding.NORMATIVE if nature in ("new", "modified", "removed") else Binding.UNCHANGED)
    return ChangeRow(
        current_clause=clause, nature=Nature(nature), binding=binding,
        compliance_requirement=kw.pop("requirement", "Do it" if nature in ("new", "modified", "removed") else "No change"),
        change_description=kw.pop("description", "Changed."), **kw,
    ).checked()  # fmt: skip


def test_the_report_counts_substantive_changes_and_lists_editorial_and_relocated_clauses_by_id():
    from docsmind.agent.subagents.change_analysis.report import _changes_section, clause_id

    rows = [
        _row("3.1.1. Account management", "new"),
        _row("3.1.2. Access enforcement", "modified"),
        _row("3.1.3. Flow", "modified", binding="informative", requirement="No change"),  # non-normative: still counted
        _row("03.05.08. Password", "removed"),
        _row("3.2.1. Awareness", "editorial"),
        _row("Appendix C Mapping", "editorial"),
        _row("3.4.2. Baseline", "relocated"),
    ]
    section = _changes_section(rows)
    assert "Substantive: New 1 · Modified 2 · Removed 1 (4 in total, 3 of them compliance-relevant)" in section
    assert "Editorial (2): 3.2.1, Appendix C" in section and "Relocated (1): 3.4.2" in section
    assert "Account management" not in section  # the table has the detail: the report does not repeat it
    assert clause_id(_row("Section 4.2 Retention", "new")) == "Section 4.2" and clause_id(_row("Overview of changes", "new")) == "Overview of changes"
    assert "Editorial" not in _changes_section([_row("1. A", "new")])  # nothing editorial: no empty line


async def test_the_report_has_the_compliance_impact_first_and_the_changelog_in_the_methodology(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    finder = {"found": True, "source": "D2 p.3", "summary": "Release notes list the changes.", "entries": [
        {"clause": "2", "kind": "new", "text": "Backups encrypted", "compliance_relevant": True}]}  # fmt: skip
    llm.queue("decider", text(json.dumps({**VERSIONS, "public": True})))
    llm.queue("scanner", text(scan_text(INVOICES, WORDING)))
    llm.queue("finder", text(json.dumps(finder)))
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF}))
    llm.queue("agent", tool_use(("check_versions", {})), tool_use(("find_changelog", {}), ("scan_documents", {})),
              tool_use(("verify_findings", {"which": "pending"})), text("Done."))  # fmt: skip
    llm.queue("reporter", text("## Whatever heading\n- **Retention**: invoices are kept longer.\n- **Other**: details."))
    result = await chat.compare(repo.v2, repo.v1)

    report = result.analysis.report
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    assert headings == ["## Compliance impact", "## Changes", "## Methodology"]
    assert report.startswith("## Compliance impact\n- **Retention**: invoices are kept longer.")  # the model's own heading is dropped
    methodology = report.split("## Methodology")[1]
    assert "Changelog: searched in the documents only; they are public, but nothing was read online" in methodology
    assert "found (A p.3). Release notes list the changes." in methodology
    assert methodology.index("Changelog:") < methodology.index("Verification:")
    assert "Changelog" not in report.split("## Methodology")[0]  # it is no section of its own any more
    facts = json.loads(llm.calls_for("reporter")[0]["messages"][0].content)
    assert "pages" not in json.dumps(facts) and facts["changelog_summary"] == "Release notes list the changes."


async def test_the_methodology_says_whether_the_changelog_was_looked_for_online(agent, llm, changes):
    from docsmind.agent.subagents.change_analysis.report import _changelog_lines

    base = {"found": False}
    assert "internal documents: no web access" in _changelog_lines({**base, "public": False}, changelog_mode=False)[0]
    assert "in the documents and online (by the publication's own link: web search is not set up)" in _changelog_lines(
        {**base, "public": True, "online": True, "web_search": False}, changelog_mode=False
    )[0]
    assert "web search is not set up and no link was found" in _changelog_lines(
        {**base, "public": True, "online": False, "web_search": False}, changelog_mode=False
    )[0]
    assert "second document is the changelog" in _changelog_lines({}, changelog_mode=True)[0]


async def test_a_cut_off_narrative_ends_after_its_last_complete_sentence():
    from docsmind.agent.subagents.change_analysis.report import _complete

    assert _complete("- One thing changed.\n- Another thing was cut off in mid (A p.") == "- One thing changed."
    assert _complete("A complete sentence.") == "A complete sentence."


def test_clause_identifiers_tie_the_two_numberings_of_one_clause_together():
    from docsmind.agent.subagents.change_analysis.matching import clause_id, row_clause_ids

    assert clause_id("03.01.08 Unsuccessful Logon Attempts") == "3.1.8"
    assert clause_id("3.1 ACCESS CONTROL \u2013 3.1.8 Limit unsuccessful logon attempts") == "3.1.8"  # the most specific number
    assert clause_id("Appendix C. Tailoring Criteria") == "" and clause_id("Section 3") == ""
    a = _row("03.01.08 Unsuccessful Logon Attempts", "modified", previous_clause="3.1 ACCESS CONTROL \u2013 3.1.8 Limit logons")
    b = _row("03.01.08", "modified", previous_clause="3.1.8")
    assert row_clause_ids(a) == row_clause_ids(b) == {"3.1.8"}


async def test_a_changelog_check_never_adds_a_row_the_scan_already_listed(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    finder = {"found": True, "source": "A p.3", "summary": "Notes.", "entries": [
        {"clause": "Retention of invoices", "kind": "modified", "text": "Invoices must be kept ten years", "compliance_relevant": True}]}  # fmt: skip
    gap = {
        "verified": True, "reason": "Real.",
        "row": {**INVOICES, "current_clause": "4.2 Retention of invoices", "change_description": "General retention revision."},
        "proof": INVOICES_PROOF["proof"],
    }  # fmt: skip
    llm.queue("decider", text(json.dumps(VERSIONS)))
    llm.queue("scanner", text(scan_text(INVOICES)))
    llm.queue("finder", text(json.dumps(finder)))
    llm.queue("matcher", text('{"matches": [{"entry": 0, "row": null}]}'))  # the matcher misses it, as it did on the NIST pair
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Invoices must be kept ten years": gap}))
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Invoices must be kept ten years": gap}))
    llm.queue("gate", text('{"duplicate_of": "R1", "reason": "The same retention change."}'))
    llm.queue(
        "agent",
        tool_use(("check_versions", {})),
        tool_use(("scan_documents", {})),
        tool_use(("verify_findings", {"which": "pending"})),
        tool_use(("find_changelog", {})),
        tool_use(("verify_findings", {"changelog_gaps": True})),
        text("Done."),
    )
    result = await chat.compare(repo.v2, repo.v1)

    assert [r.current_clause for r in result.rows] == ["Invoice retention"]  # no second row for the same change
    assert "covered by R1 already: not added" in _tool_results(llm, 5)
    stored = result.analysis.changelog
    assert stored["already_in_table"] == ["Retention of invoices: Invoices must be kept ten years (R1)"] and stored["matched"] == 1
    assert not stored["added"]
    # the gate was shown the full table and the new finding
    shown = json.loads(llm.calls_for("gate")[0]["messages"][0].content)
    assert [r["clause_a"] for r in shown["rows"]] == ["Invoice retention"] and shown["new"]["clause_a"] == "4.2 Retention of invoices"


async def test_a_changelog_entry_that_names_a_clause_number_is_matched_by_code(agent, llm, changes):
    repo = VersionsRepo()
    chat = agent(repo)
    logon = {**INVOICES, "current_clause": "03.01.08 Unsuccessful Logon Attempts", "previous_clause": "3.1 ACCESS CONTROL - 3.1.8 Limit logons"}
    finder = {"found": True, "source": "A p.3", "summary": "Notes.", "entries": [
        {"clause": "03.01.08; Section 3", "kind": "modified", "text": "Logon limit", "compliance_relevant": True}]}  # fmt: skip
    llm.queue("decider", text(json.dumps(VERSIONS)))
    llm.queue("scanner", text(scan_text(logon)))
    llm.queue("finder", text(json.dumps(finder)))
    llm.queue("agent", tool_use(("check_versions", {})), tool_use(("scan_documents", {})), tool_use(("find_changelog", {})), text("Done."))
    llm.queue("reporter", text("- ok"))
    result = await chat.compare(repo.v2, repo.v1)
    assert not llm.calls_for("matcher")  # no model call: 03.01.08 is 3.1.8
    assert result.analysis.changelog["matched"] == 1 and result.analysis.changelog["unmatched"] == []


def test_verification_status_is_stated_in_the_methodology_not_in_the_compliance_impact():
    from docsmind.agent.subagents.change_analysis.report import _facts, _verification_line, counts

    rows = [
        _row("3.1.1. A", "new", verified=True),
        _row("3.1.2. B", "modified"),  # compliance-relevant, not verified yet
        _row("3.2.1. C", "editorial"),
        _row("3.3.1. D", "modified", binding="informative", requirement="No change"),
    ]
    line = _verification_line(rows, {"verify_attempted": 2}, counts(rows))
    assert "1 compliance-relevant row is not verified yet" in line
    assert "The 2 other rows (editorial, relocated or without a normative requirement) are listed but not verified; they can be verified on request." in line
    assert all("verified" not in change for change in _facts(rows, {})["changes"])  # the narrative is not told, so it cannot comment on it
    all_done = [_row("3.1.1. A", "new", verified=True)]
    assert "not verified" not in _verification_line(all_done, {"verify_attempted": 1}, counts(all_done))


_NIST_CSV = (
    "Change analysis, Revision 2 to Revision 3\n"
    "Rev 3 ID,Rev 2 ID,Change Type,Change Detail\n"
    "03.01.08,3.1.8,Modified,Adds a limit and a time window for unsuccessful logons\n"
    "03.05.08,3.5.8,Withdrawn,Password reuse prohibition removed\n"
    "03.01.01,3.1.1,No change,\n"
)
_TABLE = {
    "url": "https://publisher.example/changes.csv",
    "clause_column": ["Rev 3 ID", "Rev 2 ID"],
    "kind_column": "Change Type",
    "text_column": "Change Detail",
}


def _serve_csv(monkeypatch, data: str = _NIST_CSV):
    from docsmind.agent.tools import web

    async def fake(url, config, *, allowed, max_bytes=None):
        return url, "text/csv", data.encode(), "utf-8"

    monkeypatch.setattr(web, "fetch_bytes", fake)


async def test_every_row_of_a_spreadsheet_changelog_is_read_by_code(agent, monkeypatch):
    from docsmind.agent.subagents.change_analysis.changelog import entries_from_table

    _serve_csv(monkeypatch)
    env = agent(VersionsRepo())._make_env(lambda _e: None, SUBAGENTS["change_analyzer"].tools)
    entries = await entries_from_table(env, _TABLE)  # a title row above the header does no harm
    assert [(e["clause"], e["kind"], e["compliance_relevant"]) for e in entries] == [
        ("03.01.08 / 3.1.8", "modified", True),
        ("03.05.08 / 3.5.8", "removed", True),  # "Withdrawn"
        ("03.01.01 / 3.1.1", "other", False),  # "No change"
    ]
    assert entries[0]["text"] == "Adds a limit and a time window for unsuccessful logons"
    assert await entries_from_table(env, {**_TABLE, "clause_column": ["No such column"]}) == []  # unreadable: nothing invented


async def test_a_spreadsheet_changelog_is_imported_matched_by_clause_number_and_kept(agent, llm, changes, monkeypatch):
    repo = VersionsRepo()
    chat = agent(repo)
    _serve_csv(monkeypatch)
    logon = {**INVOICES, "current_clause": "03.01.08 Unsuccessful Logon Attempts", "previous_clause": "3.1.8 Limit logons"}
    sheet = {"found": True, "source": "https://publisher.example/changes.csv", "summary": "Change analysis.", "entries": [], "table": _TABLE}
    llm.queue("decider", text(json.dumps({**VERSIONS, "public": True})))
    llm.queue("scanner", text(scan_text(logon)))
    llm.queue("finder", text(json.dumps(sheet)), text(json.dumps({"found": False, "entries": []})))
    llm.queue("matcher", text('{"matches": [{"entry": 0, "row": null}]}'))  # only the entry code could not place
    llm.queue("agent", tool_use(("check_versions", {})), tool_use(("scan_documents", {})), tool_use(("find_changelog", {})), text("Done."))
    llm.queue("reporter", text("- ok"), text("- ok"))
    first = await chat.compare(repo.v2, repo.v1)

    stored = first.analysis.changelog
    assert stored["imported"] == 3 and len(stored["entries"]) == 3  # all rows, not what a model could retype
    assert stored["matched"] == 1  # 03.01.08 matches the scan row by number; 03.05.08 has no row
    assert stored["unmatched"] == ["03.05.08 / 3.5.8: Password reuse prohibition removed"]
    assert len(llm.calls_for("matcher")) == 1  # one call, for the one entry code could not place

    # A later search that finds nothing must not wipe the changelog that was found.
    llm.queue("agent", tool_use(("find_changelog", {})), text("Searched again."))
    again = await chat.compare(repo.v2, repo.v1, notes="Search again")
    assert "the changelog found earlier is kept" in _tool_results(llm, len(llm.calls_for("agent")) - 1)
    assert again.analysis.changelog["imported"] == 3


async def test_the_trace_names_the_clauses_that_were_verified(agent, llm, changes):
    repo = VersionsRepo()
    await _first_run(agent, llm, repo, INVOICES, verifiers={"Invoice retention": INVOICES_PROOF})
    assert "Verification done (1 verified: Invoice retention)." in _tool_results(llm, 3)
