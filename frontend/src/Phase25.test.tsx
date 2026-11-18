import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { AlertPage } from './AlertPage'
import { megabytes } from './components/DatabaseView'
import { MemoryPage } from './MemoryPage'
import { ParameterPage } from './ParameterPage'
import { StoragePage } from './StoragePage'

const noop = () => {}
const MB = 1024 * 1024

function database(id: string, name: string, enabled = true) {
  return {
    id, display_name: name, tns_alias: name.toLowerCase(), enabled, environment: 'production', engine: 'Oracle 19c',
    owner: 'DBA team', poll_profile: {}, capability_snapshot: {}, last_connection_at: null,
    last_health_status: 'healthy', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
    last_seen: null, monitoring_status: 'healthy', critical_incident_count: 0, trend_snapshots: [],
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const SGA = {
  pools: [
    { name: 'buffer cache', bytes: 912 * MB, display: '912 MB', used_bytes: 912 * MB, free_bytes: 0, used_percent: 100 },
    { name: 'shared pool', bytes: 150 * MB, display: '150 MB', used_bytes: 103 * MB, free_bytes: 47 * MB, used_percent: 68.7 },
    { name: 'large pool', bytes: 16 * MB, display: '16 MB', used_bytes: 0.5 * MB, free_bytes: 15.5 * MB, used_percent: 2.9 },
  ],
  components: [
    { pool: 'buffer cache', name: 'buffer_cache', bytes: 912 * MB, display: '912 MB' },
    { pool: 'shared pool', name: 'free memory', bytes: 47 * MB, display: '47 MB' },
    { pool: 'shared pool', name: 'SQLA', bytes: 39 * MB, display: '39 MB' },
  ],
  shared_pool: [
    { pool: 'shared pool', name: 'free memory', bytes: 47 * MB, display: '47 MB' },
    { pool: 'shared pool', name: 'SQLA', bytes: 39 * MB, display: '39 MB' },
  ],
  total_bytes: 1078 * MB, total_display: '1.1 GB', collected_at: '2026-11-15T10:00:00', limitation: null,
  caption: 'Current allocation by pool. The table lists the 60 largest components.',
}

const MEMORY = {
  sga: { items: [], caption: '', limitation: null },
  pga: { items: [{ name: 'Target', bytes: 2048 * MB, display: '2 GB' }, { name: 'Allocated', bytes: 1024 * MB, display: '1 GB' }], high: false, limitation: null },
  background_processes: [], hit_ratio: { percent: 98.4, caption: 'Since instance startup.', limitation: null },
  shared_pool_used: { percent: 68.7, caption: '', limitation: null },
  in_memory: { display: '', in_use: false, limitation: null },
  memory_management: { lines: ['Automatic Shared Memory Management is on.'], limitation: null },
  data_guard: { stats: [], limitation: null }, result_cache: '', collected_at: '2026-11-15T10:00:00', limitations: [], snapshot: '',
}

const TABLESPACES = {
  tablespaces: [
    { name: 'SYSAUX', contents: 'PERMANENT', status: 'ONLINE', total_bytes: 613.97 * MB, free_bytes: 37.5 * MB, used_bytes: 576.47 * MB, used_percent: 93.9, fragments: 1 },
    { name: 'TEMP', contents: 'TEMPORARY', status: 'ONLINE', total_bytes: 19 * MB, free_bytes: 19 * MB, used_bytes: 0, used_percent: 0, fragments: null },
    { name: 'USERS', contents: 'PERMANENT', status: 'OFFLINE', total_bytes: 130.97 * MB, free_bytes: 56.25 * MB, used_bytes: 74.72 * MB, used_percent: 57.1, fragments: 2 },
  ],
  total_bytes: 763.94 * MB, used_bytes: 651.19 * MB, total_display: '763.9 MB', used_display: '651.2 MB',
  collected_at: '2026-11-15T10:00:00', limitation: null, caption: 'Allocated size and free space of every tablespace.',
}

function storage(detail: boolean) {
  return {
    data_files: { online_files: 5, online_tablespaces: 4, used_bytes: null, used_display: '', used_percent: null, files: [], flash_cache: '', limitation: null },
    temp_files: { files: [], limitation: null },
    control_files: { files: [], count: 2, limitation: null },
    redo_logs: {
      groups: 3, total_bytes: 600 * MB, total_display: '600 MB', active_percent: 33.3, limitation: null,
      members: detail ? [{ group: 1, thread: 1, path: '/u01/oradata/redo01.log', bytes: 200 * MB, display: '200 MB', status: 'CURRENT', current: true }] : [],
    },
    segments: { rows: [], limitation: null },
    recovery_area: {
      present: true, name: detail ? '/u01/fra' : '', used_percent: 71.2, used_display: '7.1 GB', limit_display: '10 GB', limitation: null,
      usage: [{ file_type: 'ARCHIVED LOG', percent_space_used: 60.5, number_of_files: 42 }],
    },
    archive: { log_mode: 'ARCHIVELOG', destinations: [{ destination: 'LOG_ARCHIVE_DEST_1', status: 'VALID' }], latest: 'Sequence 18', free_minutes: '', time_to_failure: '', limitation: null },
    backup: { input_type: '', status: '', started: '', ended: '', failed: false, limitation: null },
    collected_at: '2026-11-15T10:00:00', limitations: [], detail,
    detail_note: detail ? null : 'File, control-file, redo-member, and segment listings are shown to an administrator.',
  }
}

function parameters(detail: boolean) {
  return {
    version: 'Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production',
    parameters: [
      { name: 'archive_lag_target', value: '0', is_default: true, sys_modifiable: 'IMMEDIATE', description: 'Maximum number of seconds of redos the standby could lose', restricted: false },
      { name: 'audit_file_dest', value: detail ? '/u01/app/oracle/admin/ORCL/adump' : 'Shown to an administrator.', is_default: false, sys_modifiable: 'DEFERRED', description: 'Directory in which auditing files are to reside', restricted: !detail },
      { name: 'open_cursors', value: '300', is_default: false, sys_modifiable: 'IMMEDIATE', description: 'max # cursors per session', restricted: false },
      { name: 'processes', value: '300', is_default: false, sys_modifiable: 'FALSE', description: 'user processes', restricted: false },
    ],
    total: 4, changed: 3, collected_at: '2026-11-15T10:00:00', limitation: null, detail,
    detail_note: detail ? null : 'Values that hold a file path or a network address are shown to an administrator.',
    caption: '',
  }
}

const ALERT = {
  lines: [
    { at: '2026-11-15 01:10:00', text: 'ORA-01578: ORACLE data block corrupted (file # 7, block # 1234)', severity: 'error', codes: ['ORA-01578'] },
    { at: '2026-11-15 05:40:10', text: 'WARNING: Heavy swapping observed on system in last 5 mins.', severity: 'warning', codes: [] },
    { at: '2026-11-15 06:04:52', text: 'Thread 1 advanced to log sequence 19 (LGWR switch)', severity: 'info', codes: [] },
    { at: '2026-11-15 06:11:24', text: 'Resize operation completed for file# 3, fname (path shown to an administrator), old size 870400K', severity: 'info', codes: [] },
  ],
  errors: 1, warnings: 1, collected_at: '2026-11-15T10:00:00', limitation: null, detail: false,
  detail_note: 'File paths in the alert log are shown to an administrator.',
  window: 'The 300 most recent lines of the last 7 days.', caption: 'Database alert log, oldest line first.',
}

function installFetch(overrides: Record<string, () => Response> = {}, detail = true) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    for (const [part, respond] of Object.entries(overrides)) {
      if (url.includes(part)) return Promise.resolve(respond())
    }
    if (url.includes('/api/v1/auth/session')) return Promise.resolve(json({ required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local' }))
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.includes('/memory/sga')) return Promise.resolve(json(SGA))
    if (url.includes('/graph/memory')) return Promise.resolve(json(MEMORY))
    if (url.includes('/storage/tablespaces')) return Promise.resolve(json(TABLESPACES))
    if (url.includes('/graph/storage')) return Promise.resolve(json(storage(detail)))
    if (url.includes('/parameters')) return Promise.resolve(json(parameters(detail)))
    if (url.includes('/alert-log')) return Promise.resolve(json(ALERT))
    if (url.includes('/api/v1/databases?')) return Promise.resolve(json({ items: [database('target-1', 'FINANCE-PRD')], total: 1, page: 1, page_size: 100, environments: ['production'] }))
    if (/\/api\/v1\/databases\/target-1$/.test(url)) return Promise.resolve(json(database('target-1', 'FINANCE-PRD')))
    if (/\/api\/v1\/databases\/retired$/.test(url)) return Promise.resolve(json(database('retired', 'RETIRED', false)))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  }))
  return calls
}

