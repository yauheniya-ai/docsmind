import { Fragment, useCallback, useEffect, useMemo, useState } from "react"
import { BookOpen, Check, ChevronDown, ChevronRight, Download, Loader2, Plus, ShieldCheck, Star, Trash2, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { Markdown } from "@/components/chat/Markdown"
import {
  api,
  isActionable,
  type ChangeBinding,
  type ChangeDetail,
  type ChangeNature,
  type ChangeRow,
  type ChangeRowInput,
  type ChatCitation,
} from "@/lib/api"
import type { AnalysisLive } from "@/lib/chat"
import { pageCitation } from "@/lib/citations"
import { cn } from "@/lib/utils"

const NATURES: ChangeNature[] = ["new", "modified", "removed", "editorial", "relocated"]
const BINDINGS: ChangeBinding[] = ["normative", "recommended", "optional", "informative", "unchanged"]

const NATURE_STYLE: Record<ChangeNature, string> = {
  new: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  modified: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  removed: "bg-red-500/10 text-red-700 dark:text-red-400",
  editorial: "bg-secondary text-muted-foreground",
  relocated: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
}

const FIELD_LABELS: { key: keyof ChangeRowInput; label: string; long?: boolean }[] = [
  { key: "current_clause", label: "Current clause (A)" },
  { key: "previous_clause", label: "Previous clause (B)" },
  { key: "change_description", label: "Change description", long: true },
  { key: "category", label: "Category" },
  { key: "compliance_requirement", label: "Compliance requirement", long: true },
  { key: "applicability_scope", label: "Applicability scope" },
]

type Filter = "all" | "actionable"
type Tab = "table" | "report"

function NatureBadge({ nature }: { nature: ChangeNature }) {
  return (
    <span className={cn("rounded-md px-1.5 py-0.5 text-[11px] font-semibold capitalize", NATURE_STYLE[nature])}>
      {nature}
    </span>
  )
}

function proofLabel(proof: ChangeRow["proof"][number]): string {
  return `${proof.doc === "current" ? "A" : "B"}${proof.page ? ` p.${proof.page}` : ""}`
}

function VerifiedMark({
  row,
  busy,
  canVerify,
  onAsk,
}: {
  row: ChangeRow
  busy: boolean
  canVerify: boolean
  /** Opens the "launch a verification?" question; nothing starts before the person answers yes. */
  onAsk: () => void
}) {
  if (busy) return <Loader2 className="h-4 w-4 animate-spin text-accent" aria-label="Verifying" />
  const star = (
    <Star className={cn("h-4 w-4", row.verified ? "fill-amber-400 text-amber-500" : "text-muted-foreground/40")} />
  )
  if (row.verified) {
    return (
      <span title={`Verified. ${row.proof.map((p) => `${proofLabel(p)}: “${p.text}”`).join("\n")}`} aria-label="Verified">
        {star}
      </span>
    )
  }
  const why = row.verification_note ?? (isActionable(row) ? "Not verified." : "Not checked: no compliance requirement changed.")
  if (!canVerify) return <span title={why} aria-label="Not verified">{star}</span>
  return (
    <button
      type="button"
      title={`${why} Click to verify this row.`}
      aria-label="Verify this row"
      className="-m-0.5 rounded p-0.5 hover:bg-secondary"
      onClick={(e) => {
        e.stopPropagation()
        onAsk()
      }}
    >
      {star}
    </button>
  )
}

/** A small yes / no question under a table cell. Escape and No close it; only Yes acts. */
function ConfirmPopover({
  question,
  align,
  onYes,
  onNo,
}: {
  question: string
  align: "left" | "right"
  onYes: () => void
  onNo: () => void
}) {
  return (
    <div
      role="alertdialog"
      aria-label={question}
      className={cn(
        "absolute top-full z-20 mt-1 w-52 rounded-lg border border-border bg-background p-3 text-left shadow-lg",
        align === "right" ? "right-2" : "left-2",
      )}
    >
      <p className="text-sm font-medium text-foreground">{question}</p>
      <div className="mt-2 flex gap-2">
        <Button size="sm" className="h-7 bg-accent px-3 text-accent-foreground hover:bg-accent/90" onClick={onYes}>
          Yes
        </Button>
        <Button size="sm" variant="outline" className="h-7 px-3" onClick={onNo}>
          No
        </Button>
      </div>
    </div>
  )
}

/** The "approved by a person" box, in the app's accent color instead of the browser's checkbox. */
function ApproveBox({ checked, disabled, onClick }: { checked: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label="Approved"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex h-5 w-5 items-center justify-center rounded border transition-colors disabled:opacity-50",
        checked ? "border-accent bg-accent text-accent-foreground" : "border-input bg-background hover:border-accent",
      )}
    >
      {checked && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
    </button>
  )
}

