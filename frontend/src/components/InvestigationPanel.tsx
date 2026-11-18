import { useCallback, useEffect, useState } from 'react'
import { CircleAlert, Search } from 'lucide-react'
import {
  ApiError, getAgentStatus, listAgentRuns, startInvestigation, type AgentFinding, type AgentStatus,
} from '../api'
import { FindingCard, RunTimeline, useAgentRun } from './AgentRunView'

/**
 * Investigate one incident or one database with the Investigator agent, and show the newest run.
 * Renders nothing when the agent routes cannot be reached, so an older API still shows the page.
 */
export function InvestigationPanel({ incidentId, targetId }: { incidentId?: string; targetId?: string }) {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [decided, setDecided] = useState<AgentFinding | null>(null)

  const reloadStatus = useCallback(() => {
    getAgentStatus().then(setStatus).catch(() => undefined)
  }, [])
  const { run, error: runError } = useAgentRun(runId, reloadStatus)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getAgentStatus(controller.signal),
      listAgentRuns({ incidentId, targetId: incidentId ? undefined : targetId, limit: 5 }, controller.signal),
    ]).then(([agentStatus, runs]) => {
      // An API without the agent routes may answer with something else. Show the panel only for a real answer.
      if (typeof agentStatus?.enabled !== 'boolean' || !Array.isArray(runs?.items)) return
      setStatus(agentStatus)
      const newest = runs.items.find((item) => item.agent === 'investigator')
      if (newest) setRunId(newest.id)
      setLoaded(true)
    }).catch(() => undefined)
    return () => controller.abort()
  }, [incidentId, targetId])

  async function investigate() {
    setBusy(true)
    setError('')
    setDecided(null)
    try {
      const started = await startInvestigation({ incidentId, targetId })
      setRunId(started.id)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not start the investigation.')
    } finally {
      setBusy(false)
    }
  }

  if (!loaded || !status) return null
  const working = run?.status === 'queued' || run?.status === 'running'
  const finding = decided && run?.finding && decided.id === run.finding.id ? decided : run?.finding ?? null
  const subject = incidentId ? 'this incident' : 'this database'
  return (
    <section className="panel agent-panel" aria-labelledby="investigation-title">
      <div className="panel-heading">
        <div>
          <h2 id="investigation-title"><Search size={18} /> Investigation</h2>
          <p>
            {status.model_writes_findings
              ? 'The Investigator collects evidence, compares it with stored data, and the local model writes the likely cause.'
              : 'The Investigator collects evidence and compares it with stored data. The model is off, so no cause is written.'}
          </p>
        </div>
        {status.can_run && (
          <button className="button button-primary" type="button" disabled={busy || working} onClick={() => { void investigate() }}>
            <Search size={14} /> {run ? 'Investigate again' : 'Investigate'}
          </button>
        )}
      </div>
      {(error || runError) && <div className="inline-error agent-inline" role="alert"><CircleAlert size={16} /> {error || runError}</div>}
      {!status.enabled && <p className="empty-inline">Agents are turned off. An administrator can turn them on in Admin, Agents.</p>}
      {status.enabled && !status.can_run && !run && <p className="empty-inline">Your role can read investigations but cannot start one.</p>}
      {status.enabled && status.can_run && !run && !runId && (
        <p className="empty-inline">No investigation has been run for {subject}. It reads the database with the monitoring account and changes nothing.</p>
      )}
      {run && (
        <div className="agent-panel-body">
          <RunTimeline run={run} />
          {finding && <FindingCard finding={finding} canDecide={status.can_run} onDecided={setDecided} />}
        </div>
      )}
    </section>
  )
}
