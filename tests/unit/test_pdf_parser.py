"""Tests the simplified PDF parser: full text + stats, TOC-only outline (no
heuristic fallback), page-based chunking, and image extraction. Uses PDFs
generated on the fly rather than committed fixture files.
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image as PILImage

from docsmind.adapters.blobs.filesystem import FilesystemBlobStore
from docsmind.domain.documents import ChunkType, Document, DocumentVersion
from docsmind.ingestion.chunking.page import chunk_page_text
from docsmind.ingestion.parsers.pdf import PAGE_BREAK_MARKER, PDFParser


@pytest.fixture
def blob_store(tmp_path) -> FilesystemBlobStore:
    return FilesystemBlobStore(tmp_path / "blobs")


def _document_and_version() -> tuple[Document, DocumentVersion]:
    document = Document(collection="test", title="sample")
    version = DocumentVersion(
        document_id=document.id, label="v1", content_hash="h", blob_uri="file://x"
    )
    return document, version


# -- full text + stats -------------------------------------------------------


def _make_two_page_pdf(path: str) -> None:
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((50, 60), "Page one has some introductory text here.", fontsize=10)
    page2 = doc.new_page()
    page2.insert_text((50, 60), "Page two continues with more content.", fontsize=10)
    doc.save(path)
    doc.close()


@pytest.mark.asyncio
async def test_full_text_and_stats(tmp_path, blob_store):
    path = str(tmp_path / "two_page.pdf")
    _make_two_page_pdf(path)
    document, version = _document_and_version()

    parsed = await PDFParser(blob_store).parse(path, document=document, version=version)

    assert "Page one has some introductory text" in parsed.full_text
    assert "Page two continues with more content" in parsed.full_text
    assert PAGE_BREAK_MARKER in parsed.full_text  # the two pages' text is actually joined, not concatenated raw

    assert parsed.stats.char_count == len(parsed.full_text)
    assert parsed.stats.word_count == len(parsed.full_text.split())
    assert parsed.stats.token_count_estimate > 0


# -- outline: TOC only, no heuristic fallback --------------------------------


def _make_pdf_with_toc(path: str) -> None:
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((50, 60), "Introduction", fontsize=10)
    page1.insert_text((50, 90), "This section introduces the handbook.", fontsize=10)
    page2 = doc.new_page()
    page2.insert_text((50, 60), "Rules", fontsize=10)
    page2.insert_text((50, 90), "Rules must be followed at all times.", fontsize=10)
    doc.save(path)
    doc.close()

    doc2 = pymupdf.open(path)
    doc2.set_toc([[1, "Introduction", 1], [1, "Rules", 2]])
    tmp = path + ".tmp"
    doc2.save(tmp)
    doc2.close()
    import os

    os.replace(tmp, path)


@pytest.mark.asyncio
async def test_toc_becomes_outline_directly(tmp_path, blob_store):
    path = str(tmp_path / "toc.pdf")
    _make_pdf_with_toc(path)
    document, version = _document_and_version()

    parsed = await PDFParser(blob_store).parse(path, document=document, version=version)

    headings = [s.heading for s in parsed.sections]
    assert headings == ["Introduction", "Rules"]
    assert parsed.sections[0].page_start == 1
    assert parsed.sections[1].page_start == 2
    # Both top-level (TOC level 1) -> no parent.
    assert all(s.parent_id is None for s in parsed.sections)


@pytest.mark.asyncio
async def test_numbered_toc_titles_get_explicit_paths(tmp_path, blob_store):
    path = str(tmp_path / "numbered_toc.pdf")
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(path)
    doc.close()

    doc2 = pymupdf.open(path)
    doc2.set_toc([[1, "1. Incident Response", 1], [2, "1.1 Triage Timelines", 1]])
    tmp = path + ".tmp"
    doc2.save(tmp)
    doc2.close()
    import os

    os.replace(tmp, path)

    document, version = _document_and_version()
    parsed = await PDFParser(blob_store).parse(path, document=document, version=version)

    paths = {s.heading: s.path for s in parsed.sections}
    assert paths["1. Incident Response"] == "1"
    assert paths["1.1 Triage Timelines"] == "1.1"


@pytest.mark.asyncio
async def test_no_toc_means_empty_outline_no_heuristic_fallback(tmp_path, blob_store):
    """A PDF with big bold text but no bookmarks should NOT get a guessed
    section tree — this is the whole point of dropping the old heuristic
    detector. Empty outline is the correct, honest answer."""
    path = str(tmp_path / "no_toc.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), "This Looks Like A Heading", fontsize=24, fontname="hebo")
    page.insert_text((50, 100), "Some body text follows.", fontsize=10)
    doc.save(path)
    doc.close()

    document, version = _document_and_version()
    parsed = await PDFParser(blob_store).parse(path, document=document, version=version)

    assert parsed.sections == []
    # But the text is still fully captured in full_text regardless.
    assert "This Looks Like A Heading" in parsed.full_text
    assert "Some body text follows" in parsed.full_text


# -- page-based chunking ------------------------------------------------------


def test_short_page_becomes_one_chunk():
    from uuid import uuid4

    version_id = uuid4()
    text = "A short page of text, well under the size threshold." * 5  # ~275 chars

    chunks = chunk_page_text(text, version_id=version_id, page_number=3)

    assert len(chunks) == 1
    assert chunks[0].content == text.strip()
    assert chunks[0].page_start == 3
    assert chunks[0].page_end == 3
    assert chunks[0].chunk_type == ChunkType.TEXT


def test_dense_page_splits_with_overlap():
    from itertools import pairwise
    from uuid import uuid4

    version_id = uuid4()
    # Build deterministic, indexable text so overlap can be checked precisely.
    text = "".join(f"{i:04d} " for i in range(2000))  # well over 4000 chars

    chunks = chunk_page_text(
        text, version_id=version_id, page_number=1, target_chars=1000, max_chars=1200, overlap_chars=100
    )

    assert len(chunks) > 1
    assert all(c.page_start == 1 and c.page_end == 1 for c in chunks)
    # Consecutive chunks actually share the configured overlap.
    for a, b in pairwise(chunks):
        assert a.content[-100:] == b.content[:100]
    # Every chunk (except possibly the last) is close to target_chars.
    for c in chunks[:-1]:
        assert len(c.content) == 1000


def test_empty_page_produces_no_chunks():
    from uuid import uuid4

    assert chunk_page_text("   \n  ", version_id=uuid4(), page_number=1) == []


# -- image extraction (unchanged behavior) -----------------------------------


def _make_pdf_with_image(path: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), "A page with an embedded image.", fontsize=10)

    img = PILImage.new("RGB", (200, 150))
    for x in range(200):
        for y in range(150):
            img.putpixel((x, y), ((x * 7) % 255, (y * 13) % 255, (x + y) % 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(50, 100, 250, 250), stream=buf.getvalue())

    doc.save(path)
    doc.close()


@pytest.mark.asyncio
async def test_extracts_image_with_no_section_attachment(tmp_path, blob_store):
    path = str(tmp_path / "with_image.pdf")
    _make_pdf_with_image(path)
    document, version = _document_and_version()

    parsed = await PDFParser(blob_store, min_image_bytes=100).parse(
        path, document=document, version=version
    )

    assert len(parsed.images) == 1
    image = parsed.images[0]
    assert image.width == 200
    assert image.height == 150
    assert image.phash is not None

    image_chunks = [c for c in parsed.chunks if c.chunk_type == ChunkType.IMAGE]
    assert len(image_chunks) == 1
    assert image_chunks[0].image_id == image.id
    assert image_chunks[0].page_start == 1
    # Chunks are no longer attached to sections — page number is the only
    # positional metadata an image chunk carries now.
    assert image_chunks[0].section_id is None


@pytest.mark.asyncio
async def test_image_bytes_round_trip_through_blob_store(tmp_path, blob_store):
    path = str(tmp_path / "with_image.pdf")
    _make_pdf_with_image(path)
    document, version = _document_and_version()

    parsed = await PDFParser(blob_store, min_image_bytes=100).parse(
        path, document=document, version=version
    )
    image = parsed.images[0]

    data = await blob_store.get(image.uri)
    with PILImage.open(io.BytesIO(data)) as pil_image:
        assert pil_image.size == (200, 150)


@pytest.mark.asyncio
async def test_image_blob_path_is_human_readable(tmp_path, blob_store):
    path = str(tmp_path / "with_image.pdf")
    _make_pdf_with_image(path)
    document = Document(collection="Company Policies", title="Sample Handbook!!")
    version = DocumentVersion(
        document_id=document.id, label="2025 Q1", content_hash="h5", blob_uri="file://x"
    )

    parsed = await PDFParser(blob_store, min_image_bytes=100).parse(
        path, document=document, version=version
    )
    uri = parsed.images[0].uri

    assert "company-policies" in uri
    assert "sample-handbook" in uri
    assert "2025-q1" in uri
    assert version.id.hex[:8] in uri


@pytest.mark.asyncio
async def test_tiny_images_are_skipped(tmp_path, blob_store):
    path = str(tmp_path / "with_image.pdf")
    _make_pdf_with_image(path)
    document, version = _document_and_version()

    # Threshold set explicitly above whatever this image actually extracts as
    # (PyMuPDF re-encodes on insert, so its exact byte size isn't something
    # to hardcode an assumption about).
    parsed = await PDFParser(blob_store, min_image_bytes=1_000_000).parse(
        path, document=document, version=version
    )

    assert parsed.images == []
    assert all(c.chunk_type != ChunkType.IMAGE for c in parsed.chunks)