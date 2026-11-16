import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowRight, Bot, Cpu, DollarSign, Layers, Wrench, Brain, createLucideIcon, type LucideIcon } from "lucide-react"
import { AgentPanel } from "@/components/agents/AgentPanel"
import { buildGraph, humanize, ICONS, type GraphNode } from "@/components/agents/model"
import { ContextDiagram, CostDiagram, MemoryDiagram, SummarizerDiagram } from "@/components/agents/HarnessDiagrams"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type AgentInfo, type AgentStageInfo, type AgentToolInfo } from "@/lib/api"
import { cn } from "@/lib/utils"

const MIND = "mind"
const ANALYZER = "sub:change_analyzer"

/** The tool groups, in reading order. `delegate` is not here: it is the link between the two agents. */
const GROUPS: { tier: NonNullable<AgentToolInfo["tier"]>; title: string }[] = [
  { tier: "core", title: "Core tools" },
  { tier: "table", title: "Table tools" },
  { tier: "advanced", title: "Steps" },
]

/** lucide's `summary` icon: newer than the installed lucide-react, so defined here with its exact paths. */
const Summary = createLucideIcon("summary", [
  ["path", { d: "M15 4H7", key: "1" }],
  ["path", { d: "m18 16 3 3-3 3", key: "2" }],
  ["path", { d: "M3 4v13a2 2 0 0 0 2 2h16", key: "3" }],
  ["path", { d: "M7 14h7", key: "4" }],
  ["path", { d: "M7 9h12", key: "5" }],
])

const HARNESS_ICONS: Record<string, LucideIcon> = {
  context_builder: Layers,
  summarizer: Summary,
  memory_writer: Brain,
  cost_tracking: DollarSign,
}

/** An icon with a title, and nothing else: everything else opens when it is clicked. */
function Tile({
  icon: Icon,
  title,
  size = "md",
  selected = false,
  highlighted = false,
  dimmed = false,
  dashed = false,
  onClick,
}: {
  icon: LucideIcon
  title: string
  size?: "sm" | "md" | "lg"
  selected?: boolean
  highlighted?: boolean
  dimmed?: boolean
  dashed?: boolean
  onClick: () => void
}) {
  const box = size === "lg" ? "h-20 w-20" : size === "md" ? "h-12 w-12" : "h-10 w-10"
  const glyph = size === "lg" ? "h-9 w-9" : size === "md" ? "h-5 w-5" : "h-4 w-4"
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "group flex flex-col items-center gap-1.5 rounded-xl p-1 text-center transition-opacity",
        size === "lg" ? "w-28" : "w-[4.75rem]",
        dimmed && "opacity-30",
      )}
    >
      <span
        className={cn(
          "flex items-center justify-center rounded-2xl border bg-card text-foreground/80 shadow-sm transition-all group-hover:border-primary/50 group-hover:text-primary",
          box,
          dashed && "border-dashed bg-secondary/30",
          highlighted || selected ? "border-primary/70 text-primary ring-2 ring-primary/25" : "border-border/70",
        )}
      >
        <Icon className={cn(glyph, "stroke-[1.5]")} />
      </span>
      <span
        className={cn(
          "leading-tight text-foreground",
          size === "lg" ? "font-serif text-base font-medium" : "text-[11px] text-muted-foreground group-hover:text-foreground",
          (highlighted || selected) && size !== "lg" && "text-foreground",
        )}
      >
        {title}
      </span>
    </button>
  )
}

function Group({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-widest text-muted-foreground">{title}</h2>
      <div className="flex flex-wrap gap-x-1 gap-y-2">{children}</div>
    </div>
  )
}

