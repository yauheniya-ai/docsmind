/** Thin fetch wrapper for the docsmind FastAPI server (`docsmind/src/docsmind/server`).
 * In dev, Vite proxies `/api` and `/blobs` to `docsmind serve` (see vite.config.ts),
 * so these are always relative paths — no base URL to configure.
 */

export interface DocumentSummary {
  document_id: string
  id: string // version id
  title: string
  collection: string
  label: string
  created_at: string
  char_count: number | null
  word_count: number | null
  chunk_count: number
  image_count: number
  table_count: number
}

export interface DocumentDetail extends DocumentSummary {
  token_count_estimate?: number | null
  token_count?: number | null
  /** Original uploaded file served from blob storage; null for versions ingested before originals were stored. */
  file_url?: string | null
}

/** AI enrichment for a document version. */
export interface DocumentSummaryInfo {
  summary: string | null
  keywords: string[]
}

export interface Section {
  id: string
  version_id: string
  parent_id: string | null
  path: string
  heading: string | null
  number: string | null
  page_start: number | null
  page_end: number | null
  ordinal: number
}

export interface Chunk {
  id: string
  chunk_type: "text" | "image" | "table"
  content: string | null
  image_id: string | null
  page_start: number | null
  page_end: number | null
  embedding_model?: string | null
  embedding_dims?: number | null
  /** First few components only; the full vector comes from `getChunkEmbedding`. */
  embedding_preview?: number[] | null
}

export interface ImageItem {
  id: string
  version_id: string
  page_number: number
  width: number
  height: number
  format: string
  caption: string | null
  url: string | null
}

export interface TableItem {
  id: string
  version_id: string
  page_number: number
  width: number
  height: number
  format: string
  url: string | null
}

/** A source the Mind cited. `handle` (S1, S2, ...) matches the [S#] markers in the answer text. */
export interface ChatCitation {
  handle: string
  chunk_id: string
  version_id: string | null
  document_title: string
  version_label: string
  collection: string | null
  page_start: number | null
  page_end: number | null
  snippet: string
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  last_answer: string
  sources: { version_id: string; title: string }[]
}

export interface StoredMessage {
  role: "user" | "assistant"
  content: string
  citations: ChatCitation[]
  meta: { route?: "simple" | "complex"; plan?: string[]; steps?: number; analyses?: string[] }
  created_at: string
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: string
  messages: StoredMessage[]
}

/** A JSON Schema fragment, as sent to the model for a tool's arguments. */
export interface JsonSchema {
  type?: string | string[]
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  anyOf?: JsonSchema[]
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
  $ref?: string
}

export interface AgentToolInfo {
  name: string
  /** Plain-language summary for people. */
  description: string
  /** Quick-reference steps for people; never sent to the model. */
  how_it_works?: string[]
  /** The description the model reads. */
  instructions: string
  /** JSON Schema of the arguments the model must supply. */
  parameters: JsonSchema
}

/** An extra block placed after the system prompt. */
export interface AgentContextBlock {
  label: string
  text: string
  /** True when it sits in the cacheable stable prefix. */
  cached: boolean
}

export interface AgentPromptInfo {
  system_prompt: string
  context: AgentContextBlock[]
  /** What the user message contains for this agent. */
  user_prompt: string
}

export interface AgentCardInfo extends AgentPromptInfo {
  name: string
  role: string
  description: string
  model: string
  tools: AgentToolInfo[]
  /** For code-driven subagents: the steps of its pipeline, in order. */
  stages?: AgentStageInfo[]
}

/** A step inside an agent (the Mind's router and planner, the Change Analyzer's scan ...): a model call or a small loop. */
export interface AgentStageInfo extends AgentPromptInfo {
  name: string
  role: string
  description: string
  model: string
  /** What limits it, when it is not simply one model call. */
  budget?: string
  /** The tools its loop may call (a stage of a code-driven subagent); absent for a single model call. */
  tools?: AgentToolInfo[]
}

export interface AgentInfo {
  agent: AgentCardInfo
  stages: AgentStageInfo[]
  harness: {
    router_model: string
    summarizer_model: string
    memory_model: string
    memory_enabled: boolean
  }
  subagents: AgentCardInfo[]
  limits: { max_steps: number; verify_max_steps: number }
}

// --- change analysis (comparing two versions) ------------------------------------------

export type ChangeNature = "new" | "modified" | "editorial" | "removed" | "relocated"
export type ChangeBinding = "normative" | "recommended" | "optional" | "informative" | "unchanged"

