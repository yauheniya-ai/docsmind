"""Text out of downloaded files: PDF (per page), Word (.docx) and plain bytes.

Used by `read_url`. Everything extracted is untrusted data, like any document text.
"""

from __future__ import annotations

import io
from typing import Literal

from docsmind.domain.errors import ToolInputError

Kind = Literal["html", "text", "pdf", "docx"]

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def sniff_kind(content_type: str, url_path: str, data: bytes) -> Kind:
    """What a download is, from its content type, its first bytes and its file name."""
    ctype = (content_type or "").lower()
    path = url_path.lower()
    if data[:5] == b"%PDF-" or ctype == "application/pdf" or path.endswith(".pdf"):
        return "pdf"
    if ctype == DOCX_TYPE or path.endswith(".docx"):
        return "docx"
    if "html" in ctype or "xml" in ctype:
        return "html"
    if ctype.startswith("text/") or not ctype:
        return "html" if data[:512].lstrip().lower().startswith((b"<!doctype html", b"<html")) else "text"
    if data[:2] == b"PK":
        return "docx"
    raise ToolInputError(
        f"The file is {ctype}, which cannot be read here. HTML, plain text, PDF and Word (.docx) work; "
        "look for another source of the same content."
    )


def pdf_pages(data: bytes) -> list[str]:
    """The text of every page, in order ('' for a page without a text layer)."""
    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            return [page.get_text("text").strip() for page in document]
    except Exception as exc:
        raise ToolInputError("The file could not be read as a PDF (damaged, encrypted or incomplete).") from exc


def docx_text(data: bytes) -> str:
    """Paragraphs and tables of a Word file in document order. Headings keep a '#' prefix and table
    rows are written as 'cell | cell | cell'."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ToolInputError("The file could not be read as a Word (.docx) document.") from exc

    lines: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            if style.lower().startswith("heading "):
                level = style.split(" ", 1)[1]
                text = f"{'#' * (int(level) if level.isdigit() else 1)} {text}"
            lines.append(text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)
