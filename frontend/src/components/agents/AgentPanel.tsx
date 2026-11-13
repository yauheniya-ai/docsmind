import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react"
import { Check, Copy, Layers, MessageSquare, ScrollText, SlidersHorizontal, Wrench, X } from "lucide-react"
import { humanize, ICONS, toolUsers, type GraphNode } from "@/components/agents/model"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { AgentCardInfo, AgentContextBlock, AgentInfo, AgentStageInfo, AgentToolInfo, JsonSchema } from "@/lib/api"
import { cn } from "@/lib/utils"

// -- prompt rendering -------------------------------------------------------------

type PromptBlock =
  | { type: "heading"; text: string }
  | { type: "para"; text: string }
  | { type: "list"; items: string[] }

/** Prompts are plain text with light structure: blank-line separated paragraphs,
 *  "- " bullets and short heading lines that introduce a bullet list. */
function parsePrompt(text: string): PromptBlock[] {
  const blocks: PromptBlock[] = []
  for (const chunk of text.trim().split(/\n{2,}/)) {
    const lines = chunk.split("\n")
    let para: string[] = []
    let list: string[] | null = null
    const flushPara = () => {
      if (para.length) blocks.push({ type: "para", text: para.join("\n") })
      para = []
    }
    const flushList = () => {
      if (list) blocks.push({ type: "list", items: list })
      list = null
    }
    lines.forEach((line, i) => {
      if (line.startsWith("- ")) {
        flushPara()
        list = [...(list ?? []), line.slice(2)]
      } else if (list && /^\s+\S/.test(line)) {
        list[list.length - 1] += `\n${line.trim()}` // wrapped bullet
      } else {
        const isHeading = line.length <= 40 && !/[.!?,)]$/.test(line) && (lines[i + 1] ?? "").startsWith("- ")
        flushList()
        if (isHeading) {
          flushPara()
          blocks.push({ type: "heading", text: line.replace(/:$/, "") })
        } else {
          para.push(line)
        }
      }
    })
    flushPara()
    flushList()
  }
  return blocks
}

const INLINE = /(\[S\d+\]|\{.*\}|"[^"\n]{1,40}")/g

/** Highlight citation handles, JSON shapes and quoted keywords inside a line of prompt text. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(INLINE).map((part, i) => {
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>
        if (/^\[S\d+\]$/.test(part)) {
          return (
            <span
              key={i}
              className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded-md bg-primary/10 px-1 align-text-top text-[10px] font-semibold leading-none text-primary"
            >
              {part.slice(2, -1)}
            </span>
          )
        }
        return (
          <code key={i} className="rounded bg-secondary/70 px-1 py-0.5 font-mono text-[0.8em] text-foreground">
            {part}
          </code>
        )
      })}
    </>
  )
}

function ListItem({ text }: { text: string }) {
  // "search: ranked keyword search" -> the leading name reads as a label.
  const match = /^([a-z_]+): ([\s\S]*)$/.exec(text)
  return (
    <li className="whitespace-pre-line pl-1 leading-relaxed marker:text-accent">
      {match ? (
        <>
          <code className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[0.8em] font-medium text-primary">
            {match[1]}
          </code>{" "}
          <Inline text={match[2]} />
        </>
      ) : (
        <Inline text={text} />
      )}
    </li>
  )
}

function PromptText({ text }: { text: string }) {
  const blocks = useMemo(() => parsePrompt(text), [text])
  return (
    <div className="space-y-3 text-[13.5px] text-foreground/90">
      {blocks.map((block, i) => {
        if (block.type === "heading") {
          return (
            <h4 key={i} className="pt-1 text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
              {block.text}
            </h4>
          )
        }
        if (block.type === "list") {
          return (
            <ul key={i} className="list-disc space-y-1.5 pl-5">
              {block.items.map((item, k) => (
                <ListItem key={k} text={item} />
              ))}
            </ul>
          )
        }
        return (
          <p key={i} className="whitespace-pre-line leading-relaxed">
            <Inline text={block.text} />
          </p>
        )
      })}
    </div>
  )
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setCopied(true))
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : "Copy"}
    </Button>
  )
}

/** A prompt shown formatted, with a switch to the exact raw text the model receives. */
function PromptCard({ text }: { text: string }) {
  const [raw, setRaw] = useState(false)
  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-card/70">
      <div className="flex items-center gap-1 border-b border-border/50 bg-secondary/30 px-2 py-1">
        {(["Formatted", "Raw"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setRaw(mode === "Raw")}
            className={cn(
              "rounded-md px-2 py-1 text-xs font-medium transition-colors",
              (mode === "Raw") === raw ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {mode}
          </button>
        ))}
        <span className="ml-auto" />
        <CopyButton text={text} label="Copy prompt" />
      </div>
      <div className="p-4">
        {raw ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground/90">{text}</pre>
        ) : (
          <PromptText text={text} />
        )}
      </div>
    </div>
  )
}

