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
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(
    String(input).includes('/portal-notifications')
      ? jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 })
      : String(input).includes('page_size=100')
      ? jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] })
      : handler(String(input), init),
  ))
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
    expect(screen.getByLabelText('Incident status: New')).toBeInTheDocument()

    await user.click(screen.getByLabelText('View incident details'))
    expect(await screen.findByRole('heading', { name: 'Archiver error, connect internal only, until freed' })).toBeInTheDocument()
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
