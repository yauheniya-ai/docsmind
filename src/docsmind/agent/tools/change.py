"""Tools of the Change Analyzer: `page_vision`, `load_table` (subagent-only) and the Mind's `export_excel`."""

from __future__ import annotations

import asyncio
import csv
import io
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from docsmind.agent.subagents.change_analysis.export import rows_to_xlsx
from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.agent.tools.web import fetch_bytes
from docsmind.config import DocsmindConfig
from docsmind.domain.changes import Nature
from docsmind.domain.errors import ToolInputError

MAX_VISION_PAGES = 2
MAX_TABLE_ROWS = 5_000  # rows read per sheet
MAX_CELL_CHARS = 400
TABLE_PAGE_ROWS = 40


# -- page_vision ---------------------------------------------------------------------------


class PageVisionArgs(BaseModel):
    document: str = Field(description='Document handle, e.g. "D1".')
    pages: list[int] = Field(min_length=1, max_length=MAX_VISION_PAGES, description="Page numbers (1-based), at most 2.")
    question: str = Field(
        min_length=5,
        max_length=600,
        description="What to read off the page(s), e.g. 'Transcribe the table in section 4.2 and its footnotes.'",
    )


class PageVisionTool(Tool):
    name = "page_vision"
    subagent_only = True
    summary = "Looks at a page as an image and reads off what is asked, for tables, columns and layouts that text extraction garbles."
    description = (
        "Render page(s) of a PDF as images and have a vision model read them: use it when extracted text "
        "looks garbled or incomplete (multi-column layouts, tables, scanned pages) and you need to see what "
        "the page really says. Returns text transcribed from the image, without a source handle."
    )
    how_it_works = (
        "Renders the requested pages from the original PDF to images.",
        "Sends them with the question to the vision model, which transcribes the relevant text.",
        "The transcription is kept, so a verifier can later confirm a proof passage that came from it.",
        "Only offered when the vision provider's API key is set.",
    )
    Args = PageVisionArgs

    @classmethod
    def available(cls, config: DocsmindConfig) -> bool:
        key = config.anthropic_api_key if config.vision_provider == "anthropic" else config.openai_api_key
        return bool(key)

    async def run(self, args: PageVisionArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.vision is None:
            raise ToolInputError("Page vision is not configured. Work from the extracted text.")
        doc = ctx.catalog.resolve(args.document)
        pages = sorted(set(args.pages))
        if doc.page_count and pages[-1] > doc.page_count:
            raise ToolInputError(f"{doc.handle} has only {doc.page_count} pages.")
        if pages[0] < 1:
            raise ToolInputError("Pages start at 1.")

        version = await ctx.repo.get_version(doc.version_id)
        uri = (version or {}).get("blob_uri")
        if not uri:
            raise ToolInputError(f"The original file of {doc.handle} is not available.")
        pdf = await _read_blob(uri, ctx.config)
        images = await asyncio.to_thread(_render_pages, pdf, pages, ctx.config.change_vision_dpi)
        text = await ctx.vision.read(
            images,
            question=args.question,
            mime_type="image/png",
            max_tokens=ctx.config.change_vision_max_output_tokens,
        )
        if not text:
            return ToolOutput("The vision model returned nothing for those pages.", summary="no text")
        ctx.vision_log.append(text)
        span = ", ".join(str(p) for p in pages)
        return ToolOutput(
            f"Read from the image of {doc.handle} p.{span} (vision model; check it against the text):\n{text}",
            summary=f"{doc.handle} p.{span}",
        )


async def _read_blob(uri: str, config: DocsmindConfig) -> bytes:
    if config.blob_backend != "filesystem":
        raise ToolInputError("Page vision only supports the filesystem blob store.")
    from docsmind.adapters.blobs.filesystem import FilesystemBlobStore

    try:
        return await FilesystemBlobStore(config.blob_root).get(uri)
    except (OSError, ValueError) as exc:
        raise ToolInputError("The original file could not be read.") from exc


def _render_pages(pdf: bytes, pages: list[int], dpi: int) -> list[bytes]:
    import pymupdf

    out: list[bytes] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for number in pages:
            if number > document.page_count:
                raise ToolInputError(f"The file has only {document.page_count} pages.")
            out.append(document[number - 1].get_pixmap(dpi=dpi).tobytes("png"))
    return out


# -- load_table ------------------------------------------------------------------------------


class LoadTableArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="URL of an .xlsx or .csv file (e.g. a published changelog).")
    sheet: str | None = Field(default=None, description="Sheet name (xlsx). Omit for the first sheet; the sheet names are listed.")
    contains: str | None = Field(default=None, max_length=200, description="Only rows containing this text (case-insensitive).")
    offset: int = Field(default=0, ge=0, description="Skip this many matching rows, to continue a long table.")