function RowEditor({
  row,
  onSave,
  onCancel,
}: {
  row: ChangeRow
  onSave: (patch: ChangeRowInput) => Promise<void>
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<Required<ChangeRowInput>>({
    current_clause: row.current_clause,
    previous_clause: row.previous_clause,
    change_description: row.change_description,
    nature: row.nature,
    category: row.category,
    compliance_requirement: row.compliance_requirement,
    binding: row.binding,
    applicability_scope: row.applicability_scope,
  })
  const [saving, setSaving] = useState(false)
  const set = (key: keyof ChangeRowInput, value: string) => setDraft((d) => ({ ...d, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      await onSave(draft)
    } catch {
      // The panel shows the error; the form stays open so nothing typed is lost.
    } finally {
      setSaving(false)
    }
  }

  const select = "h-9 rounded-md border border-input bg-background px-2 text-sm"
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          Nature
          <select
            className={cn(select, "block w-full text-foreground")}
            value={draft.nature}
            onChange={(e) => set("nature", e.target.value)}
          >
            {NATURES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          Binding
          <select
            className={cn(select, "block w-full text-foreground")}
            value={draft.binding}
            onChange={(e) => set("binding", e.target.value)}
          >
            {BINDINGS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
      </div>
      {FIELD_LABELS.map(({ key, label, long }) => (
        <label key={key} className="block space-y-1 text-xs font-medium text-muted-foreground">
          {label}
          {long ? (
            <Textarea
              rows={2}
              value={draft[key]}
              onChange={(e) => set(key, e.target.value)}
              className="min-h-0 text-sm text-foreground"
            />
          ) : (
            <Input value={draft[key]} onChange={(e) => set(key, e.target.value)} className="h-9 text-sm text-foreground" />
          )}
        </label>
      ))}
      {row.verified && (
        <p className="text-xs text-muted-foreground">
          Changing the clauses, nature, requirement or binding removes the verification: the proof no longer applies.
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

function RowDetails({
  row,
  verifying,
  onSave,
  onDelete,
  onVerify,
  onOpenProof,
}: {
  row: ChangeRow
  verifying: boolean
  onSave: (patch: ChangeRowInput) => Promise<void>
  onDelete: () => void
  onVerify: () => void
  onOpenProof: (proof: ChangeRow["proof"][number]) => void
}) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return (
      <RowEditor
        row={row}
        onSave={async (patch) => {
          await onSave(patch)
          setEditing(false)
        }}
        onCancel={() => setEditing(false)}
      />
    )
  }
  const facts: [string, string][] = [
    ["Current clause (A)", row.current_clause],
    ["Previous clause (B)", row.previous_clause],
    ["Change", row.change_description],
    ["Category", row.category],
    ["Compliance requirement", row.compliance_requirement],
    ["Binding", row.binding],
    ["Applicability", row.applicability_scope],
  ]
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1.5 text-sm">
        {facts
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
              <dd className="text-foreground/90">{value}</dd>
            </div>
          ))}
      </dl>

      {row.proof.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Proof</div>
          {row.proof.map((p, i) => (
            <blockquote key={i} className="border-l-2 border-amber-400/70 pl-3 text-xs leading-relaxed text-muted-foreground">
              <button
                type="button"
                className="mr-2 inline-flex items-center gap-1 rounded-md bg-secondary px-1.5 py-0.5 align-baseline text-[11px] font-semibold text-accent hover:bg-secondary/70"
                title={p.page ? `Open page ${p.page} of the ${p.doc === "current" ? "current" : "previous"} version` : "Open the document"}
                onClick={() => onOpenProof(p)}
              >
                <BookOpen className="h-3 w-3" />
                {proofLabel(p)}
              </button>
              “{p.text}”
            </blockquote>
          ))}
        </div>
      )}
      {!row.verified && row.verification_note && (
        <p className="text-xs italic text-muted-foreground">{row.verification_note}</p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
          Edit
        </Button>
        {row.verified ? (
          <span className="inline-flex items-center gap-1.5 px-1 text-xs font-medium text-emerald-700 dark:text-emerald-400">
            <ShieldCheck className="h-3.5 w-3.5" />
            Verified: the proof is in the text
          </span>
        ) : (
          <Button size="sm" variant="outline" className="gap-1.5" disabled={verifying} onClick={onVerify}>
            {verifying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            {verifying ? "Verifying…" : "Verify"}
          </Button>
        )}
        <Button size="sm" variant="ghost" className="gap-1.5 text-destructive hover:text-destructive" onClick={onDelete}>
          <Trash2 className="h-3.5 w-3.5" />
          Delete
        </Button>
      </div>
    </div>
  )
}

function ReportView({
  report,
  detail,
  onOpenSource,
}: {
  report: string | null
  detail: ChangeDetail | null
  onOpenSource: (citation: ChatCitation) => void
}) {
  if (!report) {
    return <p className="p-4 text-sm text-muted-foreground">The report is written when the analysis finishes.</p>
  }
  // "(A p.12)" in the report: A is the current version, B the previous one.
  const pageRefs = detail?.current && detail.previous ? { A: detail.current, B: detail.previous } : undefined
  return (
    <div className="p-4">
      <Markdown text={detail?.report_markdown ?? report} pageRefs={pageRefs} onOpenCitation={onOpenSource} />
    </div>
  )
}

/**
 * Side panel next to the chat: the change table of one comparison (live while it is being built),
 * its report, and what a person can do with it: filter, check the proof, edit, approve, export.
 */
export function ChangePanel({
  analysisId,
  live,
  hidden = false,
  onClose,
  onOpenSource,
}: {
  analysisId: string
  live?: AnalysisLive
  /** A source is open on top of this panel: keep the state (tab, filters, open row), show nothing. */
  hidden?: boolean
  onClose: () => void
  onOpenSource: (citation: ChatCitation) => void
}) {
  const [detail, setDetail] = useState<ChangeDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const [tab, setTab] = useState<Tab>("table")
  const [filter, setFilter] = useState<Filter>("all")
  const [nature, setNature] = useState<ChangeNature | "all">("all")
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<{ id: string; kind: "approve" | "verify" } | null>(null) // the question being asked
  const [verifying, setVerifying] = useState<ReadonlySet<string>>(new Set())

  const running = live?.status === "running"
  // While the scan runs, its rows come from the stream; a re-check keeps showing the stored table.
  const streaming = running && !live?.reverify

  useEffect(() => {
    if (streaming) return
    let cancelled = false
    queueMicrotask(() => {
      if (!cancelled) setFailed(false)
    })
    api
      .getChange(analysisId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [analysisId, streaming, live?.status])

  useEffect(() => {
    if (hidden) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (confirm) setConfirm(null)
      else onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, hidden, confirm])

  const stored = detail?.analysis.id === analysisId ? detail : null
  const rows = useMemo(
    () => (streaming || !stored ? (live?.rows ?? []) : stored.rows),
    [streaming, stored, live?.rows],
  )
  const shown = rows.filter(
    (r) => (filter === "all" || isActionable(r)) && (nature === "all" || r.nature === nature),
  )
  const current = stored?.current ?? live?.current
  const previous = stored?.previous ?? live?.previous

  const replaceRow = useCallback((row: ChangeRow) => {
    setDetail((d) => (d ? { ...d, rows: d.rows.map((r) => (r.id === row.id ? row : r)) } : d))
  }, [])

  const guarded = async (action: () => Promise<void>) => {
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : "The change could not be saved.")
      throw e
    }
  }

  const save = (row: ChangeRow, patch: ChangeRowInput & { approved?: boolean }) =>
    guarded(async () => replaceRow(await api.updateChangeRow(analysisId, row.id, patch)))

  const remove = (row: ChangeRow) =>
    guarded(async () => {
      await api.deleteChangeRow(analysisId, row.id)
      setDetail((d) => (d ? { ...d, rows: d.rows.filter((r) => r.id !== row.id) } : d))
    }).catch(() => undefined)

  const add = () =>
    guarded(async () => {
      const row = await api.addChangeRow(analysisId, {
        current_clause: "New clause",
        nature: "new",
        binding: "normative",
        compliance_requirement: "Describe the requirement",
      })
      setDetail((d) => (d ? { ...d, rows: [...d.rows, row] } : d))
      setOpen(row.id)
      setFilter("all")
      setNature("all")
    }).catch(() => undefined)

  /** The Change Analyzer checks these rows against the documents (any row, whatever its nature). */
  const verify = async (ids: string[]) => {
    const todo = ids.filter((id) => !verifying.has(id))
    if (todo.length === 0) return
    setError(null)
    setVerifying((s) => new Set([...s, ...todo]))
    try {
      const result = await api.verifyChangeRows(analysisId, todo)
      result.rows.forEach(replaceRow)
    } catch (e) {
      setError(e instanceof Error ? e.message : "The verification could not run.")
    } finally {
      setVerifying((s) => new Set([...s].filter((id) => !todo.includes(id))))
    }
  }

  const openProof = (proof: ChangeRow["proof"][number]) => {
    const version = proof.doc === "current" ? current : previous
    if (version) onOpenSource(pageCitation(version, proof.page ?? null, proof.text))
  }

  const unverified = rows.filter((r) => isActionable(r) && !r.verified)
  const editable = !streaming && stored !== null
  const hasReport = Boolean(stored?.analysis.report)

  return (
    <aside
      aria-label="Change analysis"
      className={cn(
        "fixed inset-0 z-40 flex-col bg-background md:static md:z-auto md:h-full md:w-[58%] md:min-w-[400px] md:max-w-[920px] md:shrink-0 md:animate-fade-in md:border-l md:border-border/60",
        hidden ? "hidden" : "flex",
      )}
    >
      <div className="flex shrink-0 items-start gap-3 border-b border-border/60 bg-secondary/30 px-4 py-3 pt-safe md:pt-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-foreground">Change analysis</div>
          <div className="truncate text-xs text-muted-foreground">
            {current && previous
              ? `${current.title} ${current.label} (A, current) vs ${previous.title} ${previous.label} (B, previous)`
              : "Loading…"}
          </div>
        </div>
        <Button variant="ghost" size="icon" className="-mr-1 h-7 w-7 shrink-0" onClick={onClose} aria-label="Close">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border/50 px-4 py-2.5">
        <div className="inline-flex rounded-lg bg-muted p-0.5 text-sm">
          {(["table", "report"] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              disabled={t === "report" && !hasReport}
              className={cn(
                "rounded-md px-3 py-1 font-medium capitalize transition-colors disabled:opacity-50",
                tab === t ? "bg-background text-foreground shadow" : "text-muted-foreground",
              )}
            >
              {t}
            </button>
          ))}
        </div>

        {tab === "table" && (
          <>
            <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={filter === "actionable"}
                onChange={(e) => setFilter(e.target.checked ? "actionable" : "all")}
              />
              Compliance-relevant only
            </label>
            <select
              aria-label="Filter by nature"
              value={nature}
              onChange={(e) => setNature(e.target.value as ChangeNature | "all")}
              className="h-8 rounded-md border border-input bg-background px-2 text-xs"
            >
              <option value="all">All natures</option>
              {NATURES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {editable && tab === "table" && (
            <Button size="sm" variant="ghost" className="h-8 gap-1.5" onClick={() => void add()}>
              <Plus className="h-3.5 w-3.5" />
              Add row
            </Button>
          )}
          {editable && tab === "table" && rows.length > 0 && (
            <Button size="sm" variant="outline" className="h-8 gap-1.5" asChild>
              <a href={api.changeExportUrl(analysisId, filter)} download title="The change table and an Info sheet">
                <Download className="h-3.5 w-3.5" />
                Excel
              </a>
            </Button>
          )}
          {editable && tab === "report" && hasReport && (
            <>
              <Button size="sm" variant="outline" className="h-8 gap-1.5" asChild>
                <a href={api.changeReportUrl(analysisId, "docx")} download title="The report as a Word document">
                  <Download className="h-3.5 w-3.5" />
                  Word
                </a>
              </Button>
              <Button size="sm" variant="outline" className="h-8 gap-1.5" asChild>
                <a href={api.changeReportUrl(analysisId, "md")} download title="The report as a Markdown file">
                  <Download className="h-3.5 w-3.5" />
                  Markdown
                </a>
              </Button>
            </>
          )}
        </div>
      </div>

      {running && (
        <div className="flex shrink-0 items-center gap-2 border-b border-border/50 bg-primary/5 px-4 py-2 text-xs text-foreground/80">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          {live?.statusText ?? "Working…"}
          {live?.removed ? <span className="text-muted-foreground">· {live.removed} unproven removed</span> : null}
        </div>
      )}
      {error && <div className="shrink-0 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-xs text-destructive">{error}</div>}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "report" ? (
          <ReportView report={stored?.analysis.report ?? null} detail={stored} onOpenSource={onOpenSource} />
        ) : failed && !live?.rows.length ? (
          <p className="p-6 text-center text-sm text-muted-foreground">This change analysis could not be loaded.</p>
        ) : !streaming && !stored && !live?.rows.length ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : shown.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">
            {rows.length === 0 ? (running ? "Rows appear here as they are found." : "No changes were recorded.") : "No rows match the filters."}
          </p>
        ) : (
          <table className="w-full table-fixed border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-background text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60">
                <th className="w-9 px-2 py-2" aria-label="Verified" />
                <th className="w-24 px-2 py-2 font-semibold">Nature</th>
                <th className="w-[26%] px-2 py-2 font-semibold">Clause</th>
                <th className="px-2 py-2 font-semibold">Change</th>
                <th className="w-24 px-2 py-2 font-semibold">Binding</th>
                <th className="w-12 px-2 py-2 font-semibold" title="Approved by a person">
                  OK
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => {
                const isOpen = open === row.id
                return (
                  <Fragment key={row.id}>
                    <tr
                      className={cn(
                        "cursor-pointer border-b border-border/40 align-top hover:bg-secondary/30",
                        isOpen && "bg-secondary/30",
                      )}
                      onClick={() => setOpen(isOpen ? null : row.id)}
                    >
                      <td className="relative px-2 py-2" onClick={(e) => e.stopPropagation()}>
                        <VerifiedMark
                          row={row}
                          busy={verifying.has(row.id)}
                          canVerify={editable}
                          onAsk={() =>
                            setConfirm(confirm?.id === row.id && confirm.kind === "verify" ? null : { id: row.id, kind: "verify" })
                          }
                        />
                        {confirm?.id === row.id && confirm.kind === "verify" && (
                          <ConfirmPopover
                            question="Launch a verification?"
                            align="left"
                            onYes={() => {
                              setConfirm(null)
                              void verify([row.id])
                            }}
                            onNo={() => setConfirm(null)}
                          />
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <NatureBadge nature={row.nature} />
                      </td>
                      <td className="px-2 py-2 text-foreground">
                        <span className="line-clamp-2">{row.current_clause || row.previous_clause}</span>
                      </td>
                      <td className="px-2 py-2 text-foreground/80">
                        <span className="flex items-start gap-1">
                          {isOpen ? (
                            <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          ) : (
                            <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          )}
                          <span className="line-clamp-2">{row.change_description}</span>
                        </span>
                      </td>
                      <td className="px-2 py-2 text-xs text-muted-foreground">{row.binding}</td>
                      <td className="relative px-2 py-2" onClick={(e) => e.stopPropagation()}>
                        <ApproveBox
                          checked={row.approved}
                          disabled={!editable}
                          onClick={() =>
                            setConfirm(confirm?.id === row.id && confirm.kind === "approve" ? null : { id: row.id, kind: "approve" })
                          }
                        />
                        {confirm?.id === row.id && confirm.kind === "approve" && (
                          <ConfirmPopover
                            question={row.approved ? "Remove the approval?" : "Approve this row?"}
                            align="right"
                            onYes={() => {
                              setConfirm(null)
                              void save(row, { approved: !row.approved }).catch(() => undefined)
                            }}
                            onNo={() => setConfirm(null)}
                          />
                        )}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-border/60 bg-secondary/20">
                        <td colSpan={6} className="px-4 py-3">
                          {editable ? (
                            <RowDetails
                              row={row}
                              verifying={verifying.has(row.id)}
                              onSave={(patch) => save(row, patch)}
                              onDelete={() => void remove(row)}
                              onVerify={() => void verify([row.id])}
                              onOpenProof={openProof}
                            />
                          ) : (
                            <p className="text-sm text-muted-foreground">{row.change_description}</p>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {tab === "table" && editable && rows.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-border/50 px-4 py-2 text-xs text-muted-foreground">
          <span>
            {rows.length} {rows.length === 1 ? "row" : "rows"}
          </span>
          <Badge variant="secondary">{rows.filter((r) => r.verified).length} verified</Badge>
          <Badge variant="secondary">{rows.filter((r) => r.approved).length} approved</Badge>
          {unverified.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto h-7 gap-1.5 text-xs"
              disabled={unverified.some((r) => verifying.has(r.id))}
              onClick={() => void verify(unverified.map((r) => r.id))}
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              Verify {unverified.length} unverified compliance-relevant {unverified.length === 1 ? "row" : "rows"}
            </Button>
          )}
        </div>
      )}
    </aside>
  )
}
