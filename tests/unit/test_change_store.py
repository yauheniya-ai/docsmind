"""The change store contract: archive instead of delete, revisions and a stale report, identity by hash."""

from __future__ import annotations

from uuid import uuid4

import pytest

from docsmind.adapters.storage.memory import InMemoryChangeStore
from docsmind.domain.changes import Binding, ChangeAnalysis, ChangeRow, ChangeRun, Nature


@pytest.fixture
def store() -> InMemoryChangeStore:
    return InMemoryChangeStore()


def _analysis(**kw) -> ChangeAnalysis:
    return ChangeAnalysis(
        current_version_id=uuid4(), previous_version_id=uuid4(), current_hash="ha", previous_hash="hb", **kw
    )


def _row(analysis: ChangeAnalysis, clause: str = "4.1 Backups", **kw) -> ChangeRow:
    return ChangeRow(
        analysis_id=analysis.id, current_clause=clause, nature=Nature.NEW,
        compliance_requirement="Encrypt", binding=Binding.NORMATIVE, **kw,
    )  # fmt: skip


async def test_archiving_hides_a_row_keeps_it_and_never_reuses_its_handle(store):
    analysis = _analysis()
    await store.create_analysis(analysis)
    first = await store.add_row(_row(analysis))
    archived = await store.archive_row(first.id, "Deleted by user")
    assert archived.status == "archived" and archived.archive_reason == "Deleted by user" and archived.archived_at

    assert await store.list_rows(analysis.id) == []  # the table shows active rows only
    assert [r.id for r in await store.list_rows(analysis.id, status="archived")] == [first.id]
    assert [r.id for r in await store.list_rows(analysis.id, status=None)] == [first.id]

    second = await store.add_row(_row(analysis, "4.2 Logs"))
    assert second.ordinal == first.ordinal + 1  # R-handles are never reused

    restored = await store.restore_row(first.id)
    assert restored.status == "active" and restored.archive_reason is None and restored.archived_at is None
    assert {r.id for r in await store.list_rows(analysis.id)} == {first.id, second.id}


async def test_changing_active_rows_makes_a_written_report_stale(store):
    analysis = _analysis()
    await store.create_analysis(analysis)
    row = await store.add_row(_row(analysis))
    await store.update_analysis(analysis.id, status="done", report="# Report")
    fresh = await store.get_analysis(analysis.id)
    assert fresh.report_revision == fresh.table_revision and not fresh.report_stale

    await store.update_row(row.id, category="security")
    stale = await store.get_analysis(analysis.id)
    assert stale.table_revision == fresh.table_revision + 1 and stale.report_stale

    await store.update_analysis(analysis.id, report="# Report 2")  # a new report covers the table again
    again = await store.get_analysis(analysis.id)
    assert not again.report_stale and again.report_revision == again.table_revision


async def test_candidates_do_not_change_the_table_revision(store):
    analysis = _analysis()
    await store.create_analysis(analysis)
    await store.add_row(_row(analysis, status="candidate"))
    assert (await store.get_analysis(analysis.id)).table_revision == 0
    assert await store.list_rows(analysis.id) == []


async def test_an_analysis_is_found_by_the_hash_pair_and_relinked_after_re_ingest(store):
    analysis = _analysis()
    await store.create_analysis(analysis)
    assert (await store.find_analysis("ha", "hb")).id == analysis.id
    assert await store.find_analysis("hb", "ha") is None  # ordered pair

    # the document with hash "ha" was deleted: the analysis stays, its link is gone
    await store.update_analysis(analysis.id, current_version_id=None)
    assert (await store.find_analysis("ha", "hb")).current_version_id is None

    new_version = uuid4()  # the same file ingested again
    assert await store.relink_versions("ha", new_version) == 1
    assert (await store.get_analysis(analysis.id)).current_version_id == new_version
    assert await store.relink_versions("ha", uuid4()) == 0  # already linked: untouched


async def test_runs_are_listed_newest_first_and_removed_with_the_analysis(store):
    analysis = _analysis()
    await store.create_analysis(analysis)
    older = ChangeRun(analysis_id=analysis.id, request="compare")
    newer = ChangeRun(analysis_id=analysis.id, request="verify R3", kind="verify")
    await store.add_run(older)
    await store.add_run(newer)
    await store.update_run(newer.id, outcome="done", steps=4)
    runs = await store.list_runs(analysis.id)
    assert [r.request for r in runs] == ["verify R3", "compare"] and runs[0].steps == 4
    assert await store.delete_analysis(analysis.id)
    assert await store.list_runs(analysis.id) == []
