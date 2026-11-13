import { useCallback, useEffect, useState } from "react"
import { Link } from "wouter"
import { AlertCircle, ArrowRight, BookOpen, Clock, MessageSquare, Search, Trash2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type ConversationSummary } from "@/lib/api"

function relativeTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "Recently"
  const diffMin = Math.round((Date.now() - date.getTime()) / 60000)
  if (diffMin < 1) return "just now"
  if (diffMin < 60) return `${diffMin} min ago`
  const diffHr = Math.round(diffMin / 60)
  if (diffHr < 24) return `${diffHr} hr ago`
  const diffDay = Math.round(diffHr / 24)
  return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`
}

/** Drop [S#] markers: previews are plain text. */
function preview(text: string): string {
  return text.replace(/\[S\d+\]/g, "").replace(/[*_`#>|]/g, "").replace(/\s+/g, " ").trim()
}

export function Activity() {
  const [search, setSearch] = useState("")
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .listConversations()
      .then((items) => {
        setConversations(items)
        setError(null)
      })
      .catch((e: unknown) => {
        setConversations([])
        setError(e instanceof Error ? e.message : "Could not load conversations.")
      })
  }, [])

  useEffect(load, [load])

  const remove = async (id: string) => {
    setConversations((items) => items?.filter((c) => c.id !== id) ?? null)
    try {
      await api.deleteConversation(id)
    } catch {
      load()
    }
  }

  const value = search.toLowerCase()
  const filtered = (conversations ?? []).filter(
    (item) =>
      item.title.toLowerCase().includes(value) ||
      item.last_answer.toLowerCase().includes(value) ||
      item.sources.some((s) => s.title.toLowerCase().includes(value)),
  )

  return (
    <div className="mx-auto w-full max-w-4xl p-4 pt-safe pb-safe md:p-8">
      <div className="mb-8 flex flex-col justify-between gap-5 pt-4 md:flex-row md:items-end md:pt-0">
        <div>
          <h1 className="flex items-center gap-3 font-serif text-3xl font-medium tracking-tight text-foreground md:text-4xl">
            <Clock className="h-7 w-7 text-primary md:h-8 md:w-8" />
            Activity
          </h1>
          <p className="mt-2 text-sm text-muted-foreground md:text-base">
            Past conversations with the Mind, and the documents used to support them.
          </p>
        </div>
        <Button asChild className="h-11 gap-2 rounded-xl px-6 shadow-sm transition-all hover:shadow-md">
          <Link href="/">
            Ask the Mind <ArrowRight className="h-4 w-4" />
          </Link>
        </Button>
      </div>

      <div className="mb-8 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative w-full max-w-md">
          <Search className="absolute left-3.5 top-3.5 h-5 w-5 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search conversations or documents…"
            className="h-12 w-full rounded-xl border-border/60 bg-card pl-11 shadow-sm"
          />
        </div>
        <Badge
          variant="secondary"
          className="h-9 shrink-0 self-start rounded-lg bg-secondary/50 px-4 font-mono font-medium sm:self-auto"
        >
          {filtered.length} {filtered.length === 1 ? "conversation" : "conversations"}
        </Badge>
      </div>

      {conversations === null && (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full rounded-2xl" />
          <Skeleton className="h-32 w-full rounded-2xl" />
        </div>
      )}

      {error && (
        <div className="mb-6 rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {conversations !== null && conversations.length === 0 && !error && (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-border/80 bg-card/50 p-12 text-center md:p-16">
          <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-secondary/50">
            <MessageSquare className="h-10 w-10 text-muted-foreground/60" />
          </div>
          <h3 className="font-serif text-xl font-medium text-foreground md:text-2xl">No conversations yet</h3>
          <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground md:text-base">
            Conversations from the Workspace are saved here with the documents they used.
          </p>
        </div>
      )}

      {conversations !== null && conversations.length > 0 && filtered.length === 0 && (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-border/80 bg-card/50 p-12 text-center md:p-16">
          <AlertCircle className="mb-4 h-10 w-10 text-muted-foreground/60" />
          <h3 className="font-serif text-xl font-medium text-foreground md:text-2xl">Nothing matches your search</h3>
        </div>
      )}

      <div className="space-y-5 md:space-y-6">
        {filtered.map((conversation, index) => (
          <Card
            key={conversation.id}
            className="animate-slide-in-bottom overflow-hidden rounded-2xl border-border/60 bg-card/80 backdrop-blur-sm transition-all hover:border-primary/30 hover:shadow-md"
            style={{ animationDelay: `${Math.min(index, 10) * 45}ms` }}
          >
            <CardContent className="flex h-full flex-col p-0">
              <Link
                href={`/?c=${conversation.id}`}
                className="block border-b border-border/50 p-5 transition-colors hover:bg-secondary/10 md:p-8"
              >
                <div className="mb-3 flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                  <h2 className="font-serif text-xl font-medium leading-snug tracking-tight text-foreground md:text-2xl">
                    {conversation.title}
                  </h2>
                  <span className="shrink-0 self-start whitespace-nowrap rounded bg-secondary/50 px-2 py-1 font-mono text-xs text-muted-foreground">
                    {relativeTime(conversation.updated_at)}
                  </span>
                </div>
                <p className="line-clamp-3 text-sm leading-relaxed text-foreground/80 md:text-base">
                  {preview(conversation.last_answer)}
                </p>
                <p className="mt-3 text-xs text-muted-foreground">
                  {conversation.message_count} {conversation.message_count === 1 ? "message" : "messages"}
                </p>
              </Link>

              <div className="mt-auto flex flex-col justify-between gap-4 bg-secondary/20 px-5 py-4 sm:flex-row sm:items-center md:px-8">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="mr-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                    <BookOpen className="h-4 w-4" />
                    Documents
                  </span>
                  {conversation.sources.length > 0 ? (
                    conversation.sources.map((source) => (
                      <Link
                        key={source.version_id}
                        href={`/documents/${source.version_id}`}
                        className="rounded-lg border border-border/60 bg-card/80 px-3 py-1.5 text-xs font-medium text-foreground transition-all hover:border-primary/40 hover:bg-card hover:text-primary active:scale-[0.98]"
                      >
                        {source.title}
                      </Link>
                    ))
                  ) : (
                    <Badge variant="secondary" className="bg-background/50 font-medium">
                      No sources
                    </Badge>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="-ml-2 h-9 shrink-0 gap-2 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive sm:ml-0"
                  onClick={() => void remove(conversation.id)}
                >
                  <Trash2 className="h-4 w-4" />
                  Delete
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
