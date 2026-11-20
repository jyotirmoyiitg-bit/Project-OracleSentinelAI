import type {
  AlertLogEvent,
  FleetResponse,
  FleetHealthSummary,
  DatabaseRegistration,
  ConnectionTestResponse,
  TnsAliasListResponse,
  Target,
  Incident,
  IncidentDetail,
  IncidentListResponse,
  NotificationRecord,
  Observation,
  PortalNotification,
  PortalNotificationListResponse,
  TargetDetails,
  DatabaseCompliance,
  CompliancePolicyList,
  CapacityForecastResponse,
  KnowledgeDocumentList,
  KnowledgeReindexResponse,
  KnowledgeSearchResponse,
  ChatMessage,
  ChatThread,
} from './types'

export interface AuthSession {
  required: boolean
  authenticated: boolean
  principal: string
  role: string
  method: string
  oidc_enabled?: boolean
  directory_enabled?: boolean
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers as Record<string, string> | undefined),
      },
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Could not reach the Oracle Sentinel API. Check the connection and try again.')
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status} ${response.statusText || 'error'})`
    try {
      const body: unknown = await response.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        const message = (body as { detail: unknown }).detail
        if (typeof message === 'string') detail = message
      }
    } catch {
      // The response may not contain JSON.
    }
    throw new ApiError(detail, response.status)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export interface FleetParams {
  page: number
  pageSize: number
  environment: string
  healthStatus: string
}

const openSession: AuthSession = {
  required: false, authenticated: true, principal: '', role: 'operator', method: '',
}

export async function getSession(signal?: AbortSignal): Promise<AuthSession> {
  try {
    const body = await request<Partial<AuthSession>>('/api/v1/auth/session', signal)
    if (body.required !== true) return openSession
    return {
      required: true,
      authenticated: Boolean(body.authenticated),
      principal: body.principal ?? '',
      role: body.role ?? '',
      method: body.method ?? '',
      oidc_enabled: body.oidc_enabled === true,
      directory_enabled: body.directory_enabled === true,
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    // No answer at all means the API is down. That is reported, not treated as signed in.
    if (error instanceof ApiError && error.status === undefined) throw error
    return openSession
  }
}

export function login(username: string, password: string, local: boolean): Promise<AuthSession> {
  return request<AuthSession>('/api/v1/auth/login', undefined, {
    method: 'POST',
    body: JSON.stringify({ username, password, local }),
  })
}

export function logout(): Promise<{ authenticated: boolean }> {
  return request('/api/v1/auth/logout', undefined, { method: 'POST', body: '{}' })
}

export function getFleet(params: FleetParams, signal?: AbortSignal): Promise<FleetResponse> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  })
  if (params.environment) query.set('environment', params.environment)
  if (params.healthStatus) query.set('health_status', params.healthStatus)
  return request(`/api/v1/databases?${query}`, signal)
}

export function getFleetSummary(signal?: AbortSignal): Promise<FleetHealthSummary> {
  return request('/api/v1/databases/summary', signal)
}

export function checkFleetStatus(): Promise<{ checked: number }> {
  return request('/api/v1/databases/check-fleet', undefined, { method: 'POST' })
}

export function deleteDatabase(id: string): Promise<void> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}`, undefined, { method: 'DELETE' })
}

export function getTnsAliases(signal?: AbortSignal): Promise<TnsAliasListResponse> {
  return request('/api/v1/databases/tns-aliases', signal)
}

export function testTnsConnection(body: DatabaseRegistration): Promise<ConnectionTestResponse> {
  return request('/api/v1/databases/test-tns-connection', undefined, { method: 'POST', body: JSON.stringify(body) })
}

export function registerFromTns(body: DatabaseRegistration): Promise<Target> {
  return request('/api/v1/databases/register-from-tns', undefined, { method: 'POST', body: JSON.stringify(body) })
}

export function getTarget(id: string, signal?: AbortSignal): Promise<TargetDetails> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}`, signal)
}

export function getDatabaseCompliance(id: string, signal?: AbortSignal): Promise<DatabaseCompliance> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/compliance`, signal)
}

export function getCompliancePolicies(signal?: AbortSignal): Promise<CompliancePolicyList> {
  return request('/api/v1/compliance/policies', signal)
}

