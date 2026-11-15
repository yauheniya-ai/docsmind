"""The Change Analyzer: preparation, row parsing and checks, proof checks, the whole pipeline with a
scripted model, the Excel export and the table tools."""

from __future__ import annotations

import io
import json
from uuid import uuid4

import pytest
from fakes import FakeRepo, text, tool_use

from docsmind.agent.catalog import Catalog
from docsmind.agent.sources import SourceRegistry
from docsmind.agent.subagents.change_analysis.analyzer import ChangeAnalyzer
from docsmind.agent.subagents.change_analysis.export import rows_to_xlsx
from docsmind.agent.subagents.change_analysis.prep import (
    normalize,
    part_has_changes,
    plan_parts,
    prepare,
    prepare_changelog,
    render_diff,
)
from docsmind.agent.subagents.change_analysis.rows import (
    extract_json,
    parse_row_line,
    row_from_object,
    row_key,
)
from docsmind.agent.subagents.change_analysis.verify import contains_verbatim, squash
from docsmind.agent.subagents.specs import SUBAGENTS
from docsmind.agent.tools import ToolContext, build_registry
from docsmind.agent.tools.change import parse_table
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import ToolCall, Usage
from docsmind.domain.changes import Binding, ChangeRow, Nature
from docsmind.pricing import cost_breakdown, price_of

# -- preparation -----------------------------------------------------------------------------


def test_normalize_drops_page_numbers_and_repeated_headers():
    pages = [
        f"ACME Corp internal use only\nRequirement {word} applies to every system.\n{n}"
        for n, word in enumerate(["alpha", "beta", "gamma", "delta"], start=1)
    ]
    lines = normalize("\n".join(pages), pages=4)
    assert lines == [
        "Requirement alpha applies to every system.",
        "Requirement beta applies to every system.",
        "Requirement gamma applies to every system.",
        "Requirement delta applies to every system.",
    ]


def test_normalize_keeps_a_bare_number_that_is_not_a_page_number():
    assert normalize("The retention limit is shown below.\n12\nDays.", pages=3)[-1] == "12 Days."


def test_normalize_joins_wrapped_lines_and_splits_sentences():
    text_ = "Invoices are kept\nfor ten years. Logs are kept for\nninety days."
    assert normalize(text_, pages=1) == ["Invoices are kept for ten years.", "Logs are kept for ninety days."]


def test_prepare_modes():
    base = ". ".join(f"Sentence about topic {chr(97 + i)}{chr(97 + j)} stays as written" for i in range(5) for j in range(5)) + "."
    assert prepare(base, base, threshold=0.25).identical
    one_change = base.replace("topic aa stays as written", "topic aa now reads differently")
    small = prepare(base, one_change, threshold=0.25)
    assert small.mode == "diff" and 0 < small.ratio < 0.25
    other = ". ".join(f"Entirely different statement number {chr(97 + i)}{chr(97 + j)} here" for i in range(5) for j in range(5)) + "."
    assert prepare(base, other, threshold=0.25).mode == "full"


def test_render_diff_marks_current_and_previous_lines():
    prep = prepare(
        "Intro line is unchanged here.\nInvoices are kept for ten years.\nClosing line is unchanged here.",
        "Intro line is unchanged here.\nInvoices are kept for seven years.\nClosing line is unchanged here.",
        threshold=0.9,
    )
    diff = render_diff(prep)
    assert "A> Invoices are kept for ten years." in diff
    assert "B> Invoices are kept for seven years." in diff
    assert "  Intro line is unchanged here." in diff


def _tag(i: int) -> str:
    letters = ""
    for _ in range(4):
        letters = chr(97 + i % 26) + letters
        i //= 26
    return letters


def test_plan_parts_splits_a_long_document_and_skips_unchanged_parts():
    sentences = [f"The operator shall document item {_tag(i)} before release of the product." for i in range(1_500)]
    previous = list(sentences)
    current = list(sentences)
    current[10] = "The operator shall now document item changed early before release."
    current[1_400] = "The operator shall now document item changed late before release."
    prep = prepare("\n".join(current), "\n".join(previous), threshold=0.25)
    assert prep.mode == "diff"

    parts = plan_parts(prep, split_tokens=5_000, max_input_tokens=6_000)
    assert len(parts) > 2
    assert parts[0].lo == 0 and parts[-1].hi == len(prep.a)
    assert all(parts[i].hi == parts[i + 1].lo for i in range(len(parts) - 1))  # no gaps, no overlap
    with_changes = [p.index for p in parts if part_has_changes(prep, p)]
    holding_late_change = next(p.index for p in parts if p.lo <= 1_400 < p.hi)
    assert with_changes == [0, holding_late_change]  # the other parts need no model call
    assert len(with_changes) < len(parts)

    assert len(plan_parts(prep, split_tokens=90_000, max_input_tokens=180_000)) == 1


