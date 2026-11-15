import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminPage } from './AdminPage'

const platform = {
  scheduler_enabled: false,
  knowledge_enabled: false,
  llm: { provider: 'disabled', model: '', hosted_vendor: 'openai', secret_configured: false },
  notifications: { smtp_enabled: false, smtp_host_configured: false, smtp_from: '', smtp_secret_configured: false },
  roles: ['platform_admin'],
  legacy_roles: { admin: 'platform_admin', auditor: 'security_auditor' },
  retention_days: { observations: 30 },
  proposal_expiry_hours: 24,
  rules: [],
  execution_allowed: false,
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Administration page', () => {
  it('saves a stored setting for an administrator', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/admin/platform')) return Promise.resolve(json(platform))
      if (url.endsWith('/admin/operators')) return Promise.resolve(json({ items: [] }))
      if (url.endsWith('/admin/config') && (!init || init.method === undefined)) {
        return Promise.resolve(json({ items: [{ key: 'log_level', value: 'INFO', updated_at: '2026-11-13T00:00:00', section: 'Logging', editable: true }] }))
      }
      if (url.endsWith('/admin/config/log_level') && init?.method === 'PUT') {
        return Promise.resolve(json({ key: 'log_level', value: 'DEBUG', updated_at: '2026-11-13T01:00:00' }))
      }
      return Promise.resolve(json({ detail: 'not found' }, 404))
    }))
    render(<AdminPage onOpenRegistry={() => undefined} onRefreshStatus={() => undefined} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Show' }))
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const field = screen.getByRole('textbox', { name: 'log_level' })
    await user.clear(field)
    await user.type(field, 'DEBUG')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Logging saved.')
  })

  it('adds an operator, changes a role, disables an account, and resets a password', async () => {
    const accounts = [
      { username: 'sentinel', role: 'admin', enabled: true, created_at: '2026-11-13T00:00:00' },
      { username: 'olga', role: 'operator', enabled: true, created_at: '2026-11-13T00:00:00' },
    ]
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/admin/platform')) return Promise.resolve(json(platform))
      if (url.endsWith('/admin/config')) return Promise.resolve(json({ items: [] }))
      if (url.endsWith('/admin/operators') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { username: string; password: string; role: string }
        calls.push({ url, body })
        if (body.password.length < 10) return Promise.resolve(json({ detail: 'The password must be at least 10 characters.' }, 422))
        accounts.push({ username: body.username.toLowerCase(), role: body.role, enabled: true, created_at: '2026-11-14T00:00:00' })
        return Promise.resolve(json(accounts[accounts.length - 1], 201))
      }
      if (url.endsWith('/admin/operators')) return Promise.resolve(json({ items: accounts }))
      const match = url.match(/\/admin\/operators\/([^/]+)\/(role|enabled|password)$/)
      if (match && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        calls.push({ url, body })
        const account = accounts.find((item) => item.username === decodeURIComponent(match[1]))
        if (account && match[2] === 'role') account.role = String(body.role)
        if (account && match[2] === 'enabled') account.enabled = Boolean(body.enabled)
        return Promise.resolve(json({ username: match[1], updated: true }))
      }
      return Promise.resolve(json({ detail: 'not found' }, 404))
    }))
    render(<AdminPage onOpenRegistry={() => undefined} onRefreshStatus={() => undefined} currentUser="sentinel" />)
    const user = userEvent.setup()

    expect(await screen.findByRole('heading', { name: 'Operators' })).toBeInTheDocument()
    expect(await screen.findByText('olga')).toBeInTheDocument()
    expect(screen.getByText('You')).toBeInTheDocument()
    expect(screen.queryByLabelText('Role for sentinel')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('New operator username'), 'Dana')
    await user.type(screen.getByLabelText('New operator password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Add operator' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The password must be at least 10 characters.')
    await user.clear(screen.getByLabelText('New operator password'))
    await user.type(screen.getByLabelText('New operator password'), 'Operator-Pass-1')
    await user.selectOptions(screen.getByLabelText('New operator role'), 'dba')
    await user.click(screen.getByRole('button', { name: 'Add operator' }))
    expect(await screen.findByText('dana was added as DBA.')).toBeInTheDocument()
    expect(await screen.findByLabelText('Role for dana')).toHaveValue('dba')

    await user.selectOptions(screen.getByLabelText('Role for olga'), 'viewer')
    expect(await screen.findByText('olga is now Viewer.')).toBeInTheDocument()

    const olgaRow = screen.getByText('olga').closest('tr') as HTMLElement
    await user.click(within(olgaRow).getByRole('button', { name: 'Disable' }))
    expect(await screen.findByText('olga was disabled and signed out.')).toBeInTheDocument()
    expect(await within(screen.getByText('olga').closest('tr') as HTMLElement).findByRole('button', { name: 'Enable' })).toBeInTheDocument()

    await user.click(within(screen.getByText('olga').closest('tr') as HTMLElement).getByRole('button', { name: 'Reset password' }))
    await user.type(screen.getByLabelText('New password for olga'), 'Operator-Pass-2')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/The password for olga was reset/)).toBeInTheDocument()

    expect(calls.map((call) => call.url.replace('/api/v1/admin', ''))).toEqual([
      '/operators', '/operators', '/operators/olga/role', '/operators/olga/enabled', '/operators/olga/password',
    ])
    expect(calls[2].body).toEqual({ role: 'viewer' })
    expect(calls[3].body).toEqual({ enabled: false })
  })
})

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
