import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { Incident, IncidentDetail, IncidentListResponse } from './types'

const now = '2026-11-09T06:30:00Z'

function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 'incident-1',
    target_id: 'target-1',
    target_name: 'FINANCE',
    rule_id: 'ORA-00257',
    fingerprint: 'fp-1',
    state: 'new',
    severity: 'critical',
    message: 'Archiver error, connect internal only, until freed',
    first_seen: now,
    last_seen: now,
    owner: null,
    root_cause_candidate: null,
    evidence: { tablespace: 'USERS', used_pct: 97 },
    ...overrides,
  }
}

function incidentListResponse(items: Incident[] = [incident()]): IncidentListResponse {
  return { items, total: items.length, page: 1, page_size: 25 }
}

function incidentDetail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    ...incident(),
    events: [{ id: 'event-1', type: 'created', timestamp: now, actor: 'system', reason: null, before: {}, after: {} }],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/v1/auth/session')) {
      const session = await handler(url, init)
      if (session.status !== 404) return session
    }
    if (url.includes('/portal-notifications')) {
      return jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 })
    }
    if (url.includes('/databases?')) {
      const custom = await handler(url, init)
      if (custom.status !== 404) return custom
      return jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] })
    }
    if (url.includes('page_size=100')) {
      return jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] })
    }
    return handler(url, init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('Incident center', () => {
  beforeEach(() => {
    window.location.hash = ''
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('lists incidents and opens the detail view', async () => {
    window.location.hash = '#/incidents'
    mockFetch((url) => {
      if (url.includes('/api/v1/incidents/incident-1')) return jsonResponse(incidentDetail())
      if (url.includes('/api/v1/incidents')) return jsonResponse(incidentListResponse())
      return jsonResponse({ detail: 'not found' }, 404)
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Incidents' })).toBeInTheDocument()
    expect(await screen.findByText('Archiver error, connect internal only, until freed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'FINANCE' })).toBeInTheDocument()
    expect(screen.getByLabelText('Incident status: New')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh incidents' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear all incidents' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Filter by database')).toBeInTheDocument()

    await user.click(screen.getByLabelText('View incident details'))
    expect(await screen.findByRole('heading', { name: 'Archiver error, connect internal only, until freed' })).toBeInTheDocument()
  })

  it('refreshes the incident list and filters it by database', async () => {
    window.location.hash = '#/incidents'
    const finance = incident()
    const warehouse = incident({
      id: 'incident-2', target_id: 'target-2', target_name: 'WAREHOUSE', message: 'Warehouse archive is full',
    })
    const fetchMock = mockFetch((url) => {
      if (url.includes('/databases?')) {
        return jsonResponse({
          items: [
            { id: 'target-1', display_name: 'FINANCE' },
            { id: 'target-2', display_name: 'WAREHOUSE' },
          ],
          total: 2, page: 1, page_size: 100, environments: [],
        })
      }
      if (url.includes('target_id=target-2')) return jsonResponse(incidentListResponse([warehouse]))
      if (url.includes('/api/v1/incidents')) return jsonResponse(incidentListResponse([finance, warehouse]))
      return jsonResponse({ detail: 'not found' }, 404)
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText('Warehouse archive is full')).toBeInTheDocument()
    const callsBeforeRefresh = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/api/v1/incidents')).length
    await user.click(screen.getByRole('button', { name: 'Refresh incidents' }))
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter((call) => String(call[0]).includes('/api/v1/incidents')).length
      expect(calls).toBeGreaterThan(callsBeforeRefresh)
    })
    await user.selectOptions(screen.getByLabelText('Filter by database'), 'target-2')
    expect(await screen.findByText('Warehouse archive is full')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Archiver error, connect internal only, until freed')).not.toBeInTheDocument())
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes('target_id=target-2'))).toBe(true)
  })

  it('uses the registered database name when the incident has none', async () => {
    window.location.hash = '#/incidents'
    mockFetch((url) => {
      if (url.includes('/databases?')) {
        return jsonResponse({
          items: [{ id: 'target-1', display_name: 'FINANCE' }],
          total: 1, page: 1, page_size: 100, environments: [],
        })
      }
      if (url.includes('/api/v1/incidents')) {
        return jsonResponse(incidentListResponse([incident({ target_name: '' })]))
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    render(<App />)
    expect(await screen.findByRole('button', { name: 'FINANCE' })).toBeInTheDocument()
    expect(screen.queryByText('Unknown database')).not.toBeInTheDocument()
  })

  it('lets an administrator clear every incident', async () => {
    window.location.hash = '#/incidents'
    let cleared = false
    mockFetch((url, init) => {
      if (url.includes('/api/v1/auth/session')) {
        return jsonResponse({ required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local' })
      }
      if (init?.method === 'DELETE' && url.endsWith('/api/v1/incidents')) {
        cleared = true
        return jsonResponse({ deleted: 1 })
      }
      if (url.includes('/api/v1/incidents')) {
        return jsonResponse(incidentListResponse(cleared ? [] : [incident()]))
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Clear all incidents' }))
    expect(confirm).toHaveBeenCalled()
    expect(await screen.findByText('No incidents reported')).toBeInTheDocument()
    confirm.mockRestore()
  })

  it('acknowledges an incident and shows a success message', async () => {
    window.location.hash = '#/incidents/incident-1'
    mockFetch((url, init) => {
      if (url.includes('/acknowledge') && init?.method === 'POST') {
        return jsonResponse({ ...incident(), state: 'acknowledged' })
      }
      if (url.includes('/api/v1/incidents/incident-1')) {
        return jsonResponse(incidentDetail())
      }
      return jsonResponse({ detail: 'not found' }, 404)
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('button', { name: 'Acknowledge' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Acknowledge' }))
    await waitFor(() => {
      expect(screen.getByText('Incident acknowledged.')).toBeInTheDocument()
    })
  })
})
