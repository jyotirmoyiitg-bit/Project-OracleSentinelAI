import { useCallback, useEffect, useState } from 'react'
import { ApiError, createProposal, decideProposal, getFleet, listProposals, type OperationProposal } from './api'
import { PrivilegedNotice } from './features/privileged/PrivilegedNotice'

const CAN_CREATE = new Set(['dba', 'admin', 'platform_admin'])
const CAN_DECIDE = CAN_CREATE

export function OperationsPage({
  role,
  principal,
  onRefreshStatus,
}: {
  role: string
  principal: string
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [items, setItems] = useState<OperationProposal[]>([])
  const [databases, setDatabases] = useState<{ id: string; display_name: string }[]>([])
  const [targetId, setTargetId] = useState('')
  const [requestText, setRequestText] = useState('Extend USERS by 10 GB')
  const [reason, setReason] = useState('Reviewed for the change window')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [proposals, fleet] = await Promise.all([
        listProposals(),
        getFleet({ page: 1, pageSize: 100, environment: '', healthStatus: '' }),
      ])
      setItems(proposals.items)
      setDatabases(fleet.items.map((item) => ({ id: item.id, display_name: item.display_name })))
      setTargetId((current) => current || fleet.items[0]?.id || '')
    } catch (reasonCaught) {
      setError(reasonCaught instanceof ApiError ? reasonCaught.message : 'Could not load proposals.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    onRefreshStatus(false, () => { void refresh() })
    void refresh()
  }, [onRefreshStatus, refresh])

  async function submit() {
    setError('')
    try {
      await createProposal(targetId, requestText)
      setRequestText('Extend USERS by 10 GB')
      await refresh()
    } catch (reasonCaught) {
      setError(reasonCaught instanceof ApiError ? reasonCaught.message : 'The proposal was not accepted.')
    }
  }

  async function decide(id: string, approved: boolean) {
    setError('')
    try {
      await decideProposal(id, approved, reason)
      await refresh()
    } catch (reasonCaught) {
      setError(reasonCaught instanceof ApiError ? reasonCaught.message : 'The decision was not accepted.')
    }
  }

  return (
    <main className="page-shell">
      <p className="eyebrow">OPERATIONS</p>
      <h1>Proposals</h1>
      <p className="proposal-banner">Release 1 stores the change and does not run it. An approved proposal still does not change the database.</p>
      <PrivilegedNotice />
      {CAN_CREATE.has(role) && (
        <form className="proposal-form" onSubmit={(event) => { event.preventDefault(); void submit() }}>
          <label>
            Database
            <select aria-label="Database" value={targetId} onChange={(event) => setTargetId(event.target.value)}>
              {databases.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
            </select>
          </label>
          <label>
            Request
            <input aria-label="Proposal request" value={requestText} onChange={(event) => setRequestText(event.target.value)} />
          </label>
          <button className="button button-primary" type="submit">Submit proposal</button>
        </form>
      )}
      {error && <div className="inline-error" role="alert">{error}</div>}
      {loading ? <p>Loading proposals…</p> : (
        <ul className="proposal-list">
          {items.length === 0 && <li>No proposals yet.</li>}
          {items.map((item) => (
            <li key={item.id} className="proposal-card">
              <div className="proposal-card-head">
                <strong>{String(item.parameters.request_text || item.action)}</strong>
                <span className={`proposal-status proposal-status-${item.status}`}>{item.status}</span>
              </div>
              <p>{item.impact}</p>
              <pre>{item.preview_sql}</pre>
              <p className="proposal-meta">Requested by {item.requester}. Execution is {item.execution_allowed ? 'enabled' : 'not enabled'}.</p>
              {CAN_DECIDE.has(role) && item.status === 'pending' && item.requester !== principal && (
                <div className="proposal-actions">
                  <label>
                    Decision note
                    <input aria-label="Decision note" value={reason} onChange={(event) => setReason(event.target.value)} />
                  </label>
                  <button className="button button-primary" type="button" onClick={() => { void decide(item.id, true) }}>Approve</button>
                  <button className="button" type="button" onClick={() => { void decide(item.id, false) }}>Reject</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}
