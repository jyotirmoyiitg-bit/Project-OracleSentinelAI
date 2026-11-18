import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { AgentsPage } from './AgentsPage'
import { InvestigationPanel } from './components/InvestigationPanel'
import { SnapshotPanel } from './components/SnapshotPanel'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type Handler = (url: string, init?: RequestInit) => Response | undefined

function stubApi(handler: Handler) {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    return Promise.resolve(handler(url, init) ?? json({ detail: 'Not found' }, 404))
  }))
  return calls
}

const status = {
  enabled: true, can_run: true, can_configure: false, model_writes_findings: true, model: 'qwen2.5-coder:14b',
  open_findings: 1, packs: [], limits: { max_steps: 6, max_model_calls: 4, run_timeout_seconds: 180 },
}

const finding = {
  id: 'finding-1', run_id: 'run-1', target_id: 'target-1', target_name: 'FINANCE', incident_id: 'incident-1',
  severity: 'critical', title: 'Archive destination or recovery area full on FINANCE',
  summary: 'The recovery area filled because archived logs were not backed up.',
  next_steps: ['Back up and remove archived logs.'],
  evidence: [{ ref: 'metric', source: 'Stored metric', text: 'FRA storage.fra.used_pct is 97 now and was 71 about a day ago' }],
  confidence: 'medium', verified: true, verifier_note: 'Every code, figure, and database name in the conclusion is present in the evidence.',
  written_by: 'model', restricted: false, hidden: false, decision: 'open', decided_by: '', decided_at: null,
  decision_note: '', created_at: '2026-11-15T10:05:00Z',
}

const run = {
  id: 'run-1', agent: 'investigator', trigger: 'operator', scope_type: 'incident', target_id: 'target-1',
  target_name: 'FINANCE', incident_id: 'incident-1', goal: 'alert.ora: ORA-00257: archiver error',
  playbook: 'archive_destination', status: 'completed', principal: 'olga', llm_used: true, model: 'qwen2.5-coder:14b',
  steps_used: 3, model_calls: 1, note: '', created_at: '2026-11-15T10:04:00Z', started_at: '2026-11-15T10:04:00Z',
  finished_at: '2026-11-15T10:05:00Z',
  steps: [
    { seq: 1, kind: 'plan', title: 'Plan: Archive destination or recovery area full', outcome: 'ok', duration_ms: 4, lines: ['The playbook was matched by a fixed rule.'], restricted_hidden: false },
    { seq: 2, kind: 'collect', title: 'Collect: 5 of 5 readings', outcome: 'ok', duration_ms: 2400, lines: ['Recovery: 4 rows (read now)'], restricted_hidden: false },
    { seq: 3, kind: 'compare', title: 'Compare with stored data', outcome: 'ok', duration_ms: 12, lines: ['FRA storage.fra.used_pct is 97 now and was 71 about a day ago'], restricted_hidden: true },
    { seq: 4, kind: 'verify', title: 'Verify', outcome: 'ok', duration_ms: 1, lines: ['Every code, figure, and database name in the conclusion is present in the evidence.'], restricted_hidden: false },
  ],
  finding,
}

const snapshots = {
  target_id: 'target-1', target_name: 'FINANCE', enabled: true, can_collect: true,
  last_taken_at: '2026-11-15T10:00:00Z', snapshot_count: 2, retention_days: 14, packs: ['core', 'performance'], changed: 1,
  readings: [
    { kind: 'tablespace', label: 'Tablespaces', pack: 'core', taken_at: '2026-11-15T10:00:00Z', status: 'ok', row_count: 3, restricted: false, hidden: false, previous_at: '2026-11-14T10:00:00Z', changes: ['USERS used_pct went from 71 to 97'] },
    { kind: 'sessions', label: 'Sessions', pack: 'core', taken_at: '2026-11-15T10:00:00Z', status: 'ok', row_count: 9, restricted: true, hidden: true, previous_at: '2026-11-14T10:00:00Z', changes: [] },
    { kind: 'backups', label: 'Backups', pack: 'core', taken_at: '2026-11-15T10:00:00Z', status: 'not_granted', row_count: 0, restricted: false, hidden: false, previous_at: null, changes: [] },
  ],
}

