import { useState } from "react"
import { cn } from "@/lib/utils"

const SHORT = 110 // characters: a one-line question reads best as a title

/**
 * The user's message at the top of a turn. A short question is a title; a long one (an instruction, a pasted
 * passage) is a quiet card in normal text, clamped to a few lines with "Show more".
 */
export function UserQuestion({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const short = text.length <= SHORT && !text.includes("\n")
  if (short) {
    return (
      <h2 className="border-b border-border/50 pb-4 font-serif text-2xl font-medium tracking-tight text-foreground md:text-3xl">
        {text}
      </h2>
    )
  }
  const long = text.length > 320 || text.split("\n").length > 4
  return (
    <div className="rounded-xl border border-border/60 bg-secondary/30 px-4 py-3">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Your request</div>
      <p
        className={cn(
          "whitespace-pre-wrap break-words text-[15px] leading-relaxed text-foreground/90",
          long && !expanded && "line-clamp-4",
        )}
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-1.5 text-xs font-medium text-primary underline-offset-4 hover:underline"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  )
}