def test_prepare_changelog_never_diffs():
    prep = prepare_changelog("Clause one text here.\nClause two text here.", "Version 2 added clause two.")
    assert prep.mode == "full" and not prep.identical
    assert len(prep.a) == 2 and len(prep.b) == 1


# -- rows ------------------------------------------------------------------------------------


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 1} hope it helps') == {"a": 1}
    assert extract_json("no json here") is None


def test_row_from_object_accepts_aliases_and_applies_the_rules():
    row = row_from_object(
        {
            "clause": "4.2 Retention",
            "type": "Added",
            "description": "New retention rule.",
            "requirement": "Keep invoices ten years",
            "binding": "mandatory",
        }
    )
    assert row is not None
    assert row.nature == Nature.NEW and row.binding == Binding.NORMATIVE
    assert row.actionable

    editorial = row_from_object(
        {"current_clause": "1.1", "nature": "editorial", "binding": "normative", "compliance_requirement": "Do it"}
    )
    assert editorial is not None
    assert editorial.binding == Binding.UNCHANGED and editorial.compliance_requirement == "No change"
    assert not editorial.actionable


def test_row_from_object_rejects_unusable_rows():
    assert row_from_object({"current_clause": "1", "nature": "sideways"}) is None
    assert row_from_object({"nature": "new"}) is None


def test_actionable_needs_a_normative_delta_on_a_substantive_change():
    base = {"current_clause": "1", "change_description": "x"}
    assert row_from_object({**base, "nature": "modified", "binding": "normative", "compliance_requirement": "Do X"}).actionable
    assert not row_from_object({**base, "nature": "modified", "binding": "recommended", "compliance_requirement": "Do X"}).actionable
    assert not row_from_object({**base, "nature": "modified", "binding": "normative", "compliance_requirement": "No change"}).actionable
    assert not row_from_object({**base, "nature": "relocated", "binding": "normative", "compliance_requirement": "Do X"}).actionable


def test_parse_row_line_kinds():
    row_json = '{"current_clause": "1", "nature": "new", "change_description": "x"}'
    assert parse_row_line(row_json)[0] == "row"
    assert parse_row_line(row_json + ",")[0] == "row"
    assert parse_row_line('{"done": true}')[0] == "done"
    assert parse_row_line("```jsonl")[0] == "skip"
    assert parse_row_line("")[0] == "skip"
    assert parse_row_line("Some commentary from the model.")[0] == "skip"
    assert parse_row_line('{"current_clause": "1", "nature": "ne')[0] == "broken"


def test_row_key_ignores_case_and_spacing():
    a = row_from_object({"current_clause": "Clause  A", "nature": "new", "change_description": "Adds X"})
    b = row_from_object({"current_clause": "clause a", "nature": "new", "change_description": "adds   x"})
    assert row_key(a) == row_key(b)


# -- proof checks -----------------------------------------------------------------------------


def test_contains_verbatim_ignores_case_quotes_and_spacing_and_handles_ellipses():
    haystack = squash("The operator\nshall keep \u201cinvoices\u201d for ten\u00a0years, and logs for 90 days.")
    assert contains_verbatim('The operator shall keep "invoices" for ten years', haystack)
    assert contains_verbatim("The operator shall keep ... and logs for 90 days", haystack)
    assert not contains_verbatim("The operator shall keep invoices for twelve years", haystack)
    assert not contains_verbatim("ten", haystack)  # too short to be evidence


# -- export ------------------------------------------------------------------------------------


def test_rows_to_xlsx_has_the_eight_columns_and_keeps_formulas_as_text():
    from openpyxl import load_workbook

    row = ChangeRow(
        current_clause="=HYPERLINK(\"http://evil\")",
        change_description="Adds a rule",
        nature=Nature.NEW,
        compliance_requirement="Do it",
        binding=Binding.NORMATIVE,
        verified=True,
    )
    sheet = load_workbook(io.BytesIO(rows_to_xlsx([row]))).active
    header = [c.value for c in sheet[1]]
    assert len(header) == 8 and header[0] == "Current clause (A)" and "Verified" not in header
    assert sheet["A2"].value == '=HYPERLINK("http://evil")' and sheet["A2"].data_type == "s"
    assert sheet["D2"].value == "new" and sheet["G2"].value == "normative"
    assert sheet["A1"].fill.fgColor.rgb.endswith("D04725") and sheet["A1"].font.color.rgb.endswith("FFFFFF")


