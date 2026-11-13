"""The report as a stand-alone document: Markdown (what the UI shows and the .md download) and Word.

`report_markdown` puts a title, the two documents and what the versions check saw in front of the
stored report, so the file says on its own what was compared. `markdown_to_docx` converts the
small Markdown subset the report uses (headings, bullet and numbered lists, bold, italic, code).
"""

from __future__ import annotations

import io
import re

from docsmind.domain.changes import ChangeAnalysis

TITLE = "Change analysis"


def report_markdown(analysis: ChangeAnalysis, current: str, previous: str) -> str | None:
    """-> the full report document, or None while the report is not written yet."""
    if not analysis.report:
        return None
    parts = [f"# {TITLE}"]
    if current and previous:
        parts.append(f"**A, current:** {current}\n\n**B, previous:** {previous}")
    if analysis.verdict_note.strip():
        parts.append(analysis.verdict_note.strip())
    parts.append(analysis.report.strip())
    return "\n\n".join(parts) + "\n"


# -- Word ------------------------------------------------------------------------------------------

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _runs(paragraph, text: str) -> None:
    from docx.shared import Pt

    for piece in _INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            paragraph.add_run(piece[2:-2]).bold = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            run.font.name = "Courier New"
            run.font.size = Pt(10)
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            paragraph.add_run(piece[1:-1]).italic = True
        else:
            paragraph.add_run(piece)


def _list_style(kind: str, depth: int) -> str:
    base = "List Bullet" if kind == "bullet" else "List Number"
    return base if depth == 0 else f"{base} {min(depth + 1, 3)}"


def markdown_to_docx(markdown: str) -> bytes:
    from docx import Document

    document = Document()
    pending: list[str] = []

    def flush() -> None:
        if pending:
            _runs(document.add_paragraph(), " ".join(pending))
            pending.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            document.add_heading(re.sub(r"[*`]", "", heading.group(2)), level=0 if level == 1 else min(level - 1, 4))
            continue
        item = _BULLET.match(line)
        kind = "bullet"
        if not item:
            item = _NUMBERED.match(line)
            kind = "number"
        if item:
            flush()
            depth = min(len(item.group(1).replace("\t", "  ")) // 2, 2)
            _runs(document.add_paragraph(style=_list_style(kind, depth)), item.group(2))
            continue
        pending.append(line.strip())
    flush()

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
