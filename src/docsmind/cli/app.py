"""`docsmind` CLI entry point. Kept thin — each subcommand module owns its
own logic so `docsmind.cli.app` never grows into a god-file.
"""

from __future__ import annotations

import typer

from docsmind.cli import db, doctor, ingest

app = typer.Typer(
    name="docsmind",
    help="Chat with your documents. Version-aware retrieval and change analysis.",
    no_args_is_help=True,
)

app.command("ingest")(ingest.ingest)
app.add_typer(db.app, name="db")
app.command("doctor")(doctor.doctor)

# `chat` and `diff` are registered as top-level commands (not sub-apps) since
# they're single actions, not command groups.
from docsmind.cli import chat as _chat
from docsmind.cli import diff as _diff
from docsmind.cli import inspect as _inspect
from docsmind.cli import serve as _serve

app.command("chat")(_chat.chat)
app.command("diff")(_diff.diff)
app.command("versions")(_inspect.versions)
app.command("fulltext")(_inspect.fulltext)
app.command("outline")(_inspect.outline)
app.command("chunks")(_inspect.chunks)
app.command("delete")(_inspect.delete)
app.command("serve")(_serve.serve)


def main() -> None:
    app()


if __name__ == "__main__":
    main()