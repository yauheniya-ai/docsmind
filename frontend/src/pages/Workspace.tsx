import { useCallback, useEffect, useRef, useState } from "react"
import { Link } from "wouter"
import { AlertTriangle, Library, Plus, RotateCcw, Send, Sparkles, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SelectMenu } from "@/components/ui/select-menu"
import Graph3D from "@/components/art/Graph3D"
import { AgentTrace } from "@/components/chat/AgentTrace"
import { ChangeCard } from "@/components/chat/ChangeCard"
import { ChangePanel } from "@/components/chat/ChangePanel"
import { Markdown } from "@/components/chat/Markdown"
import { SourceCards } from "@/components/chat/SourceCards"
import { SourcePanel } from "@/components/chat/SourcePanel"
import { api, type ChatCitation, type ConversationDetail, type DocumentSummary } from "@/lib/api"
import { newTurn, reduceTurn, storedTurn, streamChat, type ChatEvent, type Turn } from "@/lib/chat"
import { useTypewriter } from "@/lib/useTypewriter"

const STICK_THRESHOLD_PX = 160

// Cycled (typed and erased) as the input's placeholder while it is empty.
const EXAMPLES = [
  "What changed between NIST 800-171 Revision 2 and Revision 3?",
  "Summarize the password requirements in PCI DSS.",
  "Which controls cover multi-factor authentication?",
]

function turnsFromConversation(conversation: ConversationDetail): Turn[] {
  const turns: Turn[] = []
  const messages = conversation.messages
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i]
    if (message.role !== "user") continue
    const reply = messages[i + 1]?.role === "assistant" ? messages[i + 1] : undefined
    if (reply) {
      turns.push(storedTurn(message.content, reply.content, reply.citations, reply.meta))
      i += 1
    }
  }
  return turns
}

function setUrl(search: string) {
  window.history.replaceState(null, "", `${window.location.pathname}${search}`)
}

function errorText(turn: Turn): string {
  const error = turn.error
  if (!error) return "Something went wrong."
  if (error.code === "llm_not_configured") {
    return `${error.message} Set DOCSMIND_LLM_PROVIDER and the matching API key, then restart the server.`
  }
  return error.message
}

