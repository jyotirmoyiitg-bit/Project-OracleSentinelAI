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
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
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
  signal?: AbortSignal,
): Promise<Observation[]> {
  const query = new URLSearchParams({
    metric_name: metricName,
    start: start.toISOString(),
    end: end.toISOString(),
    limit: '1000',
  })
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
    headers: { 'X-Sentinel-Role': 'admin' },
  })
}

export function createChatThread(scopeType: ChatThread['scope_type'], scopeId: string | null): Promise<ChatThread> {
  return request('/api/v1/chat/threads', undefined, {
    method: 'POST',
    body: JSON.stringify({ scope_type: scopeType, scope_id: scopeId }),
  })
}

export async function sendChatMessage(threadId: string, content: string, modelChoice?: string): Promise<ChatMessage> {
  let response: Response
  try {
    response = await fetch(`/api/v1/chat/threads/${encodeURIComponent(threadId)}/messages`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(modelChoice ? { content, model_choice: modelChoice } : { content }),
    })
  } catch {
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
  const events = parseServerEvents(await response.text())
  const message = events.find((event) => event.name === 'message')
  if (!message || !isChatMessage(message.data)) throw new ApiError('The copilot did not return an answer.')
  return message.data
}

function parseServerEvents(body: string): { name: string; data: unknown }[] {
  const events: { name: string; data: unknown }[] = []
  for (const block of body.split('\n\n')) {
    let name = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length === 0) continue
    events.push({ name, data: JSON.parse(dataLines.join('\n')) as unknown })
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
