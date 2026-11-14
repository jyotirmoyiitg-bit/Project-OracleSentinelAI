import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

const panels = [
  { id: 'memory', title: 'Memory structures', message: 'Not collected in this phase.' },
  { id: 'physical', title: 'Physical structures', message: 'Not collected in this phase.' },
  { id: 'logical', title: 'Logical structures', message: 'Not collected in this phase.' },
  { id: 'management', title: 'Management', message: 'Not collected in this phase.' },
]
const SUMMARY_NOTE = 'File, control-file, redo-member, and segment listings are shown to an administrator.'

const probe = { storageFailOn: 0 }

function database(id: string, name: string, alias: string, enabled = true) {
  return {
    id, display_name: name, tns_alias: alias, enabled, environment: 'production', engine: 'Oracle 19c',
    owner: 'DBA team', poll_profile: {}, capability_snapshot: {}, last_connection_at: null,
    last_health_status: 'unknown', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
    last_seen: null, monitoring_status: enabled ? 'unknown' : 'disabled', critical_incident_count: null,
    trend_snapshots: [],
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function storageBody(url: string, detail: boolean) {
  const redo = url.includes('target-2') ? 'D:\\ORACLE\\REDO02.LOG' : 'D:\\ORACLE\\REDO01.LOG'
  return {
    data_files: {
      online_files: 2,
      online_tablespaces: 2,
      used_bytes: 9437184,
      used_display: '9 MB',
      used_percent: 90,
      files: detail
        ? [
            { name: 'D:\\ORACLE\\SYSTEM01.DBF', tablespace: 'SYSTEM', bytes: 9437184, display: '9 MB', status: 'SYSTEM', online: true },
            { name: 'D:\\ORACLE\\USERS01.DBF', tablespace: 'USERS', bytes: 1048576, display: '1 MB', status: 'OFFLINE', online: false },
          ]
        : [],
      flash_cache: 'Not collected.',
      limitation: null,
    },
    temp_files: {
      files: detail ? [{ name: 'D:\\ORACLE\\TEMP01.DBF', tablespace: 'TEMP', bytes: 2097152, display: '2 MB', status: 'ONLINE', online: true }] : [],
      limitation: null,
    },
    control_files: {
      files: detail ? [{ name: 'D:\\ORACLE\\CONTROL01.CTL', status: 'The status was not returned.' }] : [],
      count: 1,
      limitation: null,
    },
    redo_logs: {
      groups: 1,
      total_bytes: 52428800,
      total_display: '50 MB',
      active_percent: 100,
      members: detail ? [{ group: 1, thread: 1, path: redo, bytes: 52428800, display: '50 MB', status: 'CURRENT', current: true }] : [],
      limitation: null,
    },
    collected_at: '2026-11-13T10:00:00+00:00',
    limitations: [],
    segments: {
      rows: detail ? [{ owner: 'APP', name: 'ORDERS', segment_type: 'TABLE', tablespace: 'USERS', bytes: 1048576, display: '1 MB' }] : [],
      limitation: null,
    },
    recovery_area: {
      present: true,
      name: 'USE_DB_RECOVERY_FILE_DEST',
      used_percent: 20,
      used_display: '2 GB',
      limit_display: '10 GB',
      usage: [{ file_type: 'ARCHIVELOG', percent_space_used: 15, number_of_files: 4 }],
      limitation: null,
    },
    archive: {
      log_mode: 'ARCHIVELOG',
      destinations: [{ destination: 'USE_DB_RECOVERY_FILE_DEST', status: 'VALID' }],
      latest: 'sequence 42 2026-11-13 08:00:00',
      free_minutes: 'Not collected.',
      time_to_failure: 'Not collected.',
      limitation: null,
    },
    backup: {
      input_type: 'DB FULL',
      status: 'FAILED',
      started: '2026-11-12 02:00:00',
      ended: '',
      failed: true,
      limitation: null,
    },
    detail,
    detail_note: detail ? null : SUMMARY_NOTE,
  }
}

function installFetch(role: string, detailFor: (id: string) => Response) {
  const calls: string[] = []
  const detail = role === 'admin' || role === 'platform_admin'
  let activityCalls = 0
  let storageCalls = 0
  const activityValues: Array<Array<number | null>> = [
    [100, 40, 10, 80],
    [120, 40, null, 70],
  ]
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url.includes('/api/v1/auth/session')) {
      return Promise.resolve(json({ required: true, authenticated: true, principal: 'sentinel', role, method: 'local' }))
    }
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.includes('/api/v1/graph')) return Promise.resolve(json({ panels, detail }))
    if (url.includes('/api/v1/databases?')) {
      return Promise.resolve(json({
        items: [database('target-1', 'FINANCE-PRD', 'finance-prd'), database('target-2', 'WAREHOUSE-DEV', 'warehouse-dev')],
        total: 2, page: 1, page_size: 100, environments: ['production'],
      }))
    }
    if (url.includes('/graph/memory')) {
      const processName = url.includes('target-2') ? 'oracle (SMON)' : 'oracle (PMON)'
      return Promise.resolve(json({
        sga: {
          items: [
            { name: 'Buffer Cache Size', bytes: 6442450944, display: '6 GB' },
            { name: 'Shared Pool Size', bytes: 2147483648, display: '2 GB' },
          ],
          caption: 'sga_target 8 GB. sga_max_size 8 GB.',
          limitation: null,
        },
        pga: {
          items: [
            { name: 'Allocated', bytes: 1288490188, display: '1.2 GB' },
            { name: 'Used', bytes: 419430400, display: '400 MB' },
            { name: 'Target', bytes: 2147483648, display: '2 GB' },
          ],
          high: false,
          limitation: null,
        },
        background_processes: url.includes('target-2')
          ? [{ program: processName, status: 'ACTIVE', state: 'WAITING' }]
          : [
              { program: processName, status: 'ACTIVE', state: 'WAITING' },
              { program: 'oracle (DBW0)', status: 'ACTIVE', state: 'WAITING' },
            ],
        hit_ratio: { percent: 90, caption: 'Since instance startup.', limitation: null },
        shared_pool_used: { percent: 50, caption: 'Shared pool used.', limitation: null },
        in_memory: { display: 'The in-memory area is not in use.', in_use: false, limitation: null },
        memory_management: { lines: ['Automatic Shared Memory Management is on.', 'Automatic Memory Management is off.'], limitation: null },
        data_guard: { stats: [], limitation: 'Not a Data Guard database.' },
        result_cache: 'Not collected.',
        collected_at: '2026-11-13T10:00:00+00:00',
        limitations: [],
        snapshot: 'Current instance snapshot.',
      }))
    }
    if (url.includes('/graph/storage')) {
      storageCalls += 1
      if (probe.storageFailOn === storageCalls) {
        return Promise.resolve(json({ detail: 'The database could not be read just now.' }, 500))
      }
      return Promise.resolve(json(storageBody(url, detail)))
    }
    if (url.includes('/graph/activity')) {
      const values = activityValues[Math.min(activityCalls, activityValues.length - 1)]
      activityCalls += 1
      return Promise.resolve(json({
        service: [
          { label: 'Uptime', value: '2d 11h 0m' },
          { label: 'Container database', value: 'ORCL12C' },
          { label: 'Pluggable database', value: 'PDBORCL' },
          { label: 'Response', value: 'Not collected.' },
          { label: 'Total users', value: '3' },
          { label: 'Active users', value: '1' },
          { label: 'Average active', value: 'Not collected.' },
        ],
        host: { cpu_percent: 12, cpu_caption: 'Since instance startup.', run_queue: '0.16', free_memory: '4 GB', limitation: null },
        server_processes: [
          { name: 'Dedicated', count: 3, limitation: null },
          { name: 'Shared', count: 1, limitation: null },
          { name: 'Dispatchers', count: 1, limitation: null },
          { name: 'Parallel query', count: 96, limitation: null },
          { name: 'Job queue', count: 0, limitation: null },
          { name: 'Total', count: 101, limitation: null },
        ],
        counters: ['physical reads', 'redo entries', 'parse count (total)', 'execute count'].map((name, index) => ({ name, value: values[index] })),
        collected_at: '2026-11-13T10:00:00+00:00',
        limitations: [],
      }))
    }
    const match = url.match(/\/api\/v1\/databases\/([^/?]+)$/)
    if (match) return Promise.resolve(detailFor(decodeURIComponent(match[1])))
    return Promise.resolve(json({ detail: 'not found' }, 404))
  }))
  return calls
}