function props(databaseId = 'target-1') {
  return { databaseId, onAskCopilot: noop, onOpenDatabase: noop, onRefreshStatus: noop }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.location.hash = ''
})

describe('Phase 25 database sections', () => {
  it('shows megabytes with two decimals', () => {
    expect(megabytes(613.97 * MB)).toBe('613.97')
    expect(megabytes(0)).toBe('0.00')
    expect(megabytes(null)).toBe('—')
  })

  it('MemoryDB shows the SGA by pool, the shared pool, the buffer cache, and the PGA', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<MemoryPage {...props()} />)

    expect(await screen.findByRole('heading', { name: 'SGA utilization' })).toBeInTheDocument()
    expect(screen.getByText('MEMORYDB')).toBeInTheDocument()
    expect(screen.getByText('1.1 GB')).toBeInTheDocument()
    const chart = screen.getByRole('img', { name: 'SGA utilization, Megabytes by pool' })
    expect(within(chart).getByText('buffer cache')).toBeInTheDocument()
    expect(within(chart).getByText('912.00 MB')).toBeInTheDocument()
    const components = within(screen.getByRole('region', { name: 'SGA components' }))
    expect(components.getAllByRole('row')).toHaveLength(4)
    expect(components.getByRole('row', { name: /shared pool SQLA 39\.00 39,936/ })).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Filter components' }), 'sqla')
    expect(components.getAllByRole('row')).toHaveLength(2)

    await user.click(screen.getByRole('tab', { name: 'Shared pool' }))
    expect(screen.getByRole('img', { name: 'Shared pool utilization, Megabytes by area' })).toBeInTheDocument()
    expect(screen.getByText('Shared pool 150 MB: 103.00 MB used and 47.00 MB free.')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Buffer cache' }))
    expect(screen.getByText('98.4%')).toBeInTheDocument()
    expect(screen.getByText('912 MB')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'PGA' }))
    expect(screen.getByText('2 GB')).toBeInTheDocument()
    expect(screen.getByText('Automatic Shared Memory Management is on.')).toBeInTheDocument()
  })

  it('MemoryDB still shows the SGA when the older memory picture cannot be read, and names a missing grant', async () => {
    installFetch({ '/graph/memory': () => json({ detail: 'boom' }, 500) })
    const user = userEvent.setup()
    render(<MemoryPage {...props()} />)
    expect(await screen.findByRole('img', { name: 'SGA utilization, Megabytes by pool' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'PGA' }))
    expect(screen.getByText('The PGA reading could not be read.')).toBeInTheDocument()
    cleanup()

    const denied = 'Grant SELECT on SYS.V_$SGASTAT to the monitoring account, then ask again.'
    installFetch({ '/memory/sga': () => json({ ...SGA, pools: [], components: [], shared_pool: [], total_bytes: 0, total_display: '', limitation: denied }) })
    render(<MemoryPage {...props()} />)
    expect(await screen.findByText(denied)).toBeInTheDocument()
    expect(screen.getByText('No SGA pool was returned.')).toBeInTheDocument()
  })

  it('StorageDB lists every tablespace with used and free, then redo, the recovery area, and archiving', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<StoragePage {...props()} />)

    const table = within(await screen.findByRole('region', { name: 'Tablespaces' }))
    expect(table.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Name', 'Total (MB)', 'Free (MB)', 'Used (MB)', 'Contents', 'Status', 'Fragments', 'Used',
    ])
    expect(table.getByRole('row', { name: /SYSAUX 613\.97 37\.50 576\.47 PERMANENT ONLINE 1 93\.9%/ })).toBeInTheDocument()
    expect(table.getByRole('row', { name: /TEMP 19\.00 19\.00 0\.00 TEMPORARY ONLINE — 0%/ })).toBeInTheDocument()
    expect(screen.getByText('651.2 MB of 763.9 MB')).toBeInTheDocument()
    const chart = screen.getByRole('img', { name: 'Tablespace size, Megabytes by tablespace' })
    expect(within(chart).getAllByRole('listitem').slice(2).map((item) => item.querySelector('.section-bar-label')?.textContent)).toEqual(['SYSAUX', 'USERS', 'TEMP'])

    await user.click(screen.getByRole('tab', { name: 'Online redo' }))
    expect(screen.getByText('/u01/oradata/redo01.log')).toBeInTheDocument()
    expect(screen.getByText('600 MB')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Recovery area' }))
    expect(screen.getByText('ARCHIVED LOG')).toBeInTheDocument()
    expect(screen.getByText('/u01/fra')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Archive destinations' }))
    expect(screen.getByText('ARCHIVELOG')).toBeInTheDocument()
    expect(screen.getByText('LOG_ARCHIVE_DEST_1')).toBeInTheDocument()
  })

  it('StorageDB keeps file names from a non-administrator', async () => {
    installFetch({}, false)
    const user = userEvent.setup()
    render(<StoragePage {...props()} />)
    await screen.findByRole('region', { name: 'Tablespaces' })
    await user.click(screen.getByRole('tab', { name: 'Online redo' }))
    expect(screen.getByText('Member file names are shown to an administrator.')).toBeInTheDocument()
    expect(screen.getByText('File, control-file, redo-member, and segment listings are shown to an administrator.')).toBeInTheDocument()
    expect(screen.queryByText('/u01/oradata/redo01.log')).not.toBeInTheDocument()
  })

  it('ParameterDB lists the parameters, marks the changed ones, and filters', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<ParameterPage {...props()} />)

    const panel = within(await screen.findByRole('region', { name: 'Oracle parameters' }))
    expect(panel.getByText('Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production')).toBeInTheDocument()
    expect(panel.getByText('3 of 4')).toBeInTheDocument()
    expect(panel.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Name', 'Value', 'Default', 'System modifiable', 'Description'])
    expect(panel.getAllByRole('row')).toHaveLength(5)
    expect(panel.getByRole('row', { name: /processes 300 No No user processes/ })).toBeInTheDocument()
    expect(panel.getByRole('row', { name: /archive_lag_target 0 Yes Immediate/ })).toBeInTheDocument()
    expect(panel.getByText('/u01/app/oracle/admin/ORCL/adump')).toBeInTheDocument()

    await user.click(panel.getByRole('checkbox', { name: 'Changed only' }))
    expect(panel.getAllByRole('row')).toHaveLength(4)
    expect(panel.getByText('Showing 3 of 4 parameters.')).toBeInTheDocument()
    await user.type(panel.getByRole('textbox', { name: 'Filter parameters' }), 'cursor')
    expect(panel.getAllByRole('row')).toHaveLength(2)
    await user.clear(panel.getByRole('textbox', { name: 'Filter parameters' }))
    await user.type(panel.getByRole('textbox', { name: 'Filter parameters' }), 'nothing-like-this')
    expect(panel.getByText('No parameter matches.')).toBeInTheDocument()
  })

  it('ParameterDB tells a non-administrator which values are held back', async () => {
    installFetch({}, false)
    render(<ParameterPage {...props()} />)
    expect(await screen.findByText('Values that hold a file path or a network address are shown to an administrator.')).toBeInTheDocument()
    expect(screen.getByText('Shown to an administrator.')).toBeInTheDocument()
    expect(screen.queryByText('/u01/app/oracle/admin/ORCL/adump')).not.toBeInTheDocument()
  })

  it('AlertDB shows the log oldest first with errors and warnings marked, and filters', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<AlertPage {...props()} />)

    const log = within(await screen.findByRole('log', { name: 'Alert log lines' }))
    const lines = log.getAllByRole('listitem')
    expect(lines).toHaveLength(4)
    expect(lines[0]).toHaveClass('alert-line-error')
    expect(lines[1]).toHaveClass('alert-line-warning')
    expect(lines[3]).toHaveTextContent('fname (path shown to an administrator)')
    expect(screen.getByText('File paths in the alert log are shown to an administrator.')).toBeInTheDocument()
    expect(screen.getByText('Showing 4 of 4 lines. The newest line is last.')).toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Lines shown' }), 'attention')
    expect(log.getAllByRole('listitem')).toHaveLength(2)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Lines shown' }), 'error')
    expect(log.getAllByRole('listitem')).toHaveLength(1)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Lines shown' }), 'all')
    await user.type(screen.getByRole('textbox', { name: 'Filter alert log' }), 'lgwr')
    expect(log.getAllByRole('listitem')).toHaveLength(1)
    await user.clear(screen.getByRole('textbox', { name: 'Filter alert log' }))
    await user.type(screen.getByRole('textbox', { name: 'Filter alert log' }), 'zzz')
    expect(screen.getByText('No line matches.')).toBeInTheDocument()
  })

  it('a section says so when the database is disabled, refused, or cannot be read', async () => {
    installFetch()
    render(<AlertPage {...props('retired')} />)
    expect(await screen.findByText('Database target not found')).toBeInTheDocument()
    cleanup()

    installFetch({ '/alert-log': () => json({ detail: 'Authorization failed.' }, 403) })
    render(<AlertPage {...props()} />)
    expect(await screen.findByText('AlertDB is not available')).toBeInTheDocument()
    cleanup()

    installFetch({ '/parameters': () => json({ detail: 'The readings server could not be reached.' }, 500) })
    render(<ParameterPage {...props()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The readings server could not be reached.')
  })

  it('the workspace has a tab for each section and keeps the database when the tab changes', async () => {
    window.location.hash = '#/storage/databases/target-1'
    const calls = installFetch()
    const user = userEvent.setup()
    render(<App />)

    const tabs = within(await screen.findByRole('tablist', { name: 'Database views' }))
    expect(tabs.getByRole('tab', { name: 'StorageDB' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('region', { name: 'Tablespaces' })).toBeInTheDocument()
    await user.click(tabs.getByRole('tab', { name: 'MemoryDB' }))
    expect(window.location.hash).toBe('#/memory/databases/target-1')
    expect(await screen.findByRole('heading', { name: 'SGA utilization' })).toBeInTheDocument()
    await user.click(tabs.getByRole('tab', { name: 'ParameterDB' }))
    expect(window.location.hash).toBe('#/parameters/databases/target-1')
    expect(await screen.findByRole('region', { name: 'Oracle parameters' })).toBeInTheDocument()
    await user.click(tabs.getByRole('tab', { name: 'AlertDB' }))
    expect(window.location.hash).toBe('#/alerts/databases/target-1')
    expect(await screen.findByRole('log', { name: 'Alert log lines' })).toBeInTheDocument()
    await waitFor(() => expect(calls.some((url) => url.includes('/alert-log'))).toBe(true))
    await user.click(screen.getByRole('button', { name: /Ask copilot/ }))
    expect(window.location.hash).toBe('#/copilot/databases/target-1')
  })
})
