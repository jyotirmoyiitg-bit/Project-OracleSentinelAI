import { useEffect, useState } from 'react'
import {
  BookOpen, CheckCircle2, CircleAlert, ClipboardList, Database, FileSearch, GitCompareArrows, ListChecks,
  ShieldCheck, Sparkles,
} from 'lucide-react'
import { ApiError, decideFinding, getAgentRun, type AgentFinding, type AgentRun, type AgentStep } from '../api'
import { formatDate } from '../format'

const STEP_ICONS: Record<string, typeof Database> = {
  plan: ListChecks,
  collect: Database,
  compare: GitCompareArrows,
  lookup: BookOpen,
  conclude: Sparkles,
  verify: ShieldCheck,
  finding: ClipboardList,
}
const STATUS_TEXT: Record<AgentRun['status'], string> = {
  queued: 'Waiting to start',
  running: 'Working',
  completed: 'Completed',
  failed: 'Failed',
  stopped: 'Stopped',
}
const OUTCOME_TEXT: Record<string, string> = { ok: 'Done', failed: 'Failed', skipped: 'Not needed', denied: 'Refused' }

function isActive(run: AgentRun | null): boolean {
  return run?.status === 'queued' || run?.status === 'running'
}

/** Load one run and keep reloading it while it is working. */
export function useAgentRun(runId: string | null, onFinished?: () => void) {
  const [run, setRun] = useState<AgentRun | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!runId) return undefined
    let stopped = false
    let timer: number | undefined
    const load = () => {
      getAgentRun(runId).then((result) => {
        if (stopped) return
        setRun(result)
        setError('')
        if (isActive(result)) timer = window.setTimeout(load, 1500)
        else onFinished?.()
      }).catch((reason: unknown) => {
        if (!stopped) setError(reason instanceof ApiError ? reason.message : 'Could not load the run.')
      })
    }
    load()
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
    // onFinished is a fresh function on each render; the run id alone decides when to reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  return { run: run && run.id === runId ? run : null, error }
}

export function RunStatus({ run }: { run: AgentRun }) {
  return (
    <span className={`agent-status agent-status-${run.status}`}>
      {isActive(run) && <span className="spinner" aria-hidden="true" />}
      {STATUS_TEXT[run.status]}
    </span>
  )
}

function StepRow({ step }: { step: AgentStep }) {
  const Icon = STEP_ICONS[step.kind] ?? FileSearch
  return (
    <li className={`agent-step agent-step-${step.outcome}`}>
      <span className="agent-step-icon" aria-hidden="true"><Icon size={15} /></span>
      <div className="agent-step-body">
        <div className="agent-step-head">
          <strong>{step.title}</strong>
          <span className="agent-step-outcome">{OUTCOME_TEXT[step.outcome] ?? step.outcome}</span>
          {step.duration_ms >= 100 && <span className="agent-step-time">{(step.duration_ms / 1000).toFixed(1)} s</span>}
        </div>
        {step.lines.length > 0 && (
          <ul className="agent-step-lines">
            {step.lines.map((line, index) => <li key={`${step.seq}-${index}`}>{line}</li>)}
          </ul>
        )}
        {step.restricted_hidden && <p className="agent-note">Some lines are shown to an administrator only.</p>}
      </div>
    </li>
  )
}

export function RunTimeline({ run }: { run: AgentRun }) {
  const steps = run.steps ?? []
  return (
    <div className="agent-run" aria-label="Agent run">
      <div className="agent-run-head">
        <RunStatus run={run} />
        <span>{run.agent === 'collector' ? 'Collector' : 'Investigator'}</span>
        {run.target_name && <span>{run.target_name}</span>}
        <span>Started by {run.trigger === 'incident' ? 'a new critical incident' : run.principal || 'an operator'}</span>
        {run.created_at && <span>{formatDate(run.created_at)}</span>}
        {run.llm_used && <span>Written with {run.model || 'the local model'}</span>}
      </div>
      {run.note && <p className="agent-note" role="status">{run.note}</p>}
      <ol className="agent-steps" aria-label="Run steps">
        {steps.map((step) => <StepRow key={step.seq} step={step} />)}
        {isActive(run) && <li className="agent-step agent-step-pending"><span className="spinner" aria-hidden="true" /> Working on the next step…</li>}
      </ol>
    </div>
  )
}

export function FindingCard({
  finding,
  canDecide,
  onDecided,
}: {
  finding: AgentFinding
  canDecide: boolean
  onDecided?: (finding: AgentFinding) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function decide(decision: 'accepted' | 'dismissed') {
    setBusy(true)
    setError('')
    try {
      onDecided?.(await decideFinding(finding.id, decision))
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not save the decision.')
    } finally {
      setBusy(false)
    }
  }

  const writer = finding.written_by === 'model' ? 'Cause written by the model' : 'Evidence only'
  return (
    <article className={`agent-finding agent-finding-${finding.decision}`} aria-label={`Finding: ${finding.title}`}>
      <header className="agent-finding-head">
        <h3>{finding.title}</h3>
        <div className="agent-chips">
          <span className={`agent-chip agent-chip-${finding.severity}`}>{finding.severity}</span>
          <span className="agent-chip">{writer}</span>
          {finding.written_by === 'model' && <span className="agent-chip">Confidence: {finding.confidence}</span>}
          {finding.verified
            ? <span className="agent-chip agent-chip-ok"><CheckCircle2 size={12} /> Checked against the evidence</span>
            : <span className="agent-chip agent-chip-warn"><CircleAlert size={12} /> Conclusion rejected</span>}
          {finding.restricted && <span className="agent-chip">Administrator only</span>}
        </div>
      </header>
      <p className="agent-finding-summary">{finding.summary}</p>
      {!finding.verified && finding.verifier_note && <p className="agent-note">{finding.verifier_note}</p>}
      {finding.next_steps.length > 0 && (
        <div className="agent-finding-block">
          <h4>Suggested next steps</h4>
          <ul>{finding.next_steps.map((step) => <li key={step}>{step}</li>)}</ul>
        </div>
      )}
      {finding.evidence.length > 0 && (
        <details className="agent-finding-block">
          <summary>Evidence ({finding.evidence.length})</summary>
          <ul className="agent-evidence">
            {finding.evidence.map((item, index) => (
              <li key={`${item.ref}-${index}`}><span>{item.source}</span>{item.text}</li>
            ))}
          </ul>
        </details>
      )}
      {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
      <footer className="agent-finding-foot">
        {finding.decision === 'open' ? (
          canDecide && !finding.hidden ? (
            <>
              <button className="button button-primary" type="button" disabled={busy} onClick={() => { void decide('accepted') }}>Accept</button>
              <button className="button button-secondary" type="button" disabled={busy} onClick={() => { void decide('dismissed') }}>Dismiss</button>
            </>
          ) : <span className="agent-note">Waiting for a decision.</span>
        ) : (
          <span className="agent-note">
            {finding.decision === 'accepted' ? 'Accepted' : 'Dismissed'} by {finding.decided_by || 'an operator'}
            {finding.decided_at ? `, ${formatDate(finding.decided_at)}` : ''}
          </span>
        )}
      </footer>
    </article>
  )
}
