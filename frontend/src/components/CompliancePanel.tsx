import { useEffect, useState } from 'react'
import { CircleAlert, ShieldCheck } from 'lucide-react'
import { ApiError, getCompliancePolicies, getDatabaseCompliance } from '../api'
import type { CompliancePolicyVersion, DatabaseCompliance } from '../types'
import { formatDate } from '../format'

export function CompliancePanel({ targetId, refreshKey }: { targetId: string; refreshKey: number }) {
  const [results, setResults] = useState<DatabaseCompliance | null>(null)
  const [policies, setPolicies] = useState<CompliancePolicyVersion[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([getDatabaseCompliance(targetId), getCompliancePolicies()])
      .then(([compliance, catalog]) => {
        if (!active) return
        setResults(compliance)
        setPolicies(catalog.items)
        setError('')
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason.message : 'Could not load compliance results.')
      })
    return () => { active = false }
  }, [targetId, refreshKey])

  const versions = new Map<string, string[]>()
  for (const policy of policies) {
    versions.set(policy.policy_id, [...(versions.get(policy.policy_id) ?? []), policy.version])
  }

  return (
    <section className="panel compliance-panel">
      <div className="panel-heading">
        <div><h2><ShieldCheck size={18} /> Compliance policies</h2><p>Versioned checks with collected metric evidence</p></div>
        {results && <span className="count-pill">{results.pass} pass · {results.fail} fail · {results.unknown} unknown</span>}
      </div>
      {error ? <div className="chart-error" role="alert"><CircleAlert size={16} /><span>{error}</span></div> : !results ? (
        <div className="chart-loading" role="status"><span className="spinner" /> Loading compliance results…</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table compliance-table">
            <thead><tr><th scope="col">Policy</th><th scope="col">Version history</th><th scope="col">Result</th><th scope="col">Evidence</th><th scope="col">Last checked</th><th scope="col">Recommended action</th></tr></thead>
            <tbody>{results.items.map((item) => (
              <tr key={item.policy_id}>
                <td className="primary-cell">{item.name}<small>{item.policy_id}</small></td>
                <td>{(versions.get(item.policy_id) ?? [item.policy_version]).join(', ')}</td>
                <td><span className={`policy-result policy-${item.result}`}>{item.result.replaceAll('_', ' ')}</span><small>{item.severity}</small></td>
                <td><code>{Object.keys(item.evidence).length ? JSON.stringify(item.evidence) : 'No fresh metric evidence'}</code></td>
                <td className="muted">{item.observed_at ? formatDate(item.observed_at) : 'Not evaluated'}</td>
                <td>{item.recommended_action}</td>
              </tr>
            ))}</tbody>
          </table>
          {results.not_applicable > 0 && <p className="empty-inline">{results.not_applicable} policy checks are not applicable; {results.error} returned evaluator errors.</p>}
        </div>
      )}
    </section>
  )
}
