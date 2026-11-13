import { cleanup, render, screen } from '@testing-library/react'
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
})

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
