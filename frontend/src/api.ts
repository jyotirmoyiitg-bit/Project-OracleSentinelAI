import type { FleetResponse, Observation, TargetDetails } from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { Accept: 'application/json' },
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

export function getTarget(id: string, signal?: AbortSignal): Promise<TargetDetails> {
  return request(`/api/v1/databases/${encodeURIComponent(id)}`, signal)
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
