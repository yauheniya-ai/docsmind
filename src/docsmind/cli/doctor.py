"""`docsmind doctor` — checks the environment and prints exactly what to fix.

This is the single most important command for first-run experience: most
support issues for a Postgres-backed package are "it can't connect" or
"the vector extension isn't there", and both are checkable in code.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from docsmind.config import DocsmindConfig

console = Console()


def doctor() -> None:
    config = DocsmindConfig()
    results = Table(show_header=True, header_style="bold")
    results.add_column("Check")
    results.add_column("Status")
    results.add_column("Detail")

    _check_database(config, results)
    _check_vector_extension(config, results)
    _check_blob_store(config, results)
    _check_api_keys(config, results)

    console.print(results)


def _check_database(config: DocsmindConfig, results: Table) -> None:
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        results.add_row("Database driver", "[red]missing[/red]", "pip install docsmind (asyncpg is a core dep)")
        return

    # A real implementation opens a connection here with a short timeout.
    # Left as a TODO stub so this file has no network call at import time.
    provider = "Neon" if "neon.tech" in config.database_url else (
        "Supabase" if "supabase.co" in config.database_url else "self-hosted/Docker"
    )
    results.add_row("Database URL", "[yellow]unverified[/yellow]", f"detected provider: {provider}")


def _check_vector_extension(config: DocsmindConfig, results: Table) -> None:
    hint = "run `docsmind db upgrade` — migrations create the extension"
    if config.is_managed_postgres():
        hint += " (Neon/Supabase both support `CREATE EXTENSION vector`)"
    results.add_row("pgvector extension", "[yellow]unverified[/yellow]", hint)


def _check_blob_store(config: DocsmindConfig, results: Table) -> None:
    if config.blob_backend == "filesystem":
        from pathlib import Path

        root = Path(config.blob_root)
        root.mkdir(parents=True, exist_ok=True)
        results.add_row("Blob storage", "[green]ok[/green]", f"filesystem at {root.resolve()}")
    elif config.blob_backend == "s3":
        ok = bool(config.s3_bucket)
        status = "[green]ok[/green]" if ok else "[red]missing[/red]"
        results.add_row("Blob storage", status, f"s3 bucket: {config.s3_bucket or 'DOCSMIND_S3_BUCKET not set'}")
    elif config.blob_backend == "supabase":
        ok = bool(config.supabase_url and config.supabase_key)
        status = "[green]ok[/green]" if ok else "[red]missing[/red]"
        results.add_row("Blob storage", status, f"supabase bucket: {config.supabase_bucket}")
    else:
        results.add_row("Blob storage", "[red]unknown[/red]", f"unrecognized backend: {config.blob_backend}")


def _check_api_keys(config: DocsmindConfig, results: Table) -> None:
    needed = {config.llm_provider, config.embedding_provider, config.vision_provider}
    if "anthropic" in needed:
        status = "[green]ok[/green]" if config.anthropic_api_key else "[red]missing[/red]"
        results.add_row("ANTHROPIC_API_KEY", status, "required for llm_provider/vision_provider=anthropic")
    if "openai" in needed:
        status = "[green]ok[/green]" if config.openai_api_key else "[red]missing[/red]"
        results.add_row("OPENAI_API_KEY", status, "required for embedding/llm provider=openai")


if __name__ == "__main__":
    typer.run(doctor)