class LoadTableTool(Tool):
    name = "load_table"
    subagent_only = True
    summary = "Reads a published spreadsheet or CSV (for example a changelog) and returns its rows as text, no model involved."
    description = (
        "Download a public .xlsx or .csv file and return its rows as text, the first row being the header. "
        f"Returns {TABLE_PAGE_ROWS} rows per call; pass `contains` to find rows by text, or `offset` to continue."
    )
    how_it_works = (
        "Downloads the file with the same safeguards as fetch_url (public addresses only, size limit).",
        "Parses it in code (openpyxl for xlsx, the csv module for csv); no model reads the file.",
        "Returns rows 40 at a time, optionally only those containing a text.",
    )
    Args = LoadTableArgs

    async def run(self, args: LoadTableArgs, ctx: ToolContext) -> ToolOutput:
        key = f"table:{args.url}"
        sheets: dict[str, list[list[str]]] | None = ctx.cache.get(key)
        if sheets is None:
            final_url, content_type, data, encoding = await fetch_bytes(
                args.url, ctx.config, allowed=None, max_bytes=ctx.config.change_table_max_bytes
            )
            sheets = await asyncio.to_thread(parse_table, data, final_url, content_type, encoding)
            ctx.cache[key] = sheets
        if not sheets:
            return ToolOutput("The file has no rows.", summary="empty")

        names = list(sheets)
        if args.sheet is not None and args.sheet not in sheets:
            raise ToolInputError(f"No sheet '{args.sheet}'. Sheets: {', '.join(names)}.")
        name = args.sheet or names[0]
        rows = sheets[name]
        header, body = rows[0], rows[1:]
        needle = (args.contains or "").lower()
        matching = [(i + 2, r) for i, r in enumerate(body) if not needle or needle in " ".join(r).lower()]
        page = matching[args.offset : args.offset + TABLE_PAGE_ROWS]
        lines = [f"Sheet '{name}' ({len(body)} rows; sheets: {', '.join(names)})", "header: " + " | ".join(header)]
        lines += [f"row {number}: " + " | ".join(cells) for number, cells in page]
        end = args.offset + len(page)
        if end < len(matching):
            lines.append(f"[Rows {args.offset + 1}-{end} of {len(matching)} matching. Call again with offset={end}.]")
        elif not page:
            lines.append("[No matching rows.]")
        return ToolOutput("\n".join(lines), summary=f"{len(page)} of {len(matching)} rows")


def parse_table(data: bytes, url: str, content_type: str, encoding: str) -> dict[str, list[list[str]]]:
    """xlsx or csv bytes -> {sheet name: rows of strings}. Rows are capped; empty rows dropped."""
    path = urlsplit(url).path.lower()
    is_xlsx = data[:2] == b"PK" and (path.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in content_type or "excel" in content_type)
    if is_xlsx or (data[:2] == b"PK" and not path.endswith(".csv")):
        return _parse_xlsx(data)
    if not (path.endswith((".csv", ".txt")) or content_type.startswith(("text/", "application/csv"))):
        raise ToolInputError(f"{url} does not look like a spreadsheet or CSV ({content_type or 'unknown type'}).")
    return _parse_csv(data, encoding)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:MAX_CELL_CHARS]


def _parse_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for bad files
        raise ToolInputError("The file could not be read as an Excel workbook.") from exc
    sheets: dict[str, list[list[str]]] = {}
    try:
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            for values in sheet.iter_rows(values_only=True):
                cells = [_cell(v) for v in values]
                if any(cells):
                    rows.append(cells)
                if len(rows) >= MAX_TABLE_ROWS:
                    break
            if rows:
                sheets[sheet.title] = rows
    finally:
        workbook.close()
    return sheets


def _parse_csv(data: bytes, encoding: str) -> dict[str, list[list[str]]]:
    try:
        text = data.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    sample = text[:4_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]
    rows: list[list[str]] = []
    for values in csv.reader(io.StringIO(text), dialect):
        cells = [_cell(v) for v in values]
        if any(cells):
            rows.append(cells)
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return {"data": rows} if rows else {}


# -- export_excel ------------------------------------------------------------------------------


class ExportExcelArgs(BaseModel):
    analysis_id: str | None = Field(default=None, description="Id of the change analysis. Omit for the most recent one.")
    only: Literal["all", "actionable"] = Field(
        default="all", description='"actionable": only rows with a normative compliance delta (new, modified, removed).'
    )
    natures: list[Literal["new", "modified", "editorial", "removed", "relocated"]] | None = Field(
        default=None, description="Only rows with these natures. Omit for all."
    )


class ExportExcelTool(Tool):
    name = "export_excel"
    summary = "Writes a stored change table to an Excel file and gives the user a download link."
    description = (
        "Export the change table of a comparison to an Excel (.xlsx) file and return a markdown download "
        "link to give to the user. The file has the table's 8 columns (not the verification details). "
        "Use when the user asks for the change table as Excel; you can limit it to actionable rows or some natures."
    )
    how_it_works = (
        "Loads the stored change table (the most recent comparison unless an id is given).",
        "Optionally keeps only actionable rows or chosen natures.",
        "Writes an .xlsx file under the blob folder and returns its download link.",
    )
    Args = ExportExcelArgs

    async def run(self, args: ExportExcelArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.changes is None:
            raise ToolInputError("Change tables are not available here.")
        analysis_id = await _analysis_id(args.analysis_id, ctx)
        rows = await ctx.changes.list_rows(analysis_id)
        if args.only == "actionable":
            rows = [r for r in rows if r.actionable]
        if args.natures:
            wanted = {Nature(n) for n in args.natures}
            rows = [r for r in rows if r.nature in wanted]
        if not rows:
            return ToolOutput("The table has no rows matching that filter, so nothing was exported.", summary="no rows")
        data = await asyncio.to_thread(rows_to_xlsx, rows)
        name = f"change-table-{str(analysis_id)[:8]}-{uuid4().hex[:6]}.xlsx"
        folder = Path(ctx.config.blob_root) / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((folder / name).write_bytes, data)
        return ToolOutput(
            f"Exported {len(rows)} rows: [Change table ({name})](/blobs/exports/{name})",
            summary=f"{len(rows)} rows",
        )


async def _analysis_id(value: str | None, ctx: ToolContext) -> UUID:
    if value:
        try:
            return UUID(value.strip())
        except ValueError as exc:
            raise ToolInputError("analysis_id is not a valid id.") from exc
    latest = await ctx.changes.list_analyses(limit=1)
    if not latest:
        raise ToolInputError("There is no change analysis yet. Compare two versions first.")
    return latest[0]["id"]
