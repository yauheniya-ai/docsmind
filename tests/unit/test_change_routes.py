"""The change-table routes: reading an analysis, editing rows, the Excel export."""

from __future__ import annotations

import asyncio
import io
from uuid import uuid4

import pytest
from fakes import FakeRepo, ScriptedLLM
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docsmind.adapters.storage.memory import (
    InMemoryChangeStore,
    InMemoryConversationStore,
    InMemoryMemoryStore,
)
from docsmind.agent.agent import DocsmindAgent
from docsmind.domain.changes import Binding, ChangeAnalysis, ChangeRow, Nature, Proof
from docsmind.server.routes import changes as change_routes


@pytest.fixture
def seeded(config):
    repo = FakeRepo()
    store = InMemoryChangeStore()
    llm = ScriptedLLM()
    agent = DocsmindAgent(
        config,
        llm=llm,
        fast_llm=llm,
        repository=repo,
        conversations=InMemoryConversationStore(),
        memory=InMemoryMemoryStore(),
        changes=store,
    )
    analysis = ChangeAnalysis(current_version_id=repo.v2, previous_version_id=repo.v1, status="done", mode="diff")
    verified = ChangeRow(
        analysis_id=analysis.id,
        current_clause="Backup encryption",
        nature=Nature.NEW,
        compliance_requirement="Encrypt backups",
        binding=Binding.NORMATIVE,
        verified=True,
        proof=[Proof(doc="current", text="Encryption at rest is mandatory.", page=1)],
    )
    wording = ChangeRow(analysis_id=analysis.id, current_clause="Wording", nature=Nature.EDITORIAL).checked()

    async def seed():
        await store.create_analysis(analysis)
        return [await store.add_row(verified), await store.add_row(wording)]

    rows = asyncio.run(seed())
    app = FastAPI()
    app.include_router(change_routes.router, prefix="/api")
    app.state.agent = agent
    return TestClient(app), analysis, rows


def test_get_returns_the_analysis_rows_and_documents(seeded):
    client, analysis, _ = seeded
    body = client.get(f"/api/changes/{analysis.id}").json()
    assert body["analysis"]["status"] == "done"
    assert body["current"]["title"] == "Security Policy" and body["previous"]["title"] == "Retention Policy"
    assert [r["current_clause"] for r in body["rows"]] == ["Backup encryption", "Wording"]
    assert body["rows"][0]["proof"][0]["page"] == 1


def test_unknown_analysis_and_row_are_404(seeded):
    client, analysis, _ = seeded
    assert client.get(f"/api/changes/{uuid4()}").status_code == 404
    assert client.patch(f"/api/changes/{analysis.id}/rows/{uuid4()}", json={"approved": True}).status_code == 404


def test_approving_keeps_the_proof_but_editing_the_claim_drops_it(seeded):
    client, analysis, rows = seeded
    url = f"/api/changes/{analysis.id}/rows/{rows[0].id}"

    approved = client.patch(url, json={"approved": True}).json()
    assert approved["approved"] and approved["verified"] and approved["proof"]

    reworded = client.patch(url, json={"change_description": "Clearer wording."}).json()
    assert reworded["verified"]  # the description is not the claim

    edited = client.patch(url, json={"binding": "recommended"}).json()
    assert not edited["verified"] and edited["proof"] == []
    assert "Edited after verification" in edited["verification_note"]


def test_edits_obey_the_row_rules(seeded):
    client, analysis, rows = seeded
    edited = client.patch(f"/api/changes/{analysis.id}/rows/{rows[0].id}", json={"nature": "editorial"}).json()
    assert edited["binding"] == "unchanged" and edited["compliance_requirement"] == "No change"


def test_add_and_delete_rows(seeded):
    client, analysis, _ = seeded
    assert client.post(f"/api/changes/{analysis.id}/rows", json={"change_description": "x"}).status_code == 422
    created = client.post(
        f"/api/changes/{analysis.id}/rows",
        json={"current_clause": "Audit logs", "nature": "new", "compliance_requirement": "Keep audit logs", "binding": "normative"},
    )
    assert created.status_code == 201 and created.json()["origin"] == "user" and not created.json()["verified"]

    assert client.delete(f"/api/changes/{analysis.id}/rows/{created.json()['id']}").status_code == 200
    assert len(client.get(f"/api/changes/{analysis.id}").json()["rows"]) == 2


