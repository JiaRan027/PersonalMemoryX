export interface RuntimeConnection {
  endpoint: string
  token: string
}

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
  ) {
    super(`Local API request failed with status ${status}${code ? ` (${code})` : ""}`)
    this.name = "ApiRequestError"
  }
}

export interface HealthResponse {
  status: "ready" | "degraded" | "protected"
  database: "ready" | "protected"
  version: string
  build_revision: string
}

export interface MemoryNamespaceSummary {
  namespace: string
  memory_count: number
}

export interface Connector {
  hook_receipt?: { user_message?: string; assistant_message?: string } | null
  background_sync?: boolean
  id: string
  name: string
  profile_id: string
  source_path: string | null
  target_path: string | null
  namespace: string
  capabilities: { read: boolean; write: boolean }
  read_enabled: boolean
  write_enabled: boolean
  enabled: boolean
  status: "healthy" | "syncing" | "paused" | "attention" | "unsupported"
}

export interface MemoryItem {
  id: string
  type: string
  statement: string
  reason: string | null
  namespace: string
  sensitivity: string
  epistemic_state: string
  status: string
  revision: number
  payload: Record<string, unknown>
  provenance?: MemoryProvenance
  created_at: string
  updated_at: string
  quality_issues?: QualityIssue[]
  review_assistance?: ReviewAssistance
  review_decision?: ReviewDecision
  review_triage?: ReviewTriage
}

export type ReviewVerdict = "keep" | "project_experience" | "rewrite" | "source_only" | "test_artifact" | "needs_context"

export interface ReviewDecision {
  verdict: ReviewVerdict
  memory_revision: number
  reviewed_at: string
  note: string | null
  scope_hint: string | null
}

export interface ReviewTriage {
  suggested_verdict: ReviewVerdict
  effective_verdict: ReviewVerdict
  confidence: "high" | "medium" | "low"
  reason_codes: string[]
  requires_user_review: boolean
  delivery_eligible: boolean
  graph_eligible: boolean
  scope_hint: string | null
  overridden_by_user: boolean
  memory_revision: number
}

export interface ReviewAssistance {
  locale: string
  summary: string
  explanation: string | null
  recommendation: "keep" | "remove" | "rewrite"
  recommendation_reason: string
}

export interface MemoryProvenance {
  source_revision_id: string | null
  source_kind: string | null
  source_family: string | null
  source_format: string | null
  source_signal: string | null
  source_profile: string | null
  capture_mode: string | null
  locator: string | null
  project: string | null
  actor_kind: string | null
  actor_id: string | null
  evidence_actor: string | null
  evidence_excerpt: string | null
  evidence_start: number | null
  evidence_end: number | null
  line_start: number | null
  line_end: number | null
  source_section: string | null
}

export interface QualityIssue {
  code: string
  related_memory_id?: string | null
  related_namespace?: string | null
}

export interface ActivityItem {
  id: string
  operation_id: string
  connector_id: string | null
  kind: string
  status: string
  summary: string
  details: Record<string, unknown>
  created_at: string
}

export interface ExceptionItem {
  id: string
  kind: string
  reason_code: string
  statement: string | null
  memory_type: string | null
  connector_id?: string | null
  memory_id?: string | null
  related_memory_ids?: string[] | null
  related_namespaces?: string[] | null
  created_at: string
}

export interface RecognizedSource {
  agent: string
  kind: string
  format: string
  path: string
  size_bytes: number
  file_count: number
  last_modified: string | null
  priority: number
  note: string
}

export interface RecognizeResponse {
  items: RecognizedSource[]
  scanned_roots: string[]
  summary: {
    source_count: number
    agent_count: number
    total_size_bytes: number
    by_agent: Record<string, number>
  }
}

export interface ExtractPreviewItem {
  type: string
  statement: string
  reason: string | null
  confidence: number
  importance: number
  evidence: { start: number; end: number }
  source_signal: string
  admission_action?: string | null
  admission_reason_code?: string | null
  extra?: Record<string, unknown> | null
}

export interface ImportFileResult {
  path: string
  status: string
  detail: string
}

export interface ImportResponse {
  total: number
  ingested: number
  duplicate: number
  processed: number
  skipped: number
  failed: number
  files: ImportFileResult[]
}

export interface ExtractPreviewResponse {
  dry_run: boolean
  profile_id: string
  format: string
  source_path: string
  byte_size: number
  char_count: number
  items: ExtractPreviewItem[]
  warnings: string[]
}

