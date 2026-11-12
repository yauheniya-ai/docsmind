"""Retrieval tools: search (ranked keywords) and grep (exact text)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from docsmind.agent.catalog import CatalogDoc
from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.domain.errors import ToolInputError
from docsmind.retrieval.hybrid import hybrid_rows
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
    query: str = Field(
        min_length=2,
        max_length=1000,
        description=(
            "Natural-language query: a question or a descriptive phrase. "
            "Use grep instead for exact wording, identifiers or clause numbers."
        ),
    )
    documents: list[str] | None = Field(default=None, description=_DOCUMENTS_HELP)
    limit: int = Field(default=6, ge=1, le=10, description="Maximum passages to return.")


class SearchTool(Tool):
    name = "search"
    summary = (
        "Hybrid search over the documents: stemmed keyword matching plus semantic (embedding) similarity, "
        "fused by reciprocal rank fusion. Returns the best matching passages to cite."
    )
    description = (
        "Find passages by topic or meaning in the documents' text and figure captions. Takes a "
        "natural-language query; matches keywords and paraphrases, ranked best first. Returns passages "
        "with a source handle like [S1] to cite; use read_pages to see the surrounding text. "
    )
    how_it_works = (
        "Keyword search: the query is stemmed and matched with Postgres full-text search.",
        "Semantic search: the query is embedded and the nearest passages are found by cosine similarity (skipped if the documents are not embedded).",
        "Reciprocal rank fusion: each list scores a passage 1 / (60 + rank) and the scores are added, so passages ranked well by both come first.",
        "The top passages come back with source handles to cite.",
    )
    Args = SearchArgs

    async def run(self, args: SearchArgs, ctx: ToolContext) -> ToolOutput:
        docs = ctx.catalog.resolve_many(args.documents)
        if not docs:
            return ToolOutput("There are no documents to search.", summary="no documents")
        tsquery = build_tsquery(args.query)
        if tsquery is None:
            raise ToolInputError("The query has no searchable words. Use specific terms.")

        rows, mode = await hybrid_rows(
            ctx.repo,
            ctx.embedder,
            args.query,
            tsquery,
            version_ids=[d.version_id for d in docs],
            limit=args.limit,
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
            if row.get("chunk_type") == "image":
                snippet = f"[Figure on this page, AI caption] {snippet}"
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
        return ToolOutput("\n\n".join(passages), summary=f"{len(passages)} passages ({mode})")


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
    how_it_works = (
        "Escapes the text and runs a case-insensitive substring match (ILIKE) over every page of the chosen documents.",
        "No stemming and no ranking: only literal matches, in document and page order.",
        "Returns a snippet around the first hit on each page, with the number of occurrences and a source handle.",
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