export function getDatabaseForecast(id: string, signal?: AbortSignal): Promise<CapacityForecastResponse> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/forecast`, signal)
}

export function getMetricHistory(
  id: string,
  metricName: string,
  start: Date,
  end: Date,
  labels: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Observation[]> {
  const query = new URLSearchParams({
    metric_name: metricName,
    start: start.toISOString(),
    end: end.toISOString(),
    // The newest rows win when a long range holds more than this, so keep it at the server's maximum.
    limit: '5000',
  })
  for (const [key, value] of Object.entries(labels)) query.append('label', `${key}=${value}`)
  return request(`/api/v1/databases/${encodeURIComponent(id)}/metrics?${query}`, signal)
}

export interface IncidentListParams {
  page: number
  pageSize: number
  state?: string
  severity?: string
  owner?: string
  targetId?: string
}

export function clearIncidents(): Promise<{ deleted: number }> {
  return request('/api/v1/incidents', undefined, { method: 'DELETE' })
}

export function listIncidents(params: IncidentListParams, signal?: AbortSignal): Promise<IncidentListResponse> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  })
  if (params.state) query.set('state', params.state)
  if (params.severity) query.set('severity', params.severity)
  if (params.owner) query.set('owner', params.owner)
  if (params.targetId) query.set('target_id', params.targetId)
  return request(`/api/v1/incidents?${query}`, signal)
}

export function getIncident(id: string, signal?: AbortSignal): Promise<IncidentDetail> {
  return request(`/api/v1/incidents/${encodeURIComponent(id)}`, signal)
}

export interface IncidentActionPayload {
  actor: string
  reason?: string
}

export function acknowledgeIncident(id: string, payload: IncidentActionPayload, signal?: AbortSignal): Promise<Incident> {
  return request(`/api/v1/incidents/${encodeURIComponent(id)}/acknowledge`, signal, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function resolveIncident(id: string, payload: IncidentActionPayload, signal?: AbortSignal): Promise<Incident> {
  return request(`/api/v1/incidents/${encodeURIComponent(id)}/resolve`, signal, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export interface AlertLogEventParams {
  start?: string
  end?: string
  code?: string
  severity?: string
  search?: string
  limit?: number
}

export function getAlertLogEvents(targetId: string, params: AlertLogEventParams = {}, signal?: AbortSignal): Promise<AlertLogEvent[]> {
  const query = new URLSearchParams()
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.code) query.set('code', params.code)
  if (params.severity) query.set('severity', params.severity)
  if (params.search) query.set('search', params.search)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return request(`/api/v1/databases/${encodeURIComponent(targetId)}/events${qs ? `?${qs}` : ''}`, signal)
}

export interface NotificationListParams {
  incidentId?: string
  status?: string
  limit?: number
}

export function listNotifications(params: NotificationListParams = {}, signal?: AbortSignal): Promise<NotificationRecord[]> {
  const query = new URLSearchParams()
  if (params.incidentId) query.set('incident_id', params.incidentId)
  if (params.status) query.set('status', params.status)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return request(`/api/v1/notifications${qs ? `?${qs}` : ''}`, signal)
}

export function getPortalNotifications(page: number, signal?: AbortSignal): Promise<PortalNotificationListResponse> {
  return request(`/api/v1/portal-notifications?page=${page}&page_size=10`, signal)
}

export function readPortalNotification(id: string): Promise<PortalNotification> {
  return request(`/api/v1/portal-notifications/${encodeURIComponent(id)}/read`, undefined, { method: 'POST' })
}

export function listKnowledgeDocuments(signal?: AbortSignal): Promise<KnowledgeDocumentList> {
  return request('/api/v1/knowledge/documents?page=1&page_size=100', signal)
}

export function searchKnowledge(query: string, category: string, signal?: AbortSignal): Promise<KnowledgeSearchResponse> {
  return request('/api/v1/knowledge/search', signal, {
    method: 'POST',
    body: JSON.stringify({ query, limit: 8, category: category || null, rerank: true }),
  })
}

export function reindexKnowledge(): Promise<KnowledgeReindexResponse> {
  return request('/api/v1/knowledge/reindex', undefined, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export interface KnowledgeUploadReport {
  relative_path: string
  category: 'Knowledge' | 'Policy'
  size_bytes: number
  replaced: boolean
  scanned: number
  indexed: number
  updated: number
  unchanged: number
  quarantined: number
  errors: string[]
  note: string
}

/** Send one document as the raw request body. The server saves it in its intake folder and indexes it. */
export function uploadKnowledge(file: File, category: string, replace: boolean): Promise<KnowledgeUploadReport> {
  const query = new URLSearchParams({ filename: file.name, category, replace: String(replace) })
  return request(`/api/v1/knowledge/upload?${query.toString()}`, undefined, {
    method: 'POST',
    body: file,
    headers: { 'Content-Type': 'application/octet-stream' },
  })
}

export function createChatThread(scopeType: ChatThread['scope_type'], scopeId: string | null): Promise<ChatThread> {
  return request('/api/v1/chat/threads', undefined, {
    method: 'POST',
    body: JSON.stringify({ scope_type: scopeType, scope_id: scopeId }),
  })
}

export interface ChatStreamEvent {
  name: string
  data: unknown
}

export interface SendChatOptions {
  signal?: AbortSignal
  onEvent?: (event: ChatStreamEvent) => void
}

export async function sendChatMessage(
  threadId: string, content: string, modelChoice?: string, options?: SendChatOptions,
): Promise<ChatMessage> {
  let response: Response
  try {
    response = await fetch(`/api/v1/chat/threads/${encodeURIComponent(threadId)}/messages`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(modelChoice ? { content, model_choice: modelChoice } : { content }),
      signal: options?.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Could not reach the Oracle Sentinel API. Check the connection and try again.')
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status} ${response.statusText || 'error'})`
    try {
      const body: unknown = await response.json()
      if (typeof body === 'object' && body !== null && 'detail' in body && typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // Error bodies are JSON when the API rejects the message before streaming.
    }
    throw new ApiError(detail, response.status)
  }
  const events: ChatStreamEvent[] = []
  const collect = (event: ChatStreamEvent) => { events.push(event); options?.onEvent?.(event) }
  if (response.body) {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (value) buffer += decoder.decode(value, { stream: !done })
      let split = buffer.indexOf('\n\n')
      while (split >= 0) {
        const event = parseServerEvent(buffer.slice(0, split))
        if (event) collect(event)
        buffer = buffer.slice(split + 2)
        split = buffer.indexOf('\n\n')
      }
      if (done) break
    }
    const tail = parseServerEvent(buffer)
    if (tail) collect(tail)
  } else {
    for (const event of parseServerEvents(await response.text())) collect(event)
  }
  const message = events.find((event) => event.name === 'message')
  if (!message || !isChatMessage(message.data)) throw new ApiError('The copilot did not return an answer.')
  return message.data
}