export interface MemoryGraphEdge {
  source: string
  target: string
  type: string
  origin: string
  derived: boolean
  score?: number
}

export interface GraphCluster {
  id: string
  label: string
  size: number
  node_ids: string[]
}

export interface MemoryGraphResponse {
  nodes: MemoryItem[]
  edges: MemoryGraphEdge[]
  clusters: GraphCluster[]
  counts: {
    nodes: number
    edges: number
    clusters: number
    explicit_edges: number
    derived_edges: number
  }
}

export interface CapsuleMemoryReference {
  memory_id: string
  revision: number
  type: string
  statement: string
  reason: string | null
  namespace: string
  sensitivity: string
}

export interface ResumeCapsule {
  schema_version: string
  capsule_id: string
  generated_at: string
  expires_at: string
  ttl_seconds: number
  namespace_scope: string[]
  max_sensitivity: string
  source_digest: string
  selection: {
    query: string
    namespaces: string[] | null
    allowed_types: string[]
    max_sensitivity: string
    limit: number
  }
  memory_references: CapsuleMemoryReference[]
  checkpoint: {
    current_task: string | null
    verified_status: string | null
    next_action: string | null
  }
  dropped_fields: string[]
  token_budget: number
  estimated_tokens: number
}

export interface CapsuleResponse {
  capsule: ResumeCapsule
  delivery_text: string
}

export interface CapsuleValidationIssue {
  code: string
  memory_id?: string | null
  expected_revision?: number | null
  actual_revision?: number | null
}

export interface CapsuleValidationResult {
  valid: boolean
  expired: boolean
  stale: boolean
  checked_at: string
  issues: CapsuleValidationIssue[]
}

export interface CapsuleExportResponse {
  path: string
  capsule_path?: string
  bytes_written: number
  capsule_id: string
  delivery_text: string
  validation: CapsuleValidationResult
}

export interface CapsuleBuildDraft {
  query: string
  namespaces: string[] | null
  checkpoint: {
    current_task: string | null
    verified_status: string | null
    next_action: string | null
  }
  ttl_seconds: number
  token_budget: number
}


export interface ConnectorDraft {
  name: string
  profile_id: string
  source_path: string | null
  target_path: string | null
  namespace: string
  read_enabled: boolean
  write_enabled: boolean
}