def test_export_has_an_info_sheet_and_a_dated_filename():
    from datetime import datetime, timezone

    from openpyxl import load_workbook

    from docsmind.agent.subagents.change_analysis.export import export_filename, info_rows
    from docsmind.domain.changes import ChangeAnalysis

    analysis = ChangeAnalysis(
        current_version_id=uuid4(),
        previous_version_id=uuid4(),
        mode="diff",
        status="done",
        created_at=datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc),
        meta={
            "seconds": 123,
            "input_tokens": 1000,
            "output_tokens": 200,
            "model_calls": 7,
            "models": {"scan": "gpt-6-luna"},
            "cost": {
                "usd": 0.0012,
                "complete": True,
                "models": [
                    {
                        "model": "gpt-6-luna",
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "input_price": 0.1,
                        "output_price": 0.5,
                        "usd": 0.0012,
                    }
                ],
            },
        },
    )
    assert export_filename(analysis, "Café Rules: v2 / Final!") == "20261112-cafe-rules-v2-final-change-table.xlsx"
    assert export_filename(analysis, "日本語") == "20261112-document-change-table.xlsx"

    info = dict(info_rows(analysis, "Doc A", "Doc B", []))
    assert info["Document A (current)"] == "Doc A" and info["Date"] == "2026-11-12 09:30 UTC"
    assert info["How long"] == "2 min 3 s" and info["Tokens"] == "1,200" and info["Model: scan"] == "gpt-6-luna"
    assert info["Cost"] == "$0.0012"

    book = load_workbook(io.BytesIO(rows_to_xlsx([], list(info_rows(analysis, "=Doc A", "Doc B", [])))))
    assert book.sheetnames == ["Changes", "Info"]
    assert book["Info"]["B2"].value == "=Doc A" and book["Info"]["B2"].data_type == "s"
    assert book["Info"]["A1"].fill.fgColor.rgb.endswith("D04725")


def test_report_document_has_a_title_the_documents_and_converts_to_word():
    from docx import Document

    from docsmind.agent.subagents.change_analysis.report_export import (
        markdown_to_docx,
        report_markdown,
    )
    from docsmind.domain.changes import ChangeAnalysis

    analysis = ChangeAnalysis(
        current_version_id=uuid4(),
        previous_version_id=uuid4(),
        verdict_note="Both are the same standard; A is newer.",
        report="## Substantive changes\n- **Eligibility:** TPSPs must be compliant. (A p.4)\n  - sub point\n\n*Generated by AI.*",
    )
    markdown = report_markdown(analysis, "Doc 4.0.1", "Doc 4.0")
    assert markdown.startswith("# Change analysis\n\n**A, current:** Doc 4.0.1")
    assert markdown.index("Both are the same standard") < markdown.index("## Substantive changes")
    assert report_markdown(ChangeAnalysis(current_version_id=uuid4(), previous_version_id=uuid4()), "a", "b") is None

    import io as _io

    document = Document(_io.BytesIO(markdown_to_docx(markdown)))
    texts = [(p.style.name, p.text) for p in document.paragraphs]
    assert texts[0] == ("Title", "Change analysis")
    assert ("Heading 1", "Substantive changes") in texts
    assert ("List Bullet", "Eligibility: TPSPs must be compliant. (A p.4)") in texts
    assert ("List Bullet 2", "sub point") in texts
    assert any(p.runs and p.runs[0].bold for p in document.paragraphs if p.text.startswith("Eligibility"))


# -- the pipeline --------------------------------------------------------------------------------

PREVIOUS = [
    "Invoices are kept for seven years. Logs are kept for 90 days.",
    "Staff may use personal devices.",
]
CURRENT = [
    "Invoices are kept for ten years. Logs are kept for 90 days.",
    "Staff may use personal devices. Encryption at rest is mandatory for all backups.",
]

VERSIONS = {"verdict": "versions", "newer": "A", "public": False, "query": "", "explanation": "Two versions of one policy."}

INVOICES = {
    "current_clause": "Invoice retention",
    "previous_clause": "Invoice retention",
    "change_description": "Invoices are now kept for ten years instead of seven.",
    "nature": "modified",
    "category": "Retention",
    "compliance_requirement": "Keep invoices for ten years",
    "binding": "normative",
    "applicability_scope": "All invoices",
}
ENCRYPTION = {
    "current_clause": "Backup encryption",
    "change_description": "Backups must be encrypted at rest.",
    "nature": "new",
    "category": "Security",
    "compliance_requirement": "Encrypt backups at rest",
    "binding": "normative",
    "applicability_scope": "All backups",
}
LOGS = {
    "current_clause": "Log retention",
    "previous_clause": "Log retention",
    "change_description": "Logs are now kept for 30 days.",
    "nature": "modified",
    "compliance_requirement": "Keep logs for 30 days",
    "binding": "normative",
}
WORDING = {
    "current_clause": "Staff devices wording",
    "change_description": "Reworded.",
    "nature": "editorial",
    "compliance_requirement": "No change",
    "binding": "unchanged",
}


