import { useEffect, useRef, useState } from "react"
import { useLocation } from "wouter"
import {
  BookOpen,
  Plus,
  FileText,
  Search,
  AlertCircle,
  FileUp,
  ArrowRight,
  Clock,
  ImageIcon,
  Table2,
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError, type DocumentSummary } from "@/lib/api"

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date)
}

export function Documents() {
  const [, setLocation] = useLocation()
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState("")

  const [open, setOpen] = useState(false)
  const [collection, setCollection] = useState("")
  const [version, setVersion] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const load = () => {
    setLoadError(null)
    api
      .listDocuments()
      .then(setDocuments)
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load documents."))
  }

  // Loaded after the first render, off the effect body (the repo's pattern for state set in effects).
  useEffect(() => {
    queueMicrotask(load)
  }, [])

  const filtered =
    documents?.filter(
      (d) =>
        d.title.toLowerCase().includes(search.toLowerCase()) ||
        d.collection.toLowerCase().includes(search.toLowerCase())
    ) ?? []

  const resetForm = () => {
    setCollection("")
    setVersion("")
    setFile(null)
    setSubmitError(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!file || !collection) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await api.ingestDocument(file, collection, version || undefined)
      setOpen(false)
      resetForm()
      load()
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Ingestion failed. Check the server logs.")
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (doc: DocumentSummary, event: React.MouseEvent) => {
    event.stopPropagation()
    const confirmed = window.confirm(
      `Delete "${doc.title}" (${doc.collection} / ${doc.label})? This also removes its chunks, images and tables.`
    )
    if (!confirmed) return
    setDeletingId(doc.id)
    try {
      await api.deleteDocument(doc.id)
      load()
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Failed to delete document.")
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="p-4 md:p-8 pt-safe max-w-6xl mx-auto w-full pb-safe">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-8 pt-4 md:pt-0">
        <div>
          <h1 className="text-3xl md:text-4xl font-serif font-medium text-foreground tracking-tight flex items-center gap-3">
            <BookOpen className="w-7 h-7 md:w-8 md:h-8 text-primary" />
            Register
          </h1>
          <p className="text-muted-foreground mt-2 text-sm md:text-base">
            Manage the trusted documents powering your answers.
          </p>
        </div>

        <Dialog
          open={open}
          onOpenChange={(next) => {
            setOpen(next)
            if (!next) resetForm()
          }}
        >
          <DialogTrigger asChild>
            <Button className="shrink-0 gap-2 h-11 px-6 rounded-xl font-medium shadow-sm transition-all hover:shadow-md active:scale-95">
              <Plus className="w-4 h-4" /> Add Document
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-lg max-h-[90dvh] overflow-y-auto w-[95vw] rounded-2xl p-4 md:p-6">
            <DialogHeader>
              <DialogTitle className="font-serif text-2xl">Add a document</DialogTitle>
              <DialogDescription className="text-base">
                Runs <code className="text-foreground">docsmind ingest</code> on the uploaded PDF: full
                text, outline, page chunks, images and tables are parsed and saved.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-5 py-2">
              <div className="space-y-2">
                <Label className="text-sm font-semibold uppercase tracking-widest">PDF file</Label>
                <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-border/80 bg-secondary/30 px-4 py-6 text-sm font-medium text-foreground transition-all hover:border-primary/40 hover:bg-secondary/60 active:scale-[0.98]">
                  <FileUp className="h-5 w-5 text-primary" />
                  {file ? file.name : "Choose a PDF"}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,application/pdf"
                    className="sr-only"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    required
                  />
                </label>
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-semibold uppercase tracking-widest">Collection</Label>
                <Input
                  placeholder="e.g., nist"
                  value={collection}
                  onChange={(e) => setCollection(e.target.value)}
                  required
                  className="h-11 bg-secondary/30"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-semibold uppercase tracking-widest">
                  Version <span className="normal-case text-muted-foreground font-normal">(optional)</span>
                </Label>
                <Input
                  placeholder="e.g., 4.0.1 — defaults to today's date"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  className="h-11 bg-secondary/30"
                />
              </div>
              {submitError && <p className="text-sm text-destructive font-medium">{submitError}</p>}
              <DialogFooter className="pt-2">
                <Button
                  type="submit"
                  className="h-12 rounded-xl w-full sm:w-auto"
                  disabled={submitting || !file || !collection}
                >
                  {submitting ? "Ingesting… this can take a minute" : "Ingest document"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <div className="relative mb-8 max-w-md">
        <Search className="w-5 h-5 absolute left-3.5 top-3.5 text-muted-foreground" />
        <Input
          placeholder="Search by title or collection…"
          className="pl-11 h-12 bg-card rounded-xl border-border/60 shadow-sm"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {documents === null && !loadError && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Card key={i} className="animate-pulse rounded-2xl border-border/50">
              <CardHeader className="space-y-3">
                <Skeleton className="h-6 w-1/2 rounded-md" />
                <Skeleton className="h-4 w-1/3 rounded-md" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-10 w-full rounded-xl" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {loadError && (
        <div className="p-8 text-center bg-destructive/5 rounded-2xl border border-destructive/20 text-destructive flex flex-col items-center">
          <AlertCircle className="w-10 h-10 mb-3 text-destructive/80" />
          <h3 className="font-serif text-2xl font-medium">Failed to load documents</h3>
          <p className="text-sm mt-2 opacity-80 max-w-sm">
            Is the backend running? Start it with <code>docsmind serve</code>.
          </p>
        </div>
      )}

      {documents && !loadError && filtered.length === 0 && (
        <div className="text-center p-12 md:p-16 border border-dashed border-border/80 rounded-2xl bg-card/50 flex flex-col items-center">
          <div className="w-20 h-20 rounded-full bg-secondary/50 flex items-center justify-center mb-6">
            <BookOpen className="w-10 h-10 text-muted-foreground/60" />
          </div>
          <h3 className="text-xl md:text-2xl font-serif font-medium text-foreground">No documents found</h3>
          <p className="text-muted-foreground mt-3 max-w-sm mx-auto text-sm md:text-base leading-relaxed">
            {search
              ? "No documents match your search query. Try another term."
              : "Nothing ingested yet — add your first PDF to get started."}
          </p>
          {!search && (
            <Button className="mt-8 h-12 rounded-xl px-6" variant="outline" onClick={() => setOpen(true)}>
              Add your first document
            </Button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
        {filtered.map((doc, index) => (
          <div
            key={doc.id}
            onClick={() => setLocation(`/documents/${doc.id}`)}
            className="group cursor-pointer h-full outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 rounded-2xl"
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                setLocation(`/documents/${doc.id}`)
              }
            }}
          >
            <Card
              className="relative overflow-hidden transition-all duration-300 hover:shadow-lg hover:-translate-y-1 hover:border-primary/30 flex flex-col h-full rounded-2xl border-border/60 bg-card/80 backdrop-blur-sm animate-slide-in-bottom"
              style={{ animationDelay: `${index * 50}ms` }}
            >
              <CardHeader className="pb-4">
                <div className="flex justify-between items-start mb-3">
                  <div className="p-2.5 bg-primary/5 rounded-xl text-primary shadow-sm border border-primary/10">
                    <FileText className="w-5 h-5" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Badge variant="outline" className="font-mono text-[10px] px-2 py-0.5 border-border/80 bg-background/50">
                      {doc.label}
                    </Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                      disabled={deletingId === doc.id}
                      onClick={(e) => handleDelete(doc, e)}
                      aria-label={`Delete ${doc.title}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
                <CardTitle className="text-lg md:text-xl font-serif font-medium leading-snug line-clamp-2 text-foreground group-hover:text-primary transition-colors">
                  {doc.title}
                </CardTitle>
                <div className="flex items-center gap-2 mt-3">
                  <Badge variant="secondary" className="font-medium text-xs px-2 py-0.5">
                    {doc.collection}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="mt-auto border-t border-border/50 pb-4 pt-4 text-xs text-muted-foreground">
                <div className="mb-4 flex items-center justify-between flex-wrap gap-2">
                  <div className="flex items-center gap-1.5 font-medium">
                    <Clock className="w-3.5 h-3.5" />
                    {formatDate(doc.created_at)}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono bg-secondary/40 px-2 py-1 rounded-md" title="Chunks">
                      {doc.chunk_count} chunks
                    </span>
                    <span className="flex items-center gap-1 font-mono bg-secondary/40 px-2 py-1 rounded-md" title="Images">
                      <ImageIcon className="w-3 h-3" /> {doc.image_count}
                    </span>
                    <span className="flex items-center gap-1 font-mono bg-secondary/40 px-2 py-1 rounded-md" title="Tables">
                      <Table2 className="w-3 h-3" /> {doc.table_count}
                    </span>
                  </div>
                </div>
                <div className="flex items-center text-sm font-medium text-primary mt-2">
                  Open Document <ArrowRight className="h-4 w-4 ml-1 group-hover:translate-x-1 transition-transform" />
                </div>
              </CardContent>
            </Card>
          </div>
        ))}
      </div>
    </div>
  )
}
