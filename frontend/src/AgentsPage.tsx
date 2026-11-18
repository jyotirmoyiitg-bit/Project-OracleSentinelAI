import { useCallback, useEffect, useState } from 'react'
import { Bot, Camera, CircleAlert } from 'lucide-react'
import {
  ApiError, collectFleetSnapshot, getAgentStatus, listAgentFindings, listAgentRuns,
  type AgentFinding, type AgentRun, type AgentStatus,
} from './api'
import { FindingCard, RunStatus, RunTimeline, useAgentRun } from './components/AgentRunView'
import { formatDate } from './format'

const AGENT_NAMES: Record<string, string> = { collector: 'Collector', investigator: 'Investigator' }
const TRIGGER_NAMES: Record<string, string> = { operator: 'Operator', incident: 'New incident', schedule: 'Timer' }

export function AgentsPage({
  onOpenIncident,
  onOpenTarget,
  onRefreshStatus,
}: {
  onOpenIncident: (id: string) => void
  onOpenTarget: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [findings, setFindings] = useState<AgentFinding[]>([])
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    Promise.all([getAgentStatus(), listAgentFindings({ decision: 'open' }), listAgentRuns({ limit: 30 })])
      .then(([agentStatus, openFindings, recentRuns]) => {
        setStatus(agentStatus)
        setFindings(openFindings.items)
        setRuns(recentRuns.items)
        setError('')
      })
      .catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason.message : 'Could not load the agents.')
      })
  }, [])

  useEffect(() => {
    onRefreshStatus(false, load)
    load()
  }, [load, onRefreshStatus])

  const { run } = useAgentRun(selected, load)

  async function collectFleet() {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      await collectFleetSnapshot()
      setMessage('The fleet snapshot has started. It appears under Recent runs when it finishes.')
      window.setTimeout(load, 4000)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not start the fleet snapshot.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page-shell agents-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">AGENTS</div>
          <h1>Agents</h1>
          <p>
            Agents collect evidence and investigate incidents. They read each database with the monitoring account
            and never change it.
          </p>
        </div>
        {status?.can_run && (
          <button className="button button-primary" type="button" disabled={busy} onClick={() => { void collectFleet() }}>
            <Camera size={14} /> Collect fleet snapshot
          </button>
        )}
      </section>
      {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
      {message && <div className="inline-success" role="status">{message}</div>}
      {status && (
        <p className="agents-state" role="status">
          <Bot size={15} />
          {status.enabled
            ? status.model_writes_findings
              ? `Agents are on. The local model ${status.model} writes the likely cause, and each conclusion is checked against its evidence.`
              : 'Agents are on. The model is off, so findings show the evidence without a written cause.'
            : 'Agents are turned off. An administrator can turn them on in Admin, Agents.'}
        </p>
      )}

      <section className="panel agent-panel" aria-labelledby="open-findings-title">
        <div className="panel-heading">
          <div><h2 id="open-findings-title">Open findings <span className="count-pill">{findings.length}</span></h2><p>Conclusions waiting for an operator to accept or dismiss</p></div>
        </div>
        {findings.length === 0 ? <p className="empty-inline">No finding is waiting for a decision.</p> : (
          <div className="agent-panel-body">
            {findings.map((finding) => (
              <div key={finding.id} className="agent-finding-wrap">
                <div className="agent-finding-links">
                  {finding.target_id && <button className="agent-link" type="button" onClick={() => onOpenTarget(finding.target_id as string)}>{finding.target_name || 'Database'}</button>}
                  {finding.incident_id && <button className="agent-link" type="button" onClick={() => onOpenIncident(finding.incident_id as string)}>Open the incident</button>}
                  <button className="agent-link" type="button" onClick={() => setSelected(finding.run_id)}>Show the run</button>
                </div>
                <FindingCard finding={finding} canDecide={Boolean(status?.can_run)} onDecided={load} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel agent-panel" aria-labelledby="recent-runs-title">
        <div className="panel-heading">
          <div><h2 id="recent-runs-title">Recent runs <span className="count-pill">{runs.length}</span></h2><p>Choose a run to see each step it took</p></div>
        </div>
        {runs.length === 0 ? <p className="empty-inline">No agent has run yet.</p> : (
          <div className="table-scroll">
            <table className="data-table agent-runs-table">
              <thead><tr><th>Started</th><th>Agent</th><th>Database</th><th>Started by</th><th>Goal</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {runs.map((item) => (
                  <tr key={item.id} className={item.id === selected ? 'agent-run-selected' : undefined}>
                    <td>{formatDate(item.created_at)}</td>
                    <td>{AGENT_NAMES[item.agent] ?? item.agent}</td>
                    <td>{item.target_name || (item.scope_type === 'fleet' ? 'Fleet' : '')}</td>
                    <td>{TRIGGER_NAMES[item.trigger] ?? item.trigger}{item.trigger === 'operator' && item.principal ? ` (${item.principal})` : ''}</td>
                    <td className="agent-goal">{item.goal}</td>
                    <td><RunStatus run={item} /></td>
                    <td><button className="agent-link" type="button" aria-label={`Show run steps for ${item.goal}`} onClick={() => setSelected(item.id)}>Steps</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {run && (
          <div className="agent-panel-body">
            <RunTimeline run={run} />
            {run.finding && <FindingCard finding={run.finding} canDecide={Boolean(status?.can_run)} onDecided={load} />}
          </div>
        )}
      </section>
    </main>
  )
}
