import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useRoute, Link } from "wouter"
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  X,
  FileText,
  Maximize2,
  Minimize2,
  Layers3,
  ListTree,
  AlignLeft,
  ImageIcon,
  Table2,
  AlertCircle,
  Check,
  Pencil,
  Sparkles,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  api,
  type DocumentDetail,
  type DocumentSummaryInfo,
  type EnrichmentEstimate,
  type EnrichmentStatus,
  type Section,
  type Chunk,
  type ImageItem,
  type TableItem,
} from "@/lib/api"
import { pdfViewerSrc } from "@/lib/pdfViewer"

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date)
}

function usd(value: number | null) {
  if (value == null) return "Unknown"
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(
    value,
  )
}

function OutlineTree({ sections }: { sections: Section[] }) {
  const byId = new Map(sections.map((s) => [s.id, s]))
  const depthOf = (s: Section) => {
    let depth = 0
    let parentId = s.parent_id
    while (parentId) {
      depth += 1
      parentId = byId.get(parentId)?.parent_id ?? null
    }
    return depth
  }
  return (
    <ul className="space-y-1.5 font-mono text-sm">
      {sections.map((s) => {
        const pages =
          s.page_start === s.page_end ? `p.${s.page_start}` : `p.${s.page_start}-${s.page_end}`
        return (
          <li key={s.id} style={{ paddingLeft: `${depthOf(s) * 1.25}rem` }} className="text-foreground/90">
            <span className="text-muted-foreground mr-2">[{s.path}]</span>
            {s.heading || "(untitled)"}
            <span className="text-muted-foreground ml-2 text-xs">{pages}</span>
          </li>
        )
      })}
    </ul>
  )
}

