import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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

    expect(await screen.findByRole('heading', { name: 'Copilot' })).toBeInTheDocument()
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
    expect(screen.getByText(/Ask about files/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Ask the copilot' })).toBeInTheDocument()
  })

  it('splits a model reply into answer, limitations, and next steps', async () => {
    const modelAnswer: ChatMessage = {
      ...answer,
      id: 'message-2',
      content: 'The FRA is configured and empty.\nLimitations: No backup history is stored.\nNext steps: Check the archive destination; Confirm a backup job\n\nDatabase reading:\nFRA space limit is 6.77 GB',
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
    expect(await screen.findByText('The FRA is configured and empty.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Limitations' })).toBeInTheDocument()
    expect(screen.getByText('No backup history is stored.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Next steps' })).toBeInTheDocument()
    expect(screen.getByText('Check the archive destination')).toBeInTheDocument()
    expect(screen.getByText('Confirm a backup job')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Database reading' })).toBeInTheDocument()
    expect(screen.getByText('FRA space limit is 6.77 GB')).toBeInTheDocument()
    expect(screen.getByText('Answered')).toBeInTheDocument()
  })

  it('shows the active database, offers each question once, and drops the question id from the verdict', async () => {
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

    expect(await screen.findByRole('heading', { name: 'Copilot' })).toBeInTheDocument()
    const active = await screen.findByLabelText('Active database')
    expect(active).toHaveTextContent('FINANCE-PRD')
    expect(active).toHaveTextContent('finance-prd')
    expect(active.querySelector('.health-badge')).toBeTruthy()
    expect(screen.getByRole('option', { name: '● FINANCE-PRD (active)' })).toBeInTheDocument()
    // Five offered, one repeated prompt dropped: the same prompt is never shown twice.
    expect(screen.getByText('4 available')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Common' })).toHaveTextContent('2')
    expect(screen.getByRole('tab', { name: 'Sessions' })).toHaveTextContent('1')
    await user.click(screen.getByRole('tab', { name: 'Sessions' }))
    expect(screen.getAllByRole('button', { name: 'Blocking' })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Blocking' })).toHaveAttribute('title', 'Are any sessions blocking other sessions?')
    await user.click(screen.getByRole('tab', { name: 'Common' }))
    expect(screen.getAllByRole('button', { name: 'Blocking' })).toHaveLength(1)

    await user.click(screen.getByRole('tab', { name: 'Health' }))
    await user.click(screen.getByRole('button', { name: 'Identity' }))
    const verdict = await screen.findByText('Identity', { selector: '.copilot-verdict strong' })
    expect(verdict.parentElement).toHaveTextContent('Statusok')
    expect(verdict.parentElement).toHaveTextContent('Healthhealthy')
    expect(screen.queryByText(/Q02/)).not.toBeInTheDocument()
    expect(screen.getByText('Database ORCL12C open_mode READ WRITE role PRIMARY')).toBeInTheDocument()
    expect(screen.getByText('Oracle version is not part of this reading.')).toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Database scope' }), '')
    expect(window.location.hash).toBe('#/copilot')
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
