"""Document endpoints backing the `frontend/` UI: list/ingest documents, and
read back what `docsmind ingest` saved (full text, outline, chunks, images,
extracted tables) — the HTTP equivalent of `cli/inspect.py`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile

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
        except Exception as exc:  # noqa: BLE001 - surface parser/db errors to the UI as-is
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
