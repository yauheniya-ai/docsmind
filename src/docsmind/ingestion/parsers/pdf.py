"""PDF -> ParsedDocument, using PyMuPDF (`fitz`).

Deliberately simple, on purpose — see the module-level docstrings on
`domain.documents.Section` and `ingestion.chunking.page` for the reasoning.
What this does:

1. Extract the full text of the document (page by page, joined with a
   page-break marker), and compute basic stats over it (char/word/token
   counts).
2. Read the PDF's own table of contents/bookmarks (`doc.get_toc()`), if it
   has one, and turn those entries directly into a `Section` outline —
   ground truth from whoever authored the PDF. If there's no TOC, `sections`
   is just empty. No attempt is made to reconstruct structure from font
   size/boldness/numbering patterns in the body text; that doesn't
   generalize across the huge variety of real-world PDFs.
3. Chunk each page's text independently for vector search (see
   `chunking/page.py`) — chunking has no relationship to the outline above.
4. Extract every image on every page, above a minimum size (skips icons/
   logos/spacers), with perceptual-hash dedup info and a human-readable blob
   path.

Known limitations:
  - `page.get_text()` extracts in PyMuPDF's own reading-order heuristic,
    which is usually fine but can interleave columns oddly on a genuine
    multi-column layout. Full text is saved as-is either way — a person or
    an LLM reading it can generally work around minor reordering; a
    heuristic section tree built on top of it could not.
  - A TOC entry becomes a `Section` at (page, level) as PyMuPDF reports it;
    we don't try to locate its exact position on the page (no line-matching,
    no vertical-position lookup) — the outline is navigational metadata, not
    something chunks are anchored to, so page-level granularity is enough.
"""

from __future__ import annotations

import io
import re

import pymupdf as fitz  # `import fitz` still works but is deprecated upstream

from docsmind.domain.documents import (
    Chunk,
    ChunkType,
    Document,
    DocumentStats,
    DocumentVersion,
    Image,
    ParsedDocument,
    Section,
)
from docsmind.domain.ports import BlobStore
from docsmind.ingestion.chunking.page import chunk_page_text

# Applied only to TOC titles (not body text) to give a section a clean
# numeric path like "4.2" instead of a synthetic one, when the PDF's own
# bookmark title already contains that numbering.
_NUMBERING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})[\.\)]?\s+\S")
_LABELED_NUMBERING_RE = re.compile(
    r"^(Section|Article|Chapter|Clause|Appendix)\s+(\d+(?:\.\d+)*)\b", re.IGNORECASE
)

PAGE_BREAK_MARKER = "\n\n"  # joins per-page text into `full_text`


