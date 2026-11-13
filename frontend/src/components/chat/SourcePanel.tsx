import { useEffect, useState } from "react"
import { Link } from "wouter"
import { ExternalLink, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type ChatCitation, type DocumentDetail } from "@/lib/api"
import { citationHref, pageLabel } from "@/lib/citations"
import { pdfViewerSrc } from "@/lib/pdfViewer"

/**
 * Side panel next to the chat: shows the cited document at the cited page,
 * so an answer can be checked against its source without leaving the chat.
 */
export function SourcePanel({ citation, onClose }: { citation: ChatCitation; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const versionId = citation.version_id

  useEffect(() => {
    if (!versionId) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setDoc(null)
      setFailed(false)
    })
    api
      .getDocument(versionId)
      .then((d) => {
        if (!cancelled) setDoc(d)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [versionId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const page = citation.page_start ?? undefined
  const href = citationHref(citation)
  const pdfSrc = doc?.file_url ? pdfViewerSrc(doc.file_url, page) : null

  return (
    <aside
      aria-label="Source"
      className="fixed inset-0 z-40 flex flex-col bg-background md:static md:z-auto md:h-full md:w-[48%] md:min-w-[360px] md:max-w-[820px] md:shrink-0 md:animate-fade-in md:border-l md:border-border/60"
    >
      <div className="flex shrink-0 items-start gap-3 border-b border-border/60 bg-secondary/30 px-4 py-3 pt-safe md:pt-3">
        {citation.handle && (
          <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-primary/10 px-1 text-[11px] font-semibold text-primary">
            {citation.handle.slice(1)}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-foreground">{citation.document_title}</div>
          <div className="text-xs text-muted-foreground">
            {[citation.collection, citation.version_label, pageLabel(citation)].filter(Boolean).join(" · ")}
          </div>
        </div>
        {href && (
          <Link
            href={href}
            className="inline-flex items-center gap-1 whitespace-nowrap pt-0.5 text-xs font-medium text-primary underline-offset-4 hover:underline"
          >
            Open document
            <ExternalLink className="h-3 w-3" />
          </Link>
        )}
        <Button variant="ghost" size="icon" className="-mr-1 h-7 w-7 shrink-0" onClick={onClose} aria-label="Close source">
          <X className="h-4 w-4" />
        </Button>
      </div>

      {citation.snippet && (
        <blockquote className="max-h-28 shrink-0 overflow-y-auto border-b border-border/50 bg-card/60 px-4 py-3 text-xs italic leading-relaxed text-muted-foreground">
          “{citation.snippet}”
        </blockquote>
      )}

      <div className="min-h-0 flex-1 bg-white">
        {pdfSrc ? (
          <iframe
            // Remount on a new source so the viewer jumps to the cited page.
            key={`${versionId}-${page ?? 0}`}
            src={pdfSrc}
            title={citation.document_title}
            className="block h-full w-full"
          />
        ) : failed || (doc && !doc.file_url) || !versionId ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
            The original file isn&apos;t available to preview.
          </div>
        ) : (
          <div className="space-y-3 p-6">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-[60vh] w-full" />
          </div>
        )}
      </div>
    </aside>
  )
}
