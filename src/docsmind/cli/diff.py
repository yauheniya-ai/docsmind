from __future__ import annotations

from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def diff(
    version_a: UUID = typer.Argument(..., help="Older DocumentVersion id."),
    version_b: UUID = typer.Argument(..., help="Newer DocumentVersion id."),
) -> None:
    from docsmind import Docsmind

    dm = Docsmind()
    change_set = dm.compare(version_a, version_b)

    table = Table(title=f"Changes: {version_a} -> {version_b}")
    table.add_column("Section")
    table.add_column("Kind")
    table.add_column("Severity")
    table.add_column("Rationale")

    for change in change_set.changes:
        severity = change.impact.severity.value if change.impact else "-"
        rationale = change.impact.rationale if change.impact else "-"
        table.add_row(change.section_path, change.kind.value, severity, rationale)

    console.print(table)