/** A verbatim passage that backs a row. `page` is null when it could not be pinned down. */
export interface ChangeProof {
  doc: "current" | "previous"
  text: string
  page: number | null
}

export interface ChangeRow {
  id: string
  analysis_id: string | null
  ordinal: number
  current_clause: string
  previous_clause: string
  change_description: string
  nature: ChangeNature
  category: string
  compliance_requirement: string
  binding: ChangeBinding
  applicability_scope: string
  verified: boolean
  proof: ChangeProof[]
  verification_note: string | null
  approved: boolean
  origin: "scan" | "changelog" | "user"
}

export type ChangeRowInput = Partial<
  Pick<
    ChangeRow,
    | "current_clause"
    | "previous_clause"
    | "change_description"
    | "nature"
    | "category"
    | "compliance_requirement"
    | "binding"
    | "applicability_scope"
  >
>

export interface ChangelogInfo {
  found?: boolean
  source?: string
  summary?: string
}

export interface ChangeAnalysisInfo {
  id: string
  current_version_id: string
  previous_version_id: string
  status: "running" | "done" | "failed" | "stopped" | "identical" | "unrelated"
  mode: "diff" | "full" | "changelog" | "none"
  diff_ratio: number | null
  public: boolean | null
  verdict_note: string
  changelog: ChangelogInfo
  report: string | null
  meta: Record<string, unknown>
  created_at: string
}

export interface ChangeVersionInfo {
  version_id: string
  title: string
  label: string
}

export interface ChangeDetail {
  analysis: ChangeAnalysisInfo
  current: ChangeVersionInfo | null
  previous: ChangeVersionInfo | null
  rows: ChangeRow[]
  /** Title, the two documents, what was compared and the report: what the Word / Markdown download contains. */
  report_markdown: string | null
}

/** A row counts as compliance-relevant when it is a substantive, normative change with a requirement. */
export function isActionable(row: ChangeRow): boolean {
  const delta = row.compliance_requirement.trim().toLowerCase()
  return (
    (row.nature === "new" || row.nature === "modified" || row.nature === "removed") &&
    row.binding === "normative" &&
    !(["", "no change", "none", "n/a", "-"] as string[]).includes(delta)
  )
}

export interface EnrichmentJob {
  id: string
  kind: "embeddings" | "captions"
  status: "queued" | "running" | "done" | "failed" | "cancelled"
  done: number
  total: number
  message: string
  result: {
    kind: "embeddings" | "captions"
    model: string
    processed: number
    skipped: number
    failed: number
    total: number
    cancelled: boolean
    errors: string[]
  } | null
  error: string | null
  version_ids: string[] | null
  cancelling: boolean
}

export interface EnrichmentStatusBlock {
  provider: string
  model: string
  total: number
  done: number
  pending: number
  configured: boolean
  reason: string | null
  job: EnrichmentJob | null
}

export interface EnrichmentStatus {
  embeddings: EnrichmentStatusBlock
  captions: EnrichmentStatusBlock
  versions: {
    version_id: string
    chunks_total: number
    chunks_embedded: number
    images_total: number
    images_captioned: number
  }[]
}

export interface EnrichmentEstimate {
  kind: "embeddings" | "captions"
  provider: string
  model: string
  items: number
  already_done: number
  skipped: number
  input_tokens: number
  output_tokens_low: number
  output_tokens_high: number
  cost_usd_low: number | null
  cost_usd_high: number | null
  price_known: boolean
  note: string
  configured: boolean
  reason: string | null
}

class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

