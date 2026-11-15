import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type Handler = (url: string, init?: RequestInit) => Response | undefined

function stubApi(session: Record<string, unknown>, handler?: Handler) {
  const calls: { url: string; init?: RequestInit }[] = []
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    const custom = handler?.(url, init)
    if (custom) return Promise.resolve(custom)
    if (url.includes('/api/v1/auth/session')) return Promise.resolve(json(session))
    if (url.includes('/portal-notifications')) return Promise.resolve(json({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
    if (url.endsWith('/databases/summary')) return Promise.resolve(json({ total: 0, healthy: 0, warning: 0, critical: 0, offline: 0, unknown: 0, disabled: 0, scheduler_enabled: true }))
    if (url.includes('/databases?')) return Promise.resolve(json({ items: [], total: 0, page: 1, page_size: 25, environments: [] }))
    if (url.includes('/knowledge/documents')) return Promise.resolve(json({ items: [], total: 0, page: 1, page_size: 25 }))
    return Promise.resolve(json({ detail: 'Not found' }, 404))
  })
  vi.stubGlobal('fetch', fetch)
  return calls
}

const signedOut = { required: true, authenticated: false, principal: '', role: '', method: '' }
const admin = { required: true, authenticated: true, principal: 'sentinel', role: 'admin', method: 'local' }

beforeEach(() => {
  window.location.hash = ''
  window.localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Phase 20 sign-in', () => {
  it('signs in with a local account when no directory is configured', async () => {
    const calls = stubApi({ ...signedOut, directory_enabled: false }, (url, init) => {
      if (url.endsWith('/auth/login') && init?.method === 'POST') return json(admin)
      return undefined
    })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Account type' })).not.toBeInTheDocument()
    expect(screen.getByText('Sign in with a local user id and password.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Username'), 'sentinel')
    await user.type(screen.getByLabelText('Password'), 'a-password-1')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Database fleet' })).toBeInTheDocument()
    const login = calls.find((call) => call.url.endsWith('/auth/login'))
    expect(JSON.parse(String(login?.init?.body))).toEqual({ username: 'sentinel', password: 'a-password-1', local: true })
    expect(screen.getByRole('status', { name: 'Signed in as sentinel, Admin' })).toBeInTheDocument()
  })

  it('offers the directory first when one is configured and reports a lockout', async () => {
    const calls = stubApi({ ...signedOut, directory_enabled: true }, (url, init) => {
      if (url.endsWith('/auth/login') && init?.method === 'POST') return json({ detail: 'Too many failed sign-ins. Try again later.' }, 429)
      return undefined
    })
    const user = userEvent.setup()
    render(<App />)
    const group = await screen.findByRole('radiogroup', { name: 'Account type' })
    expect(screen.getByRole('radio', { name: 'Directory account' })).toHaveAttribute('aria-checked', 'true')
    expect(group).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Local account' }))
    expect(screen.getByRole('radio', { name: 'Local account' })).toHaveAttribute('aria-checked', 'true')
    await user.type(screen.getByLabelText('Username'), 'olga')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many failed sign-ins. Try again later.')
    const login = calls.find((call) => call.url.endsWith('/auth/login'))
    expect(JSON.parse(String(login?.init?.body)).local).toBe(true)
  })

  it('reports an unreachable API instead of opening the portal', async () => {
    let down = true
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (down) return Promise.reject(new TypeError('Failed to fetch'))
      if (url.includes('/api/v1/auth/session')) return Promise.resolve(json(signedOut))
      return Promise.resolve(json({ detail: 'Not found' }, 404))
    }))
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Oracle Sentinel is not reachable')
    expect(screen.queryByRole('heading', { name: 'Database fleet' })).not.toBeInTheDocument()
    down = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('lets a local operator change their own password', async () => {
    const calls = stubApi(admin, (url, init) => {
      if (url.endsWith('/auth/password') && init?.method === 'POST') return json({ updated: true })
      return undefined
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const dialog = screen.getByRole('dialog', { name: 'Change password' })
    expect(dialog).toBeInTheDocument()
    await user.type(screen.getByLabelText('Current password'), 'Old-Password-1')
    await user.type(screen.getByLabelText('New password'), 'New-Password-2')
    await user.type(screen.getByLabelText('Repeat new password'), 'New-Password-3')
    await user.click(within(dialog).getByRole('button', { name: 'Change password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match')
    await user.clear(screen.getByLabelText('Repeat new password'))
    await user.type(screen.getByLabelText('Repeat new password'), 'New-Password-2')
    await user.click(within(dialog).getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText(/Your password was changed/)).toBeInTheDocument()
    const change = calls.find((call) => call.url.endsWith('/auth/password'))
    expect(JSON.parse(String(change?.init?.body))).toEqual({ current_password: 'Old-Password-1', new_password: 'New-Password-2' })
  })
})

describe('Phase 20 knowledge upload', () => {
  it('uploads a document as an administrator and shows the intake result', async () => {
    window.location.hash = '#/knowledge'
    const calls = stubApi(admin, (url, init) => {
      if (url.includes('/knowledge/upload') && init?.method === 'POST') {
        return json({
          relative_path: 'Policy/backup-policy.md', category: 'Policy', size_bytes: 120, replaced: false,
          scanned: 1, indexed: 1, updated: 0, unchanged: 0, quarantined: 0, errors: [], note: 'Saved and indexed.',
        }, 201)
      }
      return undefined
    })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Add a document' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reindex folders' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload and index' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a PDF, DOCX, TXT, or Markdown file.')
    const file = new File(['# Backup policy\n\nEvery production database is backed up nightly.'], 'backup-policy.md', { type: 'text/markdown' })
    await user.upload(screen.getByLabelText('Document file'), file)
    await user.selectOptions(screen.getByLabelText('Upload category'), 'Policy')
    await user.click(screen.getByRole('button', { name: 'Upload and index' }))
    expect(await screen.findByText('Policy/backup-policy.md: Saved and indexed.')).toBeInTheDocument()
    const upload = calls.find((call) => call.url.includes('/knowledge/upload'))
    expect(upload?.url).toContain('filename=backup-policy.md')
    expect(upload?.url).toContain('category=Policy')
    expect(upload?.url).toContain('replace=false')
    expect(upload?.init?.body).toBe(file)
    expect((upload?.init?.headers as Record<string, string>)['Content-Type']).toBe('application/octet-stream')
  })

  it('does not offer upload or reindex to an operator', async () => {
    window.location.hash = '#/knowledge'
    stubApi({ ...admin, principal: 'olga', role: 'operator' })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Knowledge' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('heading', { name: /Approved documents/ })).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Add a document' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reindex folders' })).not.toBeInTheDocument()
  })
})