def test_export_follows_the_filter(seeded):
    from openpyxl import load_workbook

    client, analysis, _ = seeded
    response = client.get(f"/api/changes/{analysis.id}/export.xlsx", params={"only": "actionable"})
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert [c.value for c in sheet["A"]][1:] == ["Backup encryption"]

    sheet = load_workbook(io.BytesIO(client.get(f"/api/changes/{analysis.id}/export.xlsx").content)).active
    assert sheet.max_row == 3


def test_deleting_an_analysis_removes_its_rows(seeded):
    client, analysis, _ = seeded
    assert client.delete(f"/api/changes/{analysis.id}").status_code == 200
    assert client.get(f"/api/changes/{analysis.id}").status_code == 404
    assert client.delete(f"/api/changes/{analysis.id}").status_code == 404


def test_deleting_a_row_archives_it_and_it_can_be_restored(seeded):
    client, analysis, rows = seeded
    row_id = rows[0].id
    assert client.delete(f"/api/changes/{analysis.id}/rows/{row_id}").json()["archived"] is True

    body = client.get(f"/api/changes/{analysis.id}").json()
    assert [r["current_clause"] for r in body["rows"]] == ["Wording"] and body["archived_count"] == 1
    archive = client.get(f"/api/changes/{analysis.id}/archive").json()["rows"]
    assert [r["id"] for r in archive] == [str(row_id)] and archive[0]["archive_reason"] == "Deleted by user"
    assert body["analysis"]["report_stale"] is False  # no report was written yet

    assert client.post(f"/api/changes/{analysis.id}/rows/{rows[1].id}/restore").status_code == 422  # not archived
    assert client.post(f"/api/changes/{analysis.id}/rows/{row_id}/restore").json()["status"] == "active"
    assert client.get(f"/api/changes/{analysis.id}").json()["archived_count"] == 0


def test_an_analysis_of_a_deleted_document_is_still_shown_from_its_snapshot(seeded):
    client, analysis, _ = seeded
    store = client.app.state.agent.changes
    asyncio.run(
        store.update_analysis(
            analysis.id, previous_version_id=None, previous_doc={"title": "Retention Policy", "label": "2024"}
        )
    )
    body = client.get(f"/api/changes/{analysis.id}").json()
    assert body["current"]["deleted"] is False and body["previous"]["deleted"] is True
    assert body["previous"]["title"] == "Retention Policy" and body["previous"]["version_id"] is None
    assert len(body["rows"]) == 2  # the table is untouched


def test_run_history_is_listed(seeded):
    from docsmind.domain.changes import ChangeRun

    client, analysis, _ = seeded
    asyncio.run(client.app.state.agent.changes.add_run(ChangeRun(analysis_id=analysis.id, request="compare")))
    runs = client.get(f"/api/changes/{analysis.id}/runs").json()["runs"]
    assert [r["request"] for r in runs] == ["compare"]


def test_an_edited_table_gets_its_report_rewritten_on_refresh_and_before_a_download(seeded):
    from fakes import text

    client, analysis, rows = seeded
    agent = client.app.state.agent
    asyncio.run(agent.changes.update_analysis(analysis.id, status="done", report="## Substantive changes\nOld."))

    client.patch(f"/api/changes/{analysis.id}/rows/{rows[0].id}", json={"category": "security"})
    assert client.get(f"/api/changes/{analysis.id}").json()["analysis"]["report_stale"] is True

    agent.llm.queue("reporter", text("## Substantive changes\nFresh after the edit."))
    body = client.post(f"/api/changes/{analysis.id}/report/refresh").json()
    assert body["analysis"]["report_stale"] is False and "Fresh after the edit." in body["analysis"]["report"]
    assert "Fresh after the edit." in body["report_markdown"]

    # Editing again, then downloading: the download rewrites the report first.
    client.patch(f"/api/changes/{analysis.id}/rows/{rows[0].id}", json={"category": "privacy"})
    agent.llm.queue("reporter", text("## Substantive changes\nFresh for the download."))
    download = client.get(f"/api/changes/{analysis.id}/report.md")
    assert download.status_code == 200 and "Fresh for the download." in download.text

    # A current report is not rewritten again (no scripted response is left: it would fail).
    assert client.post(f"/api/changes/{analysis.id}/report/refresh").status_code == 200
