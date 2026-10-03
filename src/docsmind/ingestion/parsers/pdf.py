"""PDF -> ParsedDocument, using PyMuPDF (`fitz`).

Deliberately simple, on purpose — see the module-level docstrings on
`domain.documents.Section` and `ingestion.chunking.page` for the reasoning.

What this does:

1. Extract the full text of the document (page by page, joined with a
   page-break marker), and compute basic stats over it (char/word/token counts).
2. Read the PDF's own table of contents/bookmarks (`doc.get_toc()`), if it
   has one, and turn those entries directly into a `Section` outline.
   If there's no TOC, `sections` is just empty.
3. Chunk each page's text independently for vector search
   (see `chunking/page.py`).
4. Extract every image on every page, above a minimum size, with
   perceptual-hash information and a human-readable blob path.
   Repeated visually identical images (e.g. a logo embedded on every page)
   are skipped after the first occurrence.
5. Detect tables with PyMuPDF, render each table's bounding box as a PNG,
   and save it to the blob store under `tables/...`.
   Repeated visually identical tables are skipped.
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
    Table,
)
from docsmind.domain.ports import BlobStore
from docsmind.ingestion.chunking.page import chunk_page_text


# Applied only to TOC titles (not body text) to give a section a clean
# numeric path like "4.2" instead of a synthetic one, when the PDF's own
# bookmark title already contains that numbering.
_NUMBERING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})[\.\)]?\s+\S")

_LABELED_NUMBERING_RE = re.compile(
    r"^(Section|Article|Chapter|Clause|Appendix)\s+(\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)

PAGE_BREAK_MARKER = "\n\n"


def _slugify(text: str) -> str:
    """Lowercase, non-alnum runs collapsed to single hyphens."""
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        text.strip().lower(),
    ).strip("-")

    return slug or "untitled"


class PDFParser:
    """Implements the `Parser` port for `.pdf` files."""

    def __init__(
        self,
        blob_store: BlobStore,
        *,
        min_image_bytes: int = 2_000,
        table_dpi: int = 150,
    ) -> None:
        self.blob_store = blob_store
        self.min_image_bytes = min_image_bytes
        self.table_dpi = table_dpi

    async def parse(
        self,
        path: str,
        *,
        document: Document,
        version: DocumentVersion,
    ) -> ParsedDocument:
        doc = fitz.open(path)

        try:
            # -----------------------------------------------------------------
            # Full text
            # -----------------------------------------------------------------

            pages_text = [
                page.get_text()
                for page in doc
            ]

            full_text = PAGE_BREAK_MARKER.join(pages_text)

            stats = self._compute_stats(full_text)

            # -----------------------------------------------------------------
            # PDF outline / TOC
            # -----------------------------------------------------------------

            sections = self._build_outline(
                doc,
                version,
            )

            # -----------------------------------------------------------------
            # Text chunks
            # -----------------------------------------------------------------

            chunks: list[Chunk] = []

            for page_number, page_text in enumerate(
                pages_text,
                start=1,
            ):
                chunks.extend(
                    chunk_page_text(
                        page_text,
                        version_id=version.id,
                        page_number=page_number,
                    )
                )

            # -----------------------------------------------------------------
            # Images + tables
            # -----------------------------------------------------------------

            images: list[Image] = []
            tables: list[Table] = []

            # Used to skip repeated visual images (e.g. a logo embedded on
            # every page) and repeated visual tables (e.g. recurring
            # headers/footers) — same technique, same de-dup granularity:
            # one entry per *visually distinct* image across the whole
            # document, not per page.
            seen_image_phashes: set[str] = set()
            seen_table_phashes: set[str] = set()

            for page_index, page in enumerate(
                doc,
                start=1,
            ):
                # -------------------------------------------------------------
                # Images
                # -------------------------------------------------------------

                for img in page.get_images(full=True):
                    xref = img[0]

                    rects = page.get_image_rects(xref)

                    if not rects:
                        continue

                    rect = rects[0]

                    bbox = (
                        rect.x0,
                        rect.y0,
                        rect.x1,
                        rect.y1,
                    )

                    image = await self._extract_image(
                        doc,
                        xref,
                        page_index,
                        bbox,
                        document,
                        version,
                        seen_image_phashes,
                    )

                    if image is None:
                        continue

                    images.append(image)

                    chunks.append(
                        Chunk(
                            version_id=version.id,
                            chunk_type=ChunkType.IMAGE,
                            content="",
                            image_id=image.id,
                            page_start=page_index,
                            page_end=page_index,
                        )
                    )

                # -------------------------------------------------------------
                # Tables
                # -------------------------------------------------------------

                page_tables = await self._extract_tables(
                    page,
                    page_index,
                    document,
                    version,
                    seen_table_phashes,
                )

                tables.extend(page_tables)

            # -----------------------------------------------------------------
            # Result
            # -----------------------------------------------------------------

            return ParsedDocument(
                full_text=full_text,
                stats=stats,
                sections=sections,
                chunks=chunks,
                images=images,
                tables=tables,
            )

        finally:
            doc.close()

    # =========================================================================
    # Full text + stats
    # =========================================================================

    @staticmethod
    def _compute_stats(
        full_text: str,
    ) -> DocumentStats:
        char_count = len(full_text)

        word_count = len(
            full_text.split()
        )

        # Same ~0.75 words/token rule of thumb used in
        # chunking/page.py's sizing.
        token_count_estimate = (
            round(word_count / 0.75)
            if word_count
            else 0
        )

        return DocumentStats(
            char_count=char_count,
            word_count=word_count,
            token_count_estimate=token_count_estimate,
        )

    # =========================================================================
    # Outline (TOC only, no heuristics)
    # =========================================================================

    def _build_outline(
        self,
        doc: fitz.Document,
        version: DocumentVersion,
    ) -> list[Section]:
        try:
            toc = doc.get_toc(
                simple=True,
            )
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

            path, parent = self._assign_path(
                title,
                level,
                level_counters,
                active_by_level,
            )

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

            for deeper in [
                lv
                for lv in active_by_level
                if lv > level
            ]:
                del active_by_level[deeper]

        return sections

    @staticmethod
    def _assign_path(
        heading_text: str,
        level: int,
        level_counters: list[int],
        active_by_level: dict[int, Section],
    ) -> tuple[str, Section | None]:
        parent_level = max(
            (
                lv
                for lv in active_by_level
                if lv < level
            ),
            default=None,
        )

        parent = (
            active_by_level.get(parent_level)
            if parent_level is not None
            else None
        )

        explicit = (
            _NUMBERING_RE.match(heading_text)
            or _LABELED_NUMBERING_RE.match(heading_text)
        )

        if explicit:
            number = (
                explicit.group(1)
                if _NUMBERING_RE.match(heading_text)
                else explicit.group(2)
            )

            return number, parent

        while len(level_counters) < level:
            level_counters.append(0)

        del level_counters[level:]

        level_counters[level - 1] += 1

        path = ".".join(
            f"s{c}"
            for c in level_counters[:level]
        )

        return path, parent

    # =========================================================================
    # Image extraction
    # =========================================================================

    async def _extract_image(
        self,
        doc: fitz.Document,
        xref: int,
        page_number: int,
        bbox: tuple[float, float, float, float],
        document: Document,
        version: DocumentVersion,
        seen_image_phashes: set[str],
    ) -> Image | None:
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            return None

        data: bytes = extracted["image"]

        if len(data) < self.min_image_bytes:
            return None

        ext = extracted.get(
            "ext",
            "png",
        )

        width = extracted.get(
            "width",
            0,
        )

        height = extracted.get(
            "height",
            0,
        )

        phash = self._perceptual_hash(
            data,
        )

        # Skip visually repeated images (e.g. a logo embedded on every
        # page) once we've already recorded one occurrence for this
        # document. Same policy as `_extract_tables` below. If hashing is
        # unavailable (`imagehash`/`PIL` not installed), keep every image —
        # no way to tell they're duplicates without it.
        if (
            phash is not None
            and phash in seen_image_phashes
        ):
            return None

        if phash is not None:
            seen_image_phashes.add(
                phash,
            )

        version_dir = (
            f"{_slugify(version.label)}-"
            f"{version.id.hex[:8]}"
        )

        key = (
            f"images/"
            f"{_slugify(document.collection)}/"
            f"{_slugify(document.title)}/"
            f"{version_dir}/"
            f"{xref}.{ext}"
        )

        uri = await self.blob_store.put(
            key,
            data,
            content_type=f"image/{ext}",
        )

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

    # =========================================================================
    # Table extraction
    # =========================================================================

    async def _extract_tables(
        self,
        page: fitz.Page,
        page_number: int,
        document: Document,
        version: DocumentVersion,
        seen_table_phashes: set[str],
    ) -> list[Table]:
        try:
            finder = page.find_tables()
        except Exception:
            return []

        tables: list[Table] = []

        for table_index, table in enumerate(
            finder.tables,
            start=1,
        ):
            try:
                x0, y0, x1, y1 = table.bbox

                bbox = (
                    float(x0),
                    float(y0),
                    float(x1),
                    float(y1),
                )

                # Render only the table region.
                pix = page.get_pixmap(
                    clip=fitz.Rect(
                        x0,
                        y0,
                        x1,
                        y1,
                    ),
                    dpi=self.table_dpi,
                    alpha=False,
                )

                data = pix.tobytes(
                    "png",
                )

            except Exception:
                continue

            phash = self._perceptual_hash(
                data,
            )

            # Skip visually repeated tables when a perceptual hash is
            # available. If hashing is unavailable, keep the table.
            if (
                phash is not None
                and phash in seen_table_phashes
            ):
                continue

            if phash is not None:
                seen_table_phashes.add(
                    phash,
                )

            version_dir = (
                f"{_slugify(version.label)}-"
                f"{version.id.hex[:8]}"
            )

            key = (
                f"tables/"
                f"{_slugify(document.collection)}/"
                f"{_slugify(document.title)}/"
                f"{version_dir}/"
                f"page-{page_number:04d}-"
                f"table-{table_index:03d}.png"
            )

            uri = await self.blob_store.put(
                key,
                data,
                content_type="image/png",
            )

            tables.append(
                Table(
                    version_id=version.id,
                    page_number=page_number,
                    bbox=bbox,
                    uri=uri,
                    width=pix.width,
                    height=pix.height,
                    format="png",
                    phash=phash,
                )
            )

        return tables

    # =========================================================================
    # Perceptual hashing
    # =========================================================================

    def _perceptual_hash(
        self,
        data: bytes,
    ) -> str | None:
        try:
            import imagehash
            from PIL import Image as PILImage
        except ImportError:
            return None

        try:
            with PILImage.open(
                io.BytesIO(data),
            ) as pil_image:
                return str(
                    imagehash.phash(
                        pil_image,
                    )
                )
        except Exception:
            return None