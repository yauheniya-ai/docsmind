import { useState } from "react"
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  FileSpreadsheet,
  FileText,
  Globe,
  Link2,
  ListTree,
  Loader2,
  Route as RouteIcon,
  Search,
  Table2,
  TextSearch,
  X,
  Bot,
  type LucideIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { CatalogEntry, SubagentItem, ToolItem, TraceItem, TraceStatus, Turn } from "@/lib/chat"

const SUBAGENT_LABELS: Record<string, string> = {
  change_analyzer: "Change Analyzer",
}

function docTitle(catalog: CatalogEntry[], handle: unknown): string {
  if (typeof handle !== "string") return "document"
  const entry = catalog.find((c) => c.handle === handle.toUpperCase())
  return entry ? entry.title : handle
}

function str(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return "a web page"
  }
}

function pages(args: Record<string, unknown>): string {
  const start = args.start_page
  const end = args.end_page
  if (typeof start !== "number") return ""
  return typeof end === "number" && end !== start ? `pp.${start}-${end}` : `p.${start}`
}

function scope(catalog: CatalogEntry[], args: Record<string, unknown>): string {
  const docs = Array.isArray(args.documents) ? (args.documents as unknown[]) : []
  if (docs.length === 0) return ""
  return ` in ${docs.map((d) => docTitle(catalog, d)).join(", ")}`
}

function describeTool(tool: ToolItem, catalog: CatalogEntry[]): { icon: LucideIcon; label: string } {
  const running = tool.status === "running"
  const a = tool.args
  switch (tool.name) {
    case "search":
      return { icon: Search, label: `${running ? "Searching" : "Searched"} “${str(a.query)}”${scope(catalog, a)}` }
    case "grep":
      return { icon: TextSearch, label: `${running ? "Looking for" : "Looked for"} “${str(a.text)}”${scope(catalog, a)}` }
    case "read_pages":
      return {
        icon: BookOpen,
        label: `${running ? "Reading" : "Read"} ${docTitle(catalog, a.document)} ${pages(a)}`.trim(),
      }
    case "get_outline":
      return { icon: ListTree, label: `${running ? "Opening" : "Opened"} outline of ${docTitle(catalog, a.document)}` }
    case "list_documents":
      return { icon: FileText, label: running ? "Listing documents" : "Listed documents" }
    case "web_search":
      return { icon: Globe, label: `${running ? "Searching the web for" : "Searched the web for"} “${str(a.query)}”` }
    case "fetch_url":
      return { icon: Link2, label: `${running ? "Reading" : "Read"} ${hostOf(str(a.url))}` }
    case "page_vision": {
      const nums = Array.isArray(a.pages) ? (a.pages as unknown[]).join(", ") : ""
      return {
        icon: Eye,
        label: `${running ? "Looking at" : "Looked at"} ${docTitle(catalog, a.document)}${nums ? ` p.${nums}` : ""} as an image`,
      }
    }
    case "load_table":
      return { icon: Table2, label: `${running ? "Reading" : "Read"} the table at ${hostOf(str(a.url))}` }
    case "export_excel":
      return { icon: FileSpreadsheet, label: running ? "Writing the Excel file" : "Wrote the Excel file" }
    default:
      return { icon: Search, label: tool.name }
  }
}

function StatusIcon({ status }: { status: TraceStatus }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
  if (status === "error") return <X className="h-3.5 w-3.5 shrink-0 text-destructive" />
  return <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
}

function ToolRow({ tool, catalog }: { tool: ToolItem; catalog: CatalogEntry[] }) {
  const { icon: Icon, label } = describeTool(tool, catalog)
  return (
    <li className="flex items-start gap-2 py-0.5">
      <StatusIcon status={tool.status} />
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 text-foreground/80">
        {label}
        {tool.summary && tool.status !== "running" && (
          <span className={cn("ml-2 text-xs", tool.status === "error" ? "text-destructive" : "text-muted-foreground")}>
            {tool.summary}
          </span>
        )}
      </span>
    </li>
  )
}

