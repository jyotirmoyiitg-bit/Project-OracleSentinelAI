import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { getGraphHistory } from './api'
import { GraphPage, QUICK_SAMPLE_MS } from './GraphPage'
import { WaitPage } from './WaitPage'

const noop = () => {}

function database(id: string, name: string) {
  return {
    id, display_name: name, tns_alias: name.toLowerCase(), enabled: true, environment: 'production', engine: 'Oracle 19c',
    owner: 'DBA team', poll_profile: {}, capability_snapshot: {}, last_connection_at: null,
    last_health_status: 'unknown', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
    last_seen: null, monitoring_status: 'unknown', critical_incident_count: null, trend_snapshots: [],
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function stored(ageSeconds: number, scale: number) {
  return {
    taken_at: '2026-11-15T10:00:00',
    age_seconds: ageSeconds,
    counters: [
      { name: 'physical reads', value: 100 * scale },
      { name: 'redo entries', value: 40 * scale },
      { name: 'parse count (total)', value: 20 * scale },
      { name: 'execute count', value: 1000 * scale },
    ],
    events: [
      { event: 'db file sequential read', wait_class: 'User I/O', total_waits: 1000 * scale, time_waited_micro: 2000000 * scale },
      { event: 'log file sync', wait_class: 'Commit', total_waits: 100 * scale, time_waited_micro: 500000 * scale },
    ],
    cpu_micro: 8000000 * scale,
    io: [
      { name: 'db block changes', value: 1000 * scale },
      { name: 'physical reads', value: 400 * scale },
      { name: 'consistent gets', value: 8000 * scale },
    ],
    files: { reads: 100 * scale, writes: 20 * scale, read_time_cs: 40 * scale, write_time_cs: 10 * scale },
  }
}

function graphActivity(executes: number) {
  return {
    service: [{ label: 'Uptime', value: '2d 11h 0m' }],
    host: { cpu_percent: 12, cpu_caption: 'Since instance startup.', run_queue: '0.16', free_memory: '4 GB', limitation: null },
    server_processes: [{ name: 'Total', count: 101, limitation: null }],
    counters: [
      { name: 'physical reads', value: 700 },
      { name: 'redo entries', value: 280 },
      { name: 'parse count (total)', value: 140 },
      { name: 'execute count', value: executes },
    ],
    collected_at: '2026-11-15T10:05:00+00:00',
    limitations: [],
  }
}

function waitActivity(scale: number) {
  return {
    locking: { locks: [{ name: 'File Object', gets: 25 * scale, waits: 0, wait_time_ms: 0 }], limitation: null, caption: '' },
    blocking: { locks: [], limitation: null, empty: 'No data', caption: '' },
    parsing: {
      counters: [{ name: 'parse count (total)', value: 1000 * scale }],
      times: [{ name: 'parse time elapsed', value: 5000000 * scale }],
      library: [], limitations: [], caption: '',
    },
    buffer_busy: { events: [], classes: [], users: [], tablespaces: [], limitations: [], caption: '' },
    io: {
      counters: [
        { name: 'db block changes', value: 1000 * scale },
        { name: 'physical reads', value: 400 * scale },
        { name: 'consistent gets', value: 8000 * scale },
      ],
      files: { reads: 100 * scale, writes: 20 * scale, read_time_cs: 40 * scale, write_time_cs: 10 * scale, limitation: null },
      limitation: null,
    },
    collected_at: '2026-11-15T10:05:00+00:00',
  }
}

function installFetch(history: unknown, historyStatus = 200) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    const seen = (part: string) => calls.filter((item) => item.includes(part)).length
    if (url.includes('/graph/history')) return Promise.resolve(json(history, historyStatus))
    if (url.includes('/graph/activity')) return Promise.resolve(json(graphActivity(4000)))
    if (url.includes('/graph/waits')) {
      const scale = seen('/graph/waits') > 1 ? 5 : 4
      return Promise.resolve(json({
        events: stored(0, scale).events, cpu_micro: 8000000 * scale,
        collected_at: '2026-11-15T10:05:00+00:00', limitations: [], caption: 'Totals are since instance startup.',
      }))
    }
    if (url.includes('/wait/activity')) return Promise.resolve(json(waitActivity(seen('/wait/activity') > 1 ? 5 : 4)))
    if (url.includes('/graph/memory') || url.includes('/graph/storage')) return Promise.resolve(json({ detail: 'Not in this test' }, 500))
    if (url.endsWith('/api/v1/graph')) return Promise.resolve(json({ panels: [], detail: true }))
    if (url.includes('/api/v1/databases?')) {
      return Promise.resolve(json({ items: [database('target-1', 'FINANCE-PRD')], total: 1, page: 1, page_size: 100, environments: ['production'] }))
    }
    if (url.includes('/api/v1/databases/target-1')) return Promise.resolve(json(database('target-1', 'FINANCE-PRD')))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  }))
  return calls
}

