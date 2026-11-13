import { useCallback, useEffect, useMemo, useState } from "react"
import { Bot, Cpu } from "lucide-react"
import { AgentGraph } from "@/components/agents/AgentGraph"
import { AgentPanel } from "@/components/agents/AgentPanel"
import { buildGraph } from "@/components/agents/model"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type AgentInfo } from "@/lib/api"

function HarnessCard({ info }: { info: AgentInfo }) {
  const rows: [string, string][] = [
    ["Router", info.harness.router_model],
    ["Summarizer", info.harness.summarizer_model],
    ["Memory writer", info.harness.memory_enabled ? info.harness.memory_model : "disabled"],
  ]
  return (
    <Card className="rounded-2xl border-border/60 bg-card/80 shadow-sm">
      <CardContent className="p-5 md:p-6">
        <div className="flex items-start gap-4">
          <div className="shrink-0 rounded-xl border border-primary/10 bg-primary/5 p-3 text-primary">
            <Cpu className="h-6 w-6 stroke-[1.5]" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-serif text-xl font-medium tracking-tight text-foreground">Harness</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Plain code around every agent, not an agent itself. It builds each agent's context, keeps the stable
              prefix cacheable, summarizes long conversations and saves durable notes to memory after each answer.
            </p>
          </div>
        </div>
        <dl className="mt-5 grid gap-4 sm:grid-cols-3">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt className="mb-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">{label}</dt>
              <dd>
                <Badge variant="secondary" className="bg-secondary/50 font-mono text-xs">
                  {value}
                </Badge>
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-xs text-muted-foreground">
          Limits: {info.limits.max_steps} steps per answer, {info.limits.verify_max_steps} per row check.
        </p>
      </CardContent>
    </Card>
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
  const selected = graph?.nodes.find((n) => n.id === selectedId)
  const closePanel = useCallback(() => setSelectedId(null), [])

  return (
    <div className="flex h-full bg-background">
      <div className="h-full min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl p-4 pt-safe pb-safe md:p-8">
          <div className="mb-8 pt-4 md:pt-0">
            <h1 className="flex items-center gap-3 font-serif text-3xl font-medium tracking-tight text-foreground md:text-4xl">
              <Bot className="h-7 w-7 text-primary md:h-8 md:w-8" />
              Agents
            </h1>
            <p className="mt-2 text-sm text-muted-foreground md:text-base">
              The Mind at the centre, the tools it uses, and the subagents it delegates to with theirs.
            </p>
          </div>

          {error && (
            <div className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
              {error}
            </div>
          )}

          {!info && !error && <Skeleton className="aspect-[1000/800] w-full rounded-2xl" />}

          {info && graph && (
            <div className="space-y-8">
              <AgentGraph nodes={graph.nodes} edges={graph.edges} selectedId={selectedId} onSelect={setSelectedId} />
              <HarnessCard info={info} />
            </div>
          )}
        </div>
      </div>
      {info && selected && <AgentPanel node={selected} info={info} onSelect={setSelectedId} onClose={closePanel} />}
    </div>
  )
}