function settings(editable: boolean, retention = 14, overridden = false) {
  return {
    target_id: 'target-1', system_enabled: true, editable,
    items: [
      { key: 'agents_enabled', label: 'Agents enabled', system_value: true, database_value: null, overridden: false, value: true },
      { key: 'agent_snapshot_packs', label: 'Packs collected', system_value: 'core,performance', database_value: null, overridden: false, value: 'core,performance' },
      { key: 'agent_snapshot_retention_days', label: 'Snapshot retention (days)', system_value: 14, database_value: overridden ? retention : null, overridden, value: retention },
      { key: 'agent_auto_investigate', label: 'Investigate a new critical incident automatically', system_value: false, database_value: null, overridden: false, value: false },
    ],
  }
}

beforeEach(() => {
  window.location.hash = ''
  window.localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Phase 22 investigation on an incident', () => {
  it('starts an investigation, shows each step, and records the decision on the finding', async () => {
    let started = false
    const calls = stubApi((url, init) => {
      if (url.endsWith('/agents/status')) return json(status)
      if (url.includes('/agents/runs?')) return json({ items: [] })
      if (url.endsWith('/agents/investigations') && init?.method === 'POST') {
        started = true
        return json({ ...run, status: 'queued', steps: undefined, finding: undefined }, 202)
      }
      if (url.endsWith('/agents/runs/run-1') && started) return json(run)
      if (url.endsWith('/agents/findings/finding-1/decision')) return json({ ...finding, decision: 'accepted', decided_by: 'olga', decided_at: '2026-11-15T10:06:00Z' })
      return undefined
    })
    const user = userEvent.setup()
    render(<InvestigationPanel incidentId="incident-1" />)
    expect(await screen.findByRole('heading', { name: 'Investigation' })).toBeInTheDocument()
    expect(screen.getByText(/No investigation has been run for this incident/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Investigate' }))
    const steps = await screen.findByRole('list', { name: 'Run steps' })
    expect(within(steps).getByText('Plan: Archive destination or recovery area full')).toBeInTheDocument()
    expect(within(steps).getByText('Collect: 5 of 5 readings')).toBeInTheDocument()
    expect(within(steps).getByText('Some lines are shown to an administrator only.')).toBeInTheDocument()
    expect(screen.getByText('Written with qwen2.5-coder:14b')).toBeInTheDocument()
    const card = screen.getByRole('article', { name: 'Finding: Archive destination or recovery area full on FINANCE' })
    expect(within(card).getByText('The recovery area filled because archived logs were not backed up.')).toBeInTheDocument()
    expect(within(card).getByText('Cause written by the model')).toBeInTheDocument()
    expect(within(card).getByText('Checked against the evidence')).toBeInTheDocument()
    expect(within(card).getByText('Back up and remove archived logs.')).toBeInTheDocument()
    expect(within(card).getByText('Evidence (1)')).toBeInTheDocument()
    const post = calls.find((call) => call.url.endsWith('/agents/investigations'))
    expect(JSON.parse(String(post?.init?.body))).toEqual({ incident_id: 'incident-1', target_id: null, goal: '' })
    await user.click(within(card).getByRole('button', { name: 'Accept' }))
    expect(await within(card).findByText(/Accepted by olga/)).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    const decision = calls.find((call) => call.url.endsWith('/decision'))
    expect(JSON.parse(String(decision?.init?.body))).toEqual({ decision: 'accepted', note: '' })
    expect(screen.getByRole('button', { name: 'Investigate again' })).toBeEnabled()
  })

  it('shows the newest earlier run, and an evidence-only finding whose conclusion was rejected', async () => {
    const rejected = {
      ...finding, written_by: 'rules', verified: false, confidence: 'none',
      summary: "The model's conclusion did not match the evidence, so only the evidence is shown.",
      verifier_note: 'The conclusion used the figure 99, which is not in the evidence.',
    }
    stubApi((url) => {
      if (url.endsWith('/agents/status')) return json({ ...status, can_run: false })
      if (url.includes('/agents/runs?')) return json({ items: [{ ...run, steps: undefined, finding: undefined }] })
      if (url.endsWith('/agents/runs/run-1')) return json({ ...run, finding: rejected })
      return undefined
    })
    render(<InvestigationPanel incidentId="incident-1" />)
    const card = await screen.findByRole('article', { name: /Finding:/ })
    expect(within(card).getByText('Evidence only')).toBeInTheDocument()
    expect(within(card).getByText('Conclusion rejected')).toBeInTheDocument()
    expect(within(card).getByText('The conclusion used the figure 99, which is not in the evidence.')).toBeInTheDocument()
    expect(within(card).getByText('Waiting for a decision.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Investigate/ })).not.toBeInTheDocument()
  })

  it('says agents are off, shows the refusal from the API, and renders nothing on an older API', async () => {
    stubApi((url) => {
      if (url.endsWith('/agents/status')) return json({ ...status, enabled: false, can_run: false, model_writes_findings: false })
      if (url.includes('/agents/runs?')) return json({ items: [] })
      return undefined
    })
    const first = render(<InvestigationPanel incidentId="incident-1" />)
    expect(await screen.findByText('Agents are turned off. An administrator can turn them on in Admin, Agents.')).toBeInTheDocument()
    expect(screen.getByText(/The model is off, so no cause is written/)).toBeInTheDocument()
    first.unmount()

    stubApi((url, init) => {
      if (url.endsWith('/agents/status')) return json(status)
      if (url.includes('/agents/runs?')) return json({ items: [] })
      if (init?.method === 'POST') return json({ detail: 'An investigation of this database is already running.' }, 409)
      return undefined
    })
    const user = userEvent.setup()
    const second = render(<InvestigationPanel targetId="target-1" />)
    await user.click(await screen.findByRole('button', { name: 'Investigate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('An investigation of this database is already running.')
    second.unmount()

    stubApi(() => undefined)
    const { container } = render(<InvestigationPanel incidentId="incident-1" />)
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})

describe('Phase 22 snapshot on a database', () => {
  it('shows what changed, collects a snapshot, and lets an administrator set a value for this database', async () => {
    let retention = 14
    let overridden = false
    const calls = stubApi((url, init) => {
      if (url.endsWith('/databases/target-1/snapshots') && init?.method === 'POST') {
        return json({ run_id: 'run-2', batch_id: 'batch-2', taken_at: '2026-11-15T11:00:00Z', answered: 14, readings: new Array(15).fill({ kind: 'x', label: 'X', status: 'ok', row_count: 1, restricted: false, note: '' }), left_out: [] })
      }
      if (url.endsWith('/databases/target-1/snapshots')) return json(snapshots)
      if (url.endsWith('/agent-settings') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { key: string; value: number | null }
        overridden = body.value !== null
        retention = body.value ?? 14
        return json(settings(true, retention, overridden))
      }
      if (url.endsWith('/agent-settings')) return json(settings(true, retention, overridden))
      return undefined
    })
    const user = userEvent.setup()
    render(<SnapshotPanel targetId="target-1" />)
    expect(await screen.findByRole('heading', { name: 'Snapshot' })).toBeInTheDocument()
    expect(screen.getByText(/2 stored, kept for 14 days/)).toBeInTheDocument()
    const changes = screen.getByRole('list', { name: 'What changed' })
    expect(within(changes).getByText('Tablespaces')).toBeInTheDocument()
    expect(within(changes).getByText('USERS used_pct went from 71 to 97')).toBeInTheDocument()
    expect(screen.getByText(/Not read: Backups\./)).toBeInTheDocument()
    expect(screen.getByText(/1 hold SQL text, user names, or configuration and are shown to an administrator only/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Collect snapshot' }))
    expect(await screen.findByText('Snapshot stored: 14 of 15 readings answered.')).toBeInTheDocument()

    await user.click(screen.getByText('Agent settings for this database'))
    const input = await screen.findByRole('spinbutton', { name: 'Snapshot retention (days)' })
    const row = input.closest('tr') as HTMLElement
    expect(within(row).getByText('System default')).toBeInTheDocument()
    await user.clear(input)
    await user.type(input, '5')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(within(row).getByText('This database')).toBeInTheDocument())
    const put = calls.filter((call) => call.init?.method === 'PUT')
    expect(JSON.parse(String(put[0].init?.body))).toEqual({ key: 'agent_snapshot_retention_days', value: 5 })
    await user.click(within(row).getByRole('button', { name: 'Use default' }))
    await waitFor(() => expect(within(row).getByText('System default')).toBeInTheDocument())
    expect(JSON.parse(String(calls.filter((call) => call.init?.method === 'PUT')[1].init?.body))).toEqual({ key: 'agent_snapshot_retention_days', value: null })

    await user.selectOptions(screen.getByRole('combobox', { name: 'Investigate a new critical incident automatically' }), 'true')
    await waitFor(() => expect(calls.filter((call) => call.init?.method === 'PUT')).toHaveLength(3))
    expect(JSON.parse(String(calls.filter((call) => call.init?.method === 'PUT')[2].init?.body))).toEqual({ key: 'agent_auto_investigate', value: true })
  })

  it('is read-only for an operator who cannot collect, and shows a refusal from the API', async () => {
    stubApi((url, init) => {
      if (url.endsWith('/databases/target-1/snapshots') && init?.method === 'POST') return json({ detail: 'A snapshot of this database was collected 40 seconds ago. Try again in 260 seconds.' }, 429)
      if (url.endsWith('/databases/target-1/snapshots')) return json({ ...snapshots, last_taken_at: null, snapshot_count: 0, readings: [], changed: 0 })
      if (url.endsWith('/agent-settings')) return json(settings(false))
      return undefined
    })
    const user = userEvent.setup()
    render(<SnapshotPanel targetId="target-1" />)
    expect(await screen.findByText('No snapshot has been collected for this database.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Collect snapshot' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 260 seconds')
    await user.click(screen.getByText('Agent settings for this database'))
    expect(await screen.findByText('An administrator can change these values for this database.')).toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Change for this database' })).not.toBeInTheDocument()
  })
})

describe('Phase 22 Agents page', () => {
  it('lists open findings and recent runs, shows the steps of a run, and starts a fleet snapshot', async () => {
    const calls = stubApi((url, init) => {
      if (url.endsWith('/agents/status')) return json(status)
      if (url.includes('/agents/findings?')) return json({ items: [finding] })
      if (url.includes('/agents/runs?')) {
        return json({ items: [
          { ...run, steps: undefined, finding: undefined },
          { ...run, id: 'run-0', agent: 'collector', scope_type: 'fleet', target_id: null, target_name: '', incident_id: null, goal: 'Collect a snapshot of every enabled database', llm_used: false, model: '' },
        ] })
      }
      if (url.endsWith('/agents/runs/run-1')) return json(run)
      if (url.endsWith('/agents/snapshots/fleet') && init?.method === 'POST') return json({ status: 'started' }, 202)
      return undefined
    })
    const opened: string[] = []
    const user = userEvent.setup()
    render(<AgentsPage onOpenIncident={(id) => opened.push(`incident:${id}`)} onOpenTarget={(id) => opened.push(`target:${id}`)} onRefreshStatus={() => undefined} />)
    expect(await screen.findByRole('heading', { name: 'Agents' })).toBeInTheDocument()
    expect(await screen.findByText(/Agents are on\. The local model qwen2.5-coder:14b writes the likely cause/)).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /Finding: Archive destination/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open the incident' }))
    await user.click(screen.getByRole('button', { name: 'FINANCE' }))
    expect(opened).toEqual(['incident:incident-1', 'target:target-1'])
    const table = screen.getByRole('table')
    expect(within(table).getByText('Collector')).toBeInTheDocument()
    expect(within(table).getByText('Fleet')).toBeInTheDocument()
    expect(within(table).getAllByText('Operator (olga)')).toHaveLength(2)
    await user.click(within(table).getByRole('button', { name: 'Show run steps for alert.ora: ORA-00257: archiver error' }))
    expect(await screen.findByRole('list', { name: 'Run steps' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Collect fleet snapshot' }))
    expect(await screen.findByText(/The fleet snapshot has started/)).toBeInTheDocument()
    expect(calls.some((call) => call.url.endsWith('/agents/snapshots/fleet') && call.init?.method === 'POST')).toBe(true)
  })

  it('is reached from the top bar', async () => {
    stubApi((url) => {
      if (url.includes('/api/v1/auth/session')) return json({ required: true, authenticated: true, principal: 'olga', role: 'operator', method: 'local' })
      if (url.includes('/portal-notifications')) return json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 })
      if (url.endsWith('/databases/summary')) return json({ total: 0, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0, disabled: 0, scheduler_enabled: true })
      if (url.includes('/databases?')) return json({ items: [], total: 0, page: 1, page_size: 25, environments: [] })
      if (url.endsWith('/agents/status')) return json({ ...status, enabled: false, can_run: false })
      if (url.includes('/agents/findings?')) return json({ items: [] })
      if (url.includes('/agents/runs?')) return json({ items: [] })
      return undefined
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Agents' }))
    expect(await screen.findByRole('heading', { name: 'Agents' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/agents')
    expect(await screen.findByText('Agents are turned off. An administrator can turn them on in Admin, Agents.')).toBeInTheDocument()
    expect(screen.getByText('No finding is waiting for a decision.')).toBeInTheDocument()
    expect(screen.getByText('No agent has run yet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Collect fleet snapshot' })).not.toBeInTheDocument()
  })
})