// -- shared layout --------------------------------------------------------------

function Section({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: typeof Wrench
  title: string
  hint?: string
  children: ReactNode
}) {
  return (
    <section className="space-y-2.5">
      <div>
        <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
          <Icon className="h-3.5 w-3.5" />
          {title}
        </h3>
        {hint && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function Chip({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  const className =
    "inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-card px-2.5 py-1 text-xs font-medium text-foreground"
  return onClick ? (
    <button type="button" onClick={onClick} className={cn(className, "transition-colors hover:border-primary/50 hover:bg-primary/5")}>
      {children}
    </button>
  ) : (
    <span className={className}>{children}</span>
  )
}

// -- agent ------------------------------------------------------------------------

function ContextBlocks({ blocks }: { blocks: AgentContextBlock[] }) {
  return (
    <div className="space-y-2.5">
      {blocks.map((block) => (
        <div key={block.label} className="overflow-hidden rounded-xl border border-border/60 bg-card/70">
          <div className="flex items-center gap-2 border-b border-border/50 bg-secondary/30 px-3 py-1.5">
            <span className="text-xs font-semibold text-foreground">{block.label}</span>
            <Badge variant="secondary" className="bg-secondary/60 text-[10px] font-medium">
              {block.cached ? "cached prefix" : "changes per turn"}
            </Badge>
          </div>
          <pre className="whitespace-pre-wrap break-words p-3 font-mono text-xs leading-relaxed text-muted-foreground">
            {block.text}
          </pre>
        </div>
      ))}
    </div>
  )
}

function AgentDetails({
  agent,
  info,
  nodeId,
  onSelect,
}: {
  agent: AgentCardInfo | AgentStageInfo
  info: AgentInfo
  nodeId: string
  onSelect: (id: string) => void
}) {
  const tools: AgentToolInfo[] = "tools" in agent && agent.tools ? agent.tools : []
  const stages: AgentStageInfo[] = "stages" in agent && agent.stages ? agent.stages : []
  const isMind = nodeId === "mind"
  const isStage = nodeId.startsWith("stage:")
  const steps = isMind ? info.limits.max_steps : isStage ? null : info.limits.verify_max_steps
  const budget =
    "budget" in agent && agent.budget
      ? agent.budget
      : steps
        ? `Up to ${steps} steps${isMind ? "" : " per row check"}`
        : "One model call, no tools"

  return (
    <div className="space-y-7">
      <p className="text-sm leading-relaxed text-muted-foreground">{agent.description}</p>

      <dl className="flex flex-wrap gap-x-8 gap-y-3">
        <div>
          <dt className="mb-1.5 text-xs font-bold uppercase tracking-widest text-muted-foreground">Model</dt>
          <dd>
            <Badge variant="secondary" className="bg-secondary/50 font-mono text-xs">
              {agent.model}
            </Badge>
          </dd>
        </div>
        <div>
          <dt className="mb-1.5 text-xs font-bold uppercase tracking-widest text-muted-foreground">Budget</dt>
          <dd className="text-sm text-foreground">{budget}</dd>
        </div>
      </dl>

      {stages.length > 0 ? (
        <Section
          icon={Layers}
          title="Pipeline"
          hint="Run by code, in this order. Tools belong to the stage that calls them: click a stage."
        >
          <div className="flex flex-wrap gap-2">
            {stages.map((stage) => {
              const Icon = ICONS[stage.name] ?? Wrench
              return (
                <Chip key={stage.name} onClick={() => onSelect(`stage:${agent.name}.${stage.name}`)}>
                  <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                  {stage.role}
                </Chip>
              )
            })}
          </div>
        </Section>
      ) : (
        tools.length > 0 && (
          <Section icon={Wrench} title="Tools" hint="Click a tool for its full definition.">
            <div className="flex flex-wrap gap-2">
              {tools.map((tool) => {
                const Icon = ICONS[tool.name] ?? Wrench
                return (
                  <Chip key={tool.name} onClick={() => onSelect(`${nodeId}:${tool.name}`)}>
                    <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                    {tool.name}
                  </Chip>
                )
              })}
            </div>
          </Section>
        )
      )}

      <Section
        icon={ScrollText}
        title="System prompt"
        hint="The stable instructions, identical on every call, so the provider can cache them."
      >
        <PromptCard text={agent.system_prompt} />
      </Section>

      {agent.context.length > 0 && (
        <Section
          icon={Layers}
          title="Context added to the system prompt"
          hint="Built by the harness around each call. Placeholders in angle brackets are filled in at run time."
        >
          <ContextBlocks blocks={agent.context} />
        </Section>
      )}

      <Section icon={MessageSquare} title="User prompt">
        <div className="rounded-xl border border-border/60 border-l-4 border-l-primary/50 bg-card/70 p-4 text-sm leading-relaxed text-foreground/90">
          {agent.user_prompt}
        </div>
      </Section>
    </div>
  )
}

// -- tool -------------------------------------------------------------------------

function typeLabel(schema: JsonSchema): string {
  if (schema.$ref) return schema.$ref.split("/").pop() ?? "object"
  if (schema.anyOf) {
    const parts = schema.anyOf.filter((s) => s.type !== "null").map(typeLabel)
    return parts.join(" | ") || "null"
  }
  if (schema.enum) return schema.enum.map((v) => JSON.stringify(v)).join(" | ")
  if (schema.type === "array") return `${schema.items ? typeLabel(schema.items) : "any"}[]`
  if (Array.isArray(schema.type)) return schema.type.join(" | ")
  return schema.type ?? "any"
}

function constraints(schema: JsonSchema): string[] {
  const out: string[] = []
  for (const s of [schema, ...(schema.anyOf ?? [])]) {
    if (s.minimum !== undefined) out.push(`min ${s.minimum}`)
    if (s.maximum !== undefined) out.push(`max ${s.maximum}`)
    if (s.minLength !== undefined) out.push(`min length ${s.minLength}`)
    if (s.maxLength !== undefined) out.push(`max length ${s.maxLength}`)
    if (s.minItems !== undefined) out.push(`min ${s.minItems} items`)
    if (s.maxItems !== undefined) out.push(`max ${s.maxItems} items`)
  }
  return out
}

function ArgumentList({ schema }: { schema: JsonSchema }) {
  const entries = Object.entries(schema.properties ?? {})
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">This tool takes no arguments.</p>
  }
  const required = new Set(schema.required ?? [])
  return (
    <div className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/60 bg-card/70">
      {entries.map(([name, prop]) => {
        const notes = constraints(prop)
        const hasDefault = prop.default !== undefined && prop.default !== null
        return (
          <div key={name} className="space-y-1.5 p-3.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <code className="font-mono text-sm font-semibold text-foreground">{name}</code>
              <code className="rounded bg-secondary/70 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                {typeLabel(prop)}
              </code>
              <Badge
                variant={required.has(name) ? "default" : "secondary"}
                className={cn("text-[10px] font-medium", !required.has(name) && "bg-secondary/60")}
              >
                {required.has(name) ? "required" : "optional"}
              </Badge>
              {hasDefault && (
                <span className="text-[11px] text-muted-foreground">
                  default <code className="font-mono">{JSON.stringify(prop.default)}</code>
                </span>
              )}
              {notes.map((note) => (
                <span key={note} className="text-[11px] text-muted-foreground">
                  {note}
                </span>
              ))}
            </div>
            {prop.description && <p className="text-[13px] leading-relaxed text-muted-foreground">{prop.description}</p>}
          </div>
        )
      })}
    </div>
  )
}

function ToolDetails({
  tool,
  info,
  onSelect,
}: {
  tool: AgentToolInfo
  info: AgentInfo
  onSelect: (id: string) => void
}) {
  const users = toolUsers(info, tool.name)
  const schemaText = JSON.stringify(tool.parameters, null, 2)
  return (
    <div className="space-y-7">
      <p className="text-sm leading-relaxed text-muted-foreground">{tool.description}</p>

      {tool.how_it_works && tool.how_it_works.length > 0 && (
        <Section icon={Layers} title="How it works">
          <ol className="space-y-2">
            {tool.how_it_works.map((step, i) => (
              <li key={i} className="flex gap-2.5 text-[13px] leading-relaxed text-foreground/90">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </Section>
      )}

      <Section icon={Wrench} title="Available to">
        <div className="flex flex-wrap gap-2">
          {users.map((user) => (
            <Chip key={user.id} onClick={() => onSelect(user.id)}>
              {user.label}
            </Chip>
          ))}
        </div>
      </Section>

      <Section
        icon={ScrollText}
        title="Description the model reads"
        hint="Sent with every call so the model knows when to use the tool."
      >
        <PromptCard text={tool.instructions} />
      </Section>

      <Section icon={SlidersHorizontal} title="Arguments" hint="What the model must supply, validated before the tool runs.">
        <ArgumentList schema={tool.parameters} />
        <details className="group rounded-xl border border-border/60 bg-card/70">
          <summary className="flex cursor-pointer select-none items-center justify-between px-3.5 py-2 text-xs font-medium text-muted-foreground hover:text-foreground">
            JSON Schema
            <span onClick={(e) => e.stopPropagation()}>
              <CopyButton text={schemaText} label="Copy JSON Schema" />
            </span>
          </summary>
          <pre className="overflow-x-auto border-t border-border/50 p-3.5 font-mono text-xs leading-relaxed text-foreground/90">
            {schemaText}
          </pre>
        </details>
      </Section>
    </div>
  )
}

// -- panel ------------------------------------------------------------------------

/**
 * Side panel next to the graph: the selected agent's prompts or the selected
 * tool's full definition, like the source panel next to the chat.
 */
export function AgentPanel({
  node,
  info,
  onSelect,
  onClose,
}: {
  node: GraphNode
  info: AgentInfo
  onSelect: (id: string) => void
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const Icon = node.icon
  const isTool = node.detail.type === "tool"

  return (
    <aside
      aria-label={isTool ? "Tool details" : "Agent details"}
      className="fixed inset-0 z-40 flex flex-col bg-background md:static md:z-auto md:h-full md:w-[48%] md:min-w-[380px] md:max-w-[820px] md:shrink-0 md:animate-fade-in md:border-l md:border-border/60"
    >
      <div className="flex shrink-0 items-start gap-3 border-b border-border/60 bg-secondary/30 px-4 py-3 pt-safe md:pt-3">
        <div className="mt-0.5 shrink-0 rounded-lg border border-primary/10 bg-primary/5 p-2 text-primary">
          <Icon className="h-5 w-5 stroke-[1.5]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2 className="font-serif text-lg font-medium text-foreground">{node.title}</h2>
            {node.code && (
              <code className="rounded bg-secondary/70 px-1.5 py-0.5 font-mono text-[11px]">{node.code}</code>
            )}
          </div>
          <div className="text-xs text-muted-foreground">{isTool ? "Tool" : node.role ?? humanize(node.kind)}</div>
        </div>
        <Button variant="ghost" size="icon" className="-mr-1 h-7 w-7 shrink-0" onClick={onClose} aria-label="Close details">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 pb-safe md:px-6">
        {node.detail.type === "agent" ? (
          <AgentDetails agent={node.detail.agent} info={info} nodeId={node.id} onSelect={onSelect} />
        ) : (
          <ToolDetails tool={node.detail.tool} info={info} onSelect={onSelect} />
        )}
      </div>
    </aside>
  )
}