function SubagentRow({ item, catalog }: { item: SubagentItem; catalog: CatalogEntry[] }) {
  const [open, setOpen] = useState(true)
  return (
    <li className="py-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 text-left"
        aria-expanded={open}
      >
        <StatusIcon status={item.status} />
        <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1">
          <span className="font-medium text-foreground">{SUBAGENT_LABELS[item.name] ?? item.name}</span>
          <span className="ml-2 line-clamp-2 text-xs text-muted-foreground">{item.task}</span>
        </span>
        {open ? (
          <ChevronDown className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
      {open && item.items.length > 0 && (
        <ul className="ml-[1.1rem] mt-1 border-l border-border/60 pl-3">
          {item.items.map((tool) => (
            <ToolRow key={tool.id} tool={tool} catalog={catalog} />
          ))}
        </ul>
      )}
    </li>
  )
}

function currentActivity(turn: Turn): string {
  const flat: ToolItem[] = turn.trace.flatMap((item) =>
    item.kind === "tool" ? [item] : item.kind === "subagent" ? item.items : [],
  )
  const subagents = turn.trace.filter((i): i is SubagentItem => i.kind === "subagent" && i.status === "running")
  const running = [...flat].reverse().find((t) => t.status === "running")
  if (subagents.length > 1) return `Working with ${subagents.length} subagents…`
  if (running) return `${describeTool(running, turn.catalog).label}…`
  if (turn.streamText) return "Writing the answer…"
  if (turn.route === "complex" && turn.plan.length === 0) return "Planning…"
  return "Thinking…"
}

function summarize(turn: Turn): string {
  let tools = 0
  let subagents = 0
  for (const item of turn.trace) {
    if (item.kind === "tool") tools += 1
    if (item.kind === "subagent") {
      subagents += 1
      tools += item.items.length
    }
  }
  const parts: string[] = []
  if (tools) parts.push(`${tools} ${tools === 1 ? "lookup" : "lookups"}`)
  if (subagents) parts.push(`${subagents} ${subagents === 1 ? "subagent" : "subagents"}`)
  if (turn.durationMs) parts.push(`${(turn.durationMs / 1000).toFixed(1)}s`)
  return parts.length ? parts.join(" · ") : "Answered directly"
}

/** What the Mind did to produce an answer: route, plan, tool calls, subagents. */
export function AgentTrace({ turn }: { turn: Turn }) {
  const running = turn.status === "running"
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? running
  const hasBody = turn.route != null || turn.trace.length > 0
  if (!running && !hasBody) return null

  return (
    <div className="rounded-xl border border-border/50 bg-secondary/20 text-sm">
      <button
        type="button"
        onClick={() => setUserOpen(!open)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
        aria-expanded={open}
      >
        {running ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        ) : (
          <RouteIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className={cn("min-w-0 flex-1 truncate", running ? "text-foreground" : "text-muted-foreground")}>
          {running ? currentActivity(turn) : summarize(turn)}
        </span>
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="space-y-3 border-t border-border/50 px-3.5 py-3">
          {turn.route && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="rounded-md bg-background/70 px-2 py-0.5 font-medium">
                {turn.route === "complex" ? "Complex question · planned" : "Direct question"}
              </span>
              <span>{turn.catalog.length} {turn.catalog.length === 1 ? "document" : "documents"} in scope</span>
            </div>
          )}

          {turn.plan.length > 0 && (
            <ol className="list-decimal space-y-0.5 pl-5 text-foreground/80 marker:text-muted-foreground">
              {turn.plan.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          )}

          {turn.trace.length > 0 && (
            <ul>
              {turn.trace.map((item: TraceItem) =>
                item.kind === "tool" ? (
                  <ToolRow key={item.id} tool={item} catalog={turn.catalog} />
                ) : item.kind === "subagent" ? (
                  <SubagentRow key={item.id} item={item} catalog={turn.catalog} />
                ) : (
                  <li key={item.id} className="py-1 text-xs italic text-muted-foreground">
                    {item.text}
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