/** The Mind's steps, in order, shown when the Mind is opened. */
function MindSteps({ info, onOpen }: { info: AgentInfo; onOpen: (id: string) => void }) {
  const stage = (name: string): AgentStageInfo | undefined => info.stages.find((s) => s.name === name)
  const steps: { id?: string; title: string; text: string; model?: string }[] = [
    { id: "stage:router", title: "Router", text: stage("router")?.description ?? "", model: stage("router")?.model },
    {
      id: "stage:planner",
      title: "Planner",
      text: (stage("planner")?.description ?? "") + " Skipped for simple questions and for requests it delegates.",
      model: stage("planner")?.model,
    },
    {
      title: "Executor (the Mind)",
      text: `Calls tools in a loop of up to ${info.limits.max_steps} steps (several in parallel), reads what they return and writes the answer. Hands version comparisons and work on change tables to the Change Analyzer with Delegate.`,
      model: info.agent.model,
    },
    {
      title: "Answer",
      text: "Streams the answer. Every [S#] citation is checked against what the tools actually returned; the conversation is saved and the memory writer runs.",
    },
  ]
  return (
    <div className="rounded-xl border border-border/60 bg-secondary/20 p-4">
      <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">How the Mind answers</h3>
      <ol className="mt-3 space-y-3">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-3 text-sm">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">{step.title}</span>
                {step.model && (
                  <Badge variant="secondary" className="bg-secondary/50 font-mono text-[10px]">
                    {step.model}
                  </Badge>
                )}
                {step.id && (
                  <button
                    type="button"
                    onClick={() => onOpen(step.id as string)}
                    className="text-xs font-medium text-primary underline-offset-4 hover:underline"
                  >
                    Prompt
                  </button>
                )}
              </div>
              <p className="leading-relaxed text-muted-foreground">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** What the Change Analyzer does and the rules that hold whatever order it picks. */
function AnalyzerNotes({ info }: { info: AgentInfo }) {
  const steps = [
    "Loads the change table of the pair, or starts one with Check Versions. One table exists per pair of documents; a repeated request reuses it.",
    "Decides the order itself. Typical first comparison: check versions, look for a changelog and scan together, verify the candidates, check changelog gaps, write the report. A follow-up does only what it needs.",
    "Works on the table: verify a row, review a chapter in depth, check the changelog, add or archive a row, rewrite the report.",
  ]
  const rules = [
    "The scan is an internal first pass that builds the foundation of the table. It runs once per pair, so you always get the same table, which is then verified and improved over time.",
    "A scan only proposes candidates. A finding joins the table only through verification with verbatim proof.",
    "A scan that failed or crashed midway continues from the rows saved so far; a scan never removes an active row.",
    "A new row is added only after it was compared with every row of the table: a finding that is already there is never added twice.",
    "Deleted and rejected rows are archived with the reason, never destroyed.",
    "The report always reflects the table: it is rewritten automatically whenever the table changes.",
  ]
  return (
    <div className="space-y-4 rounded-xl border border-border/60 bg-secondary/20 p-4">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">How the Change Analyzer works</h3>
        <ol className="mt-3 space-y-2.5">
          {steps.map((text, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                {i + 1}
              </span>
              <p className="leading-relaxed text-muted-foreground">{text}</p>
            </li>
          ))}
        </ol>
      </div>
      <div>
        <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Enforced in code, not by the prompt</h3>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground marker:text-accent">
          {rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-muted-foreground">
        Per request: {info.limits.change_agent_max_steps ?? "–"} steps and {info.limits.change_request_max_model_calls ?? "–"} model
        calls in total (loops inside tools included).
      </p>
    </div>
  )
}

export function Agents() {
  const [info, setInfo] = useState<AgentInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    api
      .getAgent()
      .then(setInfo)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load the agent registry."))
  }, [])

  const graph = useMemo(() => (info ? buildGraph(info) : null), [info])

  // Harness parts have no graph node: give each one so the side panel can show it.
  const harnessNodes = useMemo<GraphNode[]>(
    () =>
      (info?.harness.components ?? []).map((c) => ({
        id: `harness:${c.name}`,
        kind: "stage",
        x: 0,
        y: 0,
        icon: HARNESS_ICONS[c.name] ?? Cpu,
        label: c.title,
        title: c.title,
        role: "Harness · plain code around every agent",
        text: c.description,
        detail: { type: "text", description: c.description, model: c.model, prompt: c.prompt },
      })),
    [info],
  )

  const nodes = useMemo(() => [...(graph?.nodes ?? []), ...harnessNodes], [graph, harnessNodes])
  const selected = nodes.find((n) => n.id === selectedId)
  const close = useCallback(() => setSelectedId(null), [])
  const toggle = (id: string) => setSelectedId((current) => (current === id ? null : id))

  /** The node of a tool, from the first agent that has it. */
  const toolNode = (name: string) => nodes.find((n) => n.kind === "tool" && n.code === name)
  const openTool = (name: string) => {
    const node = toolNode(name)
    if (node) toggle(node.id)
  }

  // Selecting an agent highlights its tools.
  const activeAgent = selectedId === MIND || selectedId === ANALYZER ? selectedId : null
  const owners = useMemo(() => {
    const map = new Map<string, Set<string>>()
    const add = (tool: AgentToolInfo, owner: string) => map.set(tool.name, (map.get(tool.name) ?? new Set()).add(owner))
    info?.agent.tools.forEach((t) => add(t, MIND))
    info?.subagents.forEach((s) => s.tools.forEach((t) => add(t, `sub:${s.name}`)))
    return map
  }, [info])

  const allTools = useMemo(() => {
    const seen = new Map<string, AgentToolInfo>()
    info?.agent.tools.forEach((t) => seen.set(t.name, t))
    info?.subagents.forEach((s) => s.tools.forEach((t) => seen.set(t.name, seen.get(t.name) ?? t)))
    return [...seen.values()]
  }, [info])

  const extra =
    info && selectedId === "harness:context_builder" ? (
      <ContextDiagram harness={info.harness} />
    ) : info && selectedId === "harness:summarizer" ? (
      <SummarizerDiagram harness={info.harness} />
    ) : info && selectedId === "harness:memory_writer" ? (
      <MemoryDiagram harness={info.harness} />
    ) : info && selectedId === "harness:cost_tracking" ? (
      <CostDiagram harness={info.harness} />
    ) : info && selectedId === MIND ? (
      <MindSteps info={info} onOpen={setSelectedId} />
    ) : info && selectedId === ANALYZER ? (
      <AnalyzerNotes info={info} />
    ) : undefined

  const mind = info?.agent
  const analyzer = info?.subagents.find((s) => `sub:${s.name}` === ANALYZER)
  const delegate = toolNode("delegate")
  const stage = (name: string) => nodes.find((n) => n.id === `stage:${name}`)

  return (
    <div className="flex h-full bg-background">
      <div className="h-full min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col p-4 pt-safe pb-safe md:p-8">
          <div className="mb-4 pt-2 md:pt-0">
            <h1 className="flex items-center gap-3 font-serif text-3xl font-medium tracking-tight text-foreground">
              <Bot className="h-7 w-7 text-primary" />
              Agents
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">Click anything for details. Click an agent to see its tools.</p>
          </div>

          {error && (
            <div className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">{error}</div>
          )}
          {!info && !error && <Skeleton className="h-96 w-full rounded-2xl" />}

          {info && mind && (
            <div className="space-y-4">
              {/* The agents: the Mind hands work to the Change Analyzer with Delegate. */}
              <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
                <div className="flex flex-wrap items-start justify-center gap-x-6 gap-y-4">
                  <div className="flex flex-col items-center gap-2">
                    <Tile icon={ICONS.mind} title={humanize(mind.name)} size="lg" selected={selectedId === MIND} onClick={() => toggle(MIND)} />
                    <div className="flex">
                      {["router", "planner"].map((name) => {
                        const node = stage(name)
                        return node ? (
                          <Tile
                            key={name}
                            icon={node.icon}
                            title={node.title}
                            size="sm"
                            dashed
                            selected={selectedId === node.id}
                            onClick={() => toggle(node.id)}
                          />
                        ) : null
                      })}
                    </div>
                  </div>

                  {analyzer && (
                    <>
                      <div className="flex h-20 items-center gap-2 text-muted-foreground">
                        <div className="h-px w-8 bg-border" />
                        {delegate && (
                          <button
                            type="button"
                            onClick={() => toggle(delegate.id)}
                            aria-pressed={selectedId === delegate.id}
                            className={cn(
                              "flex items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/50 hover:text-primary",
                              selectedId === delegate.id ? "border-primary/70 ring-2 ring-primary/25" : "border-border/70",
                            )}
                          >
                            Delegate
                            <ArrowRight className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <div className="h-px w-8 bg-border" />
                      </div>
                      <Tile
                        icon={ICONS[analyzer.name] ?? Bot}
                        title={humanize(analyzer.name)}
                        size="lg"
                        selected={selectedId === ANALYZER}
                        onClick={() => toggle(ANALYZER)}
                      />
                    </>
                  )}
                </div>
              </div>

              {/* All tools, once, in groups. */}
              <div className="grid gap-6 rounded-2xl border border-border/60 bg-card/60 p-5 lg:grid-cols-[1.9fr_1.1fr_1.1fr]">
                {GROUPS.map(({ tier, title }) => {
                  const tools = allTools.filter((t) => t.tier === tier)
                  if (tools.length === 0) return null
                  return (
                    <Group key={tier} title={title}>
                      {tools.map((tool) => {
                        const mine = activeAgent !== null && owners.get(tool.name)?.has(activeAgent) === true
                        return (
                          <Tile
                            key={tool.name}
                            icon={ICONS[tool.name] ?? Wrench}
                            title={humanize(tool.name)}
                            highlighted={mine}
                            dimmed={activeAgent !== null && !mine}
                            selected={toolNode(tool.name)?.id === selectedId}
                            onClick={() => openTool(tool.name)}
                          />
                        )
                      })}
                    </Group>
                  )
                })}
              </div>

              {/* The harness: plain code around every agent. */}
              <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
                <Group title="Harness: plain code around every agent">
                  {harnessNodes.map((node) => (
                    <Tile
                      key={node.id}
                      icon={node.icon}
                      title={node.title}
                      dashed
                      selected={selectedId === node.id}
                      onClick={() => toggle(node.id)}
                    />
                  ))}
                </Group>
              </div>
            </div>
          )}
        </div>
      </div>
      {info && selected && <AgentPanel node={selected} info={info} onSelect={setSelectedId} onClose={close} extra={extra} />}
    </div>
  )
}
