"""`docsmind serve` — runs the FastAPI app (`server/app.py`) behind uvicorn,
for the `frontend/` UI to talk to. Requires the `server` extra:
`pip install "docsmind[server]"`.
"""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Start the docsmind API server used by the frontend UI."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - import guard
        console.print(
            "[red]uvicorn isn't installed.[/red] Run `pip install \"docsmind[server]\"` first."
        )
        raise typer.Exit(1) from exc

    console.print(f"[green]Starting docsmind server[/green] on http://{host}:{port}")
    uvicorn.run("docsmind.server.app:app", host=host, port=port, reload=reload)
