"""PATCH /api/documents/{id}: rename the title, collection and version label."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docsmind.server.routes import documents as document_routes


class RenameRepo:
    def __init__(self) -> None:
        self.version_id = uuid4()
        self.detail = {
            "document_id": uuid4(),
            "id": self.version_id,
            "title": "policy",
            "collection": "hr",
            "label": "2025-01-01",
            "created_at": "2026-11-12T10:00:00+00:00",
            "char_count": 10,
            "word_count": 2,
            "token_count_estimate": 3,
            "blob_uri": None,
        }

    async def update_version_metadata(self, version_id: UUID, **values: str) -> bool:
        if version_id != self.version_id:
            return False
        self.detail.update(values)
        return True

    async def get_version(self, version_id: UUID) -> dict | None:
        return dict(self.detail) if version_id == self.version_id else None

    async def count_chunks(self, version_id: UUID) -> int:
        return 0

    async def get_full_text(self, version_id: UUID) -> str:
        return "some text"


@pytest.fixture
def client(monkeypatch):
    repo = RenameRepo()
    monkeypatch.setattr(document_routes, "_repo", lambda _request: repo)
    app = FastAPI()
    app.include_router(document_routes.router, prefix="/api")
    app.state.config = None
    return TestClient(app), repo


def test_title_collection_and_version_can_be_renamed(client):
    http, repo = client
    body = http.patch(
        f"/api/documents/{repo.version_id}",
        json={"title": "  Security Policy ", "collection": "compliance", "label": "v2"},
    ).json()
    assert (body["title"], body["collection"], body["label"]) == ("Security Policy", "compliance", "v2")


def test_a_field_left_out_stays_as_it_is(client):
    http, repo = client
    body = http.patch(f"/api/documents/{repo.version_id}", json={"label": "rev-C"}).json()
    assert (body["title"], body["collection"], body["label"]) == ("policy", "hr", "rev-C")


def test_empty_or_missing_values_are_rejected(client):
    http, repo = client
    assert http.patch(f"/api/documents/{repo.version_id}", json={"title": "   "}).status_code == 422
    assert http.patch(f"/api/documents/{repo.version_id}", json={}).status_code == 422


def test_unknown_version_is_404(client):
    http, _ = client
    assert http.patch(f"/api/documents/{uuid4()}", json={"title": "x"}).status_code == 404
