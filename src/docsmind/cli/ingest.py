from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()


def ingest(
    path: Path = typer.Argument(..., exists=True, help="Path to a PDF/DOCX/text file."),
    collection: str = typer.Option(..., "--collection", "-c", help="Logical grouping, e.g. 'policies'."),
    version: str = typer.Option(
        None, "--version", "-v", help="Version label, e.g. '2025' or 'v2'. Defaults to today's date if omitted."
    ),
) -> None:
    from docsmind import Docsmind

    dm = Docsmind()
    result = dm.ingest(path, collection=collection, version=version)
    console.print(f"[green]Ingested[/green] {path.name} -> version {result.id} ({result.label})")