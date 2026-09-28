from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def chat(
    question: str = typer.Argument(None, help="Ask once and exit. Omit for an interactive REPL."),
    collection: str = typer.Option(None, "--collection", "-c"),
) -> None:
    from docsmind import Docsmind

    dm = Docsmind()

    if question:
        console.print(dm.chat(question, collection=collection))
        return

    console.print("[bold]The Mind[/bold] — type a question, or 'exit' to quit.")
    while True:
        q = typer.prompt(">")
        if q.strip().lower() in {"exit", "quit"}:
            break
        console.print(dm.chat(q, collection=collection))