function parseServerEvent(block: string): ChatStreamEvent | null {
  let name = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null
  return { name, data: JSON.parse(dataLines.join('\n')) as unknown }
}

function parseServerEvents(body: string): ChatStreamEvent[] {
  const events: ChatStreamEvent[] = []
  for (const block of body.split('\n\n')) {
    const event = parseServerEvent(block)
    if (event) events.push(event)
  }
  return events
}

function isChatMessage(value: unknown): value is ChatMessage {
  return typeof value === 'object' && value !== null && 'content' in value && 'llm_used' in value
}

export interface OperationProposal {
  id: string
  requester: string
  requester_role: string
  action: string
  target_id: string
  parameters: { tablespace?: string; size_mb?: number; request_text?: string }
  preview_sql: string
  impact: string
  risk: string
  status: string
  expires_at: string
  approver: string
  decision_reason: string
  execution_allowed: boolean
  execution_log: unknown[]
}

export interface AuditEventItem {
  id: string
  occurred_at: string
  principal: string
  action: string
  target: string
  outcome: string
  evidence_digest: string
}

export interface PlatformSummary {
  scheduler_enabled: boolean
  knowledge_enabled: boolean
  llm: { provider: string; model: string; hosted_vendor: string; secret_configured: boolean }
  notifications: { smtp_enabled: boolean; smtp_host_configured: boolean; smtp_from: string; smtp_secret_configured: boolean }
  roles: string[]
  legacy_roles: Record<string, string>
  retention_days: Record<string, number | null>
  proposal_expiry_hours: number
  rules: { id: string; expression: string; severity: string; enabled: boolean }[]
  execution_allowed: boolean
}

export function listProposals(signal?: AbortSignal) {
  return request<{ items: OperationProposal[] }>('/api/v1/operations/proposals', signal)
}

