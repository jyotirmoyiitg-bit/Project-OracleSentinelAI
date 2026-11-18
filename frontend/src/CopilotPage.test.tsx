import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { ChatMessage, ChatThread } from './types'

const thread: ChatThread = {
  id: 'thread-1',
  scope_type: 'fleet',
  scope_id: null,
  provider: 'disabled',
  model: '',
  llm_enabled: false,
  disclosure: 'Metrics, incidents, and document search stay available without a language model.',
  guided_commands: [
    { label: 'FRA status', prompt: 'What is the fast recovery area status?' },
    { group: 'Health', label: 'Reachable', prompt: 'Is the selected database or PDB reachable right now?' },
  ],
}

const answer: ChatMessage = {
  id: 'message-1',
  role: 'assistant',
  content: 'The language model is not answering this question.\nCollected evidence:\n- FRA: 91 percent used',
  citations: [{
    kind: 'metric',
    label: 'FRA',
    source: 'storage.fra.used_pct',
    detail: '91 percent used',
    target_id: null,
    document_path: null,
    heading: null,
  }],
  tools: [{
    tool: 'get_fra_status',
    decision: 'allowed',
    summary: 'FRA reading',
    reason: '',
    duration_ms: 2,
  }],
  provider: 'disabled',
  model: '',
  llm_used: false,
  provider_status: 'disabled',
  guided_commands: thread.guided_commands,
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function eventResponse(message: ChatMessage) {
  const body = `event: message\ndata: ${JSON.stringify(message)}\n\nevent: done\ndata: {"message_id":"${message.id}"}\n\n`
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('Copilot page', () => {
  beforeEach(() => {
    window.location.hash = '#/copilot'
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows the no-model badge, guided command, and cited answer', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] }))
      if (url.endsWith('/api/v1/chat/threads') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ scope_type: 'fleet', scope_id: null })
        return Promise.resolve(jsonResponse(thread))
      }
      if (url.includes('/api/v1/chat/threads/thread-1/messages')) {
        expect(JSON.parse(String(init?.body))).toEqual({ content: 'What is the fast recovery area status?' })
        return Promise.resolve(eventResponse(answer))
      }
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Fleet copilot' })).toBeInTheDocument()
    expect(screen.getByText('No language model')).toBeInTheDocument()
    expect(screen.queryByLabelText('Language model')).not.toBeInTheDocument()
    expect(screen.getByText(/does not run SQL/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Health' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Reachable' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Health' }))
    expect(screen.getByRole('button', { name: 'Reachable' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Health' }))
    expect(screen.queryByRole('button', { name: 'Reachable' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a question.')

    await user.click(screen.getByRole('tab', { name: 'Common' }))
    await user.click(screen.getByRole('button', { name: 'FRA status' }))
    expect(await screen.findByText('get_fra_status')).toBeInTheDocument()
    expect(screen.getByText(/storage\.fra\.used_pct/)).toBeInTheDocument()
    expect(screen.getAllByText(/91 percent used/).length).toBeGreaterThan(0)
    expect(screen.getByText('No language model')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Functions' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Collected evidence' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'New chat' }))
    expect(screen.queryByText('get_fra_status')).not.toBeInTheDocument()
    expect(screen.getByText(/Ask which databases have an incident/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Ask the copilot' })).toBeInTheDocument()
  })

  it('splits a model reply into answer, limitations, and next steps', async () => {
    const modelAnswer: ChatMessage = {
      ...answer,
      id: 'message-2',
      content: 'The FRA is configured and empty.\nLimitations: No backup history is stored.\nNext steps: Check the archive destination; Confirm a backup job\n\nDatabase reading:\nFRA space limit is 6.77 GB',
      answer: {
        kind: 'model',
        summary: 'The **FRA** is configured and empty.\n\n| Area | State |\n|---|---|\n| FRA | empty |\n\n```sql\nSELECT 1 FROM dual;\n```',
        confidence: 'high',
        limitations: 'No backup history is stored.',
        next_steps: ['Check the archive destination; then recheck', 'Confirm a backup job'],
        verdict: null,
        reading: ['FRA 0 percent used (0 of 6.77 GB)'],
        citation_note: '',
        evidence_lines: [],
        notes: [],
      },
      provider: 'hosted',
      model: 'claude-sonnet-4-5',
      llm_used: true,
      provider_status: 'answered',
      citations: [],
      tools: [],
    }
    const hostedThread: ChatThread = { ...thread, provider: 'hosted', model: 'claude-sonnet-4-5', llm_enabled: true }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] }))
      if (url.endsWith('/api/v1/chat/threads') && init?.method === 'POST') return Promise.resolve(jsonResponse(hostedThread))
      if (url.includes('/api/v1/chat/threads/thread-1/messages')) return Promise.resolve(eventResponse(modelAnswer))
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByText('Hosted model · claude-sonnet-4-5')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Common' }))
    await user.click(screen.getByRole('button', { name: 'FRA status' }))
    expect(await screen.findByText('FRA', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('Confidence')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Area' })).toBeInTheDocument()
    expect(screen.getByText('SELECT 1 FROM dual;')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Limitations' })).toBeInTheDocument()
    expect(screen.getByText('No backup history is stored.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Next steps' })).toBeInTheDocument()
    expect(screen.getByText('Check the archive destination; then recheck')).toBeInTheDocument()
    expect(screen.getByText('Confirm a backup job')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Database reading' })).toBeInTheDocument()
    expect(screen.getByText(/0 percent used/)).toBeInTheDocument()
    expect(screen.getByText('Answered')).toBeInTheDocument()
  })

  it('shows the active database, offers sample questions as hints, and says which specialist answered', async () => {
    window.location.hash = '#/copilot/databases/target-1'
    const database = {
      id: 'target-1', display_name: 'FINANCE-PRD', tns_alias: 'finance-prd', enabled: true, environment: 'production',
      engine: 'oracle', owner: 'DBA team', poll_profile: {}, capability_snapshot: {}, last_connection_at: null,
      last_health_status: 'healthy', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
      last_seen: null, monitoring_status: 'healthy', critical_incident_count: 0, trend_snapshots: [],
    }
    const scopedThread: ChatThread = {
      ...thread,
      scope_type: 'database',
      scope_id: 'target-1',
      guided_commands: [
        { label: 'FRA status', prompt: 'What is the fast recovery area status?' },
        { label: 'Blocking', prompt: 'Are any sessions currently blocking other sessions?' },
        { group: 'Health', label: 'Identity', prompt: 'What is the database identity, Oracle version, and current container?' },
        { group: 'Sessions', label: 'Blocking', prompt: 'Are any sessions currently blocking other sessions?' },
        { group: 'Sessions', label: 'Blocking', prompt: 'Are any sessions blocking other sessions?' },
      ],
    }
    const identity: ChatMessage = {
      ...answer,
      id: 'message-3',
      content: 'Identity. Status OK. Health HEALTHY.\n\nDatabase ORCL12C open_mode READ WRITE role PRIMARY\nLimitations: Oracle version is not part of this reading.\nNext steps: Use the database name and open mode below as the identity of the selected target.',
      answer: {
        kind: 'catalog', summary: '', confidence: '',
        limitations: 'Oracle version is not part of this reading.',
        next_steps: ['Use the database name and open mode below as the identity of the selected target.'],
        verdict: { label: 'Identity', status: 'OK', health: 'HEALTHY' },
        reading: ['Database ORCL12C open_mode READ WRITE role PRIMARY'],
        citation_note: '', evidence_lines: [], notes: [],
        routes: ['This database', 'Oracle knowledge'], routed_by: 'supervisor',
        source_note: 'From the snapshot stored 5 minutes ago. Ask for the value now to read the database again.',
      },
      provider_status: 'answered',
      citations: [],
      tools: [],
      guided_commands: scopedThread.guided_commands,
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) return Promise.resolve(jsonResponse({ items: [database], total: 1, page: 1, page_size: 100, environments: ['production'] }))
      if (/\/api\/v1\/databases\/target-1$/.test(url)) return Promise.resolve(jsonResponse(database))
      if (url.endsWith('/api/v1/chat/threads') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ scope_type: 'database', scope_id: 'target-1' })
        return Promise.resolve(jsonResponse(scopedThread))
      }
      if (url.includes('/api/v1/chat/threads/thread-1/messages')) return Promise.resolve(eventResponse(identity))
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Database copilot' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Fleet finders')).not.toBeInTheDocument()
    expect(screen.getByText(/Ask about this database/)).toBeInTheDocument()
    const active = await screen.findByLabelText('Active database')
    expect(active).toHaveTextContent('FINANCE-PRD')
    expect(active).toHaveTextContent('finance-prd')
    expect(active.querySelector('.health-badge')).toBeTruthy()
    expect(screen.getByRole('option', { name: '● FINANCE-PRD (active)' })).toBeInTheDocument()
    // The shelf of shortcut buttons is gone from a database chat, although the API still lists them.
    expect(screen.queryByText('Guided questions')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Sessions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Blocking' })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Ask anything about FINANCE-PRD' })).toBeInTheDocument()
    const specialists = within(screen.getByRole('list', { name: 'Who answers' })).getAllByRole('listitem')
    expect(specialists.map((item) => item.querySelector('strong')?.textContent)).toEqual([
      'This database', 'Oracle knowledge', 'Company policy', 'SQL draft',
    ])
    const samples = within(screen.getByRole('list', { name: 'Sample questions' })).getAllByRole('button')
    expect(samples).toHaveLength(6)
    expect(screen.getByRole('link', { name: /GraphDB/ })).toHaveAttribute('href', '#/graph/databases/target-1')
    expect(screen.getByRole('link', { name: /WaitDB/ })).toHaveAttribute('href', '#/wait/databases/target-1')

    // A sample is a hint. It fills the box and is sent only when the operator sends it.
    await user.click(samples[2])
    const box = screen.getByRole('textbox', { name: 'Ask the copilot' })
    expect(box).toHaveValue('How is memory split between the SGA and the PGA on this database?')
    expect(box).toHaveFocus()
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/messages'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Send' }))
    const verdict = await screen.findByText('Identity', { selector: '.copilot-verdict strong' })
    expect(verdict.parentElement).toHaveTextContent('Statusok')
    expect(verdict.parentElement).toHaveTextContent('Healthhealthy')
    expect(screen.queryByText(/Q02/)).not.toBeInTheDocument()
    expect(screen.getByText('Database ORCL12C open_mode READ WRITE role PRIMARY')).toBeInTheDocument()
    expect(screen.getByText('Oracle version is not part of this reading.')).toBeInTheDocument()
    const routes = within(screen.getByRole('list', { name: 'Answered by' })).getAllByRole('listitem')
    expect(routes.map((item) => item.textContent)).toEqual(['This database', 'Oracle knowledge'])
    expect(screen.getByRole('list', { name: 'Answered by' })).toHaveAttribute('title', 'Chosen by the supervisor')
    expect(screen.getByText('From the snapshot stored 5 minutes ago. Ask for the value now to read the database again.')).toBeInTheDocument()

    // After an answer the samples wait behind a toggle.
    expect(screen.queryByRole('heading', { name: 'Ask anything about FINANCE-PRD' })).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Sample questions' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Sample questions' }))
    await user.click(within(screen.getByRole('list', { name: 'Sample questions' })).getAllByRole('button')[0])
    expect(screen.getByRole('textbox', { name: 'Ask the copilot' })).toHaveValue('How full are the tablespaces, and which one will fill first?')
    expect(screen.queryByRole('list', { name: 'Sample questions' })).not.toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Database scope' }), '')
    expect(window.location.hash).toBe('#/copilot')
  })

  it('gives the fleet its own questions, finders, and a findings table by database', async () => {
    const fleetThread: ChatThread = {
      ...thread,
      guided_commands: [
        { group: 'Incidents', label: 'Databases with incidents', prompt: 'Which databases in the fleet have reported an incident?' },
        { group: 'Programs', label: 'java.exe', prompt: 'Which databases have an active session from java.exe?' },
        { group: 'Policy', label: 'Policy violations', prompt: 'Which databases are running in violation of organizational policy?' },
      ],
    }
    const fleetAnswer: ChatMessage = {
      ...answer,
      id: 'message-fleet',
      content: 'x_run.exe, java.exe: found on 1 of 2 databases read.',
      answer: {
        kind: 'fleet', summary: 'x_run.exe, java.exe: found on 1 of 2 databases read.', confidence: '',
        limitations: 'A database that could not be read is not known; it is not counted as clean.', next_steps: [], verdict: null,
        reading: [
          'FINANCE-PRD: java.exe 12 sessions (4 active); x_run.exe 2 sessions (0 active)',
          "No session from x_run.exe, java.exe: WAREHOUSE-DEV.",
        ],
        citation_note: '', evidence_lines: [], notes: [],
      },
      provider_status: 'answered',
      citations: [],
      tools: [{ tool: 'find_fleet_programs', decision: 'allowed', summary: 'found on 1 of 2', reason: '', duration_ms: 40 }],
      guided_commands: fleetThread.guided_commands,
    }
    const database = (id: string, name: string, health: string) => ({
      id, display_name: name, tns_alias: name.toLowerCase(), enabled: true, environment: 'production', engine: 'oracle',
      owner: '', poll_profile: {}, capability_snapshot: {}, last_connection_at: null, last_health_status: health,
      created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z', last_seen: null, monitoring_status: health,
      critical_incident_count: 0, trend_snapshots: [],
    })
    const asked: string[] = []
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) {
        return Promise.resolve(jsonResponse({
          items: [database('t1', 'FINANCE-PRD', 'healthy'), database('t2', 'WAREHOUSE-DEV', 'offline')],
          total: 2, page: 1, page_size: 100, environments: ['production'],
        }))
      }
      if (url.endsWith('/api/v1/chat/threads') && init?.method === 'POST') return Promise.resolve(jsonResponse(fleetThread))
      if (url.includes('/api/v1/chat/threads/thread-1/messages')) {
        asked.push((JSON.parse(String(init?.body)) as { content: string }).content)
        return Promise.resolve(eventResponse(fleetAnswer))
      }
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Fleet copilot' })).toBeInTheDocument()
    const card = await screen.findByLabelText('Fleet in scope')
    await within(card).findByText('2 databases')
    expect(card).toHaveTextContent('Healthy1')
    expect(card).toHaveTextContent('Down1')
    expect(screen.getByRole('option', { name: '● Fleet · all databases (active)' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Incidents1', 'Programs1', 'Policy1'])
    expect(screen.queryByRole('tab', { name: 'Common' })).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Example fleet questions' })).toBeInTheDocument()

    const finders = screen.getByLabelText('Fleet finders')
    await user.click(within(finders).getByRole('button', { name: 'Find programs' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter program or executable names')
    await user.type(screen.getByLabelText('Program names'), 'x_run.exe, java.exe')
    await user.click(within(finders).getByRole('button', { name: 'Find programs' }))
    expect(await screen.findByRole('heading', { name: 'Fleet findings' })).toBeInTheDocument()
    expect(asked).toEqual(["Which databases have a session from program 'x_run.exe' or 'java.exe'?"])
    const table = document.querySelector('.copilot-fleet-table') as HTMLElement
    expect(within(table).getByRole('rowheader', { name: 'FINANCE-PRD' })).toBeInTheDocument()
    expect(within(table).getByText('java.exe 12 sessions (4 active)')).toBeInTheDocument()
    expect(within(table).getByText('x_run.exe 2 sessions (0 active)')).toBeInTheDocument()
    expect(within(table).getByRole('rowheader', { name: 'No session from x_run.exe, java.exe' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Database reading' })).not.toBeInTheDocument()

    await user.clear(screen.getByLabelText('Datafile size in GB'))
    await user.type(screen.getByLabelText('Datafile size in GB'), '25')
    await user.click(within(finders).getByRole('button', { name: 'Find datafiles' }))
    await waitFor(() => expect(asked).toHaveLength(2))
    expect(asked[1]).toBe('Which databases have a tablespace with a datafile larger than 25 GB?')
  })

  it('shows the disclosure for the model selected in the portal', async () => {
    const choosable: ChatThread = {
      ...thread,
      provider: 'hosted',
      model: 'claude-sonnet-4-5',
      llm_enabled: true,
      disclosure: 'Questions and redacted evidence are sent to Anthropic.',
      model_choices: {
        allowed: true,
        choices: [
          { id: 'local', label: 'Local model', detail: 'llama3', ready: true, disclosure: 'The evidence is sent only to the configured local endpoint.' },
          { id: 'anthropic', label: 'Hosted Claude', detail: 'claude-sonnet-4-5', ready: true, disclosure: 'Questions and redacted evidence are sent to Anthropic.' },
          { id: 'openai', label: 'Hosted OpenAI', detail: 'gpt-4o', ready: false, disclosure: 'Questions and redacted evidence are sent to OpenAI.' },
        ],
      },
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] }))
      if (url.endsWith('/api/v1/chat/threads')) return Promise.resolve(jsonResponse(choosable))
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByText('The evidence is sent only to the configured local endpoint.')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Language model' })).toHaveValue('local')
    expect(screen.getByRole('status')).toHaveTextContent('Local model · llama3')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Language model' }), 'anthropic')
    expect(screen.getByText('Questions and redacted evidence are sent to Anthropic.')).toBeInTheDocument()
    expect(screen.queryByText('The evidence is sent only to the configured local endpoint.')).not.toBeInTheDocument()
  })
})
