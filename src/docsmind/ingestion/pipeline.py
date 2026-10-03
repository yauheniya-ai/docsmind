"""Orchestrates one file all the way into Postgres: pick a parser by
extension, run it, persist a `Document`/`DocumentVersion` row, then bulk-save
everything the parser produced (sections/images/chunks).

This is what `Docsmind.ingest()` / `docsmind ingest` actually call. Chunks
land with `embedding = NULL` — embedding + hybrid search are the next piece
to build (see `PostgresRepository.hybrid_search`'s TODO).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from docsmind.adapters.blobs.filesystem import FilesystemBlobStore
from docsmind.adapters.storage.postgres.repository import PostgresRepository
from docsmind.config import DocsmindConfig
from docsmind.domain.documents import Document, DocumentVersion
from docsmind.domain.errors import DocumentAlreadyExistsError

_PARSERS_BY_SUFFIX = {
    ".pdf": "docsmind.ingestion.parsers.pdf:PDFParser",
    # ".docx": "docsmind.ingestion.parsers.docx:DOCXParser",  # not built yet
    # ".txt":  "docsmind.ingestion.parsers.text:TextParser",  # not built yet
}


class IngestionPipeline:
    def __init__(self, config: DocsmindConfig) -> None:
        self.config = config
        self.repo = PostgresRepository(config)
        self.blob_store = self._make_blob_store(config)

    async def run(self, path: Path, *, collection: str, version_label: str | None = None) -> DocumentVersion:
        content_hash = self._hash_file(path)

        # Check *before* parsing: parsing a PDF re-extracts every image and
        # table and writes them all to blob storage, which is pure waste if
        # this exact file is already ingested (content_hash is globally
        # unique on `document_versions`). Without this check, a re-ingest of
        # an unchanged file would still pay the full parse cost and then
        # fail with a raw Postgres IntegrityError on the final insert.
        existing = await self.repo.get_version_by_content_hash(content_hash)
        if existing is not None:
            raise DocumentAlreadyExistsError(content_hash=content_hash, existing_version=existing)

        parser = self._make_parser(path)

        document = Document(collection=collection, title=path.stem)
        # Version is optional: most people ingesting a one-off document don't
        # have a meaningful label yet. Today's date is a reasonable default —
        # readable, sorts chronologically, and doesn't collide across
        # unrelated documents (only content_hash needs global uniqueness).
        version = DocumentVersion(
            document_id=document.id,
            label=version_label or datetime.now(timezone.utc).date().isoformat(),
            content_hash=content_hash,
            blob_uri=f"file://{path.resolve()}",
        )

        parsed = await parser.parse(str(path), document=document, version=version)

        # Keep the original file next to the extracted images/tables, so the
        # UI can show the source document (and it survives the upload's temp
        # directory being cleaned up). `blob_uri` then points at this copy.
        version.blob_uri = await self._store_original(path, document=document, version=version)

        await self.repo.create_document(document)
        await self.repo.create_version(version, full_text=parsed.full_text, stats=parsed.stats)
        await self.repo.save_parsed(parsed)

        return version

    async def _store_original(self, path: Path, *, document: Document, version: DocumentVersion) -> str:
        def slug(text: str) -> str:
            return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "untitled"

        suffix = path.suffix.lower()
        key = (
            f"documents/{slug(document.collection)}/{slug(document.title)}/"
            f"{slug(version.label)}-{version.id.hex[:8]}/original{suffix}"
        )
        content_type = "application/pdf" if suffix == ".pdf" else "application/octet-stream"
        return await self.blob_store.put(key, path.read_bytes(), content_type=content_type)

    def _make_parser(self, path: Path):
        suffix = path.suffix.lower()
        if suffix not in _PARSERS_BY_SUFFIX:
            supported = ", ".join(sorted(_PARSERS_BY_SUFFIX))
            raise ValueError(f"No parser registered for {suffix!r} files. Supported: {supported}")
        if suffix == ".pdf":
            from docsmind.ingestion.parsers.pdf import PDFParser

            return PDFParser(self.blob_store)
        raise NotImplementedError(suffix)  # pragma: no cover - unreachable while dict has one entry

    @staticmethod
    def _make_blob_store(config: DocsmindConfig):
        if config.blob_backend == "filesystem":
            return FilesystemBlobStore(config.blob_root)
        raise NotImplementedError(
            f"blob_backend={config.blob_backend!r} isn't wired up yet in IngestionPipeline "
            "— only 'filesystem' is implemented so far. See adapters/blobs/s3.py, supabase.py (not yet built)."
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        # Used for `document_versions.content_hash` (unique) — re-ingesting
        # the exact same bytes under the same version label will hit that
        # uniqueness constraint rather than silently duplicating everything.
        digest = hashlib.sha256()
        digest.update(path.read_bytes())
        return digest.hexdigest()