export function createProposal(targetId: string, requestText: string) {
  return request<OperationProposal>('/api/v1/operations/proposals', undefined, {
    method: 'POST',
    body: JSON.stringify({ target_id: targetId, request_text: requestText }),
  })
}

export function decideProposal(id: string, approved: boolean, reason: string) {
  return request<OperationProposal>(`/api/v1/operations/proposals/${id}/${approved ? 'approve' : 'reject'}`, undefined, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export function listAuditEvents(signal?: AbortSignal) {
  return request<{ items: AuditEventItem[] }>('/api/v1/audit/events', signal)
}

export interface AppConfigItem {
  key: string
  value: unknown
  updated_at: string
  section: string
  editable: boolean
}

export function listAppConfig(signal?: AbortSignal): Promise<{ items: AppConfigItem[] }> {
  return request('/api/v1/admin/config', signal)
}

export function updateAppConfig(key: string, value: unknown): Promise<AppConfigItem> {
  return request(`/api/v1/admin/config/${encodeURIComponent(key)}`, undefined, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })
}

export function getPlatform(signal?: AbortSignal) {
  return request<PlatformSummary>('/api/v1/admin/platform', signal)
}

export interface GraphPanel {
  id: string
  title: string
  message: string
}

export interface GraphShell {
  panels: GraphPanel[]
  detail: boolean
}

export function getGraphShell(signal?: AbortSignal): Promise<GraphShell> {
  return request('/api/v1/graph', signal)
}

export interface MemoryItem {
  name: string
  bytes: number | null
  display: string
}

export interface GraphMeter {
  percent: number | null
  caption: string
  limitation: string | null
}

export interface GraphMemory {
  sga: { items: MemoryItem[]; caption: string; limitation: string | null }
  pga: { items: MemoryItem[]; high: boolean; limitation: string | null }
  background_processes: Array<{ program: string; status: string; state: string }>
  hit_ratio: GraphMeter
  shared_pool_used: GraphMeter
  in_memory: { display: string; in_use: boolean; limitation: string | null }
  memory_management: { lines: string[]; limitation: string | null }
  data_guard: { stats: Array<{ name: string; value: string }>; limitation: string | null }
  result_cache: string
  collected_at: string
  limitations: string[]
  snapshot: string
}

export function getGraphMemory(id: string, signal?: AbortSignal): Promise<GraphMemory> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/graph/memory`, signal)
}

export interface StorageFile {
  name: string
  tablespace: string
  bytes: number | null
  display: string
  status: string
  online: boolean
}

export interface GraphStorage {
  data_files: {
    online_files: number
    online_tablespaces: number
    used_bytes: number | null
    used_display: string
    used_percent: number | null
    files: StorageFile[]
    flash_cache: string
    limitation: string | null
  }
  temp_files: { files: StorageFile[]; limitation: string | null }
  control_files: { files: Array<{ name: string; status: string }>; count: number; limitation: string | null }
  redo_logs: {
    groups: number
    total_bytes: number | null
    total_display: string
    active_percent: number | null
    members: Array<{ group: number; thread: number; path: string; bytes: number | null; display: string; status: string; current: boolean }>
    limitation: string | null
  }
  segments: {
    rows: Array<{ owner: string; name: string; segment_type: string; tablespace: string; bytes: number | null; display: string }>
    limitation: string | null
  }
  recovery_area: {
    present: boolean
    name: string
    used_percent: number | null
    used_display: string
    limit_display: string
    usage: Array<{ file_type: string; percent_space_used: number | null; number_of_files: number | null }>
    limitation: string | null
  }
  archive: {
    log_mode: string
    destinations: Array<{ destination: string; status: string }>
    latest: string
    free_minutes: string
    time_to_failure: string
    limitation: string | null
  }
  backup: {
    input_type: string
    status: string
    started: string
    ended: string
    failed: boolean
    limitation: string | null
  }
  collected_at: string
  limitations: string[]
  detail: boolean
  detail_note: string | null
}

export function getGraphStorage(id: string, signal?: AbortSignal): Promise<GraphStorage> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/graph/storage`, signal)
}

export interface GraphActivity {
  service: Array<{ label: string; value: string }>
  host: {
    cpu_percent: number | null
    cpu_caption: string
    run_queue: string | null
    free_memory: string | null
    limitation: string | null
  }
  server_processes: Array<{ name: string; count: number | null; limitation: string | null }>
  counters: Array<{ name: string; value: number | null }>
  collected_at: string
  limitations: string[]
}

