import { useEffect, useState } from "react"
import { GitCompareArrows, Loader2, FileSpreadsheet } from "lucide-react"
import { Button } from "@/components/ui/button"
import { api, isActionable, type ChangeDetail, type ChangeRow } from "@/lib/api"
import type { AnalysisLive } from "@/lib/chat"
import { cn } from "@/lib/utils"

function summary(rows: ChangeRow[]): string {
  const relevant = rows.filter(isActionable)
  const verified = relevant.filter((r) => r.verified).length
  const parts = [`${rows.length} ${rows.length === 1 ? "change" : "changes"}`]
  if (relevant.length) parts.push(`${relevant.length} compliance-relevant`, `${verified} verified`)
  return parts.join(" · ")
}

/**
 * The comparison a turn started, shown under the activity trace: what is being compared, how far it is,
 * and a button that opens the table and report next to the chat.
 */
export function ChangeCard({
  analysis,
  active,
  onOpen,
}: {
  analysis: AnalysisLive
  active: boolean
  onOpen: (id: string) => void
}) {
  // A comparison rebuilt from a stored conversation knows nothing but its id: fetch what to show.
  const [stored, setStored] = useState<ChangeDetail | null>(null)
  const needsFetch = analysis.status !== "running" && !analysis.current
  useEffect(() => {
    if (!needsFetch) return
    let cancelled = false
    api
      .getChange(analysis.id)
      .then((d) => {
        if (!cancelled) setStored(d)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [analysis.id, needsFetch])

  const current = analysis.current ?? (stored?.current ? { ...stored.current, handle: "" } : undefined)
  const previous = analysis.previous ?? (stored?.previous ? { ...stored.previous, handle: "" } : undefined)
  const rows = analysis.rows.length ? analysis.rows : (stored?.rows ?? [])
  const running = analysis.status === "running"
  const label = (d: { title: string; label: string }) => `${d.title} ${d.label}`.trim()

  return (
    <div className={cn("flex items-start gap-3 rounded-xl border bg-card p-3.5", active ? "border-primary/50" : "border-border/60")}>
      <GitCompareArrows className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="text-sm font-medium text-foreground">
          {analysis.reverify ? (running ? "Working on the table" : "Existing change analysis") : "Change analysis"}
          {current && previous ? `: ${label(current)} vs ${label(previous)}` : ""}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {running ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              <span className="truncate">
                {analysis.statusText ?? "Starting…"}
                {rows.length ? ` · ${rows.length} rows so far` : ""}
              </span>
            </>
          ) : analysis.status === "failed" || analysis.status === "stopped" ? (
            <span>{analysis.status === "failed" ? "Failed" : "Stopped"}{rows.length ? ` · ${rows.length} unverified rows kept` : ""}</span>
          ) : rows.length || stored ? (
            <span>{summary(rows)}</span>
          ) : (
            <span>Done</span>
          )}
        </div>
      </div>
      <Button size="sm" variant={active ? "secondary" : "outline"} className="shrink-0 gap-1.5" onClick={() => onOpen(analysis.id)}>
        <FileSpreadsheet className="h-4 w-4" />
        {active ? "Open" : "Table & report"}
      </Button>
    </div>
  )
}
