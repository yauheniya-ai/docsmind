import { BookOpen, ChevronRight } from "lucide-react"
import { Link } from "wouter"
import { Card, CardContent } from "@/components/ui/card"
import type { ChatCitation } from "@/lib/api"
import { citationHref, pageLabel } from "@/lib/citations"

/** The passages an answer was built from, numbered to match its [S#] markers. */
export function SourceCards({
  citations,
  activeChunkId,
  onOpen,
}: {
  citations: ChatCitation[]
  activeChunkId?: string | null
  onOpen?: (citation: ChatCitation) => void
}) {
  if (citations.length === 0) return null
  return (
    <div className="rounded-2xl border border-border/40 bg-secondary/20 p-4 md:p-5">
      <h4 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
        <BookOpen className="h-4 w-4" />
        Sources
      </h4>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {citations.map((citation) => {
          const href = citationHref(citation)
          const active = activeChunkId === citation.chunk_id
          const body = (
            <Card
              className={`h-full shadow-sm transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-primary/30 group-hover:shadow-md ${
                active ? "border-primary/60 bg-primary/5 ring-2 ring-primary/20" : "border-border/50 bg-card/80"
              }`}
            >
              <CardContent className="space-y-2 p-4">
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-primary/10 px-1 text-[11px] font-semibold text-primary">
                    {citation.handle.slice(1)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-1 text-sm font-semibold text-foreground transition-colors group-hover:text-primary">
                      {citation.document_title}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {[citation.collection, citation.version_label, pageLabel(citation)].filter(Boolean).join(" · ")}
                    </div>
                  </div>
                </div>
                <p className="line-clamp-3 text-sm italic leading-relaxed text-muted-foreground">“{citation.snippet}”</p>
                {href && (
                  <span className="flex items-center gap-1 text-xs font-medium text-primary">
                    Open at {pageLabel(citation) || "document"}
                    <ChevronRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                  </span>
                )}
              </CardContent>
            </Card>
          )
          if (onOpen) {
            return (
              <button
                key={citation.chunk_id}
                type="button"
                onClick={() => onOpen(citation)}
                className="group block w-full text-left"
              >
                {body}
              </button>
            )
          }
          return href ? (
            <Link key={citation.chunk_id} href={href} className="group block">
              {body}
            </Link>
          ) : (
            <div key={citation.chunk_id} className="group">
              {body}
            </div>
          )
        })}
      </div>
    </div>
  )
}