export function getGraphActivity(id: string, signal?: AbortSignal): Promise<GraphActivity> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/graph/activity`, signal)
}

export interface GraphWaitEvent {
  event: string
  wait_class: string
  total_waits: number | null
  time_waited_micro: number | null
}

export interface GraphWaits {
  events: GraphWaitEvent[]
  cpu_micro: number | null
  collected_at: string
  limitations: string[]
  caption: string
}

export function getGraphWaits(id: string, signal?: AbortSignal): Promise<GraphWaits> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/graph/waits`, signal)
}

export interface SgaComponent {
  pool: string
  name: string
  bytes: number
  display: string
}

export interface SgaPool {
  name: string
  bytes: number
  display: string
  used_bytes: number
  free_bytes: number
  used_percent: number | null
}

export interface SgaBreakdown {
  pools: SgaPool[]
  components: SgaComponent[]
  shared_pool: SgaComponent[]
  total_bytes: number
  total_display: string
  collected_at: string
  limitation: string | null
  caption: string
}

/** MemoryDB: how the SGA is divided between its pools. */
export function getMemorySga(id: string, signal?: AbortSignal): Promise<SgaBreakdown> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/memory/sga`, signal)
}

export interface TablespaceRow {
  name: string
  contents: string
  status: string
  total_bytes: number | null
  free_bytes: number | null
  used_bytes: number | null
  used_percent: number | null
  fragments: number | null
}

export interface TablespaceUtilization {
  tablespaces: TablespaceRow[]
  total_bytes: number
  used_bytes: number
  total_display: string
  used_display: string
  collected_at: string
  limitation: string | null
  caption: string
}

/** StorageDB: size, free space, and fragments of every tablespace. */
export function getStorageTablespaces(id: string, signal?: AbortSignal): Promise<TablespaceUtilization> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/storage/tablespaces`, signal)
}

export interface DatabaseParameter {
  name: string
  value: string
  is_default: boolean
  sys_modifiable: string
  description: string
  restricted: boolean
}

export interface ParameterList {
  version: string
  parameters: DatabaseParameter[]
  total: number
  changed: number
  collected_at: string
  limitation: string | null
  detail: boolean
  detail_note: string | null
  caption: string
}

/** ParameterDB: the initialization parameters in effect. */
export function getDatabaseParameters(id: string, signal?: AbortSignal): Promise<ParameterList> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/parameters`, signal)
}

export interface AlertLine {
  at: string
  text: string
  severity: 'info' | 'warning' | 'error' | string
  codes: string[]
}

export interface AlertTail {
  lines: AlertLine[]
  errors: number
  warnings: number
  collected_at: string
  limitation: string | null
  detail: boolean
  detail_note: string | null
  window: string
  caption: string
}

/** AlertDB: the most recent lines of the database alert log. */
export function getDatabaseAlertLog(id: string, signal?: AbortSignal): Promise<AlertTail> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/alert-log`, signal)
}

export interface GraphHistorySample {
  taken_at: string
  age_seconds: number
  counters: Array<{ name: string; value: number | null }>
  events: GraphWaitEvent[]
  cpu_micro: number | null
  io: Array<{ name: string; value: number | null }>
  files: { reads: number | null; writes: number | null; read_time_cs: number | null; write_time_cs: number | null }
}

export interface GraphHistory {
  samples: GraphHistorySample[]
  enabled: boolean
  interval_seconds: number
  caption: string
}

/** Stored counter samples from PostgreSQL. Never fails the page: a problem returns no samples. */
export async function getGraphHistory(id: string, signal?: AbortSignal, minutes = 60): Promise<GraphHistory> {
  const empty: GraphHistory = { samples: [], enabled: false, interval_seconds: 0, caption: '' }
  try {
    const result = await request<GraphHistory>(
      `/api/v1/databases/${encodeURIComponent(id)}/graph/history?minutes=${minutes}`, signal,
    )
    if (!result || !Array.isArray(result.samples)) return empty
    return {
      samples: result.samples.filter((item) => item && typeof item.age_seconds === 'number'
        && Array.isArray(item.counters) && Array.isArray(item.events) && Array.isArray(item.io) && item.files != null),
      enabled: Boolean(result.enabled),
      interval_seconds: Number(result.interval_seconds) || 0,
      caption: typeof result.caption === 'string' ? result.caption : '',
    }
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
    return empty
  }
}