class VersionsRepo(FakeRepo):
    """Two versions of one policy: v1 is the previous one, v2 the current one."""

    def __init__(self, previous: list[str] = PREVIOUS, current: list[str] = CURRENT) -> None:
        super().__init__()
        self.pages = {}
        for version, pages in ((self.v1, previous), (self.v2, current)):
            for number, content in enumerate(pages, start=1):
                self.pages[(version, number)] = {
                    "chunk_id": uuid4(), "version_id": version, "page_start": number, "page_end": number, "content": content,
                }  # fmt: skip
        for row, label, pages in ((self.rows[0], "2024", previous), (self.rows[1], "2025", current)):
            row.update(title="Retention Policy", label=label, page_count=len(pages))


def scan_text(*rows: dict) -> str:
    return "\n".join([*(json.dumps(r) for r in rows), '{"done": true}'])


def verifier(answers: dict[str, dict | str]):
    """A scripted verifier: answers by the clause name found in the task."""

    def respond(messages):
        task = messages[0].content
        for key, answer in answers.items():
            if key in task:
                return text(answer if isinstance(answer, str) else json.dumps(answer))
        raise AssertionError(f"No scripted verdict for: {task[:300]}")

    return respond


INVOICES_PROOF = {
    "verified": True,
    "reason": "Both versions state the period.",
    "proof": [
        {"doc": "A", "page": 1, "text": "Invoices are kept for ten years."},
        {"doc": "B", "page": 1, "text": "Invoices are kept for seven years."},
    ],
}
ENCRYPTION_PROOF = {
    "verified": True,
    "reason": "Only the current version has it.",
    "proof": [{"doc": "A", "page": 9, "text": "Encryption at rest is mandatory for all backups."}],
    "absence_phrases": ["encryption at rest is mandatory"],
}


def agent_plan(llm, *, gaps: bool = False, final: str = "Done."):
    """What the scripted Change Analyzer agent does: the typical order of a first comparison."""
    llm.queue(
        "agent",
        tool_use(("check_versions", {})),
        tool_use(("find_changelog", {}), ("scan_documents", {})),
        tool_use(("verify_findings", {"which": "pending"})),
        *([tool_use(("verify_findings", {"changelog_gaps": True}))] if gaps else []),
        tool_use(("write_report", {})),
        text(final),
    )


def script(
    llm,
    *,
    scan: str,
    verifiers=None,
    verifier_calls: int | None = None,
    decision=VERSIONS,
    finder: dict | None = None,
    report="## Substantive changes\nSee table.",
    plan: bool = True,
):
    if plan:
        agent_plan(llm, gaps=bool(finder and finder.get("found")))
    llm.queue("decider", text(json.dumps(decision)))
    llm.queue("scanner", text(scan))
    llm.queue("finder", text(json.dumps(finder or {"found": False})))
    llm.queue("reporter", text(report))
    # One scripted answer per verifier call; a row whose check fails is relaunched, so it takes several.
    for _ in range(len(verifiers or []) if verifier_calls is None else verifier_calls):
        llm.queue("verifier", verifier(verifiers))


