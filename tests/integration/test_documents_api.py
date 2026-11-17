"""The documents HTTP API against a real Postgres: upload, read, rename, duplicate, delete."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from docsmind.server.app import create_app


@pytest.fixture
def client(db_config):
    with TestClient(create_app(db_config)) as client:
        yield client


def _upload(client, pdf, collection="policies"):
    with open(pdf, "rb") as handle:
        return client.post(
            "/api/documents",
            files={"file": (pdf.name, handle, "application/pdf")},
            data={"collection": collection},
        )


def test_upload_read_rename_delete(client, make_pdf):
    pdf = make_pdf("Passwords must be rotated every ninety days.")

    created = _upload(client, pdf)
    assert created.status_code == 200
    version_id = created.json()["id"]

    assert version_id in [d["id"] for d in client.get("/api/documents").json()]
    assert "rotated" in client.get(f"/api/documents/{version_id}/fulltext").json()["full_text"]
    assert client.get(f"/api/documents/{version_id}/chunks").json()

    renamed = client.patch(f"/api/documents/{version_id}", json={"collection": "security"})
    assert renamed.json()["collection"] == "security"

    assert client.delete(f"/api/documents/{version_id}").status_code in (200, 204)
    assert client.get(f"/api/documents/{version_id}").status_code == 404


def test_duplicate_upload_is_a_conflict_and_other_types_are_refused(client, make_pdf, tmp_path):
    pdf = make_pdf("Same file.")
    assert _upload(client, pdf).status_code == 200
    assert _upload(client, pdf).status_code == 409

    note = tmp_path / "notes.txt"
    note.write_text("not a pdf")
    assert _upload(client, note).status_code == 400