def _slugify(text: str) -> str:
    """Lowercase, non-alnum runs collapsed to single hyphens. Used to build
    filesystem/blob paths that are actually readable, not just unique."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "untitled"


class PDFParser:
    """Implements the `Parser` port for `.pdf` files."""

    def __init__(self, blob_store: BlobStore, *, min_image_bytes: int = 2_000) -> None:
        self.blob_store = blob_store
        self.min_image_bytes = min_image_bytes

    async def parse(self, path: str, *, document: Document, version: DocumentVersion) -> ParsedDocument:
        doc = fitz.open(path)
        try:
            pages_text = [page.get_text() for page in doc]
            full_text = PAGE_BREAK_MARKER.join(pages_text)
            stats = self._compute_stats(full_text)

            sections = self._build_outline(doc, version)

            chunks: list[Chunk] = []
            for page_number, page_text in enumerate(pages_text, start=1):
                chunks.extend(
                    chunk_page_text(page_text, version_id=version.id, page_number=page_number)
                )

            images: list[Image] = []
            for page_index, page in enumerate(doc, start=1):
                for img in page.get_images(full=True):
                    xref = img[0]
                    rects = page.get_image_rects(xref)
                    if not rects:
                        continue
                    rect = rects[0]
                    bbox = (rect.x0, rect.y0, rect.x1, rect.y1)
                    image = await self._extract_image(doc, xref, page_index, bbox, document, version)
                    if image is None:
                        continue
                    images.append(image)
                    chunks.append(
                        Chunk(
                            version_id=version.id,
                            chunk_type=ChunkType.IMAGE,
                            content="",  # filled in once captioned (enrichment/caption.py)
                            image_id=image.id,
                            page_start=page_index,
                            page_end=page_index,
                        )
                    )

            return ParsedDocument(
                full_text=full_text, stats=stats, sections=sections, chunks=chunks, images=images
            )
        finally:
            doc.close()

    # -- full text + stats ----------------------------------------------------

    @staticmethod
    def _compute_stats(full_text: str) -> DocumentStats:
        char_count = len(full_text)
        word_count = len(full_text.split())
        # Same ~0.75 words/token rule of thumb used in chunking/page.py's
        # sizing — approximate, not a real tokenizer; see that module's
        # docstring for why.
        token_count_estimate = round(word_count / 0.75) if word_count else 0
        return DocumentStats(
            char_count=char_count, word_count=word_count, token_count_estimate=token_count_estimate
        )

    # -- outline (TOC only, no heuristics) -------------------------------------

    def _build_outline(self, doc: fitz.Document, version: DocumentVersion) -> list[Section]:
        try:
            toc = doc.get_toc(simple=True)  # [[level, title, page_1_indexed], ...]
        except Exception:
            return []
        if not toc:
            return []

        sections: list[Section] = []
        level_counters: list[int] = []
        active_by_level: dict[int, Section] = {}

        for level, title, page_number in toc:
            title = title.strip()
            if not title:
                continue

            path, parent = self._assign_path(title, level, level_counters, active_by_level)
            section = Section(
                version_id=version.id,
                parent_id=parent.id if parent else None,
                path=path,
                heading=title,
                page_start=page_number,
                page_end=page_number,
                ordinal=len(sections),
            )
            sections.append(section)
            active_by_level[level] = section
            for deeper in [lv for lv in active_by_level if lv > level]:
                del active_by_level[deeper]

        return sections

    @staticmethod
    def _assign_path(
        heading_text: str,
        level: int,
        level_counters: list[int],
        active_by_level: dict[int, Section],
    ) -> tuple[str, Section | None]:
        parent_level = max((lv for lv in active_by_level if lv < level), default=None)
        parent = active_by_level.get(parent_level) if parent_level is not None else None

        explicit = _NUMBERING_RE.match(heading_text) or _LABELED_NUMBERING_RE.match(heading_text)
        if explicit:
            number = (
                explicit.group(1) if _NUMBERING_RE.match(heading_text) else explicit.group(2)
            )
            return number, parent

        while len(level_counters) < level:
            level_counters.append(0)
        del level_counters[level:]
        level_counters[level - 1] += 1
        path = ".".join(f"s{c}" for c in level_counters[:level])
        return path, parent

    # -- image extraction -----------------------------------------------------

    async def _extract_image(
        self,
        doc: fitz.Document,
        xref: int,
        page_number: int,
        bbox: tuple[float, float, float, float],
        document: Document,
        version: DocumentVersion,
    ) -> Image | None:
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            return None
        data: bytes = extracted["image"]
        if len(data) < self.min_image_bytes:
            return None  # skip tiny icons/spacers/logos-as-decoration

        ext = extracted.get("ext", "png")
        width = extracted.get("width", 0)
        height = extracted.get("height", 0)
        phash = self._perceptual_hash(data)

        # A human can find this on disk without a database lookup: collection
        # / document title / version label, with a short id suffix so two
        # documents that happen to share a title+label never collide. This is
        # deliberately *not* just `images/{version.id}/...` — a raw UUID path
        # tells you nothing when you're browsing the blob store by hand.
        version_dir = f"{_slugify(version.label)}-{version.id.hex[:8]}"
        key = (
            f"images/{_slugify(document.collection)}/{_slugify(document.title)}/"
            f"{version_dir}/{xref}.{ext}"
        )
        uri = await self.blob_store.put(key, data, content_type=f"image/{ext}")

        return Image(
            version_id=version.id,
            page_number=page_number,
            bbox=bbox,
            uri=uri,
            width=width,
            height=height,
            format=ext,
            phash=phash,
        )

    def _perceptual_hash(self, data: bytes) -> str | None:
        try:
            import imagehash
            from PIL import Image as PILImage
        except ImportError:
            return None
        try:
            with PILImage.open(io.BytesIO(data)) as pil_image:
                return str(imagehash.phash(pil_image))
        except Exception:
            return None