async def test_pipeline_builds_a_verified_table_and_drops_what_it_could_not_prove(agent, llm, changes):
    repo = VersionsRepo()
    fabricated = {
        "verified": True,
        "proof": [
            {"doc": "A", "page": 1, "text": "Logs are kept for 30 days."},  # not in the document
            {"doc": "B", "page": 1, "text": "Logs are kept for 90 days."},
        ],
    }
    script(
        llm,
        scan=scan_text(INVOICES, ENCRYPTION, LOGS, WORDING),
        verifiers={"Invoice retention": INVOICES_PROOF, "Backup encryption": ENCRYPTION_PROOF, "Log retention": fabricated},
        verifier_calls=2 + 4,  # the fabricated proof is tried again 3 times before the row is dropped
    )
    events = []
    result = await agent(repo).compare(repo.v2, repo.v1, on_event=events.append)

    assert result.analysis is not None and result.analysis.status == "done"
    assert result.analysis.current_version_id == repo.v2 and result.analysis.previous_version_id == repo.v1
    by_clause = {r.current_clause: r for r in result.rows}
    assert set(by_clause) == {"Invoice retention", "Backup encryption", "Staff devices wording"}

    invoices = by_clause["Invoice retention"]
    assert invoices.verified and {(p.doc, p.page) for p in invoices.proof} == {("current", 1), ("previous", 1)}
    encryption = by_clause["Backup encryption"]
    assert encryption.verified and encryption.proof[0].page == 2  # the model said page 9; the text is on page 2
    wording = by_clause["Staff devices wording"]
    assert not wording.verified and not wording.actionable  # only actionable rows are verified

    # The unproven row is gone: not in the table, not in the report, not stored anywhere.
    report = result.analysis.report
    assert "Log retention" not in report and "not_found" not in result.analysis.meta
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    assert headings == ["## Compliance impact", "## Changes", "## Methodology"]  # in this order, and nothing else
    assert str(result.analysis.id) in result.message

    assert len(llm.calls_for("verifier")) == 6
    assert result.analysis.meta["verify_retries"] == 3
    assert len(llm.calls_for("scanner")) == len(llm.calls_for("decider")) == 1
    assert not llm.calls_for("matcher")  # the changelog was not found
    kinds = [e.type for e in events]
    assert kinds[0] == "subagent_start" and kinds[-1] == "subagent_end"
    assert kinds.count("analysis_start") == 1 and kinds.count("change_row") == 4
    assert kinds.count("change_row_removed") == 1 and "analysis_done" in kinds

    stored = await changes.list_rows(result.analysis.id)
    assert [r.id for r in stored] == [r.id for r in result.rows]


async def test_pipeline_rejects_a_new_clause_whose_text_is_in_the_previous_version(agent, llm):
    repo = VersionsRepo()
    claim = {
        "verified": True,
        "proof": [{"doc": "A", "page": 1, "text": "Logs are kept for 90 days."}],
        "absence_phrases": ["logs are kept for 90 days"],
    }
    new_logs = {**LOGS, "nature": "new", "previous_clause": ""}
    script(llm, scan=scan_text(new_logs), verifiers={"Log retention": claim})
    result = await agent(repo).compare(repo.v2, repo.v1)
    assert result.rows == []  # a finished check that finds the text in B is a conclusion: no relaunch
    assert len(llm.calls_for("verifier")) == 1
    assert not result.analysis.report  # nothing in the table: nothing to report


async def test_a_check_that_goes_wrong_is_started_again(agent, llm):
    repo = VersionsRepo()
    script(llm, scan=scan_text(INVOICES), verifiers=None)
    llm.queue("verifier", text("Sorry, I cannot produce JSON today."), verifier({"Invoice retention": INVOICES_PROOF}))
    result = await agent(repo).compare(repo.v2, repo.v1)

    (row,) = result.rows
    assert row.verified
    calls = llm.calls_for("verifier")
    assert len(calls) == 2 and "earlier attempt" in calls[1]["messages"][0].content
    assert result.analysis.meta["verify_retries"] == 1


async def test_a_row_stays_unverified_only_after_every_relaunch_failed(agent, llm, changes):
    repo = VersionsRepo()
    no_phrases = {**ENCRYPTION_PROOF, "proof": [{"doc": "A", "page": 2, "text": "Encryption at rest is mandatory for all backups."}], "absence_phrases": []}
    script(llm, scan=scan_text(ENCRYPTION, WORDING), verifiers={"Backup encryption": no_phrases}, verifier_calls=4)
    result = await agent(repo).compare(repo.v2, repo.v1)
    # a check that could not run is no conclusion: the candidate stays pending, unverified, with the reason
    (row,) = await changes.list_rows(result.analysis.id, status="candidate")
    assert not row.verified and "could not be checked" in (row.verification_note or "")
    assert "tried 4 times" in row.verification_note
    assert result.analysis.meta["verify_errors"] == 1 and len(llm.calls_for("verifier")) == 4


async def test_identical_documents_need_no_check_model_call(agent, llm):
    repo = VersionsRepo(previous=CURRENT, current=CURRENT)
    llm.queue("agent", tool_use(("check_versions", {})), text("The documents are identical: there is nothing to compare."))
    result = await agent(repo).compare(repo.v2, repo.v1)
    assert result.analysis is None and "identical" in result.message
    assert not llm.calls_for("decider") and not llm.calls_for("scanner")  # only the agent itself ran
    tool_result = llm.calls_for("agent")[1]["messages"][-1].content
    assert "identical once headers, footers and page numbers are removed" in tool_result


