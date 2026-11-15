"""`create_file`: write any result as a downloadable file (txt, md, csv or xlsx).

A core tool of every agent. The model supplies text or a table; or, for the stored change table, a
`source` so hundreds of rows never pass through its context. Files go to `<blob_root>/exports/` and
the tool returns a markdown link, which the chat shows and the saved conversation keeps.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.domain.changes import Nature
from docsmind.domain.errors import ToolInputError

Cell = str | int | float | bool | None
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


class ChangeTableSource(BaseModel):
    change_table: str | None = Field(
        default=None, description="Id of the change analysis. Omit for the most recent one."
    )
    only: Literal["all", "actionable"] = Field(
        default="all", description='"actionable": only rows with a normative compliance delta (new, modified, removed).'
    )
    natures: list[Literal["new", "modified", "editorial", "removed", "relocated"]] | None = Field(
        default=None, description="Only rows with these natures. Omit for all."
    )


class Sheet(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    columns: list[str] = Field(min_length=1)
    rows: list[list[Cell]]


class CreateFileArgs(BaseModel):
    filename: str = Field(min_length=1, max_length=100, description="Base name without extension, e.g. 'retention-periods'.")
    format: Literal["txt", "md", "csv", "xlsx"]
    content: str | None = Field(default=None, description="The text, for txt and md.")
    columns: list[str] | None = Field(default=None, description="Header cells, for csv and xlsx.")
    rows: list[list[Cell]] | None = Field(default=None, description="Table rows (strings, numbers, booleans), for csv and xlsx.")
    sheets: list[Sheet] | None = Field(default=None, description="xlsx only: several sheets instead of columns and rows.")
    source: ChangeTableSource | None = Field(
        default=None,
        description="csv/xlsx only, instead of rows: write the stored change table exactly as the UI export does.",
    )


class CreateFileTool(Tool):
    name = "create_file"
    summary = "Writes a result as a downloadable file: text, Markdown, CSV or Excel, and gives the user the link."
    description = (
        "Create a file the user can download: txt or md (pass content), csv or xlsx (pass columns and rows, or "
        "sheets for several Excel sheets). To export the stored change table of a comparison, pass "
        "source={} (optionally only='actionable' or natures) instead of rows. Returns a markdown link: "
        "give it to the user as is. Use it when the user asks for results as a file, table or Excel."
    )
    how_it_works = (
        "Checks that the arguments fit the format and the size limits.",
        "Writes the file under the blob folder (exports); spreadsheet cells that start with = + - or @ are stored as text.",
        "For the stored change table, writes the same file as the UI's export button.",
        "Returns a download link for the chat.",
    )
    Args = CreateFileArgs

    async def run(self, args: CreateFileArgs, ctx: ToolContext) -> ToolOutput:
        cfg = ctx.config
        data, detail = await self._build(args, ctx)
        if len(data) > cfg.create_file_max_bytes:
            raise ToolInputError(
                f"The file would be {len(data) / 1e6:.1f} MB; the limit is {cfg.create_file_max_bytes / 1e6:.0f} MB. Write less."
            )
        name = f"{_slug(args.filename)}-{uuid4().hex[:6]}.{args.format}"
        folder = Path(cfg.blob_root) / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((folder / name).write_bytes, data)
        return ToolOutput(f"Created {detail}: [{args.filename} ({name})](/blobs/exports/{name})", summary=f"{name}, {detail}")

    async def _build(self, args: CreateFileArgs, ctx: ToolContext) -> tuple[bytes, str]:
        fmt = args.format
        tabular = args.columns is not None or args.rows is not None or args.sheets or args.source
        if fmt in ("txt", "md"):
            if not args.content:
                raise ToolInputError(f"{fmt} needs content (the text). Columns, rows and sources are for csv and xlsx.")
            if tabular:
                raise ToolInputError(f"{fmt} takes only content; use csv or xlsx for a table.")
            return args.content.encode("utf-8"), f"{fmt} file"
        if args.content:
            raise ToolInputError(f"{fmt} takes columns and rows (or a source), not content.")

        if args.source is not None:
            if fmt != "xlsx":
                rows = await self._change_rows(args.source, ctx)
                return _csv(
                    [label for _, label in _export_columns()], [[str(v) for v in r.export_values()] for r in rows]
                ), f"{len(rows)} rows"
            return await self._change_xlsx(args.source, ctx)

        if args.sheets:
            if fmt != "xlsx":
                raise ToolInputError("sheets are for xlsx; for csv pass columns and rows.")
            self._check_cells(sum(len(s.rows) * len(s.columns) for s in args.sheets), ctx)
            return _xlsx([(s.name, s.columns, s.rows) for s in args.sheets]), f"{len(args.sheets)} sheets"
        if not args.columns or args.rows is None:
            raise ToolInputError(f"{fmt} needs columns and rows (a list of lists), or source for the change table.")
        width = len(args.columns)
        for index, row in enumerate(args.rows):
            if len(row) != width:
                raise ToolInputError(f"Row {index + 1} has {len(row)} cells but there are {width} columns.")
        self._check_cells(width * len(args.rows), ctx)
        if fmt == "csv":
            return _csv(args.columns, args.rows), f"{len(args.rows)} rows"
        return _xlsx([("Data", args.columns, args.rows)]), f"{len(args.rows)} rows"

    @staticmethod
    def _check_cells(cells: int, ctx: ToolContext) -> None:
        if cells > ctx.config.create_file_max_cells:
            raise ToolInputError(
                f"{cells:,} cells is more than the limit of {ctx.config.create_file_max_cells:,}. Write fewer rows or columns."
            )

    @staticmethod
    async def _change_rows(source: ChangeTableSource, ctx: ToolContext) -> list:
        if ctx.changes is None:
            raise ToolInputError("Change tables are not available here.")
        analysis_id = await _analysis_id(source.change_table, ctx)
        rows = await ctx.changes.list_rows(analysis_id)
        if source.only == "actionable":
            rows = [r for r in rows if r.actionable]
        if source.natures:
            wanted = {Nature(n) for n in source.natures}
            rows = [r for r in rows if r.nature in wanted]
        if not rows:
            raise ToolInputError("The table has no rows matching that filter, so nothing was exported.")
        return rows

    async def _change_xlsx(self, source: ChangeTableSource, ctx: ToolContext) -> tuple[bytes, str]:
        from docsmind.agent.subagents.change_analysis.export import rows_to_xlsx

        rows = await self._change_rows(source, ctx)
        return await asyncio.to_thread(rows_to_xlsx, rows), f"{len(rows)} rows of the change table"


def _export_columns() -> tuple[tuple[str, str], ...]:
    from docsmind.domain.changes import EXPORT_COLUMNS

    return EXPORT_COLUMNS


async def _analysis_id(value: str | None, ctx: ToolContext) -> UUID:
    if value:
        try:
            return UUID(value.strip())
        except ValueError as exc:
            raise ToolInputError("The change analysis id is not valid.") from exc
    latest = await ctx.changes.list_analyses(limit=1)
    if not latest:
        raise ToolInputError("There is no change analysis yet.")
    return UUID(str(latest[0]["id"]))


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60].strip("-") or "file"


def _safe(value: Cell) -> Any:
    """A cell that a spreadsheet could read as a formula gets a leading apostrophe in CSV."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return "" if value is None else value


def _csv(columns: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_safe(c) for c in columns])
    for row in rows:
        writer.writerow([_safe(v) for v in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")  # BOM: Excel opens it as UTF-8


def _xlsx(sheets: list[tuple[str, list[str], list[list[Cell]]]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from docsmind.agent.subagents.change_analysis.export import ACCENT

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, columns, rows in sheets:
        sheet = workbook.create_sheet(re.sub(r"[\[\]:*?/\\]", " ", name)[:31] or "Data")
        sheet.append(list(columns))
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=ACCENT)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for row in rows:
            sheet.append(list(row))
        for line in sheet.iter_rows(min_row=1):
            for cell in line:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # data, never a formula
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, column in enumerate(columns, start=1):
            longest = max([len(str(column))] + [len(str(r[index - 1])) for r in rows[:200] if index - 1 < len(r)])
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(60, max(10, longest + 2))
        sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
