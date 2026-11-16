import type { ReactNode } from "react"
import type { AgentInfo } from "@/lib/api"
import { cn } from "@/lib/utils"

type Harness = AgentInfo["harness"]

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">{children}</h3>
}

function Box({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("rounded-xl border border-border/60 bg-secondary/20 p-4", className)}>{children}</div>
}

const usd = (value: number | null | undefined) => (value == null ? "–" : `$${value.toFixed(value < 1 ? 3 : 2)}`)

// -- context builder ----------------------------------------------------------------

const LAYERS: { label: string; note: string; cached: boolean; who: [boolean, boolean, boolean] }[] = [
  { label: "Role prompt + rules", note: "identical on every call", cached: true, who: [true, true, true] },
  { label: "Register of documents", note: "changes only when documents change", cached: true, who: [true, true, true] },
  { label: "Memory", note: "notes from earlier conversations", cached: false, who: [true, true, true] },
  { label: "Conversation summary", note: "older turns, folded", cached: false, who: [true, true, true] },
  { label: "Plan", note: "only for the Mind, only if planned", cached: false, who: [false, false, true] },
  { label: "Recent history", note: "router: last 2 exchanges, planner: none", cached: false, who: [true, false, true] },
  { label: "Your question", note: "never rewritten", cached: false, who: [true, true, true] },
]

