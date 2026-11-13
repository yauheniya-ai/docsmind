import type { ChatCitation } from "./api"

export function pageLabel(c: Pick<ChatCitation, "page_start" | "page_end">): string {
  if (c.page_start == null) return ""
  return c.page_end == null || c.page_end === c.page_start ? `p.${c.page_start}` : `p.${c.page_start}-${c.page_end}`
}

/** "Retention Policy (2024) · p.12" */
export function citationLabel(c: ChatCitation): string {
  const page = pageLabel(c)
  return [`${c.document_title} (${c.version_label})`, page].filter(Boolean).join(" · ")
}

/** A page of a document version, for the source panel (no handle: it is not a chat source). */
export function pageCitation(
  version: { version_id: string; title: string; label: string },
  page: number | null,
  snippet = "",
): ChatCitation {
  return {
    handle: "",
    chunk_id: "",
    version_id: version.version_id,
    document_title: version.title,
    version_label: version.label,
    collection: null,
    page_start: page,
    page_end: page,
    snippet,
  }
}

/** Deep link into the document viewer, at the cited page when known. */
export function citationHref(c: ChatCitation): string | null {
  if (!c.version_id) return null
  return `/documents/${c.version_id}${c.page_start ? `?page=${c.page_start}` : ""}`
}
