"""Document tools: list, outline, read pages."""

from __future__ import annotations

from pydantic import BaseModel, Field

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.agent.tools.search import format_passage
from docsmind.domain.errors import ToolInputError

MAX_PAGES_PER_READ = 4
MAX_OUTLINE_ENTRIES = 200


class ListDocumentsArgs(BaseModel):
    pass


class ListDocumentsTool(Tool):
    name = "list_documents"
    description = "List the documents in the register with their handles, collection, version label, page and word counts."
    Args = ListDocumentsArgs

    async def run(self, args: ListDocumentsArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(ctx.catalog.render(), summary=f"{len(ctx.catalog)} documents")


class OutlineArgs(BaseModel):
    document: str = Field(description="Document handle, e.g. \"D1\".")


class OutlineTool(Tool):
    name = "get_outline"
    description = (
        "The document's table of contents (from the PDF's own bookmarks) with page numbers. "
        "Use it to find which pages cover a topic, then read_pages."
    )
    Args = OutlineArgs

    async def run(self, args: OutlineArgs, ctx: ToolContext) -> ToolOutput:
        doc = ctx.catalog.resolve(args.document)
        sections = await ctx.repo.get_sections(doc.version_id)
        if not sections:
            return ToolOutput(
                f"{doc.handle} has no table of contents. Use search or read_pages instead.",
                summary="no outline",
            )
        lines = []
        for section in sections[:MAX_OUTLINE_ENTRIES]:
            depth = section["path"].count(".") if section.get("path") else 0
            number = f"{section['number']} " if section.get("number") else ""
            page = f" — p.{section['page_start']}" if section.get("page_start") else ""
            lines.append(f"{'  ' * depth}{number}{section.get('heading') or '(untitled)'}{page}")
        more = len(sections) - MAX_OUTLINE_ENTRIES
        if more > 0:
            lines.append(f"… and {more} more entries")
        return ToolOutput(f"Outline of {doc.handle} · {doc.title}\n" + "\n".join(lines), summary=f"{len(sections)} entries")


class ReadPagesArgs(BaseModel):
    document: str = Field(description="Document handle, e.g. \"D1\".")
    start_page: int = Field(ge=1, description="First page to read (1-based).")
    end_page: int | None = Field(
        default=None, ge=1, description=f"Last page (inclusive). Defaults to start_page; at most {MAX_PAGES_PER_READ} pages per call."
    )


class ReadPagesTool(Tool):
    name = "read_pages"
    description = (
        "Read the full text of one or a few consecutive pages of a document. Each chunk comes with a "
        "source handle to cite."
    )
    Args = ReadPagesArgs

    async def run(self, args: ReadPagesArgs, ctx: ToolContext) -> ToolOutput:
        doc = ctx.catalog.resolve(args.document)
        end = args.end_page or args.start_page
        if end < args.start_page:
            raise ToolInputError("end_page must be greater than or equal to start_page.")
        if end - args.start_page + 1 > MAX_PAGES_PER_READ:
            raise ToolInputError(f"Read at most {MAX_PAGES_PER_READ} pages per call; split the range.")
        if doc.page_count and args.start_page > doc.page_count:
            raise ToolInputError(f"{doc.handle} has only {doc.page_count} pages.")

        rows = await ctx.repo.get_chunks_for_pages(doc.version_id, args.start_page, end)
        rows = [r for r in rows if (r.get("content") or "").strip()]
        if not rows:
            return ToolOutput(
                f"No text on pages {args.start_page}-{end} of {doc.handle} (it may be an image-only page).",
                summary="no text",
            )
        passages = []
        for row in rows:
            citation = ctx.sources.add(
                chunk_id=row["chunk_id"],
                version_id=doc.version_id,
                title=doc.title,
                collection=doc.collection,
                label=doc.label,
                page_start=row["page_start"],
                page_end=row["page_end"],
                snippet=row["content"][:300],
            )
            passages.append(
                format_passage(doc, citation.handle or "S?", row["page_start"], row["page_end"], row["content"].strip())
            )
        span = f"p.{args.start_page}" if end == args.start_page else f"p.{args.start_page}-{end}"
        return ToolOutput("\n\n".join(passages), summary=f"{doc.handle} {span}")
