import { useEffect, useMemo, useState } from 'react'
import { Copy, FileCode2, GitBranch, RefreshCw } from 'lucide-react'
import { ApiError, getSessionStatement, type PlanStep, type SessionStatement } from '../api'
import { formatSql } from '../sqlFormat'

export const STATEMENT_NOTE = 'The statement of a session and its plan are shown to an administrator.'

export interface ChosenSession {
  sid: number
  serial: number
  program: string
  status: string
}

type View = 'statement' | 'plan'

function number(value: number | null): string {
  return value == null ? '—' : value.toLocaleString('en-US')
}

function operation(step: PlanStep): string {
  return [step.operation, step.options].filter(Boolean).join(' ')
}

function PlanTable({ statement }: { statement: SessionStatement }) {
  if (statement.plan.length === 0) {
    return <p className="empty-inline">{statement.plan_note || statement.text_note || 'No plan is cached for this statement.'}</p>
  }
  return (
    <div className="table-scroll">
      <table className="data-table section-table plan-table" aria-label="Cached explain plan">
        <thead>
          <tr>
            <th scope="col" className="section-number">Id</th>
            <th scope="col">Operation</th>
            <th scope="col">Object</th>
            <th scope="col">Optimizer</th>
            <th scope="col" className="section-number">Cost</th>
            <th scope="col" className="section-number">Rows</th>
            <th scope="col" className="section-number">Bytes</th>
          </tr>
        </thead>
        <tbody>
          {statement.plan.map((step) => (
            <tr key={step.id} className={step.options === 'FULL' ? 'plan-full' : undefined}>
              <td className="section-number">{step.id}</td>
              <td>
                <span className="plan-operation" style={{ paddingLeft: `${step.depth * 18}px` }}>
                  {step.depth > 0 && <span className="plan-branch" aria-hidden="true">└</span>}
                  <strong>{operation(step)}</strong>
                </span>
              </td>
              <td className="plan-object">{step.object_name ? `${step.object_owner ? `${step.object_owner}.` : ''}${step.object_name}` : ''}</td>
              <td>{step.optimizer}</td>
              <td className="section-number">{number(step.cost)}</td>
              <td className="section-number">{number(step.cardinality)}</td>
              <td className="section-number">{number(step.bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The statement a chosen session runs, or ran last when it is idle, and the plan Oracle cached for it.
 * Read again when the session list refreshes. Nothing is explained again and nothing is run.
 */
export function SessionStatementPanel({ databaseId, chosen, detail, refreshKey }: {
  databaseId: string
  chosen: ChosenSession | null
  detail: boolean
  refreshKey: string
}) {
  const [view, setView] = useState<View>('statement')
  const [formatted, setFormatted] = useState(true)
  const [statement, setStatement] = useState<SessionStatement | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [manual, setManual] = useState(0)

  useEffect(() => {
    if (!chosen || !detail) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getSessionStatement(databaseId, chosen.sid, chosen.serial, controller.signal)
      .then((result) => setStatement(result))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setStatement(null)
        setError(reason instanceof ApiError ? reason.message : 'Could not read the statement of this session.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [databaseId, chosen, detail, refreshKey, manual])

  const shown = statement && chosen && statement.sid === chosen.sid && statement.serial === chosen.serial ? statement : null
  const text = useMemo(() => (shown ? (formatted ? formatSql(shown.text) : shown.text) : ''), [shown, formatted])

  const copy = async () => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  const title = chosen ? `SID ${chosen.sid} · ${chosen.program}` : 'No session chosen'
  return (
    <section className="panel statement-panel" aria-labelledby="session-statement">
      <div className="panel-heading section-heading-wrap">
        <div>
          <h2 id="session-statement">{shown?.kind === 'previous' ? 'Last statement' : 'Current statement'}</h2>
          <p>{title}</p>
        </div>
        {detail && chosen && (
          <div className="section-controls">
            <div className="statement-tabs" role="tablist" aria-label="Statement views">
              <button type="button" role="tab" aria-selected={view === 'statement'} className={view === 'statement' ? 'statement-tab statement-tab-active' : 'statement-tab'} onClick={() => setView('statement')}>
                <FileCode2 size={13} aria-hidden="true" />Statement
              </button>
              <button type="button" role="tab" aria-selected={view === 'plan'} className={view === 'plan' ? 'statement-tab statement-tab-active' : 'statement-tab'} onClick={() => setView('plan')}>
                <GitBranch size={13} aria-hidden="true" />Explain plan
              </button>
            </div>
            <button type="button" className="button button-secondary" onClick={() => setManual((value) => value + 1)} disabled={loading}>
              <RefreshCw size={13} aria-hidden="true" />Read again
            </button>
          </div>
        )}
      </div>
      {!detail ? (
        <p className="section-detail-note statement-note">{STATEMENT_NOTE}</p>
      ) : !chosen ? (
        <p className="empty-inline">Choose a session above to see the statement it is running and its plan.</p>
      ) : error ? (
        <p className="inline-error statement-note" role="alert">{error}</p>
      ) : !shown ? (
        <p className="empty-inline" role="status">{loading ? 'Reading the statement…' : ''}</p>
      ) : shown.limitation ? (
        <p className="section-detail-note statement-note">{shown.limitation}</p>
      ) : view === 'statement' ? (
        <div className="statement-body">
          {shown.text_note && <p className="section-detail-note statement-note">{shown.text_note}</p>}
          {shown.text && (
            <>
              <div className="statement-toolbar">
                <label className="section-check">
                  <input type="checkbox" checked={formatted} onChange={(event) => setFormatted(event.target.checked)} />
                  Format statement
                </label>
                <button type="button" className="button button-secondary" onClick={() => { void copy() }}>
                  <Copy size={13} aria-hidden="true" />{copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <ol className="statement-text" aria-label="Statement text">
                {text.split('\n').map((line, index) => <li key={index}><code>{line || ' '}</code></li>)}
              </ol>
              {shown.truncated && <p className="wait-caption section-caption">The statement is longer than 20,000 characters and is cut there.</p>}
            </>
          )}
          {shown.found && shown.sql_id && (
            <ul className="section-facts statement-facts">
              <li><span>SQL_ID</span><strong>{shown.sql_id}</strong><small>Child {shown.child_number ?? '—'}</small></li>
              <li><span>Plan hash</span><strong>{shown.plan_hash_value || '—'}</strong><small>{shown.optimizer_mode || 'Optimizer mode not returned'}</small></li>
              <li><span>Executions</span><strong>{number(shown.executions)}</strong><small>{shown.elapsed_seconds == null ? '' : `${shown.elapsed_seconds.toLocaleString('en-US')} s elapsed in all`}</small></li>
              <li><span>Buffer gets</span><strong>{number(shown.buffer_gets)}</strong><small>{shown.disk_reads == null ? '' : `${number(shown.disk_reads)} disk reads`}</small></li>
              <li><span>Rows processed</span><strong>{number(shown.rows_processed)}</strong><small>Since the statement was loaded</small></li>
            </ul>
          )}
        </div>
      ) : (
        <div className="statement-body">
          <p className="wait-caption section-caption">Cached explain plan: the plan Oracle used, read from the shared pool. The statement is not explained again.</p>
          {shown.plan_note && shown.plan.length > 0 && <p className="section-detail-note statement-note">{shown.plan_note}</p>}
          <PlanTable statement={shown} />
        </div>
      )}
    </section>
  )
}
