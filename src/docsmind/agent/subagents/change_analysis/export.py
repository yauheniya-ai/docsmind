"""The change table as an Excel file: the 8 table columns, without the UI-only ones
(Verified, Proof, Approved)."""

from __future__ import annotations

import io
from collections.abc import Iterable

from docsmind.domain.changes import EXPORT_COLUMNS, ChangeRow

WIDTHS = (34, 34, 60, 12, 18, 50, 14, 28)


def rows_to_xlsx(rows: Iterable[ChangeRow]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Changes"
    sheet.append([label for _, label in EXPORT_COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E7E5E4")
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in rows:
        sheet.append([str(v) for v in row.export_values()])
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            # Text from documents is data: never let a cell turn into a formula.
            cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, width in enumerate(WIDTHS, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
