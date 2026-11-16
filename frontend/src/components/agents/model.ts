import {
  Archive,
  BookOpen,
  Bot,
  BrainCircuit,
  Eye,
  FileSpreadsheet,
  FileText,
  GitCompare,
  GitCompareArrows,
  ListChecks,
  ListTree,
  Library,
  Globe,
  Link2,
  Pencil,
  Plus,
  RotateCcw,
  Route as RouteIcon,
  Layers,
  ScanSearch,
  ScrollText,
  Search,
  ShieldCheck,
  Table2,
  TextSearch,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react"
import type { AgentCardInfo, AgentInfo, AgentStageInfo, AgentToolInfo } from "@/lib/api"

// The graph is laid out on a fixed canvas and scaled with its container, so node
// positions are stored in canvas units and rendered as percentages.
export const W = 1000
export const H = 900
const CENTER = { x: W / 2, y: H / 2 }
const TOOL_ORBIT = 122 // Mind -> its tools
const SUBAGENT_ORBIT = { x: 310, y: 265 } // Mind -> subagents
const SUBAGENT_TOOL_ORBIT = 125 // subagent -> its tools
const STAGE_POSITIONS = [
  { x: 350, y: 75 },
  { x: 650, y: 75 },
]
const PIPELINE_Y = 790 // a subagent's pipeline: one row of stages below everything else

export const ICONS: Record<string, LucideIcon> = {
  mind: BrainCircuit,
  change_analyzer: GitCompareArrows,
  router: RouteIcon,
  planner: ListChecks,
  core: Wrench,
  table: Table2,
  advanced: Layers,
  versions_check: GitCompare,
  scan: ScanSearch,
  changelog: ScrollText,
  verify: ShieldCheck,
  report: FileText,
  search: Search,
  grep: TextSearch,
  read_pages: BookOpen,
  get_outline: ListTree,
  list_documents: Library,
  delegate: Workflow,
  web_search: Globe,
  read_url: Link2,
  page_vision: Eye,
  load_table: Table2,
  create_file: FileSpreadsheet,
  check_versions: GitCompare,
  scan_documents: ScanSearch,
  find_changelog: ScrollText,
  verify_findings: ShieldCheck,
  write_report: FileText,
  get_table: Table2,
  get_row: FileText,
  add_row: Plus,
  update_row: Pencil,
  archive_row: Archive,
  restore_row: RotateCcw,
  export_excel: FileSpreadsheet,
}

/** "change_analyzer" -> "Change Analyzer" */
export function humanize(name: string): string {
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
}

export type Kind = "mind" | "subagent" | "stage" | "tool"

export type NodeDetail =
  | { type: "agent"; agent: AgentCardInfo | AgentStageInfo }
  | { type: "tool"; tool: AgentToolInfo }
  | { type: "text"; description: string; model?: string | null; prompt?: string }

export interface GraphNode {
  id: string
  kind: Kind
  x: number
  y: number
  icon: LucideIcon
  label: string
  /** Tooltip content */
  title: string
  role?: string
  code?: string // technical name, shown for tools
  text: string
  model?: string
  detail: NodeDetail
}

export interface GraphEdge {
  from: string
  to: string
  kind: "agent" | "tool" | "stage"
}

const rad = (deg: number) => (deg * Math.PI) / 180
const polar = (cx: number, cy: number, r: number, deg: number) => ({
  x: cx + r * Math.cos(rad(deg)),
  y: cy + r * Math.sin(rad(deg)),
})

/** Spread `count` points around a circle, starting `offset` degrees from `start`. */
function ring(count: number, start: number, offset: number): number[] {
  return Array.from({ length: count }, (_, i) => start + offset + (i * 360) / count)
}

const TIER_TEXT: Record<string, { role: string; label: string; text: string }> = {
  core: {
    role: "Core tools",
    label: "Core tools",
    text: "Simple, deterministic building blocks shared with the Mind: search, grep, read pages, outline, tables, web, reading URLs (HTML, PDF, Word), page vision and file creation.",
  },
  table: {
    role: "Table tools",
    label: "Table tools",
    text: "Read and edit the change table: list and open rows, add, update, archive and restore. A deleted row is archived with its reason, never destroyed.",
  },
  advanced: {
    role: "Advanced tools (steps)",
    label: "Steps",
    text: "The former pipeline stages as tools the agent calls in any order: check versions, scan, find the changelog, verify findings, write the report. Each can use its own model. A candidate enters the table only through verification (enforced in code).",
  },
}
const TIER_ORDER = ["core", "table", "advanced"]

/** An agent without stages whose tools carry tiers: one group per tier, as a stage-like hub. */
function tierGroups(sub: AgentCardInfo): AgentStageInfo[] {
  const groups: AgentStageInfo[] = []
  for (const tier of TIER_ORDER) {
    const tools = sub.tools.filter((t) => t.tier === tier)
    if (tools.length === 0) continue
    const text = TIER_TEXT[tier]
    groups.push({
      name: tier,
      role: text.role,
      description: text.text,
      model: "",
      system_prompt: "",
      context: [],
      user_prompt: "",
      budget:
        tier === "advanced"
          ? "Each step may use its own model; verification runs one tool loop per row"
          : tier === "table"
            ? "No model call: reads and writes the stored table"
            : "Deterministic tools, no model of their own",
      tools,
    })
  }
  return groups.length > 1 ? groups : []
}

export function buildGraph(info: AgentInfo): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []

  const mind = info.agent
  nodes.push({
    id: "mind",
    kind: "mind",
    ...CENTER,
    icon: ICONS[mind.name.toLowerCase()] ?? Bot,
    label: humanize(mind.name),
    title: humanize(mind.name),
    role: mind.role,
    text: mind.description,
    model: mind.model,
    detail: { type: "agent", agent: mind },
  })

  // The Mind's own tools sit on a ring. Starting at the top keeps them clear of the
  // subagents (left/right) and of the two stages (upper left/right).
  ring(mind.tools.length, -90, 0).forEach((deg, i) => {
    const tool = mind.tools[i]
    const id = `mind:${tool.name}`
    nodes.push({
      id,
      kind: "tool",
      ...polar(CENTER.x, CENTER.y, TOOL_ORBIT, deg),
      icon: ICONS[tool.name] ?? Wrench,
      label: humanize(tool.name),
      title: humanize(tool.name),
      code: tool.name,
      text: tool.description,
      detail: { type: "tool", tool },
    })
    edges.push({ from: "mind", to: id, kind: "tool" })
  })

  // Subagents orbit the Mind (left and right for two), each with its own ring of tools.
  const count = info.subagents.length
  info.subagents.forEach((sub: AgentCardInfo, i) => {
    const deg = 180 + (i * 360) / count
    const pos = {
      x: CENTER.x + SUBAGENT_ORBIT.x * Math.cos(rad(deg)),
      y: CENTER.y + SUBAGENT_ORBIT.y * Math.sin(rad(deg)),
    }
    const id = `sub:${sub.name}`
    nodes.push({
      id,
      kind: "subagent",
      ...pos,
      icon: ICONS[sub.name] ?? Bot,
      label: humanize(sub.name),
      title: humanize(sub.name),
      role: sub.role,
      text: sub.description,
      model: sub.model,
      detail: { type: "agent", agent: sub },
    })
    edges.push({ from: "mind", to: id, kind: "agent" })

    const stages = (sub.stages ?? []).length > 0 ? (sub.stages ?? []) : tierGroups(sub)
    const tiered = (sub.stages ?? []).length === 0 && stages.length > 0
    if (stages.length === 0) {
      // A subagent without a pipeline owns its tools: spread them around it, away from the Mind.
      const towardMind = (Math.atan2(CENTER.y - pos.y, CENTER.x - pos.x) * 180) / Math.PI
      ring(sub.tools.length, towardMind, 180 / Math.max(sub.tools.length, 1)).forEach((toolDeg, k) => {
        const tool = sub.tools[k]
        const toolId = `${id}:${tool.name}`
        nodes.push({
          id: toolId,
          kind: "tool",
          ...polar(pos.x, pos.y, SUBAGENT_TOOL_ORBIT, toolDeg),
          icon: ICONS[tool.name] ?? Wrench,
          label: humanize(tool.name),
          title: humanize(tool.name),
          code: tool.name,
          text: tool.description,
          detail: { type: "tool", tool },
        })
        edges.push({ from: id, to: toolId, kind: "tool" })
      })
      return
    }

    // A code-driven subagent: its stages in order along the bottom, chained. Tools belong to the
    // stages that call them, so each stage fans its own tools out above it. A stage with more
    // tools gets more room along the row.
    const weights = stages.map((s) => 2 + 0.8 * (s.tools?.length ?? 0))
    const total = weights.reduce((a, b) => a + b, 0)
    let used = 0
    let previous = id
    stages.forEach((stage: AgentStageInfo, k) => {
      const stageId = `stage:${sub.name}.${stage.name}`
      const parent = tiered ? id : previous
      const x = ((used + weights[k] / 2) / total) * W
      used += weights[k]
      nodes.push({
        id: stageId,
        kind: "stage",
        x,
        y: PIPELINE_Y,
        icon: ICONS[stage.name] ?? Bot,
        label: tiered ? (TIER_TEXT[stage.name]?.label ?? stage.role) : stage.role,
        title: stage.role,
        role: tiered ? `${stage.role} of the ${humanize(sub.name)}` : `Stage of the ${humanize(sub.name)}`,
        text: stage.description,
        model: stage.model,
        detail: { type: "agent", agent: stage },
      })
      edges.push({ from: parent, to: stageId, kind: "stage" })
      if (!tiered) previous = stageId

      const tools = stage.tools ?? []
      const radius = 95 + 10 * tools.length
      tools.forEach((tool, t) => {
        const toolId = `${stageId}:${tool.name}`
        const deg = tools.length === 1 ? -90 : -150 + (120 * t) / (tools.length - 1) // a fan above the stage
        nodes.push({
          id: toolId,
          kind: "tool",
          ...polar(x, PIPELINE_Y, radius, deg),
          icon: ICONS[tool.name] ?? Wrench,
          label: humanize(tool.name),
          title: humanize(tool.name),
          code: tool.name,
          text: tool.description,
          detail: { type: "tool", tool },
        })
        edges.push({ from: stageId, to: toolId, kind: "tool" })
      })
    })
  })

  // Router and planner are steps inside the Mind, not agents: dashed, no tools.
  info.stages.forEach((stage: AgentStageInfo, i) => {
    const pos = STAGE_POSITIONS[i % STAGE_POSITIONS.length]
    const id = `stage:${stage.name}`
    nodes.push({
      id,
      kind: "stage",
      ...pos,
      icon: ICONS[stage.name] ?? Bot,
      label: humanize(stage.name),
      title: humanize(stage.name),
      role: `${stage.role} · stage of the Mind`,
      text: stage.description,
      model: stage.model,
      detail: { type: "agent", agent: stage },
    })
    edges.push({ from: "mind", to: id, kind: "stage" })
  })

  return { nodes, edges }
}

/** Which agents can call the tool with this name (node id + display name). */
export function toolUsers(info: AgentInfo, toolName: string): { id: string; label: string }[] {
  const users: { id: string; label: string }[] = []
  if (info.agent.tools.some((t) => t.name === toolName)) {
    users.push({ id: "mind", label: humanize(info.agent.name) })
  }
  for (const sub of info.subagents) {
    const stages = (sub.stages ?? []).length > 0 ? (sub.stages ?? []) : tierGroups(sub)
    if (stages.length === 0) {
      if (sub.tools.some((t) => t.name === toolName)) users.push({ id: `sub:${sub.name}`, label: humanize(sub.name) })
      continue
    }
    // Tools belong to the stages that call them.
    for (const stage of stages) {
      if ((stage.tools ?? []).some((t) => t.name === toolName)) {
        users.push({ id: `stage:${sub.name}.${stage.name}`, label: `${humanize(sub.name)} · ${stage.role}` })
      }
    }
  }
  return users
}
