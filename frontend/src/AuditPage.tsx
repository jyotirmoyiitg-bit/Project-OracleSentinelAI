import { useEffect, useState } from 'react'
import { ApiError, listAuditEvents, type AuditEventItem } from './api'

export function AuditPage({
  onRefreshStatus,
}: {
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [items, setItems] = useState<AuditEventItem[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    const load = () => {
      setError('')
      listAuditEvents().then((result) => setItems(result.items)).catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason.message : 'Could not load the audit trail.')
      })
    }
    onRefreshStatus(false, load)
    load()
  }, [onRefreshStatus])

  return (
    <main className="page-shell">
      <p className="eyebrow">AUDIT</p>
      <h1>Audit trail</h1>
      <p>These records are append-only. The portal cannot change or delete them.</p>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <table className="audit-table">
        <thead>
          <tr><th>When</th><th>Who</th><th>Action</th><th>Outcome</th><th>Digest</th></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.occurred_at}</td>
              <td>{item.principal}</td>
              <td>{item.action}</td>
              <td>{item.outcome}</td>
              <td><code>{item.evidence_digest.slice(0, 12)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  )
}
