"""Runs `PDFParser` against one PDF and prints what it found — no Postgres,
no embeddings, no API keys required. This is the fastest way to sanity-check
the parser (full text/stats, TOC-based outline, page-based chunking, image
extraction) on a real document before running `docsmind ingest`.

Usage:
    python examples/parse_pdf_demo.py /path/to/file.pdf
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from docsmind.adapters.blobs.filesystem import FilesystemBlobStore
from docsmind.domain.documents import Document, DocumentVersion
from docsmind.ingestion.parsers.pdf import PDFParser


async def main(pdf_path: str) -> None:
    blob_store = FilesystemBlobStore("./.docsmind/blobs")
    parser = PDFParser(blob_store)

    document = Document(collection="demo", title=Path(pdf_path).stem)
    version = DocumentVersion(
        document_id=document.id,
        label="v1",
        content_hash="demo-hash-" + uuid4().hex[:8],
        blob_uri=f"file://{Path(pdf_path).resolve()}",
    )

    parsed = await parser.parse(pdf_path, document=document, version=version)

    print(f"\n{'=' * 60}\nSTATS\n{'=' * 60}")
    print(f"{parsed.stats.char_count:,} chars, {parsed.stats.word_count:,} words, "
          f"~{parsed.stats.token_count_estimate:,} tokens")
    preview = parsed.full_text[:300].replace("\n", " ")
    print(f"\nFull text preview: {preview}...")

    print(f"\n{'=' * 60}\nOUTLINE ({len(parsed.sections)} sections)\n{'=' * 60}")
    if not parsed.sections:
        print("(no table of contents/bookmarks in this PDF — no outline, by design)")
    else:
        section_by_id = {s.id: s for s in parsed.sections}
        for s in parsed.sections:
            depth = 0
            parent_id = s.parent_id
            while parent_id is not None:
                depth += 1
                parent_id = section_by_id[parent_id].parent_id
            indent = "  " * depth
            heading = s.heading or "(untitled)"
            pages = f"p.{s.page_start}" if s.page_start == s.page_end else f"p.{s.page_start}-{s.page_end}"
            print(f"{indent}[{s.path}] {heading}  ({pages})")

    print(f"\n{'=' * 60}\nCHUNKS ({len(parsed.chunks)}) — page-based, for vector search\n{'=' * 60}")
    for c in parsed.chunks:
        label = f"p.{c.page_start}"
        if c.chunk_type.value == "image":
            print(f"[{label}] IMAGE chunk -> image_id={c.image_id}")
        else:
            preview = c.content[:90].replace("\n", " ")
            print(f"[{label}] TEXT  ({len(c.content)} chars): {preview}...")

    print(f"\n{'=' * 60}\nIMAGES ({len(parsed.images)})\n{'=' * 60}")
    for img in parsed.images:
        print(f"p.{img.page_number} {img.width}x{img.height} {img.format} phash={img.phash}")
        print(f"  -> {img.uri}")

    print(f"\nDone. {len(parsed.images)} image(s) extracted — see their paths above (under ./.docsmind/blobs/).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))