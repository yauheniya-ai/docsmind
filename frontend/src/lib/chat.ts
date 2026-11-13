/** Client for the Mind: streams a chat turn over Server-Sent Events and folds
 * the events into a `Turn` the UI can render while it is still running.
 *
 * `reduceTurn` is a pure function (no React) so the event handling is easy to
 * reason about: every server event maps to exactly one state change.
 */

import { errorFromResponse, type ChangeRow, type ChatCitation } from "./api"

// --- wire types -------------------------------------------------------------

export interface CatalogEntry {
  handle: string
  title: string
  label: string
}

export interface AnalysisDoc {
  handle: string
  title: string
  label: string
  version_id: string
}

export interface AnalysisCounts {
  total: number
  new: number
  modified: number
  removed: number
  editorial: number
  relocated: number
  actionable: number
  verified: number
}

export type ChatEvent =
  | {
      type: "route"
      data: { route: "simple" | "complex"; question: string; documents: number; catalog: CatalogEntry[] }
    }
  | { type: "plan"; data: { steps: string[] } }
  | { type: "tool_start"; data: { id: string; name: string; args: Record<string, unknown>; subagent?: string } }
  | { type: "tool_end"; data: { id: string; name: string; ok: boolean; summary: string; subagent?: string } }
  | { type: "subagent_start"; data: { id: string; name: string; task: string } }
  | { type: "subagent_end"; data: { id: string; name: string; ok: boolean } }
  | {
      type: "analysis_start"
      data: {
        id: string
        current: AnalysisDoc
        previous: AnalysisDoc
        mode: string
        diff_ratio: number | null
        public: boolean | null
        note: string
        reverify?: boolean
      }
    }
  | { type: "analysis_status"; data: { id: string | null; phase: string; text: string } }
  | { type: "change_row"; data: { row: ChangeRow } }
  | { type: "change_row_update"; data: { row: ChangeRow } }
  | { type: "change_row_removed"; data: { id: string; analysis_id: string } }
  | { type: "analysis_done"; data: { id: string; status: string; counts: AnalysisCounts | null } }
  | { type: "token"; data: { text: string } }
  | { type: "note"; data: { text: string } }
  | {
      type: "answer"
      data: {
        answer: string
        citations: ChatCitation[]
        conversation_id: string
        route: "simple" | "complex"
        plan: string[]
      }
    }
  | { type: "error"; data: { code: string; message: string } }
  | { type: "done"; data: { duration_ms: number } }
  // Client-side only:
  | { type: "aborted"; data: Record<string, never> }

export interface ChatRequest {
  message: string
  conversation_id?: string | null
  version_ids?: string[]
  collection?: string
}

// --- turn state -----------------------------------------------------------------

export type TraceStatus = "running" | "ok" | "error"

export interface ToolItem {
  kind: "tool"
  id: string
  name: string
  args: Record<string, unknown>
  status: TraceStatus
  summary?: string
}

export interface SubagentItem {
  kind: "subagent"
  id: string
  name: string
  task: string
  status: TraceStatus
  items: ToolItem[]
}

export interface NoteItem {
  kind: "note"
  id: string
  text: string
}

export type TraceItem = ToolItem | SubagentItem | NoteItem

/** A change analysis started (or re-checked) during a turn; its rows arrive while it runs. */
export interface AnalysisLive {
  id: string
  status: "running" | "done" | "failed" | "stopped"
  /** Unknown for an analysis rebuilt from a stored conversation: the panel fetches it. */
  current?: AnalysisDoc
  previous?: AnalysisDoc
  mode?: string
  reverify?: boolean
  phase?: string
  statusText?: string
  counts?: AnalysisCounts | null
  rows: ChangeRow[]
  removed: number
}

export interface Turn {
  id: string
  question: string
  status: "running" | "done" | "error" | "stopped"
  route?: "simple" | "complex"
  catalog: CatalogEntry[]
  plan: string[]
  trace: TraceItem[]
  analyses: AnalysisLive[]
  streamText: string
  answer: string | null
  citations: ChatCitation[]
  error?: { code: string; message: string }
  startedAt: number
  durationMs?: number
}

export function newTurn(question: string): Turn {
  return {
    id: crypto.randomUUID(),
    question,
    status: "running",
    catalog: [],
    plan: [],
    trace: [],
    analyses: [],
    streamText: "",
    answer: null,
    citations: [],
    startedAt: Date.now(),
  }
}

/** A finished turn rebuilt from a stored conversation (no activity trace is persisted). */
export function storedTurn(
  question: string,
  answer: string,
  citations: ChatCitation[],
  meta: { route?: "simple" | "complex"; plan?: string[]; analyses?: string[] },
): Turn {
  return {
    ...newTurn(question),
    status: "done",
    route: meta.route,
    plan: meta.plan ?? [],
    analyses: (meta.analyses ?? []).map((id) => ({ id, status: "done", rows: [], removed: 0 })),
    answer,
    citations,
  }
}

function patchTool(items: ToolItem[], id: string, patch: Partial<ToolItem>): ToolItem[] {
  return items.map((item) => (item.id === id ? { ...item, ...patch } : item))
}

function patchAnalysis(turn: Turn, id: string | null, patch: (a: AnalysisLive) => AnalysisLive): Turn {
  // Status updates carry no id when they precede the analysis row: they apply to the running one.
  const target = id ?? turn.analyses.find((a) => a.status === "running")?.id
  if (!target) return turn
  return { ...turn, analyses: turn.analyses.map((a) => (a.id === target ? patch(a) : a)) }
}

