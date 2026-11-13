import { memo, useMemo } from "react"
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import { Link } from "wouter"
import type { ChatCitation } from "@/lib/api"
import { citationHref, citationLabel, pageCitation } from "@/lib/citations"
import { cn } from "@/lib/utils"

const MARKER = /\[(S\d+)\]/g
const PAGE_REF = /\b([AB]) p\.(\d+)\b/g

/** The two versions of a change report: "A p.12" is page 12 of A. */
export interface PageRefs {
  A: { version_id: string; title: string; label: string }
  B: { version_id: string; title: string; label: string }
}

/**
 * `[S1]` -> `[S1](cite:S1)` so the markdown renderer hands the marker to us as a link; with page
 * refs, "A p.12" -> `[A p.12](page:A:12)` likewise.
 */
function linkMarkers(text: string, pages: boolean): string {
  const marked = text.replace(MARKER, (_m, handle: string) => `[${handle}](cite:${handle})`)
  return pages ? marked.replace(PAGE_REF, (m, side: string, page: string) => `[${m}](page:${side}:${page})`) : marked
}

function CitationChip({
  handle,
  citation,
  onOpen,
}: {
  handle: string
  citation?: ChatCitation
  onOpen?: (citation: ChatCitation) => void
}) {
  const number = handle.slice(1)
  const chip =
    "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded-md px-1 align-text-top text-[10px] font-semibold leading-none no-underline transition-colors"
  if (!citation) {
    return <span className={cn(chip, "bg-secondary text-muted-foreground")}>{number}</span>
  }
  const href = citationHref(citation)
  const className = cn(chip, "bg-primary/10 text-primary hover:bg-primary/20")
  const title = citationLabel(citation)
  if (onOpen) {
    return (
      <button type="button" className={className} title={title} onClick={() => onOpen(citation)}>
        {number}
      </button>
    )
  }
  return href ? (
    <Link href={href} className={className} title={title}>
      {number}
    </Link>
  ) : (
    <span className={className} title={title}>
      {number}
    </span>
  )
}

export const Markdown = memo(function Markdown({
  text,
  citations = [],
  pageRefs,
  onOpenCitation,
  className,
}: {
  text: string
  citations?: ChatCitation[]
  pageRefs?: PageRefs
  onOpenCitation?: (citation: ChatCitation) => void
  className?: string
}) {
  const byHandle = useMemo(() => new Map(citations.map((c) => [c.handle, c])), [citations])
  const source = useMemo(() => linkMarkers(text, Boolean(pageRefs)), [text, pageRefs])

  const components: Components = useMemo(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("cite:")) {
          const handle = href.slice(5)
          return <CitationChip handle={handle} citation={byHandle.get(handle)} onOpen={onOpenCitation} />
        }
        if (href?.startsWith("page:")) {
          const [, side, page] = href.split(":")
          const ref = pageRefs?.[side as "A" | "B"]
          if (!ref || !onOpenCitation) return <span>{children}</span>
          const n = Number(page)
          return (
            <button
              type="button"
              className="font-medium text-primary underline-offset-4 hover:underline"
              title={`${ref.title} ${ref.label}, page ${n}`}
              onClick={() => onOpenCitation(pageCitation(ref, n))}
            >
              {children}
            </button>
          )
        }
        return (
          <a href={href} target="_blank" rel="noreferrer noopener">
            {children}
          </a>
        )
      },
      // Model output is untrusted: never load images it points at.
      img: () => null,
      table({ children }) {
        return (
          <div className="not-prose my-4 overflow-x-auto rounded-lg border border-border/60">
            <table className="w-full border-collapse text-sm">{children}</table>
          </div>
        )
      },
      th({ children }) {
        return <th className="border-b border-border/60 bg-secondary/40 px-3 py-2 text-left font-semibold">{children}</th>
      },
      td({ children }) {
        return <td className="border-b border-border/40 px-3 py-2 align-top">{children}</td>
      },
    }),
    [byHandle, pageRefs, onOpenCitation],
  )

  return (
    <div className={cn("prose prose-slate max-w-none leading-relaxed text-foreground md:text-[1.05rem]", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        urlTransform={(url) => (url.startsWith("cite:") || url.startsWith("page:") ? url : defaultUrlTransform(url))}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
})