async def test_versions_check_can_swap_the_documents(agent, llm):
    repo = VersionsRepo()
    script(llm, scan=scan_text(), decision={**VERSIONS, "newer": "B"})
    result = await agent(repo).compare(repo.v1, repo.v2)  # passed oldest first
    assert result.analysis.current_version_id == repo.v2
    assert result.analysis.previous_version_id == repo.v1


async def test_unrelated_documents_stop_before_any_scan(agent, llm, changes):
    repo = VersionsRepo()
    llm.queue("decider", text(json.dumps({"verdict": "unrelated", "explanation": "One is a menu."})))
    llm.queue("agent", tool_use(("check_versions", {})), text("These are not versions of one document."))
    result = await agent(repo).compare(repo.v2, repo.v1)
    assert result.analysis is None and "not versions of one document" in result.message
    assert "One is a menu." in llm.calls_for("agent")[1]["messages"][-1].content
    assert not llm.calls_for("scanner") and await changes.list_analyses() == []


async def test_scan_is_resumed_after_a_cut_off_without_repeating_rows(agent, llm):
    repo = VersionsRepo()
    first = json.dumps(INVOICES) + '\n{"current_clause": "Backup enc'  # cut off mid-row
    agent_plan(llm)
    llm.queue("decider", text(json.dumps(VERSIONS)))
    llm.queue("scanner", text(first), text(scan_text(INVOICES, ENCRYPTION)))  # the retry repeats row 1
    llm.queue("finder", text('{"found": false}'))
    llm.queue("reporter", text("## Substantive changes\nx"))
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Backup encryption": ENCRYPTION_PROOF}))
    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF, "Backup encryption": ENCRYPTION_PROOF}))

    result = await agent(repo).compare(repo.v2, repo.v1)
    assert sorted(r.current_clause for r in result.rows) == ["Backup encryption", "Invoice retention"]
    resumed = llm.calls_for("scanner")[1]["messages"][0].content
    assert "<rows_so_far>" in resumed and "Invoice retention" in resumed
    assert result.analysis.meta["scan"]["retries"] == 1


async def test_changelog_entry_without_a_row_is_checked_and_added(agent, llm):
    repo = VersionsRepo()
    finder = {
        "found": True,
        "source": "Revision history on page 2",
        "summary": "One compliance-relevant entry.",
        "entries": [{"clause": "2", "kind": "new", "text": "Backups must be encrypted at rest.", "compliance_relevant": True}],
    }
    gap = {
        "verified": True,
        "reason": "The current version requires it.",
        "row": {**ENCRYPTION, "current_clause": "Backup encryption"},
        "proof": [{"doc": "A", "page": 2, "text": "Encryption at rest is mandatory for all backups."}],
        "absence_phrases": ["encryption at rest is mandatory"],
    }
    script(llm, scan=scan_text(), finder=finder, verifiers={"Backups must be encrypted": gap})
    result = await agent(repo).compare(repo.v2, repo.v1)

    (row,) = result.rows
    assert row.origin == "changelog" and row.verified and row.current_clause == "Backup encryption"
    assert result.analysis.changelog["added"] == ["Backup encryption"]
    assert "Added to the table from the changelog, with proof (1): Backup encryption" in result.analysis.report  # in the methodology


async def test_verify_existing_rechecks_a_row_that_could_not_be_checked(agent, llm, changes):
    repo = VersionsRepo()
    chat_agent = agent(repo)
    script(llm, scan=scan_text(INVOICES, WORDING), verifiers={"Invoice retention": "I could not decide."}, verifier_calls=4)
    result = await chat_agent.compare(repo.v2, repo.v1)
    (row,) = await changes.list_rows(result.analysis.id, status="candidate")  # the check could not run: still pending
    assert not row.verified and "no usable result" in row.verification_note

    llm.queue("verifier", verifier({"Invoice retention": INVOICES_PROOF}))
    catalog = Catalog.from_rows(await repo.list_catalog([repo.v1, repo.v2]))
    analyzer = ChangeAnalyzer(chat_agent._make_env(lambda _e: None, SUBAGENTS["change_analyzer"].tools), catalog)
    message = await analyzer.verify_existing(result.analysis.id, [str(row.id)])

    stored = await changes.get_row(row.id)
    assert stored.verified and stored.verification_note is None and stored.status == "active"  # proven: it joins the table
    assert "1 verified" in message
    assert (await changes.get_analysis(result.analysis.id)).status == "done"  # re-checking changes nothing else


