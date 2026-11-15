"""Document endpoints backing the `frontend/` UI: list/ingest documents, and
read back what `docsmind ingest` saved (full text, outline, chunks, images,
extracted tables) — the HTTP equivalent of `cli/inspect.py`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from docsmind.domain.errors import DocumentAlreadyExistsError

router = APIRouter(tags=["documents"])


def _blob_url(request: Request, uri: str | None) -> str | None:
    """Turn a `file://...` blob URI into a URL the browser can load, by
    resolving it relative to `blob_root` (mounted at `/blobs` in `app.py`).
    Returns None if the URI can't be resolved under `blob_root` (e.g. a
    different blob backend is configured).
    """
    if not uri:
        return None
    if not uri.startswith("file://"):
        return None
    config = request.app.state.config
    blob_root = Path(config.blob_root).resolve()
    path = Path(uri[len("file://") :])
    try:
        relative = path.resolve().relative_to(blob_root)
    except ValueError:
        return None
    return f"/blobs/{relative.as_posix()}"


def _count_tokens(text: str) -> int | None:
    """Real token count via tiktoken (`cl100k_base`). Returns None if tiktoken
    or its encoding file is unavailable (e.g. offline on first use)."""
    if not text:
        return 0
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 - stats are best-effort; don't fail the page
        return None


def _repo(request: Request):
    from docsmind.adapters.storage.postgres.repository import PostgresRepository

    return PostgresRepository(request.app.state.config)


@router.get("/documents")
async def list_documents(request: Request) -> list[dict]:
    repo = _repo(request)
    rows = await repo.list_versions()
    out = []
    for row in rows:
        chunk_count = await repo.count_chunks(row["id"])
        images = await repo.get_images(row["id"])
        tables = await repo.get_tables(row["id"])
        out.append(
            {
                **row,
                "chunk_count": chunk_count,
                "image_count": len(images),
                "table_count": len(tables),
            }
        )
    # Newest first — nicer for a UI than the CLI's chronological order.
    out.reverse()
    return out


@router.post("/documents")
async def create_document(
    request: Request,
    file: UploadFile,
    collection: str = Form(...),
    version: str | None = Form(None),
) -> dict:
    from docsmind.client import AsyncDocsmind

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    suffix = Path(file.filename).suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported right now (DOCX/text parsers are planned).",
        )

    data = await file.read()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / file.filename
        tmp_path.write_bytes(data)

        dm = AsyncDocsmind(request.app.state.config)
        try:
            result = await dm.aingest(tmp_path, collection=collection, version=version or None)
        except DocumentAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "already_exists",
                    "message": str(exc),
                    "existing_version": exc.existing_version,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = _repo(request)
    detail = await repo.get_version(result.id)
    if detail is None:
        raise HTTPException(status_code=500, detail="Ingested, but the version could not be read back.")
    detail["file_url"] = _blob_url(request, detail.pop("blob_uri", None))
    return detail


@router.get("/documents/{version_id}")
async def get_document(request: Request, version_id: UUID) -> dict:
    repo = _repo(request)
    detail = await repo.get_version(version_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    detail["chunk_count"] = await repo.count_chunks(version_id)
    # Original uploaded file (PDF), served from blob storage. None for versions
    # ingested before originals were stored (re-ingest to get a viewable copy).
    detail["file_url"] = _blob_url(request, detail.pop("blob_uri", None))
    text = await repo.get_full_text(version_id) or ""
    if detail.get("char_count") is None and text:
        detail["char_count"] = len(text)
    if detail.get("word_count") is None and text:
        detail["word_count"] = len(text.split())
    detail["token_count"] = _count_tokens(text)
    return detail


class DocumentPatch(BaseModel):
    """What is normally given at ingest time. A field that is left out stays as it is."""

    title: str | None = Field(default=None, max_length=300)
    collection: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=100)


@router.patch("/documents/{version_id}")
async def edit_document(request: Request, version_id: UUID, body: DocumentPatch) -> dict:
    """Rename the title and collection (kept on the document) and the version label."""
    values = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items()}
    if not values:
        raise HTTPException(status_code=422, detail="Nothing to change: give a title, collection or version.")
    empty = [k for k, v in values.items() if not v]
    if empty:
        raise HTTPException(status_code=422, detail=f"{', '.join(empty)} cannot be empty.")
    repo = _repo(request)
    if not await repo.update_version_metadata(version_id, **values):
        raise HTTPException(status_code=404, detail="Version not found.")
    return await get_document(request, version_id)


@router.delete("/documents/{version_id}")
async def delete_document(request: Request, version_id: UUID) -> dict:
    """Delete one version (sections/images/tables/chunks cascade with it).
    Does not delete the parent document row. Mainly useful for clearing out
    versions ingested before a pipeline fix, so they can be re-ingested
    cleanly — re-ingesting identical bytes is otherwise a no-op.
    """
    repo = _repo(request)
    deleted = await repo.delete_version(version_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Version not found.")
    return {"deleted": True, "id": str(version_id)}


@router.get("/documents/{version_id}/normalized-text")
async def get_normalized_text(request: Request, version_id: UUID) -> dict:
    """The sentence-per-line text (headers, footers, page numbers removed) the change analysis
    diffs. Saved at ingestion; versions ingested before that are computed once and saved here."""
    repo = _repo(request)
    text = await repo.get_normalized_text(version_id)
    if text is None:
        from docsmind.agent.subagents.change_analysis.prep import normalize

        raw = await repo.get_full_text(version_id) or ""
        catalog = await repo.list_catalog([version_id])
        pages = (catalog[0].get("page_count") if catalog else None) or None
        text = "\n".join(normalize(raw, pages=pages))
        await repo.set_normalized_text(version_id, text)
    return {"normalized_text": text}


@router.get("/documents/{version_id}/fulltext")
async def get_fulltext(request: Request, version_id: UUID) -> dict:
    repo = _repo(request)
    text = await repo.get_full_text(version_id)
    return {"full_text": text or ""}


@router.get("/documents/{version_id}/outline")
async def get_outline(request: Request, version_id: UUID) -> list[dict]:
    repo = _repo(request)
    return await repo.get_sections(version_id)


@router.get("/documents/{version_id}/chunks")
async def get_chunks(request: Request, version_id: UUID, page: int | None = None) -> list[dict]:
    repo = _repo(request)
    return await repo.get_chunks(version_id, page=page)


@router.get("/documents/{version_id}/chunks/{chunk_id}/embedding")
async def get_chunk_embedding(request: Request, version_id: UUID, chunk_id: UUID) -> dict:
    """The full embedding vector of one chunk (the chunk list only carries a preview)."""
    repo = _repo(request)
    vector = await repo.get_chunk_embedding(version_id, chunk_id)
    if vector is None:
        raise HTTPException(status_code=404, detail="This chunk has no embedding.")
    return {"dims": len(vector), "embedding": vector}


@router.get("/documents/{version_id}/images")
async def get_images(request: Request, version_id: UUID) -> list[dict]:
    repo = _repo(request)
    images = await repo.get_images(version_id)
    for image in images:
        image["url"] = _blob_url(request, image.get("uri"))
    return images


@router.get("/documents/{version_id}/tables")
async def get_tables(request: Request, version_id: UUID) -> list[dict]:
    repo = _repo(request)
    tables = await repo.get_tables(version_id)
    for table in tables:
        table["url"] = _blob_url(request, table.get("uri"))
    return tables
