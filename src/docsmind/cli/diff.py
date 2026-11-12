from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

console = Console()


def diff(
    version_a: UUID = typer.Argument(..., help="DocumentVersion id of one of the two versions."),
    version_b: UUID = typer.Argument(..., help="DocumentVersion id of the other version (order does not matter)."),
    only_actionable: bool = typer.Option(False, "--actionable", help="Show only normative compliance changes."),
    xlsx: Path | None = typer.Option(None, "--xlsx", help="Also write the change table to this Excel file."),
) -> None:
    """Compare two versions of a document: a change table plus a written report."""
    from docsmind import Docsmind
    from docsmind.agent.subagents.change_analysis.export import rows_to_xlsx

    result = Docsmind().compare(version_a, version_b)
    if result.analysis is None:
        console.print(result.message)
        return

    rows = [r for r in result.rows if r.actionable] if only_actionable else result.rows
    table = Table(title=f"Changes: {result.analysis.id} ({result.analysis.mode})", show_lines=True)
    for column in ("Current clause (A)", "Previous clause (B)", "Nature", "Change", "Compliance requirement", "Binding", "Verified"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row.current_clause,
            row.previous_clause,
            row.nature.value,
            row.change_description,
            row.compliance_requirement,
            row.binding.value,
            "yes" if row.verified else "no",
        )
    console.print(table)
    console.print(Markdown(result.analysis.report or result.message))
    if xlsx is not None:
        xlsx.write_bytes(rows_to_xlsx(rows))
        console.print(f"Wrote {xlsx}")
