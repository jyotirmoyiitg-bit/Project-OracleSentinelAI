import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { SessionList, SessionRow, SessionStatement } from './api'
import { idleText, SessionPage } from './SessionPage'
import { formatSql } from './sqlFormat'

const noop = () => {}

function database(id: string, name: string) {
  return {
    id, display_name: name, tns_alias: name.toLowerCase(), enabled: true, environment: 'dev', engine: 'Oracle 19c',
    owner: '', poll_profile: {}, capability_snapshot: {}, last_connection_at: null, last_health_status: 'healthy',
    created_at: '2026-11-20T00:00:00Z', updated_at: '2026-11-20T00:00:00Z', last_seen: null, monitoring_status: 'healthy',
    critical_incident_count: 0, trend_snapshots: [],
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function row(sid: number, overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    sid, serial: sid * 100, username: 'LOANAPP', os_user: 'svc_loans', terminal: 'WS-0142', program: 'teller.exe', status: 'INACTIVE',
    module: 'teller.exe', action: '', logon_time: '2026-11-20 08:04:46', last_call: '2026-11-20 08:26:58',
    idle_seconds: 462, sql_id: '', restricted: false, ...overrides,
  }
}

function sessions(detail = true): SessionList {
  const hide = (item: SessionRow): SessionRow => (detail ? item : { ...item, username: '', os_user: '', terminal: '', restricted: true })
  const toad = { program: 'Toad.exe', module: 'TOAD 26.2.272.9090', username: 'APPUSER', os_user: 'jdoe', terminal: 'WS-0177' }
  return {
    groups: [
      { program: 'TELLER.EXE', active: 0, sessions: [row(246), row(488)].map(hide) },
      {
        program: 'TOAD.EXE', active: 1, sessions: [
          row(608, { ...toad, action: '69524976,395748208', idle_seconds: 281 }),
          row(730, { ...toad, status: 'ACTIVE', action: '69524976,493129008', sql_id: '5jm8qnb729z8u', idle_seconds: 2 }),
        ].map(hide),
      },
    ],
    total: 4, active: 1, inactive: 3, programs: 2, capped: false, collected_at: '2026-11-20T08:35:00',
    limitation: null, detail, detail_note: detail ? null : 'User names, operating system users, terminals, and client host names are shown to an administrator.',
    caption: 'User sessions grouped by program.',
  }
}

function statement(overrides: Partial<SessionStatement>): SessionStatement {
  return {
    sid: 246, serial: 24600, found: true, status: 'INACTIVE', kind: 'previous', sql_id: 'gc77vs203t2v4', child_number: 0,
    text: "DELETE FROM LX_DTA.LX_FOL WHERE FOLMOD = 'J$BATCHCTL' AND FOLFMT = 'J$C0108' AND FOLJ_L = '003156' AND FOLLVL = 'F'",
    truncated: false, text_note: 'The session is idle. This is the last statement it ran.', plan_hash_value: '3043255311',
    optimizer_mode: 'ALL_ROWS', executions: 1, elapsed_seconds: 0.01, buffer_gets: 7, disk_reads: 0, rows_processed: 0,
    plan: [
      { id: 0, parent_id: null, depth: 0, operation: 'DELETE STATEMENT', options: '', object_owner: '', object_name: '', optimizer: 'ALL_ROWS', cost: 2, cardinality: null, bytes: null },
      { id: 1, parent_id: 0, depth: 1, operation: 'DELETE', options: '', object_owner: 'LX_DTA', object_name: 'LX_FOL', optimizer: '', cost: null, cardinality: null, bytes: null },
      { id: 2, parent_id: 1, depth: 2, operation: 'TABLE ACCESS', options: 'FULL', object_owner: 'LX_DTA', object_name: 'LX_FOL', optimizer: '', cost: 2, cardinality: 1, bytes: 32 },
    ],
    plan_note: null, collected_at: '2026-11-20T08:53:08', limitation: null, ...overrides,
  }
}

const IDLE_STATEMENT = statement({})
const ACTIVE_STATEMENT = statement({
  sid: 730, serial: 73000, status: 'ACTIVE', kind: 'current', sql_id: '5jm8qnb729z8u', text_note: null,
  text: 'SELECT a.id FROM loans.accounts a WHERE a.status = :b1', plan: [], plan_note: 'Grant SELECT on SYS.V_$SQL_PLAN to the monitoring account to see the plan.',
})

function installFetch(detail = true, overrides: Record<string, () => Response> = {}) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    for (const [part, respond] of Object.entries(overrides)) {
      if (url.includes(part)) return Promise.resolve(respond())
    }
    if (url.includes('/api/v1/auth/session')) return Promise.resolve(json({ required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local' }))
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.includes('/statement?')) return Promise.resolve(json(url.includes('/sessions/730/') ? ACTIVE_STATEMENT : IDLE_STATEMENT))
    if (url.includes('/sessions')) return Promise.resolve(json(sessions(detail)))
    if (url.includes('/api/v1/databases?')) return Promise.resolve(json({ items: [database('target-1', 'MY19C')], total: 1, page: 1, page_size: 100, environments: ['dev'] }))
    if (/\/api\/v1\/databases\/target-1$/.test(url)) return Promise.resolve(json(database('target-1', 'MY19C')))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  }))
  return calls
}

