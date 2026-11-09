import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { DatabaseRegistration, FleetHealthSummary, Target, TargetDetails } from './types'

function target(id: string, health = 'unknown', enabled = true): Target {
  return {
    id, display_name: id, tns_alias: id, environment: 'production', enabled,
    engine: 'oracle', owner: 'DBA', poll_profile: {}, capability_snapshot: {},
    last_connection_at: null, last_health_status: health, created_at: '2026-11-09T10:00:00Z',
    updated_at: '2026-11-09T10:00:00Z', last_seen: null, monitoring_status: health,
    critical_incident_count: null, trend_snapshots: [],
  }
}

function details(item: Target): TargetDetails {
  return {
    ...item, latest_observations: [], poll_status: [], compliance: { pass: 0, fail: 0, unknown: 7 },
    tablespaces: [], fra: { size_bytes: null, used_bytes: null, used_pct: null, breakdown: [] },
    archive: { log_mode: null, destinations: [] },
  }
}

function summary(total = 0, overrides: Partial<FleetHealthSummary> = {}): FleetHealthSummary {
  return { total, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: total, disabled: 0, scheduler_enabled: true, ...overrides }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setupApi(items: Target[] = [], override?: (url: string, init?: RequestInit) => Response | undefined) {
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const custom = override?.(url, init)
    if (custom) return Promise.resolve(custom)
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.endsWith('/databases/summary')) return Promise.resolve(json(summary(items.length)))
    if (url.includes('/metrics?')) return Promise.resolve(json([]))
    if (url.includes('/databases?')) return Promise.resolve(json({ items, total: items.length, page: 1, page_size: 100, environments: ['production'] }))
    const item = items.find((item) => url.endsWith(`/databases/${item.id}`))
    if (item) return Promise.resolve(json(details(item)))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

beforeEach(() => {
  window.location.hash = ''
  window.localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Multi-database workspace', () => {
  it('shows all databases, truthful status colors and fleet-wide counts, then opens selected details on the right', async () => {
    const items = [target('FINANCE', 'healthy'), target('WAREHOUSE', 'warning'), target('REPORTING', 'offline'), target('DEVELOPMENT')]
    setupApi(items, (url) => {
      if (url.endsWith('/databases/summary')) return json(summary(4, { healthy: 1, warning: 1, offline: 1, unknown: 1 }))
      if (url.includes('page_size=25')) return json({ items: [items[0]], total: 4, page: 1, page_size: 25, environments: ['production'] })
    })
    const user = userEvent.setup()
    render(<App />)
    const sidebar = screen.getByRole('complementary', { name: 'Registered databases' })
    await within(sidebar).findByRole('button', { name: 'Select FINANCE' })
    expect(within(sidebar).getByLabelText('FINANCE: Healthy')).toHaveClass('sidebar-health-healthy')
    expect(within(sidebar).getByLabelText('WAREHOUSE: Warning')).toHaveClass('sidebar-health-warning')
    expect(within(sidebar).getByLabelText('REPORTING: Down')).toHaveClass('sidebar-health-offline')
    expect(within(sidebar).getByLabelText('DEVELOPMENT: Unknown / stale')).toHaveClass('sidebar-health-unknown')
    const metrics = screen.getByRole('region', { name: 'Fleet summary' })
    await waitFor(() => expect(within(metrics).getByText('Needs attention').closest('article')).toHaveTextContent('2'))
    expect(within(metrics).getByText('Registered databases').closest('article')).toHaveTextContent('4')
    expect(screen.getByRole('button', { name: 'Fleet overview' })).toHaveAttribute('aria-current', 'page')
    await user.click(within(sidebar).getByRole('button', { name: 'Select REPORTING' }))
    expect(await screen.findByRole('heading', { name: 'REPORTING' })).toBeInTheDocument()
    expect(within(sidebar).getByRole('button', { name: 'Select REPORTING' })).toHaveAttribute('aria-current', 'page')
    expect(within(sidebar).getByRole('button', { name: 'Select FINANCE' })).toBeInTheDocument()
    await user.click(within(sidebar).getByRole('button', { name: 'Fleet overview' }))
    expect(await screen.findByRole('heading', { name: 'Database fleet' })).toBeInTheDocument()
  })

  it('registers from TNS, tests the connection, reloads the sidebar and preserves registrations on remount', async () => {
    const items = [target('FINANCE')]
    const fetch = setupApi(items, (url, init) => {
      if (url.endsWith('/tns-aliases')) return json({ items: [{ name: 'FINANCE', registered: true }, { name: 'REPORTING', registered: false }], scheduler_enabled: true })
      if (url.endsWith('/test-tns-connection')) return json({ status: 'connected', message: 'Connected', version: '19c' })
      if (url.endsWith('/register-from-tns')) {
        const payload: DatabaseRegistration = JSON.parse(String(init?.body))
        const registered = { ...target('reporting-id'), ...payload }
        items.push(registered)
        return json(registered, 201)
      }
    })
    const user = userEvent.setup()
    const view = render(<App />)
    await user.click(screen.getByRole('button', { name: 'Add database' }))
    const alias = await screen.findByLabelText('TNS alias')
    await waitFor(() => expect(screen.getByRole('option', { name: 'FINANCE (already registered)' })).toBeDisabled())
    await user.selectOptions(alias, 'REPORTING')
    await user.clear(screen.getByLabelText('Display name'))
    await user.type(screen.getByLabelText('Display name'), 'Reporting production')
    await user.selectOptions(screen.getByLabelText('Environment'), 'production')
    await user.type(screen.getByLabelText('Owner (optional)'), 'Reporting DBA')
    await user.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('Connection successful (Oracle 19c). Ready to register.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Register database' }))
    expect(await screen.findByRole('heading', { name: 'Reporting production' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Select Reporting production' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/v1/databases/register-from-tns', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ tns_alias: 'REPORTING', display_name: 'Reporting production', environment: 'production', owner: 'Reporting DBA' }),
    }))
    view.unmount()
    window.location.hash = ''
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Database fleet' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Select Reporting production' })).toBeInTheDocument()
    expect(window.localStorage.getItem('databases')).toBeNull()
  })

  it('keeps failed registrations editable and reports scheduler-disabled state', async () => {
    window.location.hash = '#/register'
    setupApi([], (url) => {
      if (url.endsWith('/tns-aliases')) return json({ items: [{ name: 'REPORTING', registered: false }], scheduler_enabled: false })
      if (url.endsWith('/register-from-tns')) return json({ detail: 'Connection failed; check backend credentials' }, 422)
    })
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('option', { name: 'REPORTING' })
    await user.selectOptions(screen.getByLabelText('TNS alias'), 'REPORTING')
    await user.click(screen.getByRole('button', { name: 'Register database' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection failed; check backend credentials')
    expect(screen.getByLabelText('Display name')).toHaveValue('REPORTING')
    expect(screen.getByText(/Automatic monitoring is disabled/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Select REPORTING' })).not.toBeInTheDocument()
  })

  it('reports an alias-list failure and lets operators retry', async () => {
    window.location.hash = '#/register'
    let fail = true
    setupApi([], (url) => {
      if (url.endsWith('/tns-aliases')) return fail ? json({ detail: 'TNS configuration unavailable' }, 503) : json({ items: [], scheduler_enabled: true })
    })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('TNS configuration unavailable')
    expect(screen.getByRole('button', { name: 'Register database' })).toBeDisabled()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Retry aliases' }))
    expect(await screen.findByText(/No unregistered aliases are available/)).toBeInTheDocument()
  })

  it('loads every sidebar page rather than silently limiting registrations to 100', async () => {
    const items = Array.from({ length: 101 }, (_, i) => target(`DATABASE-${i + 1}`))
    const fetch = setupApi([], (url) => {
      if (url.includes('page_size=100')) {
        const page = Number(new URL(url, 'http://localhost').searchParams.get('page'))
        return json({ items: items.slice((page - 1) * 100, page * 100), total: 101, page, page_size: 100, environments: [] })
      }
    })
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Select DATABASE-101' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('page=2&page_size=100'), expect.anything())
  })

  it('shows disabled targets in gray and makes sidebar failures explicit', async () => {
    const items = [target('DISABLED', 'healthy', false)]
    setupApi(items)
    render(<App />)
    expect(await screen.findByLabelText('DISABLED: Disabled')).toHaveClass('sidebar-health-disabled')
    cleanup()
    let fail = true
    setupApi(items, (url) => url.includes('page_size=100') && fail ? json({ detail: 'Registry unavailable' }, 503) : undefined)
    const user = userEvent.setup()
    render(<App />)
    const sidebar = screen.getByRole('complementary', { name: 'Registered databases' })
    expect(await within(sidebar).findByRole('alert')).toHaveTextContent('Registry unavailable')
    fail = false
    await user.click(screen.getByRole('button', { name: 'Retry database list' }))
    expect(await within(sidebar).findByRole('button', { name: 'Select DISABLED' })).toBeInTheDocument()
  })

  it('does not present a failed summary request as zero healthy or zero incidents', async () => {
    setupApi([target('FINANCE')], (url) => url.endsWith('/databases/summary') ? json({ detail: 'Health summary unavailable' }, 503) : undefined)
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Fleet summary unavailable')
    const summary = screen.getByRole('region', { name: 'Fleet summary' })
    expect(within(summary).getByText('Healthy databases').closest('article')).toHaveTextContent('—')
    expect(screen.queryByRole('region', { name: 'Fleet health breakdown' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Select FINANCE' })).toBeInTheDocument()
  })

  it('defaults to 30-second refresh but respects an explicitly saved off preference', async () => {
    setupApi()
    const view = render(<App />)
    expect(screen.getByLabelText('Auto refresh interval')).toHaveValue('30')
    await screen.findByText('No databases registered. Add a database to begin monitoring.')
    view.unmount()
    window.localStorage.setItem('oracle-sentinel-refresh', '0')
    render(<App />)
    expect(screen.getByLabelText('Auto refresh interval')).toHaveValue('0')
  })
})
