"""On-demand enrichment commands.

`docsmind embed` and `docsmind caption` show an estimate first and only run after
explicit confirmation (`--yes` skips the prompt for automation).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import typer
from rich.console import Console

from docsmind.adapters.storage.postgres.repository import PostgresRepository
from docsmind.config import DocsmindConfig
from docsmind.enrichment.service import EnrichmentService

console = Console()


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:,.4f}"


def _scope(version_id: UUID | None) -> list[UUID] | None:
    return [version_id] if version_id else None


async def _run(kind: str, version_id: UUID | None, yes: bool) -> None:
    config = DocsmindConfig()
    service = EnrichmentService(config, PostgresRepository(config))
    reason = service.readiness(kind)
    if reason:
        console.print(f"[red]{reason}[/red]")
        raise typer.Exit(code=1)

    estimate = await service.estimate(kind, _scope(version_id))
    console.print(f"[bold]{kind}[/bold] via [cyan]{estimate['provider']}[/cyan] model [cyan]{estimate['model']}[/cyan]")
    console.print(f"Items: [bold]{estimate['items']}[/bold]")
    console.print(f"Input tokens: [bold]{estimate['input_tokens']:,}[/bold]")
    if kind == "captions":
        console.print(
            "Output tokens: "
            f"[bold]{estimate['output_tokens_low']:,}[/bold] - [bold]{estimate['output_tokens_high']:,}[/bold]"
        )
    if estimate["cost_usd_high"] != estimate["cost_usd_low"]:
        console.print(
            "Estimated cost: "
            f"[bold]{_fmt_usd(estimate['cost_usd_low'])} - {_fmt_usd(estimate['cost_usd_high'])}[/bold]"
        )
    else:
        console.print(f"Estimated cost: [bold]{_fmt_usd(estimate['cost_usd_low'])}[/bold]")
    console.print(f"[dim]{estimate['note']}[/dim]")

    if estimate["items"] <= 0:
        console.print("[green]Nothing to do.[/green]")
        return
    if not yes and not typer.confirm("Proceed?"):
        console.print("Cancelled.")
        return

    def progress(done: int, total: int, message: str) -> None:
        total = max(total, done, 1)
        console.print(f"[dim]{done}/{total}[/dim] {message}")

    result = await service.run(kind, _scope(version_id), on_progress=progress)
    status = "cancelled" if result["cancelled"] else "done"
    console.print(
        f"[green]{status}[/green]: processed={result['processed']}, skipped={result['skipped']}, failed={result['failed']}"
    )
    if result["errors"]:
        console.print("[yellow]Sample errors:[/yellow]")
        for line in result["errors"]:
            console.print(f" - {line}")


def embed(
    version_id: UUID | None = typer.Option(None, "--version-id", help="Limit to one document version."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation and run immediately."),
) -> None:
    """Create missing embeddings on demand."""

    asyncio.run(_run("embeddings", version_id, yes))


def caption(
    version_id: UUID | None = typer.Option(None, "--version-id", help="Limit to one document version."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation and run immediately."),
) -> None:
    """Create missing image captions on demand."""

    asyncio.run(_run("captions", version_id, yes))
