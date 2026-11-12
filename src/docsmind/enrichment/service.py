"""Status, cost estimates and execution for on-demand embeddings and captions.

Embeddings and captions are created only when the user asks (and confirms the
estimate). Both jobs are *resumable and idempotent*: they only touch rows that
still need work, saving after every batch, so a cancelled or failed run loses
nothing and a re-run continues where it stopped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from docsmind.config import DocsmindConfig
from docsmind.domain.errors import EnrichmentConfigurationError, EnrichmentError
from docsmind.pricing import (
    caption_estimate,
    count_tokens,
    embedding_estimate,
    image_tokens,
)

log = logging.getLogger(__name__)

ENRICHMENT_KINDS = ("embeddings", "captions")
ProgressFn = Callable[[int, int, str], None]

_CAPTION_CONCURRENCY = 3
_PAGE = 500  # rows per page while estimating
_MAX_FAILURES_BEFORE_ABORT = 3  # consecutive failures with zero successes


def _mime(fmt: str) -> str:
    fmt = (fmt or "png").lower().lstrip(".")
    return "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"


class EnrichmentService:
    def __init__(
        self,
        config: DocsmindConfig,
        repo,
        *,
        blobs=None,
        embedder=None,
        captioner=None,
    ) -> None:
        self.config = config
        self.repo = repo
        self._blobs = blobs
        self._embedder = embedder
        self._captioner = captioner

    # -- collaborators -------------------------------------------------------------

    @property
    def embedding_model(self) -> str:
        return self._embedder.model_name if self._embedder is not None else self.config.embedding_model

    @property
    def vision_model(self) -> str:
        if self._captioner is not None:
            return self._captioner.model_name
        return self.config.resolved_vision_model

    def _get_embedder(self):
        if self._embedder is None:
            from docsmind.adapters.embeddings import create_embedder

            self._embedder = create_embedder(self.config)
        return self._embedder

    def _get_captioner(self):
        if self._captioner is None:
            from docsmind.adapters.vision import create_captioner

            self._captioner = create_captioner(self.config)
        return self._captioner

    def _get_blobs(self):
        if self._blobs is None:
            if self.config.blob_backend != "filesystem":
                raise EnrichmentError(
                    f"blob_backend={self.config.blob_backend!r} isn't supported for captioning yet."
                )
            from docsmind.adapters.blobs.filesystem import FilesystemBlobStore

            self._blobs = FilesystemBlobStore(self.config.blob_root)
        return self._blobs

    def readiness(self, kind: str) -> str | None:
        """None when `kind` can run, otherwise a message saying what to fix."""
        _check_kind(kind)
        try:
            if kind == "embeddings":
                self._get_embedder()
            else:
                self._get_captioner()
        except EnrichmentConfigurationError as exc:
            return str(exc)
        return None

    # -- status ------------------------------------------------------------------------

    async def status(self, version_ids: list[UUID] | None = None) -> dict:
        rows = await self.repo.enrichment_status(self.embedding_model, version_ids)
        chunks_total = sum(r["chunks_total"] for r in rows)
        chunks_done = sum(r["chunks_embedded"] for r in rows)
        images_total = sum(r["images_total"] for r in rows)
        images_done = sum(r["images_captioned"] for r in rows)
        embed_reason = self.readiness("embeddings")
        caption_reason = self.readiness("captions")
        return {
            "embeddings": {
                "provider": self.config.embedding_provider,
                "model": self.embedding_model,
                "total": chunks_total,
                "done": chunks_done,
                "pending": chunks_total - chunks_done,
                "configured": embed_reason is None,
                "reason": embed_reason,
            },
            "captions": {
                "provider": self.config.vision_provider,
                "model": self.vision_model,
                "total": images_total,
                "done": images_done,
                "pending": images_total - images_done,
                "configured": caption_reason is None,
                "reason": caption_reason,
            },
            "versions": [
                {
                    "version_id": str(r["version_id"]),
                    "chunks_total": r["chunks_total"],
                    "chunks_embedded": r["chunks_embedded"],
                    "images_total": r["images_total"],
                    "images_captioned": r["images_captioned"],
                }
                for r in rows
            ],
        }

    # -- estimates ---------------------------------------------------------------------

    async def estimate(self, kind: str, version_ids: list[UUID] | None = None) -> dict:
        _check_kind(kind)
        if kind == "embeddings":
            return (await self._estimate_embeddings(version_ids)).to_dict()
        return (await self._estimate_captions(version_ids)).to_dict()

    async def _estimate_embeddings(self, version_ids):
        model = self.embedding_model
        items = tokens = 0
        after: UUID | None = None
        while True:
            rows = await self.repo.fetch_chunks_to_embed(
                model, version_ids=version_ids, after_id=after, limit=_PAGE
            )
            if not rows:
                break
            items += len(rows)
            tokens += sum(await asyncio.to_thread(lambda r=rows: [count_tokens(x["content"]) for x in r]))
            after = rows[-1]["id"]
        status = await self.repo.enrichment_status(model, version_ids)
        done = sum(r["chunks_embedded"] for r in status)
        return embedding_estimate(
            provider=self.config.embedding_provider,
            model=model,
            items=items,
            tokens=tokens,
            already_done=done,
            price_override=self.config.embedding_price_per_million,
        )

    async def _estimate_captions(self, version_ids):
        provider = self.config.vision_provider
        model = self.vision_model
        items = skipped = image_tokens_total = 0
        after: UUID | None = None
        while True:
            rows = await self.repo.fetch_images_to_caption(version_ids=version_ids, after_id=after, limit=_PAGE)
            if not rows:
                break
            for row in rows:
                if await self._too_small(row["uri"]):
                    skipped += 1
                    continue
                items += 1
                image_tokens_total += image_tokens(row["width"], row["height"], provider=provider)
            after = rows[-1]["id"]
        status = await self.repo.enrichment_status(self.embedding_model, version_ids)
        return caption_estimate(
            provider=provider,
            model=model,
            items=items,
            skipped=skipped,
            already_done=sum(r["images_captioned"] for r in status),
            image_input_tokens=image_tokens_total,
            input_price_override=self.config.vision_input_price_per_million,
            output_price_override=self.config.vision_output_price_per_million,
            max_output_tokens=self.config.vision_max_output_tokens,
        )

    async def _blob_size(self, uri: str) -> int | None:
        if uri.startswith("file://"):
            try:
                return Path(uri[len("file://") :]).stat().st_size
            except OSError:
                return None
        try:
            return len(await self._get_blobs().get(uri))
        except Exception:  # noqa: BLE001
            return None

    async def _too_small(self, uri: str) -> bool:
        size = await self._blob_size(uri)
        return size is not None and size < self.config.caption_image_min_bytes

    # -- running -----------------------------------------------------------------------

    async def run(
        self,
        kind: str,
        version_ids: list[UUID] | None = None,
        *,
        on_progress: ProgressFn | None = None,
        cancel: asyncio.Event | None = None,
    ) -> dict:
        _check_kind(kind)
        reason = self.readiness(kind)
        if reason:
            raise EnrichmentConfigurationError(reason)
        progress = on_progress or (lambda done, total, message: None)
        cancel = cancel or asyncio.Event()
        if kind == "embeddings":
            return await self._run_embeddings(version_ids, progress, cancel)
        return await self._run_captions(version_ids, progress, cancel)

    async def _run_embeddings(self, version_ids, progress: ProgressFn, cancel: asyncio.Event) -> dict:
        embedder = self._get_embedder()
        model = embedder.model_name
        status = await self.repo.enrichment_status(model, version_ids)
        total = sum(r["chunks_total"] - r["chunks_embedded"] for r in status)
        batch = max(16, self.config.embedding_batch_size)
        done = 0
        progress(0, total, "Starting")
        while not cancel.is_set():
            rows = await self.repo.fetch_chunks_to_embed(model, version_ids=version_ids, limit=batch)
            if not rows:
                break
            vectors = await embedder.embed([r["content"] for r in rows])
            await self.repo.save_embeddings([(r["id"], v) for r, v in zip(rows, vectors, strict=True)], model)
            done += len(rows)
            progress(done, max(total, done), f"Embedded {done} of {max(total, done)} passages")
        return {
            "kind": "embeddings",
            "model": model,
            "processed": done,
            "skipped": 0,
            "failed": 0,
            "total": total,
            "cancelled": cancel.is_set(),
            "errors": [],
        }

    async def _run_captions(self, version_ids, progress: ProgressFn, cancel: asyncio.Event) -> dict:
        captioner = self._get_captioner()
        blobs = self._get_blobs()
        model = captioner.model_name
        status = await self.repo.enrichment_status(self.embedding_model, version_ids)
        total = sum(r["images_total"] - r["images_captioned"] for r in status)
        processed = skipped = 0
        errors: list[str] = []
        failed_ids: set[UUID] = set()
        progress(0, total, "Starting")

        async def handle(row: dict) -> str:
            """-> 'captioned' | 'skipped' ; raises on provider failure."""
            data = await blobs.get(row["uri"])
            if len(data) < self.config.caption_image_min_bytes:
                await self.repo.save_caption(row["id"], "", f"skipped:{model}")
                return "skipped"
            caption = await captioner.caption(data, mime_type=_mime(row["format"]))
            if caption.strip().lower().rstrip(".") == "decorative":
                caption = ""
            await self.repo.save_caption(row["id"], caption, model)
            return "captioned"

        while not cancel.is_set():
            rows = await self.repo.fetch_images_to_caption(version_ids=version_ids, limit=_CAPTION_CONCURRENCY * 4)
            rows = [r for r in rows if r["id"] not in failed_ids]
            if not rows:
                break
            for i in range(0, len(rows), _CAPTION_CONCURRENCY):
                if cancel.is_set():
                    break
                group = rows[i : i + _CAPTION_CONCURRENCY]
                results = await asyncio.gather(*(handle(r) for r in group), return_exceptions=True)
                for row, result in zip(group, results, strict=True):
                    if isinstance(result, BaseException):
                        failed_ids.add(row["id"])
                        errors.append(f"p.{row['page_number']}: {result}")
                        log.warning("caption failed for image %s: %s", row["id"], result)
                    elif result == "skipped":
                        skipped += 1
                    else:
                        processed += 1
                if len(failed_ids) >= _MAX_FAILURES_BEFORE_ABORT and processed == 0 and skipped == 0:
                    raise EnrichmentError(f"Captioning is failing, so it was stopped. First error: {errors[0]}")
                handled = processed + skipped
                progress(handled, max(total, handled + len(failed_ids)), f"Captioned {handled} of {total} images")
        return {
            "kind": "captions",
            "model": model,
            "processed": processed,
            "skipped": skipped,
            "failed": len(failed_ids),
            "total": total,
            "cancelled": cancel.is_set(),
            "errors": errors[:5],
        }


def _check_kind(kind: str) -> None:
    if kind not in ENRICHMENT_KINDS:
        raise ValueError(f"Unknown enrichment kind {kind!r}; expected one of {ENRICHMENT_KINDS}.")