function quantile(sorted: number[], q: number) {
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

function ChunkLengthChart({ chunks }: { chunks: Chunk[] }) {
  const lengths = chunks.map((c) => (c.content ?? "").length)
  const sorted = [...lengths].sort((a, b) => a - b)
  const n = sorted.length
  const min = sorted[0]
  const max = sorted[n - 1]
  const mean = lengths.reduce((a, b) => a + b, 0) / n
  const median = quantile(sorted, 0.5)
  const q1 = quantile(sorted, 0.25)
  const q3 = quantile(sorted, 0.75)
  const iqr = q3 - q1
  const lowFence = q1 - 1.5 * iqr
  const highFence = q3 + 1.5 * iqr
  const isOutlier = (len: number) => len < lowFence || len > highFence
  const outlierCount = lengths.filter(isOutlier).length

  const binCount = Math.min(24, Math.max(6, Math.ceil(Math.sqrt(n))))
  const span = Math.max(max - min, 1)
  const binWidth = span / binCount
  const bins = Array.from({ length: binCount }, (_, i) => ({
    from: min + i * binWidth,
    to: min + (i + 1) * binWidth,
    count: 0,
    outliers: 0,
  }))
  for (const len of lengths) {
    const idx = Math.min(Math.floor((len - min) / binWidth), binCount - 1)
    bins[idx].count += 1
    if (isOutlier(len)) bins[idx].outliers += 1
  }
  const maxCount = Math.max(...bins.map((b) => b.count))
  const pct = (value: number) => `${((value - min) / span) * 100}%`
  const fmt = (v: number) => Math.round(v).toLocaleString()

  const stats = [
    { label: "Chunks", value: n.toLocaleString() },
    { label: "Min", value: fmt(min) },
    { label: "Median", value: fmt(median) },
    { label: "Mean", value: fmt(mean) },
    { label: "Max", value: fmt(max) },
    { label: "Outliers", value: outlierCount.toLocaleString() },
  ]

  return (
    <Card className="mb-6 rounded-2xl border-border/60 shadow-sm overflow-hidden bg-card/80">
      <div className="bg-secondary/30 border-b border-border/50 px-4 py-3 text-xs font-bold uppercase tracking-widest text-muted-foreground">
        Chunk length distribution (characters)
      </div>
      <CardContent className="p-4 md:p-6">
        <div className="mb-5 grid grid-cols-3 sm:grid-cols-6 gap-3 text-center">
          {stats.map((s) => (
            <div key={s.label}>
              <div className="font-mono text-base text-foreground">{s.value}</div>
              <div className="text-xs text-muted-foreground">{s.label}</div>
            </div>
          ))}
        </div>

        <div className="relative h-40 border-b border-border/60">
          <div className="absolute inset-0 flex items-end gap-0.5">
            {bins.map((bin, i) => (
              <div
                key={i}
                className="flex-1 h-full flex items-end"
                title={`${fmt(bin.from)}–${fmt(bin.to)} chars: ${bin.count} chunk${bin.count === 1 ? "" : "s"}${bin.outliers ? ` (${bin.outliers} outlier${bin.outliers === 1 ? "" : "s"})` : ""}`}
              >
                <div
                  className={`w-full rounded-t-sm ${bin.outliers > 0 ? "bg-destructive/70" : "bg-primary/60"}`}
                  style={{ height: bin.count === 0 ? 0 : `${Math.max((bin.count / maxCount) * 100, 2)}%` }}
                />
              </div>
            ))}
          </div>
          <div
            className="absolute inset-y-0 border-l-2 border-dashed border-foreground/70 pointer-events-none"
            style={{ left: pct(median) }}
            title={`Median: ${fmt(median)}`}
          />
          <div
            className="absolute inset-y-0 border-l-2 border-dotted border-amber-500 pointer-events-none"
            style={{ left: pct(mean) }}
            title={`Mean: ${fmt(mean)}`}
          />
        </div>

        <div className="mt-1 flex justify-between font-mono text-xs text-muted-foreground">
          <span>{fmt(min)}</span>
          <span>{fmt(max)}</span>
        </div>

        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t-2 border-dashed border-foreground/70" /> Median
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-4 border-t-2 border-dotted border-amber-500" /> Mean
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-destructive/70" /> Contains outliers (outside
            1.5×IQR)
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

function formatVector(values: number[]): string {
  return `[${values.map((v) => v.toFixed(5)).join(", ")}]`
}

/** One line showing the start of the chunk's embedding vector; expands to the full vector. */
function EmbeddingRow({ chunk, versionId }: { chunk: Chunk; versionId?: string }) {
  const [expanded, setExpanded] = useState(false)
  const [full, setFull] = useState<number[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)

  const preview = chunk.embedding_preview
  if (!preview || preview.length === 0) return null
  const dims = chunk.embedding_dims ?? preview.length
  const truncated = dims > preview.length

  const toggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && !full && !loading && versionId) {
      setLoading(true)
      setFailed(false)
      api
        .getChunkEmbedding(versionId, chunk.id)
        .then(setFull)
        .catch(() => setFailed(true))
        .finally(() => setLoading(false))
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-border/50 bg-secondary/30 px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-medium">Embedding</span>
        <span className="font-mono">
          {dims.toLocaleString()} dims{chunk.embedding_model ? ` · ${chunk.embedding_model}` : ""}
        </span>
        {truncated && (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="ml-auto font-medium text-primary hover:underline underline-offset-4"
          >
            {expanded ? "Collapse" : "Expand"}
          </button>
        )}
      </div>
      {expanded ? (
        <div className="mt-1.5 max-h-48 overflow-y-auto break-all font-mono text-[11px] leading-relaxed text-foreground/80">
          {full ? formatVector(full) : failed ? "Could not load the full vector." : loading ? "Loading…" : formatVector(preview)}
        </div>
      ) : (
        <div className="mt-1.5 truncate font-mono text-[11px] text-foreground/80" title="Expand to see the full vector">
          {formatVector(preview)}
          {truncated ? " …" : ""}
        </div>
      )}
    </div>
  )
}

function ChunkList({ chunks, versionId }: { chunks: Chunk[]; versionId?: string }) {
  if (chunks.length === 0) {
    return (
      <div className="text-center p-12 border border-dashed border-border/80 rounded-2xl bg-card/50 text-muted-foreground">
        No chunks extracted from this document.
      </div>
    )
  }
  return (
    <>
      <ChunkLengthChart chunks={chunks} />
      <ol className="space-y-3">
      {chunks.map((chunk, index) => {
        const pages =
          chunk.page_start == null
            ? null
            : chunk.page_start === chunk.page_end || chunk.page_end == null
              ? `p.${chunk.page_start}`
              : `p.${chunk.page_start}-${chunk.page_end}`
        return (
          <li key={chunk.id}>
            <Card className="rounded-xl border-border/60 shadow-sm bg-card/80">
              <div className="flex items-center gap-2 border-b border-border/50 bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
                <span className="font-mono">Chunk {index + 1}</span>
                <span className="font-mono">
                  ({(chunk.content ?? "").length.toLocaleString()} chars)
                </span>
                {pages && <span className="ml-auto font-medium">{pages}</span>}
              </div>
              <CardContent className="p-4">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                  {chunk.content}
                </p>
                <EmbeddingRow chunk={chunk} versionId={versionId} />
              </CardContent>
            </Card>
          </li>
        )
      })}
    </ol>
    </>
  )
}

function PdfViewer({ fileUrl, title, page }: { fileUrl: string; title: string; page?: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    const onChange = () => setIsFullscreen(document.fullscreenElement === containerRef.current)
    document.addEventListener("fullscreenchange", onChange)
    return () => document.removeEventListener("fullscreenchange", onChange)
  }, [])

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void containerRef.current?.requestFullscreen()
  }

  // PDF.js's own viewer (bundled under /pdfjs, see public/pdfjs): search,
  // outline/thumbnails sidebar, zoom, page navigation, print/download — the
  // same in every browser, unlike the browser's built-in PDF plugin.
  const src = pdfViewerSrc(fileUrl, page)

  return (
    <div
      ref={containerRef}
      className="flex flex-col overflow-hidden rounded-2xl border border-border/60 bg-card shadow-sm"
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border/50 bg-secondary/30 px-4 py-2 text-xs text-muted-foreground">
        <span className="font-medium">Original file</span>
        <div className="flex items-center gap-4">
          <a
            href={fileUrl}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-primary hover:underline underline-offset-4"
          >
            Open raw file
          </a>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs"
            onClick={toggleFullscreen}
          >
            {isFullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            {isFullscreen ? "Exit full screen" : "Full screen"}
          </Button>
        </div>
      </div>
      <iframe
        src={src}
        title={title}
        className={isFullscreen ? "block w-full flex-1 bg-white" : "block h-[78vh] min-h-[520px] w-full bg-white"}
      />
    </div>
  )
}