export function Workspace() {
  const params = new URLSearchParams(window.location.search)
  const [query, setQuery] = useState("")
  const [scope, setScope] = useState<string>(() => params.get("scope") ?? "all")
  const [turns, setTurns] = useState<Turn[]>([])
  const [conversationId, setConversationId] = useState<string | null>(() => params.get("c"))
  const [loadingConversation, setLoadingConversation] = useState(() => params.get("c") !== null)
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null)
  const [openSource, setOpenSource] = useState<ChatCitation | null>(null)
  const [openChange, setOpenChange] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const stickRef = useRef(true)
  const conversationRef = useRef<string | null>(conversationId)

  const asking = turns.some((turn) => turn.status === "running")
  const canAsk = (documents?.length ?? 0) > 0
  const typedPlaceholder = useTypewriter(EXAMPLES, canAsk && query === "")

  useEffect(() => {
    api
      .listDocuments()
      .then(setDocuments)
      .catch(() => setDocuments([]))
  }, [])

  // Open an existing conversation from `/?c=<id>`.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("c")
    if (!id) return
    let cancelled = false
    api
      .getConversation(id)
      .then((conversation) => {
        if (cancelled) return
        setTurns(turnsFromConversation(conversation))
        conversationRef.current = conversation.id
        setConversationId(conversation.id)
      })
      .catch(() => {
        if (cancelled) return
        conversationRef.current = null
        setConversationId(null)
        setUrl("")
      })
      .finally(() => {
        if (!cancelled) setLoadingConversation(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Abort an in-flight turn when leaving the page.
  useEffect(() => () => abortRef.current?.abort(), [])

  // Keep the newest content in view, unless the user scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [turns])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX
  }

  const patchTurn = useCallback((id: string, event: ChatEvent) => {
    setTurns((previous) => previous.map((turn) => (turn.id === id ? reduceTurn(turn, event) : turn)))
  }, [])

  const send = useCallback(
    async (question: string, replaceId?: string) => {
      const text = question.trim()
      if (!text || abortRef.current) return

      const turn = newTurn(text)
      setTurns((previous) => [...previous.filter((t) => t.id !== replaceId), turn])
      stickRef.current = true

      const controller = new AbortController()
      abortRef.current = controller
      try {
        await streamChat(
          {
            message: text,
            conversation_id: conversationRef.current,
            version_ids: scope === "all" ? undefined : [scope],
          },
          (event) => {
            if (event.type === "answer") {
              conversationRef.current = event.data.conversation_id
              setConversationId(event.data.conversation_id)
              setUrl(`?c=${event.data.conversation_id}`)
            }
            if (event.type === "analysis_start" && !event.data.reverify) {
              // Show the table as it is being built.
              setOpenChange(event.data.id)
              setOpenSource(null)
            }
            patchTurn(turn.id, event)
          },
          controller.signal,
        )
      } catch (error) {
        if (controller.signal.aborted) {
          patchTurn(turn.id, { type: "aborted", data: {} })
        } else {
          const message = error instanceof Error ? error.message : "The request failed."
          patchTurn(turn.id, { type: "error", data: { code: "request_failed", message } })
        }
      } finally {
        abortRef.current = null
        textareaRef.current?.focus()
      }
    },
    [patchTurn, scope],
  )

  const submit = () => {
    if (!query.trim() || asking || !canAsk) return
    const question = query
    setQuery("")
    if (textareaRef.current) textareaRef.current.style.height = ""
    void send(question)
  }

  const stop = () => abortRef.current?.abort()

  const closeSource = useCallback(() => setOpenSource(null), [])
  const closeChange = useCallback(() => setOpenChange(null), [])
  const showSource = useCallback((citation: ChatCitation) => {
    // Layered over an open change panel, which keeps its state underneath.
    setOpenSource(citation)
  }, [])
  const showChange = useCallback((id: string) => {
    setOpenChange(id)
    setOpenSource(null)
  }, [])
  // The newest state of the open comparison (a later turn may be re-checking it).
  const liveChange = openChange
    ? turns.flatMap((turn) => turn.analyses).findLast((analysis) => analysis.id === openChange)
    : undefined

  const newChat = () => {
    abortRef.current?.abort()
    abortRef.current = null
    conversationRef.current = null
    setConversationId(null)
    setOpenSource(null)
    setOpenChange(null)
    setTurns([])
    setQuery("")
    setUrl("")
  }

  // "Ask again" links (`/?q=...`) auto-submit once the Register has loaded.
  const autoSubmitted = useRef(false)
  useEffect(() => {
    if (autoSubmitted.current || !documents || documents.length === 0 || loadingConversation) return
    const q = new URLSearchParams(window.location.search).get("q")
    if (!q) return
    autoSubmitted.current = true
    setUrl(conversationRef.current ? `?c=${conversationRef.current}` : "")
    queueMicrotask(() => void send(q))
  }, [documents, loadingConversation, send])

  const empty = turns.length === 0 && !loadingConversation

  return (
    <div className="flex h-full bg-background pt-safe md:pt-0">
    <div className="flex h-full min-w-0 flex-1 flex-col">
      {conversationId && (
        <div className="flex shrink-0 items-center justify-end px-4 pt-3 md:px-8">
          <Button variant="ghost" size="sm" onClick={newChat} className="gap-1.5 text-muted-foreground">
            <Plus className="h-4 w-4" />
            New chat
          </Button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto" ref={scrollRef} onScroll={handleScroll}>
        {empty ? (
          <div className="mx-auto flex min-h-full w-full max-w-3xl animate-fade-in flex-col items-center justify-center p-6 text-center md:p-8">
            <div className="mb-4 h-[280px] w-[320px] shrink-0">
              <Graph3D />
            </div>
            <h1 className="mb-4 font-serif text-3xl font-medium tracking-tight text-foreground md:text-4xl">
              Ask the Mind
            </h1>
            <p className="mb-8 max-w-xl text-base leading-relaxed text-muted-foreground md:text-lg">
              Questions are answered from your documents, with every claim linked back to the page it came
              from.
            </p>
            <div className="flex items-center gap-2 rounded-full border border-border/60 bg-card/50 px-4 py-2 text-sm text-muted-foreground backdrop-blur-sm">
              <Library className="h-4 w-4 text-primary" />
              {documents === null
                ? "Connecting to your Register…"
                : `${documents.length} ${documents.length === 1 ? "Document" : "Documents"} connected`}
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-4xl animate-fade-in space-y-12 p-4 pb-32 md:p-8">
            {turns.map((turn) => (
              <section key={turn.id} className="animate-slide-in-bottom space-y-5">
                <h2 className="border-b border-border/50 pb-4 font-serif text-2xl font-medium tracking-tight text-foreground md:text-3xl">
                  {turn.question}
                </h2>

                <AgentTrace turn={turn} />

                {turn.analyses.map((analysis) => (
                  <ChangeCard
                    key={analysis.id}
                    analysis={analysis}
                    active={openChange === analysis.id}
                    onOpen={showChange}
                  />
                ))}

                {(turn.answer ?? turn.streamText) && (
                  <div className="flex items-start gap-3">
                    <Sparkles className="mt-1.5 h-5 w-5 shrink-0 text-accent" />
                    <Markdown
                      text={turn.answer ?? turn.streamText}
                      citations={turn.citations}
                      onOpenCitation={showSource}
                    />
                  </div>
                )}

                {turn.status === "stopped" && <p className="text-sm italic text-muted-foreground">Stopped.</p>}

                {turn.status === "error" && (
                  <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive md:text-base">
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive/80" />
                    <div className="flex-1 space-y-2">
                      <p>{errorText(turn)}</p>
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        disabled={asking}
                        onClick={() => void send(turn.question, turn.id)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        Try again
                      </Button>
                    </div>
                  </div>
                )}

                <SourceCards
                  citations={turn.citations}
                  activeChunkId={openSource?.chunk_id}
                  onOpen={showSource}
                />
              </section>
            ))}
          </div>
        )}
      </div>

      <div className="pb-safe mx-auto w-full max-w-4xl shrink-0 bg-gradient-to-t from-background via-background to-transparent p-4 pt-6 md:p-6">
        <div className="mb-2.5 flex items-center justify-between gap-3 px-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground md:text-xs">
            Search scope
          </span>
          <Link href="/documents" className="text-xs font-medium text-primary underline-offset-4 hover:underline">
            Manage Register
          </Link>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="rounded-2xl border border-border/80 bg-card shadow-sm transition-all focus-within:border-primary/50 focus-within:ring-4 focus-within:ring-primary/10"
        >
          <SelectMenu
            aria-label="Search scope"
            placement="top"
            value={scope}
            onChange={setScope}
            triggerClassName="rounded-t-2xl border-b border-border/60 hover:bg-secondary/30 transition-colors"
            options={[
              { value: "all", label: "All Documents in the Register" },
              ...(documents ?? []).map((doc) => ({
                value: doc.id,
                label: doc.title,
                description: `${doc.collection} / ${doc.label}`,
              })),
            ]}
          />
          <div className="flex items-end gap-2 p-2">
            <textarea
              ref={textareaRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                e.target.style.height = "auto"
                e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  submit()
                }
              }}
              placeholder={canAsk ? typedPlaceholder : "Add a document to the Register first…"}
              aria-label="Ask the Mind about your documents"
              disabled={!canAsk}
              rows={1}
              className="flex-1 resize-none bg-transparent px-3 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 md:text-base"
            />
            {asking ? (
              <Button
                type="button"
                size="icon"
                variant="secondary"
                className="h-10 w-10 shrink-0 rounded-xl"
                onClick={stop}
                aria-label="Stop"
              >
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                className="h-10 w-10 shrink-0 rounded-xl"
                disabled={!query.trim() || !canAsk}
                aria-label="Ask"
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </form>
      </div>
    </div>
    {openChange && (
      <ChangePanel
        key={openChange}
        analysisId={openChange}
        live={liveChange}
        hidden={Boolean(openSource)}
        onClose={closeChange}
        onOpenSource={showSource}
      />
    )}
    {openSource && <SourcePanel citation={openSource} onClose={closeSource} />}
    </div>
  )
}