async def test_a_person_can_verify_any_row_and_the_proof_gets_its_pages(agent, llm, changes):
    repo = VersionsRepo()
    chat_agent = agent(repo)
    script(llm, scan=scan_text(WORDING))
    result = await chat_agent.compare(repo.v2, repo.v1)
    (row,) = result.rows
    assert not row.verified and not row.actionable  # the analyzer checks only compliance-relevant rows

    proof = {
        "verified": True,
        "reason": "Both versions word it so.",
        "proof": [  # no pages given: they are looked up in the stored text
            {"doc": "A", "text": "Staff may use personal devices."},
            {"doc": "B", "text": "Staff may use personal devices."},
        ],
    }
    llm.queue("verifier", verifier({"Staff devices wording": proof}))
    (checked,) = await chat_agent.verify_rows(result.analysis.id, [row.id])

    assert checked.verified and {(p.doc, p.page) for p in checked.proof} == {("current", 2), ("previous", 2)}
    assert (await changes.get_row(row.id)).verified


# -- page references as clickable citations ------------------------------------------------------------


def test_cost_comes_from_a_table_of_list_prices():
    cost = cost_breakdown(
        {
            "gpt-6-luna": Usage(input_tokens=833_834, output_tokens=17_137),
            "gpt-6.1-sol": Usage(input_tokens=1_000_000, output_tokens=100_000),
        }
    )
    luna, sol = cost["models"]
    assert luna["usd"] == pytest.approx(833_834 * 0.1 / 1e6 + 17_137 * 0.5 / 1e6)
    assert sol["usd"] == pytest.approx(2.0 + 1.0)  # $2 per 1M input, $10 per 1M output
    assert cost["complete"] and cost["usd"] == pytest.approx(luna["usd"] + sol["usd"])
    assert price_of("gpt-6.1-terra-2026") == (1.0, 5.0)  # longest prefix

    partial = cost_breakdown({"gpt-6-luna": Usage(input_tokens=10), "mystery": Usage(input_tokens=10)})
    assert not partial["complete"] and partial["models"][1]["usd"] is None


def test_cost_uses_cache_prices_and_shows_what_caching_saved():
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cached_input_tokens=600_000, cache_write_tokens=100_000)
    assert usage.uncached_input_tokens == 300_000
    cost = cost_breakdown({"claude-sonnet-5-5": usage})  # $2 in, 0.1x read, 1.25x write, $10 out
    assert cost["usd"] == pytest.approx(0.3 * 2.0 + 0.6 * 0.2 + 0.1 * 2.5 + 0.1 * 10.0)
    assert cost["usd_without_cache"] == pytest.approx(1.0 * 2.0 + 0.1 * 10.0)
    assert cost["cache_hit_rate"] == pytest.approx(0.6) and not cost["upper_bound"]
    (entry,) = cost["models"]
    assert entry["uncached_input_tokens"] == 300_000 and entry["cached_read_price"] == pytest.approx(0.2)


def test_a_model_without_a_cached_price_is_an_upper_bound_until_one_is_configured():
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=500_000)
    bound = cost_breakdown({"gpt-6.1-terra": usage})
    assert bound["upper_bound"] and bound["usd"] == pytest.approx(1.0)  # cached read priced as input
    exact = cost_breakdown({"gpt-6.1-terra": usage}, {"gpt-6.1": {"cached_read": 0.1}})
    assert not exact["upper_bound"] and exact["usd"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.1)


def test_cost_by_tool_splits_the_run():
    from docsmind.pricing import cost_by_tool

    by_tool = {
        ("scan_documents", "gpt-6.1-terra"): Usage(input_tokens=1_000_000),
        ("verify_findings", "gpt-6.1-terra"): Usage(input_tokens=2_000_000),
    }
    tools = cost_by_tool(by_tool)
    assert [t["tool"] for t in tools] == ["verify_findings", "scan_documents"]
    assert tools[0]["usd"] == pytest.approx(2.0)


async def test_the_report_states_what_the_run_cost(agent, llm):
    repo = VersionsRepo()
    script(llm, scan=scan_text(INVOICES), verifiers={"Invoice retention": INVOICES_PROOF})
    result = await agent(repo).compare(repo.v2, repo.v1)
    report = result.analysis.report
    assert "- Cost: at least $0.0000 at list prices" in report  # the scripted model has no price
    assert "Cache hit rate" in report
    assert "  - fake-model:" in report and "not priced" in report
    cost = result.analysis.meta["cost"]
    assert not cost["complete"] and cost["models"][0]["model"] == "fake-model"


