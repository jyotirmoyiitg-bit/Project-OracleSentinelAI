import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

function database(id: string, name: string, alias: string) {
  return {
    id, display_name: name, tns_alias: alias, enabled: true, environment: 'production', engine: 'Oracle 19c',
    owner: 'DBA team', poll_profile: {}, capability_snapshot: {}, last_connection_at: null,
    last_health_status: 'unknown', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
    last_seen: null, monitoring_status: 'unknown', critical_incident_count: null, trend_snapshots: [],
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function activity(second: boolean) {
  return {
    locking: {
      locks: [
        { name: 'Process Startup', gets: second ? 2010 : 1950, waits: second ? 5 : 3, wait_time_ms: second ? 118 : 73 },
        { name: 'File Object', gets: 25, waits: 0, wait_time_ms: 0 },
      ],
      limitation: null,
      caption: 'Gets, waits, and wait time are since instance startup.',
    },
    blocking: {
      locks: [{
        sid: 12, serial_no: 3, username: 'APPUSER', pid: '1234',
        lock_type: 'TX', mode_wanted: 'Exclusive', object_name: '',
      }],
      limitation: null,
      empty: 'No data',
      caption: 'These are current sessions waiting for a lock. At most 20 are shown.',
    },
    parsing: {
      counters: [
        { name: 'parse count (total)', value: second ? 1400 : 1000 },
        { name: 'parse count (hard)', value: second ? 120 : 100 },
        { name: 'parse count (failures)', value: 2 },
      ],
      times: [
        { name: 'parse time elapsed', value: second ? 7000000 : 5000000 },
        { name: 'hard parse elapsed time', value: 1000000 },
        { name: 'failed parse elapsed time', value: 0 },
      ],
      library: [{ namespace: 'SQL AREA', gets: 1000, gethits: 900, reloads: 1, invalidations: 0 }],
      limitations: [],
      caption: 'Counts and times are since instance startup.',
    },
    buffer_busy: {
      events: [{
        event: 'buffer busy waits',
        wait_class: 'Concurrency',
        total_waits: second ? 80 : 40,
        time_waited_micro: second ? 1600000 : 800000,
      }],
      classes: [
        { name: 'data block', count: 100, time_cs: 50 },
        { name: 'undo header', count: 5, time_cs: 2 },
      ],
      users: [{ username: 'APPUSER', sid: 12, total_waits: 10, waited_ms: 0 }],
      tablespaces: [
        { name: 'SYSTEM', sessions: 1 },
        { name: 'USERS', sessions: 2 },
      ],
      limitations: [],
      caption: 'Event totals and block class are since instance startup. Tablespace is sessions waiting now.',
    },
    io: {
      counters: [
        { name: 'db block changes', value: second ? 1400 : 1000 },
        { name: 'physical reads', value: second ? 500 : 400 },
        { name: 'consistent gets', value: second ? 9000 : 8000 },
      ],
      files: {
        reads: second ? 140 : 100,
        writes: second ? 30 : 20,
        read_time_cs: second ? 80 : 40,
        write_time_cs: second ? 20 : 10,
        limitation: null,
      },
      limitation: null,
    },
    collected_at: '2026-11-13T10:00:00+00:00',
  }
}

function installFetch() {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url.includes('/api/v1/auth/session')) {
      return Promise.resolve(json({ required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local' }))
    }
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.includes('/api/v1/databases?')) {
      return Promise.resolve(json({
        items: [database('target-1', 'FINANCE-PRD', 'finance-prd'), database('target-2', 'WAREHOUSE-DEV', 'warehouse-dev')],
        total: 2, page: 1, page_size: 100, environments: ['production'],
      }))
    }
    if (url.includes('/graph/waits')) {
      const second = calls.filter((item) => item.includes('/graph/waits')).length > 1
      return Promise.resolve(json({
        events: [{
          event: 'db file sequential read',
          wait_class: 'User I/O',
          total_waits: second ? 1600 : 1000,
          time_waited_micro: second ? 5000000 : 2000000,
        }],
        cpu_micro: second ? 11000000 : 8000000,
        collected_at: '2026-11-13T10:00:00+00:00',
        limitations: [],
        caption: 'Totals are since instance startup. Idle waits are not included.',
      }))
    }
    if (url.includes('/wait/activity')) {
      const second = calls.filter((item) => item.includes('/wait/activity')).length > 1
      return Promise.resolve(json(activity(second)))
    }
    const match = url.match(/\/api\/v1\/databases\/([^/?]+)$/)
    if (match) return Promise.resolve(json(database(decodeURIComponent(match[1]), 'FINANCE-PRD', 'finance-prd')))
    return Promise.resolve(json({ detail: 'not found' }, 404))
  }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.location.hash = ''
})

describe('WaitDB page', () => {
  it('asks for a database and then shows waits, locks, blocking, parsing, and buffer busy waits', async () => {
    window.location.hash = '#/wait'
    let now = 1_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    installFetch()
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'WaitDB' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'WaitDB' })).not.toBeInTheDocument()
    const chooser = await screen.findByRole('region', { name: 'Registered databases' })
    await user.click(within(chooser).getByRole('button', { name: /FINANCE-PRD/ }))

    expect(await screen.findByRole('heading', { name: 'Block activity' })).toBeInTheDocument()
    expect(screen.getAllByText('The chart needs a second sample on this page.').length).toBeGreaterThan(0)
    expect(screen.getByText('10.00%')).toBeInTheDocument()
    expect(screen.getByText('90.00%')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View waits' })).not.toBeInTheDocument()

    now = 3_000
    await user.click(screen.getByRole('button', { name: 'Refresh data' }))
    await waitFor(() => expect(screen.getAllByRole('img').length).toBeGreaterThan(0))

    await user.click(screen.getByRole('tab', { name: 'Event waits' }))
    expect(await screen.findByText('db file sequential read')).toBeInTheDocument()
    expect(screen.getByText('300')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Blocking locks' }))
    expect(screen.getByText('APPUSER')).toBeInTheDocument()
    expect(screen.getByText('Exclusive')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Lock activity' }))
    expect(screen.getAllByText('Process Startup')).toHaveLength(2)

    await user.click(screen.getByRole('tab', { name: 'Buffer busy waits' }))
    expect(screen.getByText('data block')).toBeInTheDocument()
    expect(screen.getByText('USERS')).toBeInTheDocument()
    expect(screen.getByText('buffer busy waits')).toBeInTheDocument()
  })
})
