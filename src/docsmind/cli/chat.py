from __future__ import annotations

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

console = Console()


def _print_answer(answer: str, citations: list[dict]) -> None:
    console.print(Markdown(answer))
    if citations:
        console.print("\n[dim]Sources[/dim]")
        for c in citations:
            pages = ""
            if c.get("page_start") is not None:
                end = c.get("page_end")
                pages = f" p.{c['page_start']}" + (f"-{end}" if end and end != c["page_start"] else "")
            console.print(f"[dim]  {escape('[' + str(c.get('handle', '?')) + ']')} {escape(str(c.get('document_title', '')))}{pages}[/dim]")


def chat(
    question: str = typer.Argument(None, help="Ask once and exit. Omit for an interactive REPL."),
    collection: str = typer.Option(None, "--collection", "-c", help="Limit answers to one collection."),
) -> None:
    from docsmind import Docsmind

    dm = Docsmind()

    if question:
        result = dm.chat_result(question, collection=collection)
        _print_answer(result.answer, result.citations)
        return

    console.print("[bold]The Mind[/bold]: type a question, or 'exit' to quit.")
    conversation_id = None
    while True:
        q = typer.prompt(">")
        if q.strip().lower() in {"exit", "quit"}:
            break
        result = dm.chat_result(q, collection=collection, conversation_id=conversation_id)
        conversation_id = result.conversation_id
        _print_answer(result.answer, result.citations)
