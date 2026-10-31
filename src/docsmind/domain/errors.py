"""Domain-level exceptions shared across ingestion, the CLI, and the server.

Kept separate from `documents.py` (pure data models) since these carry
behavior (a formatted message) rather than being serializable shapes.
"""

from __future__ import annotations


class LLMConfigurationError(Exception):
    """The configured LLM provider can't be used (missing API key, SDK not
    installed, unknown provider). The message says exactly what to fix."""


class AgentError(Exception):
    """The Mind could not produce an answer (message is safe to show)."""


class ConversationNotFoundError(AgentError):
    """The requested conversation id does not exist."""


class ToolInputError(Exception):
    """A tool call had bad arguments. The message is shown to the model so it
    can correct itself, so keep it short and actionable."""


class DocumentAlreadyExistsError(Exception):
    """Raised by `IngestionPipeline.run()` when a file's content hash exactly
    matches an already-ingested version. Re-ingesting identical bytes would
    just hit `document_versions.content_hash`'s unique constraint after
    wastefully re-parsing (re-extracting every image/table) — this is raised
    *before* parsing starts instead, so callers (CLI, HTTP API) can show a
    clear "this is already here" message rather than a raw integrity error.
    """

    def __init__(self, *, content_hash: str, existing_version: dict) -> None:
        self.content_hash = content_hash
        self.existing_version = existing_version

        title = existing_version.get("title", "?")
        collection = existing_version.get("collection", "?")
        label = existing_version.get("label", "?")
        version_id = existing_version.get("id", "?")

        super().__init__(
            f"This exact file is already ingested as '{title}' "
            f"({collection} / {label}, version {version_id}) — identical "
            "content, nothing to re-parse. Use a different file, or pass a "
            "new --version label only if the bytes actually changed."
        )
