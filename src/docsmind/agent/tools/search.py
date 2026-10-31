"""Retrieval tools: search (ranked keywords) and grep (exact text)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from docsmind.agent.catalog import CatalogDoc
from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.domain.errors import ToolInputError
from docsmind.retrieval.keyword import build_tsquery, query_terms, snippet_around

_DOCUMENTS_HELP = "Document handles to restrict to, e.g. [\"D1\", \"D3\"]. Omit to use all documents."


def format_passage(doc: CatalogDoc, handle: str, page_start: int | None, page_end: int | None, text: str) -> str:
    if page_start is None:
        where = ""
    elif page_end in (None, page_start):
        where = f" · p.{page_start}"
    else:
        where = f" · p.{page_start}-{page_end}"
    return f"[{handle}] {doc.handle} · {doc.title} ({doc.collection} / {doc.label}){where}\n{text}"


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, description="Distinctive words to look for (not a full sentence).")
    documents: list[str] | None = Field(default=None, description=_DOCUMENTS_HELP)
    limit: int = Field(default=6, ge=1, le=10, description="Maximum passages to return.")


class SearchTool(Tool):
    name = "search"
    summary = "Ranked keyword search across the documents. Returns the best matching passages to cite."
    description = (
        "Ranked keyword search over the documents' text. Returns the best matching passages, each "
        "with a source handle like [S1] to cite. Matches are stemmed and any of the words may match; "
        "use read_pages on a hit to see the surrounding text."
    )
    Args = SearchArgs

    async def run(self, args: SearchArgs, ctx: ToolContext) -> ToolOutput:
        docs = ctx.catalog.resolve_many(args.documents)
        if not docs:
            return ToolOutput("There are no documents to search.", summary="no documents")
        tsquery = build_tsquery(args.query)
        if tsquery is None:
            raise ToolInputError("The query has no searchable words. Use specific terms.")

        rows = await ctx.repo.keyword_search(
            tsquery, version_ids=[d.version_id for d in docs], limit=args.limit
        )
        if not rows:
            return ToolOutput(
                f"No passages matched '{args.query}'. Try fewer or different words, synonyms, or grep for an exact phrase.",
                summary="no matches",
            )
        terms = query_terms(args.query)
        passages = []
        for row in rows:
            doc = ctx.catalog.by_version(row["version_id"])
            if doc is None:  # defence in depth: never surface out-of-scope rows
                continue
            snippet = snippet_around(row["content"], terms)
            citation = ctx.sources.add(
                chunk_id=row["chunk_id"],
                version_id=doc.version_id,
                title=doc.title,
                collection=doc.collection,
                label=doc.label,
                page_start=row["page_start"],
                page_end=row["page_end"],
                snippet=snippet,
            )
            passages.append(
                format_passage(doc, citation.handle or "S?", row["page_start"], row["page_end"], snippet)
            )
        return ToolOutput("\n\n".join(passages), summary=f"{len(passages)} passages")


class GrepArgs(BaseModel):
    text: str = Field(min_length=2, description="Exact text to find (case-insensitive), e.g. '4.2.1' or 'data subject'.")
    documents: list[str] | None = Field(default=None, description=_DOCUMENTS_HELP)
    limit: int = Field(default=8, ge=1, le=15, description="Maximum pages to return.")


class GrepTool(Tool):
    name = "grep"
    summary = "Finds an exact phrase, clause number or identifier, ignoring case. No stemming."
    description = (
        "Find an exact phrase, clause number or identifier (case-insensitive, literal match, no stemming). "
        "Returns the pages containing it, each with a source handle to cite."
    )
    Args = GrepArgs

    async def run(self, args: GrepArgs, ctx: ToolContext) -> ToolOutput:
        docs = ctx.catalog.resolve_many(args.documents)
        if not docs:
            return ToolOutput("There are no documents to search.", summary="no documents")
        rows = await ctx.repo.grep_chunks(
            args.text, version_ids=[d.version_id for d in docs], limit=args.limit
        )
        if not rows:
            return ToolOutput(f"'{args.text}' does not appear in the selected documents.", summary="no matches")

        needle = args.text.lower()
        passages = []
        for row in rows:
            doc = ctx.catalog.by_version(row["version_id"])
            if doc is None:
                continue
            content = row["content"]
            occurrences = content.lower().count(needle)
            snippet = snippet_around(content, [needle], width=500)
            citation = ctx.sources.add(
                chunk_id=row["chunk_id"],
                version_id=doc.version_id,
                title=doc.title,
                collection=doc.collection,
                label=doc.label,
                page_start=row["page_start"],
                page_end=row["page_end"],
                snippet=snippet,
            )
            extra = f"\n({occurrences} occurrences on this page)" if occurrences > 1 else ""
            passages.append(
                format_passage(doc, citation.handle or "S?", row["page_start"], row["page_end"], snippet) + extra
            )
        return ToolOutput("\n\n".join(passages), summary=f"{len(passages)} pages")