export interface WaitLock {
  name: string
  gets: number | null
  waits: number | null
  wait_time_ms: number | null
}

export interface WaitLocks {
  locks: WaitLock[]
  limitation: string | null
  caption: string
}

export interface WaitBlockingLock {
  sid: number | null
  serial_no: number | null
  username: string
  pid: string
  lock_type: string
  mode_wanted: string
  object_name: string
}

export interface WaitBlocking {
  locks: WaitBlockingLock[]
  limitation: string | null
  empty: string
  caption: string
}

export interface WaitNamedValue {
  name: string
  value: number | null
}

export interface WaitLibrary {
  namespace: string
  gets: number | null
  gethits: number | null
  reloads: number | null
  invalidations: number | null
}

export interface WaitParsing {
  counters: WaitNamedValue[]
  times: WaitNamedValue[]
  library: WaitLibrary[]
  limitations: string[]
  caption: string
}

export interface WaitBufferBusy {
  events: GraphWaitEvent[]
  classes: Array<{ name: string; count: number | null; time_cs: number | null }>
  users: Array<{ username: string; sid: number | null; total_waits: number | null; waited_ms: number | null }>
  tablespaces: Array<{ name: string; sessions: number | null }>
  limitations: string[]
  caption: string
}

export interface WaitIo {
  counters: WaitNamedValue[]
  files: {
    reads: number | null
    writes: number | null
    read_time_cs: number | null
    write_time_cs: number | null
    limitation: string | null
  }
  limitation: string | null
}

export interface WaitActivity {
  locking: WaitLocks
  blocking: WaitBlocking
  parsing: WaitParsing
  buffer_busy: WaitBufferBusy
  io: WaitIo
  collected_at: string
}

export function getWaitActivity(id: string, signal?: AbortSignal): Promise<WaitActivity> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/wait/activity`, signal)
}

export interface OperatorAccount {
  username: string
  role: string
  enabled: boolean
  created_at: string
}

export function listOperators(signal?: AbortSignal): Promise<{ items: OperatorAccount[] }> {
  return request('/api/v1/admin/operators', signal)
}

export function createOperator(username: string, password: string, role: string): Promise<OperatorAccount> {
  return request('/api/v1/admin/operators', undefined, { method: 'POST', body: JSON.stringify({ username, password, role }) })
}

export function setOperatorRole(username: string, role: string): Promise<{ username: string; role: string }> {
  return request(`/api/v1/admin/operators/${encodeURIComponent(username)}/role`, undefined, { method: 'POST', body: JSON.stringify({ role }) })
}

export function setOperatorEnabled(username: string, enabled: boolean): Promise<OperatorAccount> {
  return request(`/api/v1/admin/operators/${encodeURIComponent(username)}/enabled`, undefined, { method: 'POST', body: JSON.stringify({ enabled }) })
}

export function resetOperatorPassword(username: string, password: string): Promise<{ username: string; updated: boolean }> {
  return request(`/api/v1/admin/operators/${encodeURIComponent(username)}/password`, undefined, { method: 'POST', body: JSON.stringify({ password }) })
}

export function changeOwnPassword(currentPassword: string, newPassword: string): Promise<{ updated: boolean }> {
  return request('/api/v1/auth/password', undefined, {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
}

// ---- Agents: snapshots, investigations, findings, and per-database settings

export interface AgentStatus {
  enabled: boolean
  can_run: boolean
  can_configure: boolean
  model_writes_findings: boolean
  model: string
  open_findings: number
  packs: { name: string; label: string; readings: string[] }[]
  limits: { max_steps: number; max_model_calls: number; run_timeout_seconds: number }
}

export interface AgentEvidence {
  ref: string
  source: string
  text: string
}

export interface AgentFinding {
  id: string
  run_id: string
  target_id: string | null
  target_name: string
  incident_id: string | null
  severity: string
  title: string
  summary: string
  next_steps: string[]
  evidence: AgentEvidence[]
  confidence: string
  verified: boolean
  verifier_note: string
  written_by: string
  restricted: boolean
  hidden: boolean
  decision: 'open' | 'accepted' | 'dismissed'
  decided_by: string
  decided_at: string | null
  decision_note: string
  created_at: string | null
}

export interface AgentStep {
  seq: number
  kind: string
  title: string
  outcome: string
  duration_ms: number
  lines: string[]
  restricted_hidden: boolean
}

export interface AgentRun {
  id: string
  agent: string
  trigger: string
  scope_type: string
  target_id: string | null
  target_name: string
  incident_id: string | null
  goal: string
  playbook: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'stopped'
  principal: string
  llm_used: boolean
  model: string
  steps_used: number
  model_calls: number
  note: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  steps?: AgentStep[]
  finding?: AgentFinding | null
}

export interface SnapshotReading {
  kind: string
  label: string
  pack: string
  taken_at: string
  status: string
  row_count: number
  restricted: boolean
  hidden: boolean
  previous_at: string | null
  changes: string[]
}

export interface DatabaseSnapshots {
  target_id: string
  target_name: string
  enabled: boolean
  can_collect: boolean
  last_taken_at: string | null
  snapshot_count: number
  retention_days: number
  packs: string[]
  readings: SnapshotReading[]
  changed: number
}

export interface SnapshotResult {
  run_id: string
  batch_id: string
  taken_at: string
  answered: number
  readings: { kind: string; label: string; status: string; row_count: number; restricted: boolean; note: string }[]
  left_out: string[]
}

export interface AgentSettingItem {
  key: string
  label: string
  system_value: boolean | number | string
  database_value: boolean | number | string | null
  overridden: boolean
  value: boolean | number | string
}

export interface DatabaseAgentSettings {
  target_id: string
  system_enabled: boolean
  editable: boolean
  items: AgentSettingItem[]
}

export function getAgentStatus(signal?: AbortSignal): Promise<AgentStatus> {
  return request('/api/v1/agents/status', signal)
}

export function getDatabaseSnapshots(id: string, signal?: AbortSignal): Promise<DatabaseSnapshots> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/snapshots`, signal)
}

