"""Citations are first-class, not an afterthought — every answer should be
traceable back to the exact chunk(s) that support it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: UUID
    document_title: str
    version_label: str
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str
    image_id: UUID | None = None  # set when the citation points at an image chunk
    # Set by the agent: the short handle the model cites with (e.g. "S3"), and
    # what the UI needs to link back to the document.
    handle: str | None = None
    version_id: UUID | None = None
    collection: str | None = None

    def format_short(self) -> str:
        loc = f"p.{self.page_start}" if self.page_start else ""
        if self.section_path:
            loc = f"§{self.section_path} {loc}".strip()
        return f"{self.document_title} ({self.version_label}) {loc}".strip()