export class ApiClient {
  constructor(private readonly connection: RuntimeConnection) {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.connection.endpoint}/api/v1${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.connection.token}`,
        ...init.headers,
      },
    })
    if (!response.ok) {
      let code: string | null = null
      try {
        const payload = await response.json() as { detail?: { code?: unknown } }
        if (typeof payload.detail?.code === "string") code = payload.detail.code
      } catch {
        // Keep the HTTP status when the local service returns no structured body.
      }
      throw new ApiRequestError(response.status, code)
    }
    if (response.status === 204) {
      return undefined as T
    }
    return response.json() as Promise<T>
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health")
  }

  connectors(): Promise<{ items: Connector[] }> {
    return this.request("/connectors")
  }

  previewConnector(draft: Omit<ConnectorDraft, "name" | "namespace">) {
    return this.request("/connectors/preview", {
      method: "POST",
      body: JSON.stringify(draft),
    })
  }

  createConnector(draft: ConnectorDraft): Promise<Connector> {
    return this.request("/connectors", { method: "POST", body: JSON.stringify(draft) })
  }

  patchConnector(id: string, patch: Partial<Pick<Connector, "enabled" | "read_enabled" | "write_enabled">>): Promise<Connector> {
    return this.request(`/connectors/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
  }

  syncConnector(id: string): Promise<{ ingested: number; projected: number; failed: number }> {
    return this.request(`/connectors/${id}/sync`, { method: "POST" })
  }
  removeConnector(id: string): Promise<void> {
    return this.request(`/connectors/${id}`, { method: "DELETE" })
  }

  memories(params: URLSearchParams = new URLSearchParams()): Promise<{ items: MemoryItem[]; total: number }> {
    const query = params.size ? `?${params.toString()}` : ""
    return this.request(`/memories${query}`)
  }

  memoryNamespaces(): Promise<{ items: MemoryNamespaceSummary[]; total: number }> {
    return this.request("/memories/namespaces")
  }

  graph(): Promise<MemoryGraphResponse> {
    return this.request("/memories/graph")
  }

  reviseMemory(id: string, revision: number, statement: string, reason: string | null): Promise<MemoryItem> {
    return this.request(`/memories/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: revision, statement, reason }),
    })
  }

  reviewMemory(id: string, revision: number, verdict: ReviewVerdict, scopeHint?: string | null): Promise<ReviewDecision> {
    return this.request(`/memories/${id}/review`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: revision,
        verdict,
        scope_hint: scopeHint ?? null,
      }),
    })
  }

  deleteMemory(id: string): Promise<void> {
    return this.request(`/memories/${id}`, { method: "DELETE" })
  }

  activities(): Promise<{ items: ActivityItem[] }> {
    return this.request("/activities")
  }

  buildCapsule(draft: CapsuleBuildDraft): Promise<CapsuleResponse> {
    return this.request("/capsules", {
      method: "POST",
      body: JSON.stringify({
        selection: {
          query: draft.query,
          namespaces: draft.namespaces,
          max_sensitivity: "personal",
          limit: 10,
        },
        checkpoint: draft.checkpoint,
        ttl_seconds: draft.ttl_seconds,
        token_budget: draft.token_budget,
      }),
    })
  }

  validateCapsule(capsule: ResumeCapsule): Promise<CapsuleValidationResult> {
    return this.request("/capsules/validate", {
      method: "POST",
      body: JSON.stringify({ capsule }),
    })
  }

  rebuildCapsule(capsule: ResumeCapsule, ttlSeconds: number): Promise<CapsuleResponse> {
    return this.request("/capsules/rebuild", {
      method: "POST",
      body: JSON.stringify({ capsule, ttl_seconds: ttlSeconds }),
    })
  }

  exportCapsule(capsule: ResumeCapsule, path: string): Promise<CapsuleExportResponse> {
    return this.request("/capsules/export", {
      method: "POST",
      body: JSON.stringify({ capsule, path }),
    })
  }

  undo(operationId: string): Promise<{ undone: number; operation_id: string }> {
    return this.request(`/activities/${operationId}/undo`, { method: "POST" })
  }

  exceptions(): Promise<{ items: ExceptionItem[]; total: number }> {
    return this.request("/exceptions")
  }

  exportPackage(path: string): Promise<{ valid: boolean; memory_count: number; memory_space_id: string; package_path: string; readable_path: string }> {
    return this.request("/protocol/export", {
      method: "POST",
      body: JSON.stringify({ package_path: path }),
    })
  }

  inspectPackage(path: string): Promise<{ valid: boolean; memory_count: number; memory_space_id: string }> {
    return this.request("/protocol/inspect", {
      method: "POST",
      body: JSON.stringify({ package_path: path }),
    })
  }

  importPackage(path: string): Promise<{ imported: number; skipped_tombstoned: number; skipped_existing: number }> {
    return this.request("/protocol/import", {
      method: "POST",
      body: JSON.stringify({ package_path: path }),
    })
  }

  recognizeSources(roots?: Record<string, string>): Promise<RecognizeResponse> {
    return this.request("/connectors/recognize", {
      method: "POST",
      body: JSON.stringify(roots ? { roots } : {}),
    })
  }

  extractPreview(profileId: string, sourcePath: string): Promise<ExtractPreviewResponse> {
    return this.request("/sources/extract", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, source_path: sourcePath }),
    })
  }

  importSource(profileId: string, sourcePath: string, namespace: string): Promise<ImportResponse> {
    return this.request("/sources/import", {
      method: "POST",
      body: JSON.stringify({
        profile_id: profileId,
        source_path: sourcePath,
        namespace,
      }),
    })
  }
}

export function runtimeClient(): ApiClient | null {
  const runtime = window.__PERSONALMEMORY__
  if (runtime) return new ApiClient(runtime)
  // Vite development uses an explicit local endpoint when the browser host
  // freezes window globals (the Tauri initialization script is unavailable).
  const endpoint = import.meta.env.VITE_PERSONALMEMORY_ENDPOINT
  const token = import.meta.env.VITE_PERSONALMEMORY_TOKEN
  return import.meta.env.MODE !== "test" && endpoint && token
    ? new ApiClient({ endpoint, token })
    : null
}

export function requireClient(): ApiClient {
  const client = runtimeClient()
  if (!client) throw new Error("Memory Service is unavailable")
  return client
}