const props = { databaseId: 'target-1', onAskCopilot: noop, onOpenDatabase: noop, onRefreshStatus: noop }

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.location.hash = ''
})

describe('Phase 28 SessionDB', () => {
  it('groups sessions by program with the columns of a session browser', async () => {
    installFetch()
    render(<SessionPage {...props} />)

    const panel = within(await screen.findByRole('region', { name: 'Sessions by program' }))
    expect(panel.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Program / user name', 'OS user', 'Terminal', 'SID', 'Status', 'Module', 'Action', 'Logon time', 'Last call', 'SQL_ID',
    ])
    expect(panel.getByRole('button', { name: 'TELLER.EXE, 2 sessions' })).toHaveAttribute('aria-expanded', 'true')
    expect(panel.getByRole('button', { name: 'TOAD.EXE, 2 sessions' })).toBeInTheDocument()
    expect(panel.getByText('1 active')).toBeInTheDocument()
    const active = panel.getByRole('row', { name: /APPUSER jdoe WS-0177 730 ACTIVE TOAD 26.2.272.9090/ })
    expect(active).toHaveClass('session-active')
    expect(within(active).getByText('5jm8qnb729z8u')).toBeInTheDocument()
    expect(within(active).getByText('2026-11-20 08:26:58')).toHaveAttribute('title', '2s since the last call')
    expect(panel.getByText('Showing 4 of 4 sessions in 2 programs.')).toBeInTheDocument()
  })

  it('filters by status and text, and collapses a program', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<SessionPage {...props} />)
    const panel = within(await screen.findByRole('region', { name: 'Sessions by program' }))

    await user.selectOptions(panel.getByRole('combobox', { name: 'Sessions shown' }), 'active')
    expect(panel.getByText('Showing 1 of 4 sessions in 1 programs.')).toBeInTheDocument()
    expect(panel.queryByRole('button', { name: /TELLER.EXE/ })).not.toBeInTheDocument()
    await user.selectOptions(panel.getByRole('combobox', { name: 'Sessions shown' }), 'all')

    await user.type(panel.getByRole('textbox', { name: 'Filter sessions' }), '488')
    expect(panel.getByText('Showing 1 of 4 sessions in 1 programs.')).toBeInTheDocument()
    await user.clear(panel.getByRole('textbox', { name: 'Filter sessions' }))
    await user.type(panel.getByRole('textbox', { name: 'Filter sessions' }), 'nothing-like-this')
    expect(panel.getByText('No session matches.')).toBeInTheDocument()
    await user.clear(panel.getByRole('textbox', { name: 'Filter sessions' }))

    await user.click(panel.getByRole('button', { name: 'TELLER.EXE, 2 sessions' }))
    expect(panel.getByRole('button', { name: 'TELLER.EXE, 2 sessions' })).toHaveAttribute('aria-expanded', 'false')
    expect(panel.queryByRole('row', { name: /LOANAPP/ })).not.toBeInTheDocument()
    await user.click(panel.getByRole('button', { name: 'Collapse all' }))
    expect(panel.queryAllByRole('row', { name: /ACTIVE|INACTIVE/ })).toHaveLength(0)
    await user.click(panel.getByRole('button', { name: 'Expand all' }))
    expect(panel.getAllByRole('row', { name: /ACTIVE|INACTIVE/ })).toHaveLength(4)
  })

  it('tells a non-administrator which columns are held back', async () => {
    installFetch(false)
    render(<SessionPage {...props} />)
    expect(await screen.findByText('User names, operating system users, terminals, and client host names are shown to an administrator.')).toBeInTheDocument()
    expect(screen.queryByText('LOANAPP')).not.toBeInTheDocument()
    expect(screen.queryByText('jdoe')).not.toBeInTheDocument()
    expect(screen.getAllByTitle('Shown to an administrator').length).toBe(12)
    expect(screen.getByText('5jm8qnb729z8u')).toBeInTheDocument()
  })

  it('names a missing grant instead of an empty list', async () => {
    installFetch(true, {
      '/sessions': () => json({ ...sessions(), groups: [], total: 0, active: 0, inactive: 0, programs: 0, limitation: 'Grant SELECT on SYS.V_$SESSION to the monitoring account, then ask again.' }),
    })
    render(<SessionPage {...props} />)
    expect(await screen.findByText('Grant SELECT on SYS.V_$SESSION to the monitoring account, then ask again.')).toBeInTheDocument()
  })

  it('describes idle time in short words', () => {
    expect(idleText(null)).toBe('')
    expect(idleText(42)).toBe('42s since the last call')
    expect(idleText(462)).toBe('7m 42s since the last call')
    expect(idleText(3 * 3600 + 120)).toBe('3h 2m since the last call')
    expect(idleText(3 * 86400 + 3600)).toBe('3d 1h since the last call')
  })

  it('has its own workspace tab after WaitDB and keeps the database', async () => {
    window.location.hash = '#/sessions/databases/target-1'
    installFetch()
    const user = userEvent.setup()
    render(<App />)

    const tabs = within(await screen.findByRole('tablist', { name: 'Database views' }))
    expect(tabs.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Overview', 'GraphDB', 'WaitDB', 'SessionDB', 'MemoryDB', 'StorageDB', 'ParameterDB', 'AlertDB', 'Events', 'Copilot',
    ])
    expect(tabs.getByRole('tab', { name: 'SessionDB' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('region', { name: 'Sessions by program' })).toBeInTheDocument()
    await user.click(tabs.getByRole('tab', { name: 'ParameterDB' }))
    expect(window.location.hash).toBe('#/parameters/databases/target-1')
    await user.click(tabs.getByRole('tab', { name: 'SessionDB' }))
    expect(window.location.hash).toBe('#/sessions/databases/target-1')
  })

  it('shows the statement of the chosen session, formatted, with its cached plan', async () => {
    const calls = installFetch()
    const user = userEvent.setup()
    render(<SessionPage {...props} />)
    const panel = within(await screen.findByRole('region', { name: 'Sessions by program' }))
    expect(screen.getByText('Choose a session above to see the statement it is running and its plan.')).toBeInTheDocument()

    await user.click(panel.getByRole('row', { name: /LOANAPP svc_loans WS-0142 246 INACTIVE/ }))
    expect(panel.getByRole('row', { name: /246 INACTIVE/ })).toHaveAttribute('aria-selected', 'true')
    const statementPanel = within(await screen.findByRole('region', { name: 'Last statement' }))
    expect(calls.some((url) => url.endsWith('/api/v1/databases/target-1/sessions/246/statement?serial=24600'))).toBe(true)
    expect(statementPanel.getByText('SID 246 · TELLER.EXE')).toBeInTheDocument()
    expect(statementPanel.getByText('The session is idle. This is the last statement it ran.')).toBeInTheDocument()
    const lines = within(statementPanel.getByRole('list', { name: 'Statement text' })).getAllByRole('listitem').map((item) => item.textContent)
    expect(lines).toEqual([
      'DELETE FROM LX_DTA.LX_FOL', "WHERE FOLMOD = 'J$BATCHCTL'", "    AND FOLFMT = 'J$C0108'", "    AND FOLJ_L = '003156'", "    AND FOLLVL = 'F'",
    ])
    expect(statementPanel.getByText('gc77vs203t2v4')).toBeInTheDocument()
    await user.click(statementPanel.getByRole('checkbox', { name: 'Format statement' }))
    expect(within(statementPanel.getByRole('list', { name: 'Statement text' })).getAllByRole('listitem')).toHaveLength(1)

    await user.click(statementPanel.getByRole('tab', { name: 'Explain plan' }))
    const plan = within(statementPanel.getByRole('table', { name: 'Cached explain plan' }))
    expect(plan.getAllByRole('row').slice(1).map((item) => item.textContent)).toEqual([
      '0DELETE STATEMENTALL_ROWS2——', '1└DELETELX_DTA.LX_FOL———', '2└TABLE ACCESS FULLLX_DTA.LX_FOL2132',
    ])
  })

  it('keeps the statement when the plan cannot be read, and says why', async () => {
    installFetch()
    const user = userEvent.setup()
    render(<SessionPage {...props} />)
    const panel = within(await screen.findByRole('region', { name: 'Sessions by program' }))
    await user.click(panel.getByRole('row', { name: /730 ACTIVE/ }))
    const statementPanel = within(await screen.findByRole('region', { name: 'Current statement' }))
    expect(await statementPanel.findByText('5jm8qnb729z8u')).toBeInTheDocument()
    await user.click(statementPanel.getByRole('tab', { name: 'Explain plan' }))
    expect(statementPanel.getByText('Grant SELECT on SYS.V_$SQL_PLAN to the monitoring account to see the plan.')).toBeInTheDocument()
  })

  it('does not ask for a statement for a non-administrator', async () => {
    const calls = installFetch(false)
    const user = userEvent.setup()
    render(<SessionPage {...props} />)
    const panel = within(await screen.findByRole('region', { name: 'Sessions by program' }))
    await user.click(panel.getAllByRole('row', { name: /INACTIVE/ })[0])
    expect(screen.getByText('The statement of a session and its plan are shown to an administrator.')).toBeInTheDocument()
    expect(calls.some((url) => url.includes('/statement'))).toBe(false)
  })

  it('formats a statement without touching quoted text or comments', () => {
    expect(formatSql("select a from t where x = 'and where' and y = 1")).toBe("select a\nfrom t\nwhere x = 'and where'\n    and y = 1")
    expect(formatSql('SELECT 1 FROM t WHERE id IN (SELECT id FROM u WHERE z = 2) ORDER BY 1')).toBe(
      'SELECT 1\nFROM t\nWHERE id IN (\n    SELECT id\n    FROM u\n    WHERE z = 2)\nORDER BY 1')
    expect(formatSql('/* from where */ SELECT 1 FROM dual')).toBe('/* from where */\nSELECT 1\nFROM dual')
    expect(formatSql('SELECT a\n  FROM t')).toBe('SELECT a\n  FROM t')
    expect(formatSql('')).toBe('')
  })
})
