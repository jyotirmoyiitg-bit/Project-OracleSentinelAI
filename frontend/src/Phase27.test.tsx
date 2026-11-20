import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { TimelineChart } from './components/TimelineChart'
import { seriesFor, seriesKey } from './series'
import type { Observation, TargetDetails } from './types'

const now = '2026-11-20T02:44:00Z'
const earlier = '2026-11-20T02:39:00Z'

function used(tablespace: string, value: number, timestamp = now, metric = 'storage.tablespace.used_pct'): Observation {
  return {
    id: `${metric}-${tablespace}-${timestamp}`, target_id: 'target-1', metric_name: metric, timestamp, value,
    value_str: null, unit: 'percent', labels: { tablespace_name: tablespace, source: 'dba_tablespaces' },
    quality: 'measured', source: 'dba_tablespaces',
  }
}

// The same mix the MY19C overview showed: three nearly full tablespaces, one small, one almost empty.
const latest = [used('INDX', 0.47), used('SYSAUX', 94.06), used('SYSTEM', 94.19), used('UNDOTBS1', 10.58), used('USERS', 94.41)]

function detail(): TargetDetails {
  return {
    id: 'target-1', display_name: 'MY19C', environment: 'dev', enabled: true, tns_alias: 'MY19C', engine: 'oracle',
    owner: '', poll_profile: {}, capability_snapshot: {}, last_connection_at: now, last_health_status: 'warning',
    created_at: now, updated_at: now, last_seen: now, monitoring_status: 'warning', critical_incident_count: 0,
    trend_snapshots: [],
    latest_observations: latest,
    poll_status: [],
    compliance: { pass: 0, fail: 0, unknown: 0, not_applicable: 0, error: 0 },
    tablespaces: [
      { name: 'USERS', used_pct: 94.41, used_pct_of_max: 2.9, max_bytes: 32 * 1024 ** 3, size_bytes: 1024 ** 3, autoextend: true, timestamp: now },
      { name: 'INDX', used_pct: 0.47, used_pct_of_max: null, max_bytes: null, size_bytes: 1024 ** 3, autoextend: false, timestamp: now },
    ],
    fra: { size_bytes: null, used_bytes: null, used_pct: null, breakdown: [] },
    archive: { log_mode: 'ARCHIVELOG', destinations: [] },
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockFetch(history: (url: string) => Observation[]) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/v1/auth/session')) return Promise.resolve(json({ required: false, authenticated: true, principal: '', role: 'operator', method: '' }))
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.includes('/compliance/policies')) return Promise.resolve(json({ items: [] }))
    if (url.includes('/compliance')) return Promise.resolve(json({ target_id: 'target-1', environment: 'dev', pass: 0, fail: 0, unknown: 0, not_applicable: 0, error: 0, items: [] }))
    if (url.includes('/forecast')) return Promise.resolve(json({ target_id: 'target-1', generated_at: now, items: [] }))
    if (url.endsWith('/databases/summary')) return Promise.resolve(json({ total: 0, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0, disabled: 0, scheduler_enabled: true }))
    if (url.includes('/metrics?')) return Promise.resolve(json(history(url)))
    if (url.endsWith('/target-1')) return Promise.resolve(json(detail()))
    if (url.includes('page_size=')) return Promise.resolve(json({ items: [], total: 0, page: 1, page_size: 100, environments: [] }))
    return Promise.resolve(json([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function metricCalls(fetchMock: ReturnType<typeof mockFetch>): URL[] {
  return fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.includes('/metrics?'))
    .map((url) => new URL(url, 'http://localhost'))
}

describe('Phase 27 tablespace series', () => {
  beforeEach(() => {
    window.location.hash = '#/databases/target-1'
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('orders series by their latest value and ignores the source label', () => {
    const series = seriesFor(latest, 'storage.tablespace.used_pct')
    expect(series.map((item) => item.name)).toEqual(['USERS', 'SYSTEM', 'SYSAUX', 'UNDOTBS1', 'INDX'])
    expect(series[0].labels).toEqual({ tablespace_name: 'USERS' })
    expect(seriesKey({ source: 'x' })).toBe('')
    expect(seriesFor([used('USERS', 50, earlier), used('USERS', 60)], 'storage.tablespace.used_pct')[0].value).toBe(60)
  })

  it('shows the fullest tablespace on the card, by name', async () => {
    mockFetch(() => [])
    render(<App />)

    const card = (await screen.findByText('Tablespace usage')).closest('.metric-card') as HTMLElement
    expect(card).toHaveTextContent('94.4 percent')
    expect(card).toHaveTextContent('USERS, the fullest of 5 · Updated')
    expect(card).not.toHaveTextContent('0.5 percent')
  })

  it('charts the fullest tablespace by default and asks for that series only', async () => {
    const fetchMock = mockFetch((url) => {
      const label = new URL(url, 'http://localhost').searchParams.get('label')
      return label === 'tablespace_name=USERS' ? [used('USERS', 94.3, earlier), used('USERS', 94.41)] : []
    })
    render(<App />)

    expect(await screen.findByRole('img', { name: 'Tablespace Used Pct · USERS timeline with 2 measurements' })).toBeInTheDocument()
    const picker = screen.getByLabelText('Timeline series')
    expect(picker).toHaveValue(seriesKey({ tablespace_name: 'USERS' }))
    expect(within(picker).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'USERS (94.4%)', 'SYSTEM (94.2%)', 'SYSAUX (94.1%)', 'UNDOTBS1 (10.6%)', 'INDX (0.5%)', 'All tablespaces',
    ])
    const calls = metricCalls(fetchMock)
    expect(calls.at(-1)?.searchParams.getAll('label')).toEqual(['tablespace_name=USERS'])
    expect(calls.at(-1)?.searchParams.get('limit')).toBe('5000')
  })

  it('draws every tablespace as its own line when all are chosen', async () => {
    const fetchMock = mockFetch((url) => (new URL(url, 'http://localhost').searchParams.get('label')
      ? []
      : latest.flatMap((row) => [{ ...row, id: `${row.id}-old`, timestamp: earlier }, row])))
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(await screen.findByLabelText('Timeline series'), 'All tablespaces')
    expect(await screen.findByRole('img', { name: 'Tablespace Used Pct timeline with 10 measurements across 5 series' })).toBeInTheDocument()
    await waitFor(() => expect(metricCalls(fetchMock).at(-1)?.searchParams.getAll('label')).toEqual([]))
  })

  it('switches series when a person picks another tablespace', async () => {
    const fetchMock = mockFetch(() => [])
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(await screen.findByLabelText('Timeline series'), 'INDX (0.5%)')
    await waitFor(() => expect(metricCalls(fetchMock).at(-1)?.searchParams.getAll('label')).toEqual(['tablespace_name=INDX']))
  })

  it('never joins two tablespaces into one line', () => {
    render(<TimelineChart observations={[used('INDX', 0.47, earlier), used('USERS', 94.41, earlier), used('INDX', 0.47), used('USERS', 94.41)]} unit="percent" label="Tablespace Used Pct" />)
    expect(screen.getByRole('img', { name: 'Tablespace Used Pct timeline with 4 measurements across 2 series' })).toBeInTheDocument()
  })

  it('shows used space against the autoextend maximum in the tablespace table', async () => {
    mockFetch(() => [])
    render(<App />)

    expect(await screen.findByRole('button', { name: 'Sort by Of maximum' })).toBeInTheDocument()
    expect(screen.getByText('2.9%')).toBeInTheDocument()
  })
})