async def test_the_minds_delegate_returns_a_short_status_without_sources(agent, llm):
    repo = VersionsRepo()
    chat_agent = agent(repo)
    script(
        llm,
        scan=scan_text(ENCRYPTION),
        verifiers={"Backup encryption": ENCRYPTION_PROOF},
        report="## Substantive changes\nBackups must now be encrypted (A p.2, B p.1).",
    )
    catalog = Catalog.from_rows(await repo.list_catalog([repo.v1, repo.v2]))
    registry = SourceRegistry()
    delegate = chat_agent._make_delegate(catalog, registry, Usage(), lambda _e: None)

    out = await delegate("change_analyzer", "Compare these versions", ["D2", "D1"], {})
    assert "Change analysis" in out and "1 changes, 1 compliance-relevant, 1 verified" in out
    assert "Backups must now be encrypted" not in out and "## Substantive changes" not in out
    assert "[S1]" not in out and "(A p.2" not in out
    assert len(out.splitlines()) <= 8
    assert registry.get("S1") is None


# -- tables and the Excel export ---------------------------------------------------------------------


def test_parse_table_reads_csv_and_xlsx():
    from openpyxl import Workbook

    csv_bytes = b"version,change\n2.0,Added encryption\n1.0,Initial\n"
    sheets = parse_table(csv_bytes, "https://example.com/log.csv", "text/csv", "utf-8")
    assert sheets["data"][0] == ["version", "change"] and sheets["data"][1] == ["2.0", "Added encryption"]

    workbook = Workbook()
    workbook.active.title = "History"
    workbook.active.append(["Clause", "Change"])
    workbook.active.append(["4.2", "Retention extended"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    sheets = parse_table(buffer.getvalue(), "https://example.com/log.xlsx", "", "")
    assert sheets == {"History": [["Clause", "Change"], ["4.2", "Retention extended"]]}


def _ctx(config, changes=None, cache=None):
    return ToolContext(
        repo=FakeRepo(),
        catalog=Catalog.from_rows(FakeRepo().rows),
        sources=SourceRegistry(),
        config=config,
        emit=lambda e: None,
        changes=changes,
        cache=cache if cache is not None else {},
    )


async def _call(ctx, name, args):
    return await build_registry().execute(ToolCall(id="1", name=name, arguments=args), ctx)


async def test_load_table_pages_and_filters_rows(config):
    url = "https://example.com/changelog.csv"
    rows = [["Clause", "Change"], *[[str(i), f"Change number {i}"] for i in range(1, 61)]]
    ctx = _ctx(config, cache={f"table:{url}": {"data": rows}})

    first = await _call(ctx, "load_table", {"url": url})
    assert not first.is_error and "header: Clause | Change" in first.content and "offset=40" in first.content
    found = await _call(ctx, "load_table", {"url": url, "contains": "number 57"})
    assert "Change number 57" in found.content and "Change number 1\n" not in found.content
    missing = await _call(ctx, "load_table", {"url": url, "sheet": "Nope"})
    assert missing.is_error and "Sheets: data" in missing.content


async def test_create_file_exports_the_change_table_and_filters(config, changes, tmp_path):
    from openpyxl import load_workbook

    from docsmind.domain.changes import ChangeAnalysis

    config = DocsmindConfig(_env_file=None, blob_root=str(tmp_path))
    analysis = ChangeAnalysis(current_version_id=uuid4(), previous_version_id=uuid4())
    await changes.create_analysis(analysis)
    actionable = ChangeRow(
        analysis_id=analysis.id, current_clause="Backup encryption", nature=Nature.NEW,
        compliance_requirement="Encrypt", binding=Binding.NORMATIVE,
    )  # fmt: skip
    wording = ChangeRow(analysis_id=analysis.id, current_clause="Wording", nature=Nature.EDITORIAL).checked()
    await changes.add_row(actionable)
    await changes.add_row(wording)
    ctx = _ctx(config, changes=changes)

    out = await _call(ctx, "create_file", {"filename": "table", "format": "xlsx", "source": {"only": "actionable"}})
    assert not out.is_error and "1 rows" in out.content
    name = out.content.split("/blobs/exports/")[1].rstrip(")")
    sheet = load_workbook(tmp_path / "exports" / name).active
    assert [c.value for c in sheet["A"]][1:] == ["Backup encryption"]

    everything = await _call(ctx, "create_file", {"filename": "table", "format": "xlsx", "source": {"change_table": str(analysis.id)}})
    assert "2 rows" in everything.content
    nothing = await _call(ctx, "create_file", {"filename": "table", "format": "xlsx", "source": {"natures": ["removed"]}})
    assert nothing.is_error and "nothing was exported" in nothing.content


async def test_create_file_without_an_analysis_is_an_error_the_model_can_read(config, changes):
    out = await _call(_ctx(config, changes=changes), "create_file", {"filename": "t", "format": "csv", "source": {}})
    assert out.is_error and "no change analysis yet" in out.content.lower()
