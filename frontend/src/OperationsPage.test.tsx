import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationsPage } from './OperationsPage'

const proposal = {
  id: 'proposal-1',
  requester: 'ada',
  requester_role: 'dba',
  action: 'tablespace.add_omf_datafile',
  target_id: 'db-1',
  parameters: { tablespace: 'USERS', size_mb: 10240, request_text: 'Extend USERS by 10 GB' },
  preview_sql: 'ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M',
  impact: 'Adds one Oracle-managed datafile of 10240 MB to USERS. Release 1 does not run this statement.',
  risk: 'medium',
  status: 'pending',
  expires_at: '2026-11-13T00:00:00',
  approver: '',
  decision_reason: '',
  execution_allowed: false,
  execution_log: [],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Operations page', () => {
  it('shows a pending proposal and does not offer execution', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/operations/proposals')) return Promise.resolve(json({ items: [proposal] }))
      if (url.includes('/databases')) return Promise.resolve(json({ items: [{ id: 'db-1', display_name: 'PDBORCL' }], total: 1, page: 1, page_size: 100, environments: [] }))
      return Promise.resolve(json({ detail: 'not found' }, 404))
    }))
    render(<OperationsPage role="admin" principal="sentinel" onRefreshStatus={() => undefined} />)
    expect(await screen.findByText('Extend USERS by 10 GB')).toBeInTheDocument()
    expect(screen.getByText(/does not run it/)).toBeInTheDocument()
    expect(screen.getByText('ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M')).toBeInTheDocument()
    expect(screen.getByText(/not enabled/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByText(/Privileged operations are disabled in Release 1/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Execute' })).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Approve' }))
  })
})

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