function count(calls: string[], part: string): number {
  return calls.filter((item) => item.includes(part)).length
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Phase 23 stored history', () => {
  it('reads history without failing the page when the answer is missing or malformed', async () => {
    installFetch({ unexpected: true })
    expect((await getGraphHistory('target-1')).samples).toEqual([])
    installFetch({ detail: 'Authorization failed.' }, 403)
    expect(await getGraphHistory('target-1')).toEqual({ samples: [], enabled: false, interval_seconds: 0, caption: '' })
    installFetch({ samples: [stored(300, 1), { taken_at: 'x' }], enabled: true, interval_seconds: 300, caption: 'Stored.' })
    const history = await getGraphHistory('target-1')
    expect(history.samples).toHaveLength(1)
    expect(history.caption).toBe('Stored.')
  })

  it('fills the GraphDB meters from the stored sample, then replaces them with a live rate', async () => {
    const calls = installFetch({ samples: [stored(600, 0.5), stored(300, 1)], enabled: true, interval_seconds: 300, caption: 'Stored.' })
    render(<GraphPage databaseId="target-1" onChoose={noop} onBack={noop} onAskCopilot={noop} onOpenDatabase={noop} onRefreshStatus={noop} />)

    const meters = await screen.findByRole('region', { name: 'Rate meters' })
    // 4000 executions now, 1000 in the sample stored 300 seconds ago: 10 a second.
    await waitFor(() => expect(within(meters).getByText('10 /s')).toBeInTheDocument())
    expect(within(meters).getByText(/^Average since the stored sample at .+\. A live rate follows\.$/)).toBeInTheDocument()
    expect(within(meters).queryByText('Rate needs a second sample.')).not.toBeInTheDocument()
    expect(count(calls, '/graph/activity')).toBe(1)
    expect(count(calls, '/graph/history')).toBe(1)

    await waitFor(
      () => expect(within(meters).getByText('Since the previous refresh on this page.')).toBeInTheDocument(),
      { timeout: QUICK_SAMPLE_MS + 3000 },
    )
    expect(count(calls, '/graph/activity')).toBe(2)
    expect(count(calls, '/graph/memory')).toBe(1)
    expect(count(calls, '/graph/storage')).toBe(1)
    expect(count(calls, '/graph/history')).toBe(1)
    expect(within(meters).getAllByText('0 /s')).toHaveLength(4)
  }, 15000)

  it('takes a quick second GraphDB sample when nothing is stored', async () => {
    const calls = installFetch({ samples: [], enabled: false, interval_seconds: 300, caption: '' })
    render(<GraphPage databaseId="target-1" onChoose={noop} onBack={noop} onAskCopilot={noop} onOpenDatabase={noop} onRefreshStatus={noop} />)

    const meters = await screen.findByRole('region', { name: 'Rate meters' })
    await waitFor(() => expect(count(calls, '/graph/activity')).toBe(1))
    expect(within(meters).getAllByText('Rate needs a second sample.')).toHaveLength(4)
    await waitFor(() => expect(within(meters).getAllByText('0 /s')).toHaveLength(4), { timeout: QUICK_SAMPLE_MS + 3000 })
    expect(count(calls, '/graph/activity')).toBe(2)
  }, 15000)

  it('opens the WaitDB charts from stored samples and then samples again', async () => {
    const caption = 'Earlier points are stored samples taken every 5 minutes. Later points are live samples on this page.'
    const calls = installFetch({
      samples: [stored(900, 1), stored(600, 2), stored(300, 3)], enabled: true, interval_seconds: 300, caption,
    })
    render(<WaitPage databaseId="target-1" onChoose={noop} onBack={noop} onAskCopilot={noop} onOpenDatabase={noop} onRefreshStatus={noop} />)

    expect(await screen.findByText(caption)).toBeInTheDocument()
    for (const title of ['Block activity, blocks/s', 'Event waits, ms/s', 'Top waits, ms/s', 'Data file requests, requests/s', 'Data file latency, ms per request']) {
      expect(screen.getByRole('img', { name: title })).toBeInTheDocument()
    }
    expect(screen.queryByText('The chart needs a second sample on this page.')).not.toBeInTheDocument()
    expect(count(calls, '/graph/waits')).toBe(1)
    expect(count(calls, '/graph/history')).toBe(1)
    // Parsing is not stored, so it still waits for the second live sample.
    expect(screen.getAllByText('Needs a second sample.').length).toBeGreaterThan(0)

    await waitFor(() => expect(count(calls, '/graph/waits')).toBe(2), { timeout: QUICK_SAMPLE_MS + 3000 })
    await waitFor(() => expect(screen.queryByText('Needs a second sample.')).not.toBeInTheDocument())
    expect(count(calls, '/wait/activity')).toBe(2)
    expect(count(calls, '/graph/history')).toBe(1)
  }, 15000)

  it('keeps the WaitDB live caption when nothing is stored, and still samples twice', async () => {
    const calls = installFetch({ detail: 'Not found' }, 404)
    render(<WaitPage databaseId="target-1" onChoose={noop} onBack={noop} onAskCopilot={noop} onOpenDatabase={noop} onRefreshStatus={noop} />)

    expect(await screen.findByText('Rates and charts are since the previous sample on this page. This is not a stored history.')).toBeInTheDocument()
    await waitFor(() => expect(count(calls, '/graph/waits')).toBe(1))
    expect(screen.getAllByText('The chart needs a second sample on this page.').length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByRole('img', { name: 'Event waits, ms/s' })).toBeInTheDocument(), { timeout: QUICK_SAMPLE_MS + 3000 })
    expect(count(calls, '/graph/waits')).toBe(2)
  }, 15000)
})