function Gallery({
  items,
  emptyLabel,
}: {
  items: (ImageItem | TableItem)[]
  emptyLabel: string
}) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)

  if (items.length === 0) {
    return (
      <div className="text-center p-12 border border-dashed border-border/80 rounded-2xl bg-card/50 text-muted-foreground">
        {emptyLabel}
      </div>
    )
  }
  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 items-start gap-4">
        {items.map((item, index) => (
          <GalleryCard key={item.id} item={item} onOpen={() => setOpenIndex(index)} />
        ))}
      </div>
      {openIndex !== null && items[openIndex] && (
        <Lightbox
          items={items}
          index={openIndex}
          onIndexChange={setOpenIndex}
          onClose={() => setOpenIndex(null)}
        />
      )}
    </>
  )
}

/** Full-view overlay: frosted-glass backdrop, the image, and its full caption beneath it. */
function Lightbox({
  items,
  index,
  onIndexChange,
  onClose,
}: {
  items: (ImageItem | TableItem)[]
  index: number
  onIndexChange: (index: number) => void
  onClose: () => void
}) {
  const item = items[index]
  const caption = "caption" in item ? item.caption : null
  const hasPrev = index > 0
  const hasNext = index < items.length - 1

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      else if (e.key === "ArrowLeft" && index > 0) onIndexChange(index - 1)
      else if (e.key === "ArrowRight" && index < items.length - 1) onIndexChange(index + 1)
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", onKey)
    return () => {
      window.removeEventListener("keydown", onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [index, items.length, onClose, onIndexChange])

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Page ${item.page_number}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-xl backdrop-saturate-150 sm:p-8"
      onClick={onClose}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full border border-white/30 bg-white/20 p-2 text-white shadow-lg backdrop-blur-md transition hover:bg-white/30"
      >
        <X className="h-5 w-5" />
      </button>
      {hasPrev && (
        <button
          type="button"
          aria-label="Previous"
          onClick={(e) => {
            e.stopPropagation()
            onIndexChange(index - 1)
          }}
          className="absolute left-4 top-1/2 -translate-y-1/2 rounded-full border border-white/30 bg-white/20 p-2 text-white shadow-lg backdrop-blur-md transition hover:bg-white/30"
        >
          <ChevronLeft className="h-6 w-6" />
        </button>
      )}
      {hasNext && (
        <button
          type="button"
          aria-label="Next"
          onClick={(e) => {
            e.stopPropagation()
            onIndexChange(index + 1)
          }}
          className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full border border-white/30 bg-white/20 p-2 text-white shadow-lg backdrop-blur-md transition hover:bg-white/30"
        >
          <ChevronRight className="h-6 w-6" />
        </button>
      )}
      <div
        className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex min-h-0 flex-1 items-center justify-center bg-white p-4">
          {item.url ? (
            <img
              src={item.url}
              alt={caption || `Page ${item.page_number}`}
              className="max-h-[65vh] max-w-full rounded-lg object-contain"
            />
          ) : (
            <ImageIcon className="h-12 w-12 text-muted-foreground/40" />
          )}
        </div>
        <div className="max-h-[30vh] shrink-0 overflow-y-auto border-t border-border/60 bg-white px-5 py-4 text-foreground">
          <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
            <span className="font-medium">
              p.{item.page_number} · {item.width}×{item.height}
            </span>
            {item.url && (
              <a
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-primary hover:underline underline-offset-4"
              >
                Open raw file
              </a>
            )}
          </div>
          {caption ? (
            <p className="text-sm leading-relaxed">{caption}</p>
          ) : caption === "" ? (
            <p className="text-sm italic text-muted-foreground">Decorative, no caption</p>
          ) : "caption" in item ? (
            <p className="text-sm italic text-muted-foreground">Not captioned yet</p>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  )
}

function GalleryCard({ item, onOpen }: { item: ImageItem | TableItem; onOpen: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const caption = "caption" in item ? item.caption : null
  const long = (caption?.length ?? 0) > 110

  return (
    <div className="rounded-xl border border-border/60 bg-card/80 overflow-hidden hover:border-primary/40 hover:shadow-md transition-all">
      <button type="button" onClick={onOpen} className="group block w-full cursor-zoom-in text-left">
        <div className="aspect-square bg-secondary/30 flex items-center justify-center overflow-hidden">
          {item.url ? (
            <img
              src={item.url}
              alt={caption || `Page ${item.page_number}`}
              className="w-full h-full object-contain group-hover:scale-105 transition-transform"
              loading="lazy"
            />
          ) : (
            <ImageIcon className="w-8 h-8 text-muted-foreground/40" />
          )}
        </div>
        <div className="px-3 py-2 flex items-center justify-between text-xs text-muted-foreground">
          <span className="font-medium">p.{item.page_number}</span>
          <span className="font-mono">{item.width}×{item.height}</span>
        </div>
      </button>
      {caption !== null && (
        <div className="px-3 pb-3 text-xs leading-snug text-foreground/80">
          {caption ? (
            <>
              <p className={expanded ? "" : "line-clamp-3"}>{caption}</p>
              {long && (
                <button
                  type="button"
                  onClick={() => setExpanded((v) => !v)}
                  className="mt-1 font-medium text-primary hover:underline underline-offset-4"
                >
                  {expanded ? "Show less" : "Show more"}
                </button>
              )}
            </>
          ) : (
            <span className="italic text-muted-foreground">Decorative, no caption</span>
          )}
        </div>
      )}
    </div>
  )
}

/** Title, version and collection: shown as they are, editable in place (they are normally given at ingest). */
function DocumentHeading({ doc, onSaved }: { doc: DocumentDetail; onSaved: (doc: DocumentDetail) => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(doc.title)
  const [collection, setCollection] = useState(doc.collection)
  const [label, setLabel] = useState(doc.label)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = () => {
    setTitle(doc.title)
    setCollection(doc.collection)
    setLabel(doc.label)
    setError(null)
    setEditing(true)
  }

  const save = (event: React.FormEvent) => {
    event.preventDefault()
    if (!title.trim() || !collection.trim() || !label.trim()) {
      setError("Title, collection and version cannot be empty.")
      return
    }
    setSaving(true)
    setError(null)
    api
      .updateDocument(doc.id, { title: title.trim(), collection: collection.trim(), label: label.trim() })
      .then((saved) => {
        onSaved(saved)
        setEditing(false)
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  if (editing) {
    return (
      <form onSubmit={save} className="space-y-3">
        <Input
          aria-label="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="h-12 font-serif text-xl md:text-2xl"
          autoFocus
        />
        <div className="flex flex-col gap-3 sm:flex-row">
          <label className="flex-1 space-y-1">
            <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Collection</span>
            <Input value={collection} onChange={(e) => setCollection(e.target.value)} />
          </label>
          <label className="flex-1 space-y-1">
            <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Version</span>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
        </div>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button type="submit" disabled={saving} className="gap-2">
            <Check className="h-4 w-4" /> {saving ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="ghost" disabled={saving} onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </form>
    )
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge className="capitalize font-semibold tracking-wide">{doc.label}</Badge>
        <Badge variant="outline" className="font-medium bg-background/50">
          {doc.collection}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={start}
          className="h-7 gap-1.5 px-2 text-muted-foreground hover:text-foreground"
        >
          <Pencil className="h-3.5 w-3.5" /> Edit
        </Button>
      </div>
      <h1 className="font-serif text-3xl md:text-5xl font-medium tracking-tight text-foreground leading-tight mb-4">
        {doc.title}
      </h1>
    </>
  )
}

function EnrichmentBanner({
  kind,
  enrichment,
  onEstimate,
  onCancel,
}: {
  kind: "embeddings" | "captions"
  enrichment: EnrichmentStatus | null
  onEstimate: (kind: "embeddings" | "captions") => void
  onCancel: (jobId: string) => void
}) {
  const status = enrichment?.[kind]
  if (!status || status.pending <= 0) return null
  const job = status.job
  const active = job && (job.status === "queued" || job.status === "running")
  const title =
    kind === "embeddings"
      ? "Embeddings are not complete for this document."
      : "Image captions are not complete for this document."
  const unit = kind === "embeddings" ? "chunks embedded" : "images captioned"
  return (
    <Card className="mb-4 rounded-xl border-amber-500/40 bg-amber-500/5">
      <CardContent className="p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm text-foreground/90">
          <p className="font-medium">{title}</p>
          <p className="text-muted-foreground">
            {status.done.toLocaleString()} / {status.total.toLocaleString()} {unit}.
            {job?.status === "running" && ` Running: ${job.done}/${job.total}.`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {active ? (
            <Button variant="outline" onClick={() => onCancel(job.id)} disabled={job.cancelling}>
              {job.cancelling ? "Cancelling..." : "Cancel"}
            </Button>
          ) : (
            <Button onClick={() => onEstimate(kind)}>Estimate & run</Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export function DocumentDetails() {
  const [, params] = useRoute<{ id: string }>("/documents/:id")
  const id: string | undefined = params ? params.id : undefined

  const [doc, setDoc] = useState<DocumentDetail | null>(null)
  const [fullText, setFullText] = useState<string | null>(null)
  const [normalizedText, setNormalizedText] = useState<string | null>(null)
  const [textView, setTextView] = useState<"extracted" | "normalized">("extracted")
  const [summary, setSummary] = useState<DocumentSummaryInfo | null>(null)
  const [outline, setOutline] = useState<Section[] | null>(null)
  const [chunks, setChunks] = useState<Chunk[] | null>(null)
  const [images, setImages] = useState<ImageItem[] | null>(null)
  const [tables, setTables] = useState<TableItem[] | null>(null)
  const [enrichment, setEnrichment] = useState<EnrichmentStatus | null>(null)
  const [enrichmentDialog, setEnrichmentDialog] = useState<"embeddings" | "captions" | null>(null)
  const [estimate, setEstimate] = useState<EnrichmentEstimate | null>(null)
  const [estimateLoading, setEstimateLoading] = useState(false)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [runBusy, setRunBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshEnrichment = useCallback(() => {
    if (!id) return
    api.getEnrichmentStatus(id).then(setEnrichment).catch(() => setEnrichment(null))
  }, [id])

  useEffect(() => {
    if (!id) return
    queueMicrotask(() => {
      setDoc(null)
      setFullText(null)
      setSummary(null)
      setOutline(null)
      setChunks(null)
      setImages(null)
      setTables(null)
      setEnrichment(null)
      setError(null)
    })

    api.getDocument(id).then(setDoc).catch((e) => setError(e instanceof Error ? e.message : String(e)))
    api.getFulltext(id).then(setFullText).catch(() => setFullText(""))
    api.getNormalizedText(id).then(setNormalizedText).catch(() => setNormalizedText(""))
    api.getSummary(id).then(setSummary).catch(() => setSummary({ summary: null, keywords: [] }))
    api.getOutline(id).then(setOutline).catch(() => setOutline([]))
    api
      .getChunks(id)
      .then((all) => setChunks(all.filter((c) => c.chunk_type === "text")))
      .catch(() => setChunks([]))
    api.getImages(id).then(setImages).catch(() => setImages([]))
    api.getTables(id).then(setTables).catch(() => setTables([]))
    refreshEnrichment()
  }, [id, refreshEnrichment])

  // Captions are written by a background job; reload the gallery whenever the
  // number of captioned images changes so new captions appear without a refresh.
  const captionsDone = enrichment?.captions.done
  useEffect(() => {
    if (!id || captionsDone === undefined) return
    api.getImages(id).then(setImages).catch(() => {})
  }, [id, captionsDone])

  useEffect(() => {
    if (!enrichment) return
    const active = [enrichment.embeddings.job, enrichment.captions.job].some(
      (job) => job && (job.status === "queued" || job.status === "running"),
    )
    if (!active) return
    const timer = window.setInterval(() => refreshEnrichment(), 2000)
    return () => window.clearInterval(timer)
  }, [enrichment, refreshEnrichment])

  const openEstimate = (kind: "embeddings" | "captions") => {
    if (!id) return
    setEnrichmentDialog(kind)
    setEstimate(null)
    setEstimateError(null)
    setRunError(null)
    setEstimateLoading(true)
    api
      .estimateEnrichment(kind, [id])
      .then(setEstimate)
      .catch((e) => setEstimateError(e instanceof Error ? e.message : String(e)))
      .finally(() => setEstimateLoading(false))
  }

  const runEnrichment = () => {
    if (!id || !enrichmentDialog) return
    setRunBusy(true)
    setRunError(null)
    api
      .runEnrichment(enrichmentDialog, [id])
      .then(() => {
        setEnrichmentDialog(null)
        refreshEnrichment()
      })
      .catch((e) => setRunError(e instanceof Error ? e.message : String(e)))
      .finally(() => setRunBusy(false))
  }

  const cancelJob = (jobId: string) => {
    api.cancelEnrichmentJob(jobId).finally(() => refreshEnrichment())
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center bg-background/50">
        <AlertCircle className="h-10 w-10 text-destructive/80 mb-4" />
        <h1 className="font-serif text-3xl font-medium tracking-tight">Document not found</h1>
        <Button asChild className="mt-8 h-12 px-6 rounded-xl gap-2">
          <Link href="/documents">
            <ArrowLeft className="h-4 w-4" /> Back to Register
          </Link>
        </Button>
      </div>
    )
  }

  if (!doc) {
    return (
      <div className="p-4 md:p-8 pt-safe mx-auto w-full max-w-4xl space-y-6">
        <Skeleton className="h-10 w-24 rounded-lg" />
        <div className="flex gap-4">
          <Skeleton className="h-16 w-16 rounded-xl" />
          <div className="space-y-3 flex-1">
            <Skeleton className="h-8 w-3/4 rounded-lg" />
            <Skeleton className="h-4 w-1/3 rounded-md" />
          </div>
        </div>
        <Skeleton className="h-96 w-full rounded-2xl mt-8" />
      </div>
    )
  }

  const pageParam = Number(new URLSearchParams(window.location.search).get("page"))
  const initialPage = Number.isInteger(pageParam) && pageParam > 0 ? pageParam : undefined

  const stats: { label: string; value: number }[] = []
  if (doc.char_count != null) stats.push({ label: "characters", value: doc.char_count })
  if (doc.word_count != null) stats.push({ label: "words", value: doc.word_count })
  if (doc.token_count != null) stats.push({ label: "tokens", value: doc.token_count })

  return (
    <div className="mx-auto w-full max-w-4xl p-4 md:p-8 pt-safe pb-safe">
      <Button
        asChild
        variant="ghost"
        className="mb-8 md:mb-10 gap-2 h-10 px-3 rounded-lg hover:bg-secondary/50 transition-colors -ml-3 text-muted-foreground hover:text-foreground"
      >
        <Link href="/documents">
          <ChevronLeft className="h-5 w-5" /> Back to Register
        </Link>
      </Button>

      <div className="mb-10 md:mb-12 flex flex-col md:flex-row md:items-start gap-5 md:gap-6">
        <div className="rounded-2xl bg-primary/5 border border-primary/10 p-5 md:p-6 text-primary shadow-sm inline-flex">
          <FileText className="h-10 w-10 md:h-12 md:w-12 stroke-[1.5]" />
        </div>

        <div className="min-w-0 flex-1">
          <DocumentHeading doc={doc} onSaved={(saved) => setDoc((old) => (old ? { ...old, ...saved } : saved))} />

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground bg-secondary/20 p-3 rounded-xl border border-border/40 inline-flex">
            <span>Ingested {formatDate(doc.created_at)}</span>
            {stats.map(({ label, value }) => (
              <Fragment key={label}>
                <div className="w-1 h-1 rounded-full bg-border" />
                <span>
                  <span className="font-mono bg-secondary/50 px-1.5 py-0.5 rounded text-foreground/80">
                    {value.toLocaleString()}
                  </span>{" "}
                  {label}
                </span>
              </Fragment>
            ))}
          </div>
        </div>
      </div>

      <div className="mb-8 flex flex-col sm:flex-row">
        <Button asChild className="gap-2 h-12 rounded-xl px-6 w-full sm:w-auto text-base">
          <Link href={`/?scope=${doc.id}`}>
            Ask the Mind about this Document <ArrowLeft className="h-4 w-4 rotate-180" />
          </Link>
        </Button>
      </div>

      <Tabs defaultValue="document">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="document" className="gap-1.5">
            <FileText className="h-4 w-4" /> Document
          </TabsTrigger>
          <TabsTrigger value="summary" className="gap-1.5">
            <Sparkles className="h-4 w-4" /> Summary
          </TabsTrigger>
          <TabsTrigger value="outline" className="gap-1.5">
            <ListTree className="h-4 w-4" /> Outline
          </TabsTrigger>
          <TabsTrigger value="text" className="gap-1.5">
            <AlignLeft className="h-4 w-4" /> Text
          </TabsTrigger>
          <TabsTrigger value="chunks" className="gap-1.5">
            <Layers3 className="h-4 w-4" /> Chunks{" "}
            {chunks && <span className="font-mono text-xs opacity-70">({chunks.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="images" className="gap-1.5">
            <ImageIcon className="h-4 w-4" /> Images{" "}
            {images && <span className="font-mono text-xs opacity-70">({images.length})</span>}
          </TabsTrigger>
          <TabsTrigger value="tables" className="gap-1.5">
            <Table2 className="h-4 w-4" /> Tables{" "}
            {tables && <span className="font-mono text-xs opacity-70">({tables.length})</span>}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="document">
          {doc.file_url ? (
            <PdfViewer fileUrl={doc.file_url} title={doc.title} page={initialPage} />
          ) : (
            <div className="text-center p-12 border border-dashed border-border/80 rounded-2xl bg-card/50 text-muted-foreground">
              The original file isn't stored for this version. Delete it and re-ingest to keep a viewable copy.
            </div>
          )}
        </TabsContent>

        <TabsContent value="summary">
          {summary === null ? (
            <Skeleton className="h-48 w-full rounded-2xl" />
          ) : !summary.summary && summary.keywords.length === 0 ? (
            <div className="text-center p-12 border border-dashed border-border/80 rounded-2xl bg-card/50 text-muted-foreground">
              No summary has been aqdded for this document yet.
            </div>
          ) : (
            <Card className="rounded-2xl border-border/60 shadow-sm overflow-hidden bg-card/80 backdrop-blur-sm">
              <CardContent className="p-6 md:p-10 space-y-8">
                {summary.summary && (
                  <section>
                    <h2 className="mb-3 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                      Summary
                    </h2>
                    <p className="font-serif text-lg leading-relaxed text-foreground/90 whitespace-pre-wrap">
                      {summary.summary}
                    </p>
                  </section>
                )}
                {summary.keywords.length > 0 && (
                  <section>
                    <h2 className="mb-3 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                      Keywords
                    </h2>
                    <div className="flex flex-wrap gap-2">
                      {summary.keywords.map((keyword) => (
                        <Badge key={keyword} variant="outline" className="font-medium bg-background/50">
                          {keyword}
                        </Badge>
                      ))}
                    </div>
                  </section>
                )}
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Sparkles className="h-3.5 w-3.5" /> AI-generated
                </p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="outline">
          {outline === null ? (
            <Skeleton className="h-64 w-full rounded-2xl" />
          ) : outline.length === 0 ? (
            <div className="text-center p-12 border border-dashed border-border/80 rounded-2xl bg-card/50 text-muted-foreground">
              No outline found in this document.
            </div>
          ) : (
            <Card className="rounded-2xl border-border/60 shadow-sm overflow-hidden bg-card/80 backdrop-blur-sm">
              <CardContent className="p-6">
                <OutlineTree sections={outline} />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="text">
          <div className="mb-4 inline-flex rounded-lg border border-border/60 bg-muted/40 p-0.5 text-sm">
            {(["extracted", "normalized"] as const).map((view) => (
              <button
                key={view}
                type="button"
                onClick={() => setTextView(view)}
                className={`rounded-md px-3 py-1 font-medium transition-colors ${
                  textView === view ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {view === "extracted" ? "Extracted" : "Normalized for diff"}
              </button>
            ))}
          </div>
          {textView === "normalized" && (
            <p className="mb-4 text-sm text-muted-foreground">
              One sentence per line, with headers, footers and page numbers removed. This is the text the
              change analysis compares.
            </p>
          )}
          <Card className="rounded-2xl border-border/60 shadow-sm overflow-hidden bg-card/80 backdrop-blur-sm">
            <CardContent className="p-6 md:p-10">
              {(textView === "extracted" ? fullText : normalizedText) === null ? (
                <div className="space-y-3">
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-2/3" />
                </div>
              ) : (textView === "extracted" ? fullText : normalizedText)!.length === 0 ? (
                <p className="text-muted-foreground italic">
                  No full text saved for this version (re-ingest to backfill).
                </p>
              ) : (
                <div
                  className={`prose prose-slate max-w-none leading-relaxed text-foreground/90 whitespace-pre-wrap ${
                    textView === "extracted" ? "font-serif" : "font-mono text-sm"
                  }`}
                >
                  {textView === "extracted" ? fullText : normalizedText}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="chunks">
          <EnrichmentBanner
            kind="embeddings"
            enrichment={enrichment}
            onEstimate={openEstimate}
            onCancel={cancelJob}
          />
          {chunks === null ? (
            <Skeleton className="h-64 w-full rounded-2xl" />
          ) : (
            <ChunkList chunks={chunks} versionId={id} />
          )}
        </TabsContent>

        <TabsContent value="images">
          <EnrichmentBanner
            kind="captions"
            enrichment={enrichment}
            onEstimate={openEstimate}
            onCancel={cancelJob}
          />
          {images === null ? (
            <Skeleton className="h-64 w-full rounded-2xl" />
          ) : (
            <Gallery items={images} emptyLabel="No images extracted from this document." />
          )}
        </TabsContent>

        <TabsContent value="tables">
          {tables === null ? (
            <Skeleton className="h-64 w-full rounded-2xl" />
          ) : (
            <Gallery items={tables} emptyLabel="No tables extracted from this document." />
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={enrichmentDialog !== null} onOpenChange={(open) => !open && setEnrichmentDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {enrichmentDialog === "embeddings" ? "Create embeddings on demand?" : "Create image captions on demand?"}
            </DialogTitle>
            <DialogDescription>
              This action sends data to {estimate?.provider ?? "the configured"} model `{estimate?.model ?? "..."}` and may incur cost.
            </DialogDescription>
          </DialogHeader>

          {estimateLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-5 w-full" />
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-5 w-4/5" />
            </div>
          ) : estimateError ? (
            <p className="text-sm text-destructive">{estimateError}</p>
          ) : estimate ? (
            <div className="space-y-3 text-sm">
              <p>
                Items to process: <span className="font-mono">{estimate.items.toLocaleString()}</span>
              </p>
              <p>
                Input tokens: <span className="font-mono">{estimate.input_tokens.toLocaleString()}</span>
              </p>
              {estimate.kind === "captions" && (
                <p>
                  Output tokens: <span className="font-mono">{estimate.output_tokens_low.toLocaleString()}</span> -{" "}
                  <span className="font-mono">{estimate.output_tokens_high.toLocaleString()}</span>
                </p>
              )}
              <p>
                Estimated cost: <span className="font-mono">{usd(estimate.cost_usd_low)}</span>
                {estimate.cost_usd_high != null && estimate.cost_usd_high !== estimate.cost_usd_low && (
                  <>
                    {" "}- <span className="font-mono">{usd(estimate.cost_usd_high)}</span>
                  </>
                )}
              </p>
              <p className="text-muted-foreground">{estimate.note}</p>
              {!estimate.configured && estimate.reason && <p className="text-destructive">{estimate.reason}</p>}
              {runError && <p className="text-destructive">{runError}</p>}
            </div>
          ) : null}

          <DialogFooter>
            <Button variant="outline" onClick={() => setEnrichmentDialog(null)} disabled={runBusy}>
              Not now
            </Button>
            <Button
              onClick={runEnrichment}
              disabled={runBusy || !estimate || !estimate.configured || estimate.items <= 0}
            >
              {runBusy ? "Starting..." : "Proceed"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
