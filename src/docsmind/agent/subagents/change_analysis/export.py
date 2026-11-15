"""The change table as an Excel file: the 8 table columns, without the UI-only ones
(Verified, Proof, Approved), plus an "Info" sheet about the run (documents, date, time, tokens,
cost, models)."""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Iterable, Sequence

from docsmind.domain.changes import EXPORT_COLUMNS, ChangeAnalysis, ChangeRow
from docsmind.pricing import usd

WIDTHS = (34, 34, 60, 12, 18, 50, 14, 28)
INFO_WIDTHS = (28, 90)
ACCENT = "D04725"  # the UI accent, hsl(12 70% 48%)

InfoRows = Sequence[tuple[str, str]]


def export_filename(
    analysis: ChangeAnalysis, current_title: str, *, kind: str = "change-table", ext: str = "xlsx"
) -> str:
    """yyyymmdd-doc-a-title-change-table.xlsx: the day of the analysis and the title of document A
    (the current version), reduced to ASCII so it is safe in a download header. The report is
    `...-change-report.docx` / `.md`."""
    ascii_title = unicodedata.normalize("NFKD", current_title or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60].strip("-") or "document"
    return f"{analysis.created_at:%Y%m%d}-{slug}-{kind}.{ext}"


def _duration(seconds: int) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def info_rows(
    analysis: ChangeAnalysis, current_title: str, previous_title: str, rows: Sequence[ChangeRow]
) -> list[tuple[str, str]]:
    """What the "Info" sheet lists, as (label, value) pairs. Everything comes from what the run stored."""
    meta = analysis.meta or {}
    out: list[tuple[str, str]] = [
        ("Document A (current)", current_title),
        ("Document B (previous)", previous_title),
        ("Date", f"{analysis.created_at:%Y-%m-%d %H:%M} UTC"),
    ]
    if "seconds" in meta:
        out.append(("How long", _duration(meta["seconds"])))
    if "input_tokens" in meta:
        output = meta.get("output_tokens", 0)
        out += [
            ("Tokens", f"{meta['input_tokens'] + output:,}"),
            ("  input", f"{meta['input_tokens']:,}"),
            ("  output", f"{output:,}"),
        ]
    cost = meta.get("cost") or {}
    if cost.get("models"):
        head = usd(cost.get("usd", 0.0))
        if not cost.get("complete"):
            head = f"at least {head} (a model has no price in the table)"
        elif cost.get("upper_bound"):
            head = f"at most {head} (no cached price configured for a model)"
        out.append(("Cost", head))
        if cost.get("usd_without_cache"):
            out.append(("  without caching", usd(cost["usd_without_cache"])))
        out.append(("  cache hit rate", f"{cost.get('cache_hit_rate', 0.0):.0%}"))
        for m in cost["models"]:
            if m["usd"] is None:
                out.append((f"  {m['model']}", f"{m['input_tokens']:,} input and {m['output_tokens']:,} output tokens, not priced"))
                continue
            used = (
                f"{m.get('uncached_input_tokens', m['input_tokens']):,} uncached + {m.get('cached_read_tokens', 0):,} cached read + "
                f"{m.get('cache_write_tokens', 0):,} cache write + {m['output_tokens']:,} output"
            )
            out.append((f"  {m['model']}", f"{used} = {usd(m['usd'])}"))
        for t in cost.get("tools") or []:
            if t["usd"] > 0:
                out.append((f"  tool: {t['tool']}", usd(t["usd"])))
        out.append(("Cost basis", "List prices with cache discounts; page vision tokens are not included."))
    for stage, model in (meta.get("models") or {}).items():
        out.append((f"Model: {stage}", model))
    if meta.get("model_calls") is not None:
        out.append(("Model calls", f"{meta['model_calls']} ({meta.get('tool_steps', 0)} in tool loops)"))
    if meta.get("vision_reads"):
        out.append(("Page vision reads", str(meta["vision_reads"])))
    ratio = f" (diff ratio {analysis.diff_ratio:.0%})" if analysis.diff_ratio is not None else ""
    out += [
        ("Mode", f"{analysis.mode}{ratio}"),
        ("Rows in the table", str(len(rows))),
        ("Verified rows", str(sum(1 for r in rows if r.verified))),
        ("Compliance-relevant rows", str(sum(1 for r in rows if r.actionable))),
        ("Status", analysis.status),
        ("Analysis id", str(analysis.id)),
    ]
    return out


def rows_to_xlsx(rows: Iterable[ChangeRow], info: InfoRows | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    def style_header(sheet) -> None:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=ACCENT)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Changes"
    sheet.append([label for _, label in EXPORT_COLUMNS])
    style_header(sheet)
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

    if info:
        details = workbook.create_sheet("Info")
        details.append(["Item", "Value"])
        style_header(details)
        for label, value in info:
            details.append([label, value])
        for line in details.iter_rows(min_row=2):
            for cell in line:
                cell.data_type = "s"  # document titles are data too
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            line[0].font = Font(bold=True)
        for index, width in enumerate(INFO_WIDTHS, start=1):
            details.column_dimensions[details.cell(row=1, column=index).column_letter].width = width
        details.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
