import { useMemo, useState } from "react"
import { H, W, type GraphEdge, type GraphNode, type Kind } from "@/components/agents/model"
import { cn } from "@/lib/utils"

const NODE_STYLE: Record<Kind, { box: string; icon: string; radius: number }> = {
  mind: {
    box: "h-24 w-24 rounded-[1.75rem] border-primary/40 bg-card shadow-lg ring-4 ring-primary/10",
    icon: "h-11 w-11 text-primary",
    radius: 48,
  },
  subagent: {
    box: "h-[4.5rem] w-[4.5rem] rounded-2xl border-border bg-card shadow-md",
    icon: "h-8 w-8 text-primary",
    radius: 36,
  },
  stage: {
    box: "h-14 w-14 rounded-xl border-dashed border-muted-foreground/50 bg-secondary",
    icon: "h-6 w-6 text-muted-foreground",
    radius: 28,
  },
  tool: {
    box: "h-11 w-11 rounded-full border-border/70 bg-card shadow-sm",
    icon: "h-[1.15rem] w-[1.15rem] text-muted-foreground",
    radius: 22,
  },
}

function Tooltip({ node }: { node: GraphNode }) {
  const { radius } = NODE_STYLE[node.kind]
  // Keep the card inside the canvas: lean away from the nearest side edge, and
  // open upward when the node is in the lower half.
  const shift = node.x < 250 ? "-15%" : node.x > 750 ? "-85%" : "-50%"
  const above = node.y > H * 0.6
  const gap = above ? -(radius + 8) : radius + 30 // below also clears the node's label
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-30 w-64 rounded-xl border border-border/70 bg-popover/95 p-3.5 text-left shadow-xl backdrop-blur-sm animate-fade-in"
      style={{
        left: `${(node.x / W) * 100}%`,
        top: `calc(${(node.y / H) * 100}% + ${gap}px)`,
        transform: `translate(${shift}, ${above ? "-100%" : "0"})`,
      }}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-serif text-base font-medium text-foreground">{node.title}</span>
        {node.code && <code className="rounded bg-secondary/70 px-1.5 py-0.5 font-mono text-[11px]">{node.code}</code>}
        {node.role && (
          <span className="rounded-md border border-border/60 bg-background/60 px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
            {node.role}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">{node.text}</p>
      {node.model && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Model <code className="ml-1 rounded bg-primary px-1.5 py-0.5 font-mono text-primary-foreground">{node.model}</code>
        </p>
      )}
      <p className="mt-2 text-[11px] font-medium text-primary">Click for full details</p>
    </div>
  )
}

/** The Mind at the centre, its tools around it, subagents (with their own tools) beyond. */
export function AgentGraph({
  nodes,
  edges,
  selectedId,
  onSelect,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const [hovered, setHovered] = useState<string | null>(null)
  const activeId = hovered ?? selectedId
  const hoveredNode = hovered ? byId.get(hovered) : undefined

  // Highlight the node's own connections, and fade the rest a little.
  const related = useMemo(() => {
    if (!activeId) return null
    const ids = new Set<string>([activeId])
    for (const edge of edges) {
      if (edge.from === activeId) ids.add(edge.to)
      if (edge.to === activeId) ids.add(edge.from)
    }
    return ids
  }, [activeId, edges])

  return (
    <div className="overflow-x-auto rounded-2xl border border-border/60 bg-card/40">
      <div
        className="relative mx-auto min-w-[760px] max-w-5xl"
        style={{ aspectRatio: `${W} / ${H}` }}
        onClick={() => onSelect(null)}
      >
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 h-full w-full" aria-hidden="true">
          {edges.map((edge) => {
            const a = byId.get(edge.from)
            const b = byId.get(edge.to)
            if (!a || !b) return null
            const lit = !related || (related.has(edge.from) && related.has(edge.to))
            return (
              <line
                key={`${edge.from}-${edge.to}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                strokeLinecap="round"
                className={cn(
                  "transition-opacity duration-150",
                  edge.kind === "agent" && "stroke-primary/45",
                  edge.kind === "tool" && "stroke-border",
                  edge.kind === "stage" && "stroke-muted-foreground/40",
                  !lit && "opacity-25",
                )}
                strokeWidth={edge.kind === "agent" ? 3 : 1.5}
                strokeDasharray={edge.kind === "stage" ? "6 6" : undefined}
              />
            )
          })}
        </svg>

        {nodes.map((node) => {
          const style = NODE_STYLE[node.kind]
          const Icon = node.icon
          const dim = related !== null && !related.has(node.id)
          const selected = selectedId === node.id
          return (
            <div
              key={node.id}
              className="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
              style={{ left: `${(node.x / W) * 100}%`, top: `${(node.y / H) * 100}%` }}
            >
              <button
                type="button"
                aria-label={`${node.title}: ${node.text}`}
                aria-pressed={selected}
                onMouseEnter={() => setHovered(node.id)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(node.id)}
                onBlur={() => setHovered(null)}
                onClick={(event) => {
                  event.stopPropagation()
                  setHovered(null)
                  onSelect(selected ? null : node.id)
                }}
                className={cn(
                  "flex items-center justify-center border transition-all duration-150",
                  "hover:-translate-y-0.5 hover:border-primary/60 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                  style.box,
                  selected && "border-primary bg-secondary ring-2 ring-primary/40",
                  dim && "opacity-40",
                )}
              >
                <Icon className={cn("stroke-[1.5]", style.icon)} />
              </button>
              <span
                className={cn(
                  "pointer-events-none mt-1.5 whitespace-nowrap text-center transition-opacity duration-150",
                  node.kind === "mind" && "font-serif text-lg font-medium text-foreground",
                  node.kind === "subagent" && "font-serif text-base font-medium text-foreground",
                  node.kind === "stage" && "text-xs font-medium text-muted-foreground",
                  node.kind === "tool" && "text-[11px] text-muted-foreground",
                  dim && "opacity-40",
                )}
              >
                {node.label}
              </span>
            </div>
          )
        })}

        {hoveredNode && <Tooltip node={hoveredNode} />}
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border/50 bg-secondary/20 px-4 py-2.5 text-xs text-muted-foreground">
        <LegendItem swatch="h-4 w-4 rounded-md border border-primary/40 bg-card" label="Agent" />
        <LegendItem swatch="h-4 w-4 rounded-full border border-border bg-card" label="Tool" />
        <LegendItem swatch="h-4 w-4 rounded-md border border-dashed border-muted-foreground/60" label="Stage: a step inside an agent" />
        <span className="ml-auto hidden sm:inline">Hover for a summary, click for prompts and details</span>
      </div>
    </div>
  )
}

function LegendItem({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="flex items-center gap-2">
      <span className={swatch} />
      {label}
    </span>
  )
}