export function collectSnapshot(id: string): Promise<SnapshotResult> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/snapshots`, undefined, { method: 'POST', body: JSON.stringify({}) })
}

export function collectFleetSnapshot(): Promise<{ status: string }> {
  return request('/api/v1/agents/snapshots/fleet', undefined, { method: 'POST' })
}

export function startInvestigation(scope: { incidentId?: string; targetId?: string; goal?: string }): Promise<AgentRun> {
  return request('/api/v1/agents/investigations', undefined, {
    method: 'POST',
    body: JSON.stringify({ incident_id: scope.incidentId ?? null, target_id: scope.targetId ?? null, goal: scope.goal ?? '' }),
  })
}

export function listAgentRuns(
  filter: { incidentId?: string; targetId?: string; limit?: number } = {}, signal?: AbortSignal,
): Promise<{ items: AgentRun[] }> {
  const query = new URLSearchParams()
  if (filter.incidentId) query.set('incident_id', filter.incidentId)
  if (filter.targetId) query.set('target_id', filter.targetId)
  query.set('limit', String(filter.limit ?? 30))
  return request(`/api/v1/agents/runs?${query.toString()}`, signal)
}

export function getAgentRun(id: string, signal?: AbortSignal): Promise<AgentRun> {
  return request(`/api/v1/agents/runs/${encodeURIComponent(id)}`, signal)
}

export function listAgentFindings(
  filter: { decision?: 'open' | 'accepted' | 'dismissed' | 'all'; incidentId?: string; targetId?: string } = {},
  signal?: AbortSignal,
): Promise<{ items: AgentFinding[] }> {
  const query = new URLSearchParams()
  query.set('decision', filter.decision ?? 'open')
  if (filter.incidentId) query.set('incident_id', filter.incidentId)
  if (filter.targetId) query.set('target_id', filter.targetId)
  return request(`/api/v1/agents/findings?${query.toString()}`, signal)
}

export function decideFinding(id: string, decision: 'accepted' | 'dismissed', note = ''): Promise<AgentFinding> {
  return request(`/api/v1/agents/findings/${encodeURIComponent(id)}/decision`, undefined, {
    method: 'POST',
    body: JSON.stringify({ decision, note }),
  })
}

export function getDatabaseAgentSettings(id: string, signal?: AbortSignal): Promise<DatabaseAgentSettings> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/agent-settings`, signal)
}

export function setDatabaseAgentSetting(
  id: string, key: string, value: boolean | number | string | null,
): Promise<DatabaseAgentSettings> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}/agent-settings`, undefined, {
    method: 'PUT',
    body: JSON.stringify({ key, value }),
  })
}
