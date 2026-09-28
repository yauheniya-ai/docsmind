from __future__ import annotations

import subprocess

import typer
from rich.console import Console

app = typer.Typer(help="Database lifecycle: local Docker Postgres and Alembic migrations.")
console = Console()


@app.command("up")
def up() -> None:
    """Start local Postgres+pgvector via docker compose. No-op if using a managed provider."""
    console.print("[bold]Starting docker compose (postgres + pgvector)...[/bold]")
    subprocess.run(["docker", "compose", "up", "-d"], check=True)


@app.command("upgrade")
def upgrade() -> None:
    """Apply Alembic migrations (creates the vector extension and schema)."""
    console.print("[bold]Applying migrations...[/bold]")
    subprocess.run(["alembic", "upgrade", "head"], check=True)


@app.command("psql")
def psql() -> None:
    """Open a psql shell against the configured database.

    Works against the local Docker container (`docsmind db up`) or against
    Neon/Supabase if you just want a quick look without a separate DB client —
    either way it reads DOCSMIND_DATABASE_URL, same as the app itself.
    """
    from docsmind.config import DocsmindConfig

    config = DocsmindConfig()
    # asyncpg URLs (`postgresql+asyncpg://...`) aren't what psql expects;
    # strip the driver suffix to get a plain libpq-style URL.
    url = config.database_url.replace("postgresql+asyncpg://", "postgresql://")
    subprocess.run(["psql", url], check=True)


if __name__ == "__main__":
    app()