export function ContextDiagram({ harness }: { harness: Harness }) {
  const budget = harness.settings?.history_budget_tokens
  return (
    <div className="space-y-4">
      <Box>
        <Heading>What goes into one prompt, top to bottom</Heading>
        <ol className="mt-3 space-y-1.5">
          {LAYERS.map((layer, i) => (
            <li key={layer.label}>
              <div
                className="harness-layer flex items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 text-sm"
                style={{
                  animationDelay: `${i * 120}ms`,
                  borderColor: layer.cached ? "hsl(var(--primary) / 0.5)" : undefined,
                }}
              >
                <span className="font-medium text-foreground">{layer.label}</span>
                <span className="text-right text-xs text-muted-foreground">{layer.note}</span>
              </div>
              {i === 1 && (
                <div className="my-1.5 flex items-center gap-2 text-[11px] font-medium text-accent">
                  <div className="harness-flow h-0.5 flex-1" />
                  <span className="harness-glow rounded-full border border-accent/40 px-2 py-0.5">cache mark</span>
                  <div className="harness-flow h-0.5 flex-1" />
                </div>
              )}
            </li>
          ))}
        </ol>
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Everything above the cache mark is the same from call to call, so the provider can reuse it at a fraction of the
          price. Everything below changes per turn.
          {budget ? ` History is trimmed from the oldest end to fit ${budget.toLocaleString()} tokens.` : ""}
        </p>
      </Box>

      <Box>
        <Heading>Who sees what</Heading>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium" />
                {["Router", "Planner", "Mind"].map((h) => (
                  <th key={h} className="px-2 py-1 text-center font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {LAYERS.map((layer) => (
                <tr key={layer.label} className="border-t border-border/50">
                  <td className="py-1.5 pr-3 text-foreground">{layer.label}</td>
                  {layer.who.map((yes, i) => (
                    <td key={i} className={cn("px-2 py-1.5 text-center", yes ? "text-primary" : "text-muted-foreground/40")}>
                      {yes ? "●" : "–"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          The Change Analyzer's stages build their own fresh prompts: no history, memory or plan.
        </p>
      </Box>
    </div>
  )
}

// -- summarizer -------------------------------------------------------------------------

function Chip({ kind, label, className, style }: { kind: "old" | "recent"; label: string; className?: string; style?: React.CSSProperties }) {
  return (
    <span
      style={style}
      className={cn(
        "inline-flex h-7 min-w-9 items-center justify-center rounded-md border px-1.5 font-mono text-[10px]",
        kind === "old" ? "border-border/70 bg-secondary/60 text-muted-foreground" : "border-primary/50 bg-card text-foreground",
        className,
      )}
    >
      {label}
    </span>
  )
}

export function SummarizerDiagram({ harness }: { harness: Harness }) {
  const s = harness.settings
  const recent = s?.recent_turns ?? 4
  const older = 5
  return (
    <div className="space-y-4">
      <Box>
        <Heading>One conversation, once it has outgrown the budget</Heading>
        <div className="mt-4 flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="w-24 shrink-0 text-xs text-muted-foreground">Running summary</span>
            <div className="harness-glow flex-1 rounded-lg border border-accent/50 bg-card px-3 py-2 text-xs text-foreground">
              Goal, documents and pages discussed, findings, decisions, open questions
              <span className="ml-1 text-muted-foreground">· at most {s?.summary_max_words ?? 250} words</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="w-24 shrink-0 text-xs text-muted-foreground">Folded in</span>
            <div className="flex flex-wrap gap-1.5">
              {Array.from({ length: older }, (_, i) => (
                <Chip
                  key={i}
                  kind="old"
                  label={`Q${i + 1}·A${i + 1}`}
                  className="harness-fold"
                  style={{ animationDelay: `${i * 160}ms`, ["--fold-x" as string]: "-24px" }}
                />
              ))}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="w-24 shrink-0 text-xs text-muted-foreground">Word for word</span>
            <div className="flex flex-wrap gap-1.5">
              {Array.from({ length: recent }, (_, i) => (
                <Chip key={i} kind="recent" label={`Q${older + i + 1}·A${older + i + 1}`} />
              ))}
              <Chip kind="recent" label="new question" className="border-accent/60" />
            </div>
          </div>
        </div>
      </Box>

      <Box>
        <Heading>When it runs and what it costs</Heading>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground marker:text-accent">
          <li>
            At the start of a turn (never in the background), only if the history not yet summarized is over{" "}
            <b className="text-foreground">{(s?.history_budget_tokens ?? 6000).toLocaleString()} tokens</b>. Below that it costs
            nothing.
          </li>
          <li>
            It then takes the old summary plus all turns older than the last {recent} exchanges, and writes a new summary
            that replaces the old one. The summary is rewritten, not appended: it stays at{" "}
            <b className="text-foreground">{s?.summary_max_words ?? 250} words at most</b> (roughly 350 tokens; hard cap{" "}
            {s?.summary_max_output_tokens ?? 600} output tokens), however long the conversation gets.
          </li>
          <li>If it fails, the full history is replayed instead; the conversation never breaks.</li>
          <li>Citation markers like [S1] are stripped before summarizing, because they only mean something within one turn.</li>
        </ul>
      </Box>
    </div>
  )
}

// -- memory writer ------------------------------------------------------------------------

function Step({ title, text, last }: { title: string; text: string; last?: boolean }) {
  return (
    <div className="flex items-stretch gap-2">
      <div className="flex-1 rounded-lg border border-border/70 bg-card px-3 py-2">
        <div className="text-xs font-semibold text-foreground">{title}</div>
        <div className="text-xs leading-relaxed text-muted-foreground">{text}</div>
      </div>
      {!last && <span className="self-center text-muted-foreground">→</span>}
    </div>
  )
}

export function MemoryDiagram({ harness }: { harness: Harness }) {
  const s = harness.settings
  const rows: [string, string, string][] = [
    ["Scope", "One conversation", "All conversations"],
    ["Kind", "Short-term (working) memory", "Long-term memory: facts and document notes"],
    ["Holds", "A summary of what was said", "Durable facts the user stated, verified navigation notes"],
    ["Written", "When the history outgrows its budget", "After every answer"],
    ["Size", `${s?.summary_max_words ?? 250} words`, `up to ${s?.memory_max_items_per_kind ?? 3} facts + ${s?.memory_max_items_per_kind ?? 3} notes per answer, ${s?.memory_note_max_chars ?? 240} characters each`],
    ["Read", "Always, for that conversation", `Newest ${s?.memory_recall_limit ?? 20} items on every turn`],
  ]
  return (
    <div className="space-y-4">
      <Box>
        <Heading>How a note is written and recalled</Heading>
        <div className="mt-3 grid gap-2">
          <Step title="1 · Answer is sent" text="The question, the answer and the documents it used." />
          <Step title="2 · Small model extracts" text={`Up to ${s?.memory_max_items_per_kind ?? 3} facts and ${s?.memory_max_items_per_kind ?? 3} document notes, as JSON.`} />
          <Step title="3 · Code validates" text="Notes about an unknown document are dropped; text is cleaned and cut; duplicates are ignored." />
          <Step title="4 · Stored in the database" text="The agent never writes memory itself." last />
        </div>
        <div className="mt-2 flex justify-center"><div className="harness-flow h-5 w-0.5 [background-size:2px_12px] [animation-name:none]" /></div>
        <Step
          title="Next conversation: recalled"
          text={`No search and no embeddings: the newest ${s?.memory_recall_limit ?? 20} items. Facts are always included; a document note only when that document is in the register. They are shown to the agent in a <memory> block, labelled possibly outdated, to be verified against the documents.`}
          last
        />
      </Box>

      <Box>
        <Heading>Summarizer vs. memory</Heading>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium" />
                <th className="px-2 py-1 font-medium">Summarizer</th>
                <th className="px-2 py-1 font-medium">Memory writer</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([k, a, b]) => (
                <tr key={k} className="border-t border-border/50 align-top">
                  <td className="py-1.5 pr-3 font-medium text-foreground">{k}</td>
                  <td className="px-2 py-1.5 text-muted-foreground">{a}</td>
                  <td className="px-2 py-1.5 text-muted-foreground">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          In short: the summarizer lets one long conversation fit in the context; the memory carries what is worth knowing into
          the next one. There is no episodic memory: past conversations are not replayed, only the notes taken from them.
        </p>
      </Box>
    </div>
  )
}

// -- cost tracking ------------------------------------------------------------------------

export function CostDiagram({ harness }: { harness: Harness }) {
  const prices = harness.prices ?? []
  return (
    <div className="space-y-4">
      <Box>
        <Heading>How a call is priced</Heading>
        <p className="mt-2 rounded-lg border border-border/60 bg-card px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground">
          cost = uncached × input + cached read × cached price + cache write × write price + output × output price
        </p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground marker:text-accent">
          <li>Every model call reports its tokens in four buckets; the harness adds them up per model, per tool and per run.</li>
          <li>The cache hit rate is cached input tokens divided by all input tokens. The cost without caching is computed too, so the saving is visible.</li>
          <li>A model without a known price shows no cost rather than a wrong one. If only the cache prices are unknown, the input price is used and the figure is marked as an upper bound.</li>
        </ul>
      </Box>

      <Box>
        <Heading>Prices in force (USD per 1M tokens)</Heading>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                {["Model", "Input", "Cached read", "Cache write", "Output"].map((h, i) => (
                  <th key={h} className={cn("py-1 font-medium", i > 0 && "px-2 text-right")}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {prices.map((p) => (
                <tr key={p.model} className="border-t border-border/50 align-top">
                  <td className="py-1.5 pr-2">
                    <div className="font-mono text-foreground">{p.model}</div>
                    <div className="text-[11px] text-muted-foreground">{p.used_by}</div>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-foreground">{usd(p.input)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-foreground">
                    {usd(p.cached_read)}
                    {p.cached_read != null && !p.cache_prices_known && "*"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-foreground">
                    {usd(p.cache_write)}
                    {p.cache_write != null && !p.cache_prices_known && "*"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-foreground">{usd(p.output)}</td>
                </tr>
              ))}
              {prices.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-2 text-muted-foreground">
                    No prices available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          Built-in table, overridable with <code className="font-mono">DOCSMIND_MODEL_PRICES</code>. Prices drift over time: check them against your provider.
          * not published for this model: the input price is used, so costs are an upper bound.
        </p>
      </Box>
    </div>
  )
}
