import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { FleetResponse, Observation, Target, TargetDetails } from './types'

const now = '2026-11-09T06:30:00Z'

function target(overrides: Partial<Target> = {}): Target {
  return {
    id: 'target-1',
    display_name: 'FINANCE-PRD',
    environment: 'production',
    enabled: true,
    tns_alias: 'finance-prd',
    engine: 'Oracle 19c',
    owner: 'DBA team',
    poll_profile: { name: 'standard' },
    capability_snapshot: {},
    last_connection_at: now,
    last_health_status: 'healthy',
    created_at: now,
    updated_at: now,
    last_seen: now,
    monitoring_status: 'active',
    critical_incident_count: null,
    trend_snapshots: [],
    ...overrides,
  }
}

function observation(overrides: Partial<Observation> = {}): Observation {
  return {
    id: 'obs-1',
    target_id: 'target-1',
    metric_name: 'storage.tablespace.used_pct',
    timestamp: now,
    value: 72.5,
    value_str: null,
    unit: '%',
    labels: {},
    quality: 'measured',
    source: 'tablespace_probe',
    ...overrides,
  }
}

function fleetResponse(items: Target[] = [target()], overrides: Partial<FleetResponse> = {}): FleetResponse {
  return { items, total: items.length, page: 1, page_size: 25, environments: ['production', 'staging'], ...overrides }
}