const pathNamed = (path: string) => (_content: string, element: Element | null) =>
  element?.classList.contains('storage-path') === true && element.textContent === path

function block(id: string): HTMLElement {
  const heading = document.getElementById(id)
  const container = heading?.closest('.storage-block')
  if (!(container instanceof HTMLElement)) throw new Error(`No storage block for ${id}`)
  return container
}

describe('GraphDB page', () => {
  afterEach(() => {
    cleanup()
    probe.storageFailOn = 0
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.location.hash = ''
  })

  it('shows the performance-home columns for the selected database', async () => {
    window.location.hash = '#/graph/databases/target-1'
    installFetch('admin', (id) => json(database(id, id === 'target-2' ? 'WAREHOUSE-DEV' : 'FINANCE-PRD', id === 'target-2' ? 'warehouse-dev' : 'finance-prd')))
    const user = userEvent.setup()
    render(<App />)

    const title = await screen.findByRole('heading', { name: 'FINANCE-PRD' })
    expect(title.parentElement).toHaveTextContent('finance-prd')
    const active = document.querySelector('.graph-active') as HTMLElement
    expect(active).toHaveTextContent('Active database')
    expect(active).toHaveTextContent('FINANCE-PRD')
    expect(active).toHaveTextContent('production')
    expect(active.querySelector('.health-badge')).toBeTruthy()
    expect(screen.getByRole('option', { name: '● FINANCE-PRD (active)' })).toBeInTheDocument()
    for (const name of ['Service', 'Host', 'Server processes', 'SGA', 'Background processes', 'Disk storage']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }
    expect(await screen.findByText('Buffer Cache Size')).toBeInTheDocument()
    expect(screen.getByText('6 GB')).toBeInTheDocument()
    expect(screen.getByText('sga_target 8 GB. sga_max_size 8 GB.')).toBeInTheDocument()
    expect(screen.getByText('oracle (PMON)')).toBeInTheDocument()
    expect(screen.getAllByText('Current instance snapshot.').length).toBeGreaterThan(0)
    expect((await screen.findAllByText('Since instance startup.')).length).toBeGreaterThan(0)

    const service = screen.getByRole('heading', { name: 'Service' }).closest('section')
    expect(service).toHaveTextContent('Uptime')
    expect(service).toHaveTextContent('2d 11h 0m')
    expect(service).toHaveTextContent('Response')
    expect(service).toHaveTextContent('Not collected.')
    expect(service).toHaveTextContent('Average active')

    const host = screen.getByRole('heading', { name: 'Host' }).closest('section')
    expect(host).toHaveTextContent('Total CPU usage')
    expect(host).toHaveTextContent('12%')
    expect(host).toHaveTextContent('Run queue')
    expect(host).toHaveTextContent('0.16')
    expect(host).toHaveTextContent('4 GB')

    const processes = screen.getByRole('heading', { name: 'Server processes' }).closest('section')
    expect(processes).toHaveTextContent('PGA')
    expect(processes).toHaveTextContent('2 GB')
    expect(processes?.querySelectorAll('.graph-oval')).toHaveLength(5)
    expect(processes?.querySelector('.graph-total')).toHaveTextContent('101')
    expect(screen.queryByText('EM Agent')).not.toBeInTheDocument()

    const background = screen.getByRole('heading', { name: 'Background processes' }).closest('section')
    expect(within(background as HTMLElement).getByText('DBW0')).toHaveClass('graph-pill')
    expect(within(background as HTMLElement).getByText('LGWR')).toHaveClass('graph-pill-empty')
    expect(background).toHaveTextContent('Database writer')
    expect(background).toHaveTextContent('oracle (DBW0)')
    expect(background).toHaveTextContent('Not a Data Guard database.')

    const sga = screen.getByRole('heading', { name: 'SGA' }).closest('section')
    expect(sga).toHaveTextContent('Buffer cache hit ratio')
    expect(sga).toHaveTextContent('90%')
    expect(sga).toHaveTextContent('Shared pool used')
    expect(sga).toHaveTextContent('Result cache Not collected.')
    expect(sga).toHaveTextContent('Automatic Shared Memory Management is on.')

    const storage = screen.getByRole('heading', { name: 'Disk storage' }).closest('section')
    expect(block('graph-database-files')).toHaveTextContent('Online files')
    expect(block('graph-database-files')).toHaveTextContent('9 MB 90%')
    expect(block('graph-database-files')).toHaveTextContent('Flash cache')
    expect(block('graph-database-files').querySelector('.storage-cylinder-fill')).toBeTruthy()
    const redoCard = block('graph-redo')
    expect(redoCard).toHaveTextContent('Groups')
    expect(redoCard.querySelector('.storage-cylinder-cap-top')).toBeTruthy()
    expect(redoCard.querySelector('.storage-cylinder-cap-bottom')).toBeTruthy()
    expect(redoCard.querySelector('.storage-cylinder-fill')).toBeTruthy()
    expect(redoCard.querySelector('svg.storage-cylinder')).toHaveAttribute('aria-label', '100% used')
    expect(redoCard.querySelector('.storage-cylinder-cap-full')).toBeTruthy()
    expect(block('graph-recovery').querySelector('svg.storage-cylinder')).toHaveAttribute('aria-label', '20% used')
    expect(block('graph-recovery').querySelector('.storage-cylinder-surface')).toBeTruthy()
    expect(block('graph-recovery').querySelectorAll('.storage-cylinder-band')).toHaveLength(3)
    const archiveCylinder = block('graph-archive').querySelector('svg.storage-cylinder')
    expect(archiveCylinder).toHaveClass('storage-cylinder-empty')
    expect(archiveCylinder?.querySelector('.storage-cylinder-fill')).toBeNull()
    expect(block('graph-recovery')).toHaveTextContent('10 GB')
    expect(block('graph-archive')).toHaveTextContent('ARCHIVELOG')
    expect(block('graph-archive')).toHaveTextContent('Time to failure')
    const backup = block('graph-backup')
    expect(backup).toHaveTextContent('FAILED')
    expect(backup.querySelector('.health-critical')).toBeTruthy()

    expect(storage).toHaveTextContent('These are the 20 largest segments.')
    expect(screen.getByText('APP ORDERS')).toBeInTheDocument()
    expect(screen.getByText('Offline')).toBeInTheDocument()
    expect(screen.getByText('Current group')).toBeInTheDocument()
    expect(screen.getByText(pathNamed('D:\\ORACLE\\REDO01.LOG'))).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /REDO01/ })).not.toBeInTheDocument()
    expect(screen.queryByText(SUMMARY_NOTE)).not.toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Ask copilot' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /execute/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /backup/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^run/i })).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Graph database'), 'target-2')
    const next = await screen.findByRole('heading', { name: 'WAREHOUSE-DEV' })
    expect(screen.queryByRole('heading', { name: 'FINANCE-PRD' })).not.toBeInTheDocument()
    expect(next.parentElement).toHaveTextContent('warehouse-dev')
    expect(await screen.findByText('oracle (SMON)')).toBeInTheDocument()
    expect(screen.queryByText('oracle (PMON)')).not.toBeInTheDocument()
    expect(screen.getByText(pathNamed('D:\\ORACLE\\REDO02.LOG'))).toBeInTheDocument()
    expect(screen.queryByText(pathNamed('D:\\ORACLE\\REDO01.LOG'))).not.toBeInTheDocument()
  })

  it('lets an administrator open a database from the chooser', async () => {
    window.location.hash = '#/graph'
    installFetch('admin', (id) => json(database(id, 'FINANCE-PRD', 'finance-prd')))
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('button', { name: 'GraphDB' })).toBeInTheDocument()
    const chooser = await screen.findByRole('region', { name: 'Registered databases' })
    await user.click(within(chooser).getByRole('button', { name: /FINANCE-PRD/ }))
    expect(await screen.findByRole('heading', { name: 'SGA' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'FINANCE-PRD' })).toBeInTheDocument()
  })

  it('shows an operator the picture with the disk-storage summary only', async () => {
    window.location.hash = '#/graph/databases/target-1'
    installFetch('operator', () => json(database('target-1', 'FINANCE-PRD', 'finance-prd')))
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('button', { name: 'GraphDB' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'FINANCE-PRD' })).toBeInTheDocument()
    expect(await screen.findByText('Buffer Cache Size')).toBeInTheDocument()
    expect(await screen.findByText(SUMMARY_NOTE)).toBeInTheDocument()
    expect(block('graph-database-files')).toHaveTextContent('Online files')
    expect(block('graph-database-files')).toHaveTextContent('9 MB 90%')
    expect(block('graph-redo')).toHaveTextContent('Groups')
    expect(block('graph-redo').querySelector('.storage-cylinder-fill')).toBeTruthy()
    expect(block('graph-recovery')).toHaveTextContent('10 GB')
    expect(block('graph-backup')).toHaveTextContent('FAILED')
    expect(screen.queryByText(pathNamed('D:\\ORACLE\\REDO01.LOG'))).not.toBeInTheDocument()
    expect(screen.queryByText(/SYSTEM01/)).not.toBeInTheDocument()
    expect(screen.queryByText('APP ORDERS')).not.toBeInTheDocument()
    expect(screen.queryByText('These are the 20 largest segments.')).not.toBeInTheDocument()
    expect(screen.queryByText(/Redo members/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Ask copilot' }))
    expect(window.location.hash).toBe('#/copilot/databases/target-1')
  })

  it('shows the not-found state for a disabled database', async () => {
    window.location.hash = '#/graph/databases/target-9'
    installFetch('admin', () => json(database('target-9', 'RETIRED-DB', 'retired', false)))
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Database target not found')
    expect(screen.queryByRole('heading', { name: 'RETIRED-DB' })).not.toBeInTheDocument()
    expect(screen.queryByText('Not collected in this phase.')).not.toBeInTheDocument()
  })

  it('shows a rate only after a second sample on this page', async () => {
    window.location.hash = '#/graph/databases/target-1'
    let now = 1_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    const calls = installFetch('admin', (id) => json(database(id, 'FINANCE-PRD', 'finance-prd')))
    const user = userEvent.setup()
    render(<App />)

    const strip = () => document.querySelector('.rate-strip') as HTMLElement
    expect(await screen.findByRole('heading', { name: 'SGA' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh data' })).not.toHaveClass('is-refreshing'))
    expect(within(strip()).getAllByText('Rate needs a second sample.')).toHaveLength(4)
    expect(within(strip()).getByText('blks/s')).toBeInTheDocument()

    now = 3_000
    await user.click(screen.getByRole('button', { name: 'Refresh data' }))
    await waitFor(() => expect(within(strip()).getByText('10 /s')).toBeInTheDocument())
    expect(within(strip()).getByText('0 /s')).toBeInTheDocument()
    expect(within(strip()).getAllByText('Not collected.')).toHaveLength(2)
    expect(within(strip()).getByText('Since the previous refresh on this page.')).toBeInTheDocument()
    expect(strip().querySelectorAll('.rate-fill')).toHaveLength(2)
    expect(calls.filter((url) => url.includes('/graph/memory'))).toHaveLength(2)
    expect(calls.filter((url) => url.includes('/graph/storage'))).toHaveLength(2)
    expect(calls.filter((url) => url.includes('/graph/activity'))).toHaveLength(2)
  })

  it('keeps the memory card when a refreshed storage reading fails', async () => {
    window.location.hash = '#/graph/databases/target-1'
    probe.storageFailOn = 2
    installFetch('admin', (id) => json(database(id, 'FINANCE-PRD', 'finance-prd')))
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText('Buffer Cache Size')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh data' })).not.toHaveClass('is-refreshing'))
    await user.click(screen.getByRole('button', { name: 'Refresh data' }))
    expect(await screen.findByText('The database could not be read just now.')).toBeInTheDocument()
    expect(screen.getByText('Buffer Cache Size')).toBeInTheDocument()
  })
})
