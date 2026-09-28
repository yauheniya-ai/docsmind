"""Read-back commands for what `docsmind ingest` actually saved to Postgres.

`docsmind versions` — find a version_id (you need this for the three below)
`docsmind fulltext <version_id> [--full]` — the saved whole-document text
`docsmind outline <version_id>` — the saved section tree (only if the PDF had a TOC)
`docsmind chunks <version_id> [--page N] [--full]` — the saved page-based chunks

These read from the database rather than re-parsing the file — this is how
you check that what's in Postgres actually matches what the parser produced,
without hand-writing SQL joins each time.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import typer
from rich.console import Console

console = Console()

_PREVIEW_CHARS = 500


def versions() -> None:
    """List every ingested document version, with its saved stats and chunk count.

    Deliberately not a single wide table — a UUID in a table cell gets
    truncated with an ellipsis whenever the terminal is too narrow to fit
    every column, which makes it uncopyable. Printing the id alone on its
    own line means it's always the full 36 characters, regardless of
    terminal width.
    """

    async def _run() -> None:
        from docsmind.adapters.storage.postgres.repository import PostgresRepository
        from docsmind.config import DocsmindConfig

        repo = PostgresRepository(DocsmindConfig())
        rows = await repo.list_versions()

        if not rows:
            console.print(
                "[yellow]No versions ingested yet.[/yellow] "
                "Run `docsmind ingest <file> --collection ...` first."
            )
            return

        for row in rows:
            count = await repo.count_chunks(row["id"])
            words = row["word_count"]
            stats = (
                f"{words:,} words, {count} chunks"
                if words is not None
                else f"{count} chunks (no stats — re-ingest to backfill)"
            )
            console.print(
                f"[bold]{row['title']}[/bold]  "
                f"([cyan]{row['collection']}[/cyan] / {row['label']}, {stats})"
            )
            console.print(f"  {row['id']}")
            console.print()

    asyncio.run(_run())


def fulltext(
    version_id: UUID = typer.Argument(..., help="A version id from `docsmind versions`."),
    full: bool = typer.Option(
        False, "--full", help=f"Print the entire text instead of a {_PREVIEW_CHARS}-char preview."
    ),
) -> None:
    """Print the saved whole-document text for one version."""

    async def _run() -> None:
        from docsmind.adapters.storage.postgres.repository import PostgresRepository
        from docsmind.config import DocsmindConfig

        repo = PostgresRepository(DocsmindConfig())
        text = await repo.get_full_text(version_id)

        if not text:
            console.print(
                f"[yellow]No full text saved for version {version_id}.[/yellow] "
                "(Ingested before full-text saving was added? Re-ingest to backfill.)"
            )
            return

        if full:
            console.print(text)
        else:
            preview = text[:_PREVIEW_CHARS]
            suffix = "..." if len(text) > _PREVIEW_CHARS else ""
            console.print(preview + suffix)
            console.print()
            console.print(f"[dim]{len(text):,} chars total — use --full to print everything[/dim]")

    asyncio.run(_run())


def outline(
    version_id: UUID = typer.Argument(..., help="A version id from `docsmind versions`."),
) -> None:
    """Print the saved section tree for one version — empty if the source PDF had no table of contents."""

    async def _run() -> None:
        from docsmind.adapters.storage.postgres.repository import PostgresRepository
        from docsmind.config import DocsmindConfig

        repo = PostgresRepository(DocsmindConfig())
        sections = await repo.get_sections(version_id)

        if not sections:
            console.print(
                f"[yellow]No outline for version {version_id}.[/yellow] "
                "This PDF has no table of contents/bookmarks — see `docsmind fulltext` instead."
            )
            return

        by_id = {s["id"]: s for s in sections}
        for s in sections:
            depth = 0
            parent_id = s["parent_id"]
            while parent_id is not None:
                depth += 1
                parent_id = by_id[parent_id]["parent_id"]
            indent = "  " * depth
            heading = s["heading"] or "(untitled root)"
            page_start, page_end = s["page_start"], s["page_end"]
            pages = f"p.{page_start}" if page_start == page_end else f"p.{page_start}-{page_end}"
            console.print(f"{indent}[{s['path']}] {heading}  ({pages})")

    asyncio.run(_run())


def chunks(
    version_id: UUID = typer.Argument(..., help="A version id from `docsmind versions`."),
    page: int = typer.Option(None, "--page", "-p", help="Only show chunks from this page number."),
    full: bool = typer.Option(False, "--full", help="Print full chunk content instead of a 200-char preview."),
) -> None:
    """Print the saved chunks for one version — page-based, so most pages are
    exactly one chunk; a dense page may show up as several."""

    async def _run() -> None:
        from docsmind.adapters.storage.postgres.repository import PostgresRepository
        from docsmind.config import DocsmindConfig

        repo = PostgresRepository(DocsmindConfig())
        rows = await repo.get_chunks(version_id, page=page)

        if not rows:
            what = f"page {page}" if page is not None else f"version {version_id}"
            console.print(f"[yellow]No chunks found for {what}.[/yellow]")
            return

        for row in rows:
            label = f"p.{row['page_start']}"
            if row["chunk_type"] == "image":
                console.print(f"[{label}] IMAGE chunk -> image_id={row['image_id']}")
                continue
            content = row["content"] or ""
            word_count = len(content.split())
            if full:
                console.print(f"\n[bold]\\[{label}][/bold] TEXT ({word_count} words):")
                console.print(content)
            else:
                preview = content[:200].replace("\n", " ")
                suffix = "..." if len(content) > 200 else ""
                console.print(f"[{label}] TEXT ({word_count} words): {preview}{suffix}")

    asyncio.run(_run())