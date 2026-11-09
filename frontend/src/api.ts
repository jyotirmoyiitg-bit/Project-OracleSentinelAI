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
} from './types'

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