function upsertRow(rows: ChangeRow[], row: ChangeRow): ChangeRow[] {
  return rows.some((r) => r.id === row.id) ? rows.map((r) => (r.id === row.id ? row : r)) : [...rows, row]
}

export function reduceTurn(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case "route":
      return {
        ...turn,
        route: event.data.route,
        catalog: event.data.catalog ?? [],
      }
    case "plan":
      return { ...turn, plan: event.data.steps }
    case "token":
      return { ...turn, streamText: turn.streamText + event.data.text }
    case "note":
      // Text the model wrote before calling tools is narration, not the answer.
      return {
        ...turn,
        streamText: "",
        trace: [...turn.trace, { kind: "note", id: crypto.randomUUID(), text: event.data.text }],
      }
    case "subagent_start":
      return {
        ...turn,
        trace: [
          ...turn.trace,
          { kind: "subagent", id: event.data.id, name: event.data.name, task: event.data.task, status: "running", items: [] },
        ],
      }
    case "subagent_end":
      return {
        ...turn,
        trace: turn.trace.map((item) =>
          item.kind === "subagent" && item.id === event.data.id
            ? { ...item, status: event.data.ok ? "ok" : "error" }
            : item,
        ),
      }
    case "analysis_start": {
      const { id, current, previous, mode, reverify } = event.data
      const known = turn.analyses.find((a) => a.id === id)
      const next: AnalysisLive = {
        ...(known ?? { rows: [], removed: 0 }),
        id,
        status: "running",
        current,
        previous,
        mode,
        reverify: reverify ?? false,
        phase: undefined,
        statusText: undefined,
      }
      return {
        ...turn,
        analyses: known ? turn.analyses.map((a) => (a.id === id ? next : a)) : [...turn.analyses, next],
      }
    }
    case "analysis_status":
      return patchAnalysis(turn, event.data.id, (a) => ({ ...a, phase: event.data.phase, statusText: event.data.text }))
    case "change_row":
    case "change_row_update": {
      const row = event.data.row
      return patchAnalysis(turn, row.analysis_id, (a) => ({ ...a, rows: upsertRow(a.rows, row) }))
    }
    case "change_row_removed":
      return patchAnalysis(turn, event.data.analysis_id, (a) => ({
        ...a,
        rows: a.rows.filter((r) => r.id !== event.data.id),
        removed: a.removed + 1,
      }))
    case "analysis_done": {
      const status = event.data.status === "failed" || event.data.status === "stopped" ? event.data.status : "done"
      return patchAnalysis(turn, event.data.id, (a) => ({
        ...a,
        status,
        counts: event.data.counts,
        phase: undefined,
        statusText: undefined,
      }))
    }
    case "tool_start": {
      if (event.data.name === "delegate") return turn // shown as the subagent row instead
      const tool: ToolItem = {
        kind: "tool",
        id: event.data.id,
        name: event.data.name,
        args: event.data.args,
        status: "running",
      }
      const parent = event.data.subagent
      if (parent) {
        return {
          ...turn,
          trace: turn.trace.map((item) =>
            item.kind === "subagent" && item.id === parent ? { ...item, items: [...item.items, tool] } : item,
          ),
        }
      }
      return { ...turn, trace: [...turn.trace, tool] }
    }
    case "tool_end": {
      if (event.data.name === "delegate") return turn
      const patch: Partial<ToolItem> = { status: event.data.ok ? "ok" : "error", summary: event.data.summary }
      const parent = event.data.subagent
      return {
        ...turn,
        trace: turn.trace.map((item) => {
          if (parent) {
            return item.kind === "subagent" && item.id === parent
              ? { ...item, items: patchTool(item.items, event.data.id, patch) }
              : item
          }
          return item.kind === "tool" && item.id === event.data.id ? { ...item, ...patch } : item
        }),
      }
    }
    case "answer":
      return {
        ...turn,
        status: "done",
        streamText: "",
        answer: event.data.answer,
        citations: event.data.citations,
        route: event.data.route,
        plan: event.data.plan.length ? event.data.plan : turn.plan,
      }
    case "done":
      return { ...turn, durationMs: event.data.duration_ms }
    case "error":
      return { ...turn, status: "error", streamText: "", error: event.data }
    case "aborted":
      return { ...turn, status: "stopped", streamText: "" }
  }
}

// --- transport -------------------------------------------------------------------------

function parseFrame(frame: string): ChatEvent | null {
  let type = ""
  const dataLines: string[] = []
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue // keep-alive comment
    if (line.startsWith("event:")) type = line.slice(6).trim()
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""))
  }
  if (!type || dataLines.length === 0) return null
  try {
    return { type, data: JSON.parse(dataLines.join("\n")) } as ChatEvent
  } catch {
    return null
  }
}

/** POST a message and call `onEvent` for each server event until the stream
 * ends. Rejects on HTTP errors and when the connection drops mid-answer. */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) throw await errorFromResponse(response)
  if (!response.body) throw new Error("The server sent an empty response.")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let finished = false

  const flush = (final: boolean) => {
    buffer = buffer.replace(/\r\n/g, "\n")
    let boundary = buffer.indexOf("\n\n")
    while (boundary >= 0 || (final && buffer.trim())) {
      const frame = boundary >= 0 ? buffer.slice(0, boundary) : buffer
      buffer = boundary >= 0 ? buffer.slice(boundary + 2) : ""
      const event = parseFrame(frame)
      if (event) {
        if (event.type === "answer" || event.type === "error") finished = true
        onEvent(event)
      }
      boundary = buffer.indexOf("\n\n")
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    flush(false)
  }
  buffer += decoder.decode()
  flush(true)

  if (!finished) throw new Error("The connection closed before the answer finished.")
}