function detailResponse(overrides: Partial<TargetDetails> = {}): TargetDetails {
  return {
    ...target(),
    latest_observations: [observation()],
    poll_status: [{ probe_type: 'connectivity', status: 'healthy', last_success_at: now, last_attempt_at: now, error_class: null }],
    compliance: { pass: 8, fail: 1, unknown: 2, not_applicable: 0, error: 0 },
    tablespaces: [{ name: 'USERS', used_pct: 72.5, size_bytes: 1024 ** 3, autoextend: true, timestamp: now }],
    fra: { size_bytes: 10 * 1024 ** 3, used_bytes: 6 * 1024 ** 3, used_pct: 60, breakdown: [] },
    archive: { log_mode: 'ARCHIVELOG', destinations: [] },
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockFetch(handler: (url: string) => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => Promise.resolve(
    String(input).includes('/api/v1/auth/session')
      ? jsonResponse({ required: false, authenticated: true, principal: '', role: 'operator', method: '' })
      : String(input).includes('/portal-notifications')
      ? jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 })
      : String(input).includes('/compliance/policies')
      ? jsonResponse({ items: [] })
      : String(input).includes('/compliance')
      ? jsonResponse({ target_id: 'target-1', environment: 'production', pass: 0, fail: 0, unknown: 0, not_applicable: 0, error: 0, items: [] })
      : String(input).includes('/forecast')
      ? jsonResponse({ target_id: 'target-1', generated_at: now, items: [] })
      : String(input).endsWith('/databases/summary')
      ? jsonResponse({ total: 0, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0, disabled: 0, scheduler_enabled: true })
      : String(input).includes('page_size=100')
      ? jsonResponse(fleetResponse([]))
      : handler(String(input)),
  ))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('Oracle Sentinel dashboard', () => {
  beforeEach(() => {
    window.location.hash = ''
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders fleet health and shows unavailable incident counts without inventing them', async () => {
    mockFetch(() => jsonResponse(fleetResponse([
      target(),
      target({ id: 'target-2', display_name: 'WAREHOUSE-DEV', environment: 'development', last_health_status: 'offline', last_seen: null }),
    ])))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Database fleet' })).toBeInTheDocument()
    expect(await screen.findByText('FINANCE-PRD')).toBeInTheDocument()
    expect(screen.getByText('WAREHOUSE-DEV')).toBeInTheDocument()
    expect(screen.getByLabelText('Health status: Healthy')).toBeInTheDocument()
    expect(screen.getByLabelText('Health status: Offline')).toBeInTheDocument()
    expect(screen.getAllByText('Not available')).toHaveLength(2)
    expect(screen.getByText('No telemetry received')).toBeInTheDocument()
  })

  it('requests filtered and paginated fleet data from the API', async () => {
    const fetchMock = mockFetch(() => jsonResponse(fleetResponse()))
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('FINANCE-PRD')

    await user.selectOptions(screen.getByLabelText('Filter by environment'), 'staging')
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('environment=staging'), expect.anything())
    })
    await user.selectOptions(screen.getByLabelText('Filter by health status'), 'warning')
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('health_status=warning'), expect.anything())
    })
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('page_size=25'), expect.anything())
  })

  it('shows API errors explicitly and lets the operator retry', async () => {
    let attempts = 0
    mockFetch(() => {
      attempts += 1
      return attempts === 1
        ? jsonResponse({ detail: 'Collector API is unavailable' }, 503)
        : jsonResponse(fleetResponse())
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Collector API is unavailable')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('FINANCE-PRD')).toBeInTheDocument()
  })

  it('renders database details and an explicit empty history chart', async () => {
    window.location.hash = '#/databases/target-1'
    mockFetch((url) => {
      if (url.includes('/metrics?')) return jsonResponse([])
      if (url.endsWith('/target-1')) return jsonResponse(detailResponse())
      return jsonResponse({ detail: 'Not found' }, 404)
    })
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'FINANCE-PRD' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: /Tablespaces/ })).toBeInTheDocument()
    expect(screen.getByText('ARCHIVELOG')).toBeInTheDocument()
    expect(screen.getByText('USERS')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
    expect(await screen.findByText('No history available')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Recovery area 60.0 percent used' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Compliance policies' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Capacity forecasts' })).toBeInTheDocument()
  })

  it('plots metric history returned by the metrics endpoint', async () => {
    window.location.hash = '#/databases/target-1'
    const samples = [
      observation({ id: 'obs-old', timestamp: '2026-11-09T05:30:00Z', value: 64 }),
      observation({ id: 'obs-new', timestamp: '2026-11-09T06:30:00Z', value: 72.5 }),
    ]
    mockFetch((url) => {
      if (url.includes('/metrics?')) return jsonResponse(samples)
      if (url.endsWith('/target-1')) return jsonResponse(detailResponse())
      return jsonResponse([])
    })
    render(<App />)

    expect(await screen.findByRole('img', { name: 'Tablespace Used Pct timeline with 2 measurements' })).toBeInTheDocument()
    const range = screen.getByLabelText('Timeline time range')
    expect(range).toHaveValue('24h')
    expect(within(range.parentElement as HTMLElement).getByRole('option', { name: 'Last 7 days' })).toBeInTheDocument()
  })

  it('shows an explicit detail API failure', async () => {
    window.location.hash = '#/databases/missing'
    mockFetch(() => jsonResponse({ detail: 'Target does not exist' }, 404))
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Target does not exist')
    expect(screen.getByRole('button', { name: 'Back to fleet' })).toBeInTheDocument()
  })

  it('shows the signed-in user, role, and face icon', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/auth/session')) {
        return Promise.resolve(jsonResponse({
          required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local',
        }))
      }
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.endsWith('/databases/summary')) return Promise.resolve(jsonResponse({ total: 0, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0, disabled: 0, scheduler_enabled: true }))
      if (url.includes('/databases')) return Promise.resolve(jsonResponse(fleetResponse([])))
      return Promise.resolve(jsonResponse({}))
    }))
    render(<App />)
    const signedIn = await screen.findByRole('status', { name: 'Signed in as sentinel, Admin' })
    expect(within(signedIn).getByText('sentinel')).toBeInTheDocument()
    expect(within(signedIn).getByText('Admin')).toBeInTheDocument()
    expect(signedIn.querySelector('.lucide-user-round')).toBeTruthy()
  })
})