/** Build an ApiError from a failed response, preferring the server's `detail`. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let detail: string = response.statusText
  try {
    const body = await response.json()
    if (typeof body.detail === "string") {
      detail = body.detail
    } else if (Array.isArray(body.detail)) {
      // FastAPI validation errors: [{ loc, msg, ... }]
      detail = body.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ") || detail
    } else if (body.detail && typeof body.detail === "object") {
      // Structured error (e.g. 409 duplicate-document: { error, message, existing_version }).
      detail = body.detail.message ?? JSON.stringify(body.detail)
    }
  } catch {
    // ignore - body wasn't JSON
  }
  return new ApiError(detail, response.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) throw await errorFromResponse(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  listDocuments: () => request<DocumentSummary[]>("/api/documents"),

  getDocument: (versionId: string) => request<DocumentDetail>(`/api/documents/${versionId}`),

  /** Rename what is given at ingest time: the title and collection (of the document) and the version label. */
  updateDocument: (versionId: string, fields: { title?: string; collection?: string; label?: string }) =>
    request<DocumentDetail>(`/api/documents/${versionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    }),

  ingestDocument: (file: File, collection: string, version?: string) => {
    const form = new FormData()
    form.append("file", file)
    form.append("collection", collection)
    if (version) form.append("version", version)
    return request<DocumentDetail>("/api/documents", { method: "POST", body: form })
  },

  getFulltext: (versionId: string) =>
    request<{ full_text: string }>(`/api/documents/${versionId}/fulltext`).then((r) => r.full_text),

  /** Sentence-per-line text (headers/footers/page numbers removed) that the change analysis diffs. */
  getNormalizedText: (versionId: string) =>
    request<{ normalized_text: string }>(`/api/documents/${versionId}/normalized-text`).then(
      (r) => r.normalized_text,
    ),

  /** AI-generated summary + keywords. Resolves to an empty result if enrichment isn't available yet. */
  getSummary: (versionId: string) =>
    request<DocumentSummaryInfo>(`/api/documents/${versionId}/summary`).catch(
      (e): DocumentSummaryInfo => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
          return { summary: null, keywords: [] }
        }
        throw e
      },
    ),

  getOutline: (versionId: string) => request<Section[]>(`/api/documents/${versionId}/outline`),

  getChunks: (versionId: string, page?: number) =>
    request<Chunk[]>(`/api/documents/${versionId}/chunks${page ? `?page=${page}` : ""}`),

  getChunkEmbedding: (versionId: string, chunkId: string) =>
    request<{ dims: number; embedding: number[] }>(
      `/api/documents/${versionId}/chunks/${chunkId}/embedding`,
    ).then((r) => r.embedding),

  getImages: (versionId: string) => request<ImageItem[]>(`/api/documents/${versionId}/images`),

  getTables: (versionId: string) => request<TableItem[]>(`/api/documents/${versionId}/tables`),

  deleteDocument: (versionId: string) =>
    request<{ deleted: boolean; id: string }>(`/api/documents/${versionId}`, { method: "DELETE" }),

  listConversations: () => request<ConversationSummary[]>("/api/conversations"),

  getConversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<{ deleted: boolean; id: string }>(`/api/conversations/${id}`, { method: "DELETE" }),

  getAgent: () => request<AgentInfo>("/api/agent"),

  getChange: (id: string) => request<ChangeDetail>(`/api/changes/${id}`),

  addChangeRow: (id: string, row: ChangeRowInput) =>
    request<ChangeRow>(`/api/changes/${id}/rows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(row),
    }),

  updateChangeRow: (id: string, rowId: string, patch: ChangeRowInput & { approved?: boolean }) =>
    request<ChangeRow>(`/api/changes/${id}/rows/${rowId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),

  deleteChangeRow: (id: string, rowId: string) =>
    request<{ deleted: boolean; id: string }>(`/api/changes/${id}/rows/${rowId}`, { method: "DELETE" }),

  /** Runs the Change Analyzer's verification on any rows of the table; resolves with the rows afterwards. */
  verifyChangeRows: (id: string, rowIds: string[]) =>
    request<{ rows: ChangeRow[] }>(`/api/changes/${id}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row_ids: rowIds }),
    }),

  /** Download link for the Excel export; `only` follows the table's "compliance-relevant only" filter. */
  changeExportUrl: (id: string, only: "all" | "actionable" = "all") =>
    `/api/changes/${id}/export.xlsx${only === "actionable" ? "?only=actionable" : ""}`,

  /** Download link for the report alone (not the table). */
  changeReportUrl: (id: string, format: "docx" | "md") => `/api/changes/${id}/report.${format}`,

  getEnrichmentStatus: (versionId?: string) =>
    request<EnrichmentStatus>(`/api/enrichment/status${versionId ? `?version_id=${versionId}` : ""}`),

  estimateEnrichment: (kind: "embeddings" | "captions", versionIds?: string[]) =>
    request<EnrichmentEstimate>(`/api/enrichment/${kind}/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version_ids: versionIds?.length ? versionIds : null }),
    }),

  runEnrichment: (kind: "embeddings" | "captions", versionIds?: string[]) =>
    request<EnrichmentJob>(`/api/enrichment/${kind}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version_ids: versionIds?.length ? versionIds : null }),
    }),

  getEnrichmentJob: (jobId: string) => request<EnrichmentJob>(`/api/enrichment/jobs/${jobId}`),

  cancelEnrichmentJob: (jobId: string) =>
    request<EnrichmentJob>(`/api/enrichment/jobs/${jobId}/cancel`, { method: "POST" }),
}

export { ApiError }
