import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Eye,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import {
  acknowledgeIncident,
  ApiError,
  clearIncidents,
  getFleet,
  getIncident,
  getTarget,
  listIncidents,
  resolveIncident,
} from './api'
import { formatDate, relativeTime } from './format'
import { IncidentStatusBadge } from './components/IncidentStatusBadge'
import { SeverityBadge } from './components/SeverityBadge'
import { StatePanel } from './components/StatePanel'
import { InvestigationPanel } from './components/InvestigationPanel'
import type { Incident, IncidentDetail, IncidentListResponse, Target } from './types'

const PAGE_SIZE = 25

function canClearIncidents(role: string | undefined): boolean {
  return role === 'admin' || role === 'platform_admin'
}

function databaseLabel(incident: Pick<Incident, 'target_id' | 'target_name'>, databases: Target[]): string {
  const named = incident.target_name?.trim()
  if (named) return named
  const id = incident.target_id.toLowerCase()
  const match = databases.find((item) => item.id.toLowerCase() === id)
  return match?.display_name || 'Unknown database'
}

async function loadRegisteredDatabases(signal: AbortSignal): Promise<Target[]> {
  const collected: Target[] = []
  let page = 1
  let total = 0
  do {
    const fleet = await getFleet({ page, pageSize: 100, environment: '', healthStatus: '' }, signal)
    collected.push(...fleet.items)
    total = fleet.total
    page += 1
  } while (collected.length < total)
  return collected
}

function IncidentListView({
  onOpenIncident,
  onOpenTarget,
  refreshSeconds,
  onRefreshStatus,
  role,
  onCleared,
}: {
  onOpenIncident: (id: string) => void
  onOpenTarget: (id: string) => void
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
  role?: string
  onCleared: () => void
}) {
  const [response, setResponse] = useState<IncidentListResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [state, setState] = useState('')
  const [severity, setSeverity] = useState('')
  const [targetId, setTargetId] = useState('')
  const [databases, setDatabases] = useState<Target[]>([])
  const [page, setPage] = useState(1)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [clearing, setClearing] = useState(false)
  const inFlight = useRef(false)
  const allowClear = canClearIncidents(role)
  const query = useMemo(
    () => ({ page, pageSize: PAGE_SIZE, state, severity, targetId }),
    [page, state, severity, targetId],
  )

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setRefreshing(true)
    setError('')
    const controller = new AbortController()
    try {
      const result = await listIncidents(query, controller.signal)
      let fleet: Target[] = []
      try {
        fleet = await loadRegisteredDatabases(controller.signal)
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
        fleet = []
      }
      setResponse(result)
      setDatabases(fleet)
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading incidents.')
    } finally {
      inFlight.current = false
      setRefreshing(false)
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void refresh(true)
  }, [refresh])
  useEffect(() => {
    onRefreshStatus(loading, () => { void refresh(false) })
  }, [loading, onRefreshStatus, refresh])
  useEffect(() => {
    if (refreshSeconds === 0) return
    const timer = window.setInterval(() => { void refresh(false) }, refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [refresh, refreshSeconds])

  const items = response?.items ?? []
  const pageCount = Math.max(1, Math.ceil((response?.total ?? 0) / PAGE_SIZE))

  function changeState(value: string) {
    setState(value)
    setPage(1)
  }
  function changeSeverity(value: string) {
    setSeverity(value)
    setPage(1)
  }
  function changeDatabase(value: string) {
    setTargetId(value)
    setPage(1)
  }
  const filtersActive = Boolean(state || severity || targetId)

  async function clearAll() {
    const confirmed = window.confirm(
      'Clear every incident? This removes the incident records for all registered databases. Oracle is not changed.',
    )
    if (!confirmed) return
    setClearing(true)
    setError('')
    try {
      await clearIncidents()
      onCleared()
      await refresh(false)
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setError(reason instanceof ApiError ? reason.message : 'Could not clear the incidents.')
    } finally {
      setClearing(false)
    }
  }

  return (
    <main className="page-shell">
      <section className="page-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> INCIDENT CENTER</div>
          <h1>Incidents</h1>
          <p>Active and resolved incidents raised from alert-log rules.</p>
        </div>
        <div className="heading-meta">
          {allowClear && (
            <button
              type="button"
              className="button heading-action heading-danger"
              aria-label="Clear all incidents"
              disabled={clearing || refreshing}
              onClick={() => { void clearAll() }}
            >
              <Trash2 size={14} /> {clearing ? 'Clearing…' : 'Clear all incidents'}
            </button>
          )}
          <button
            type="button"
            className="button button-primary heading-action"
            aria-label="Refresh incidents"
            disabled={refreshing || clearing}
            onClick={() => { void refresh(false) }}
          >
            <RefreshCw size={14} /> {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <span className="updated-note"><RefreshCw size={13} /> {lastUpdated ? `Updated ${relativeTime(lastUpdated)}` : 'Waiting for data'}</span>
        </div>
      </section>

      <section className="panel fleet-panel" aria-labelledby="incident-table-title">
        <div className="panel-heading fleet-panel-heading">
          <div>
            <h2 id="incident-table-title">All incidents <span className="count-pill">{response?.total ?? '—'}</span></h2>
            <p>Latest incidents from every registered database</p>
          </div>
          <div className="filters">
            <label className="filter-control">
              <span className="sr-only">Filter by database</span>
              <select aria-label="Filter by database" value={targetId} onChange={(event) => changeDatabase(event.target.value)}>
                <option value="">All databases</option>
                {databases.map((database) => <option value={database.id} key={database.id}>{database.display_name}</option>)}
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            <label className="filter-control">
              <span className="sr-only">Filter by state</span>
              <select aria-label="Filter by state" value={state} onChange={(event) => changeState(event.target.value)}>
                <option value="">All states</option>
                <option value="new">New</option>
                <option value="acknowledged">Acknowledged</option>
                <option value="resolved">Resolved</option>
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            <label className="filter-control">
              <span className="sr-only">Filter by severity</span>
              <select aria-label="Filter by severity" value={severity} onChange={(event) => changeSeverity(event.target.value)}>
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            {filtersActive && (
              <button className="clear-filters" onClick={() => { changeState(''); changeSeverity(''); changeDatabase('') }}>
                <X size={13} /> Clear
              </button>
            )}
          </div>
        </div>
        {error ? (
          <StatePanel
            tone="error"
            title="Could not load incidents"
            message={error}
            icon={<CircleAlert size={23} />}
            action={<button className="button button-primary" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>}
          />
        ) : loading && !response ? (
          <div className="loading-state" role="status"><span className="spinner" /> Loading incidents…</div>
        ) : items.length === 0 ? (
          <StatePanel
            title={filtersActive ? 'No incidents match these filters' : 'No incidents reported'}
            message={filtersActive ? 'Try another database, state, or severity.' : 'Incidents will appear here when alert-log rules trigger.'}
            icon={<ShieldCheck size={24} />}
            action={filtersActive ? <button className="button button-secondary" onClick={() => { changeState(''); changeSeverity(''); changeDatabase('') }}>Clear filters</button> : undefined}
          />
        ) : (
          <>
            <div className="table-scroll">
              <table className="data-table fleet-table">
                <thead>
                  <tr>
                    <th scope="col">Status</th>
                    <th scope="col">Severity</th>
                    <th scope="col">Message</th>
                    <th scope="col">Database</th>
                    <th scope="col">First seen</th>
                    <th scope="col">Last seen</th>
                    <th scope="col">Owner</th>
                    <th scope="col"><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((incident) => (
                    <tr key={incident.id} className="fleet-row" onClick={() => onOpenIncident(incident.id)}>
                      <td><IncidentStatusBadge state={incident.state} /></td>
                      <td><SeverityBadge severity={incident.severity} /></td>
                      <td className="primary-cell incident-message-cell">{incident.message}</td>
                      <td>
                        <button className="database-link" onClick={(event) => { event.stopPropagation(); onOpenTarget(incident.target_id) }}>
                          <span>{databaseLabel(incident, databases)}</span>
                        </button>
                      </td>
                      <td><span className="last-seen">{relativeTime(incident.first_seen)}</span></td>
                      <td><span className="last-seen">{relativeTime(incident.last_seen)}</span></td>
                      <td>{incident.owner ?? <span className="muted">Unassigned</span>}</td>
                      <td><button className="row-action" aria-label="View incident details" onClick={(event) => { event.stopPropagation(); onOpenIncident(incident.id) }}><ArrowRight size={17} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-footer">
              <span>Showing <strong>{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, response?.total ?? 0)}</strong> of <strong>{response?.total ?? 0}</strong> incidents</span>
              <div className="pagination">
                <button aria-label="Previous page" className="page-button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}><ArrowLeft size={15} /> Previous</button>
                <span>Page {page} of {pageCount}</span>
                <button aria-label="Next page" className="page-button" disabled={page >= pageCount} onClick={() => setPage((current) => Math.min(pageCount, current + 1))}>Next <ArrowRight size={15} /></button>
              </div>
            </div>
          </>
        )}
      </section>
    </main>
  )
}

function ActionForm({
  label,
  busy,
  onSubmit,
}: {
  label: string
  busy: boolean
  onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  return (
    <form
      className="incident-action-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit(reason)
      }}
    >
      <input
        type="text"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="Optional reason…"
        aria-label={`${label} reason`}
      />
      <button type="submit" className="button button-primary" disabled={busy}>{busy ? 'Working…' : label}</button>
    </form>
  )
}

function IncidentDetailView({
  incidentId,
  onBack,
  onOpenTarget,
  onAskCopilot,
  onRefreshStatus,
}: {
  incidentId: string
  onBack: () => void
  onOpenTarget: (id: string) => void
  onAskCopilot?: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [incident, setIncident] = useState<IncidentDetail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [actionError, setActionError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [databaseName, setDatabaseName] = useState('')
  const inFlight = useRef(false)

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setError('')
    try {
      const result = await getIncident(incidentId)
      setIncident(result)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading the incident.')
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }, [incidentId])

  useEffect(() => {
    void refresh(true)
  }, [refresh])
  useEffect(() => {
    onRefreshStatus(loading, () => { void refresh(false) })
  }, [loading, onRefreshStatus, refresh])
  useEffect(() => {
    if (!incident) return
    const named = incident.target_name?.trim()
    if (named) {
      setDatabaseName(named)
      return
    }
    const controller = new AbortController()
    getTarget(incident.target_id, controller.signal)
      .then((target) => setDatabaseName(target.display_name))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setDatabaseName('')
      })
    return () => controller.abort()
  }, [incident])

  async function runAction(action: 'acknowledge' | 'resolve', reason: string) {
    setBusy(true)
    setActionError('')
    setActionMessage('')
    try {
      const payload = { actor: 'console-user', ...(reason ? { reason } : {}) }
      if (action === 'acknowledge') await acknowledgeIncident(incidentId, payload)
      else await resolveIncident(incidentId, payload)
      setActionMessage(action === 'acknowledge' ? 'Incident acknowledged.' : 'Incident resolved.')
      await refresh(false)
    } catch (reason_) {
      setActionError(reason_ instanceof ApiError ? reason_.message : `Could not ${action} this incident.`)
    } finally {
      setBusy(false)
    }
  }

  if (loading && !incident) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Loading incident…</div></main>
  }
  if (error && !incident) {
    return (
      <main className="page-shell">
        <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Back to incidents</button>
        <StatePanel tone="error" title="Could not load incident" message={error} icon={<CircleAlert size={23} />} action={<button className="button button-primary" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>} />
      </main>
    )
  }
  if (!incident) return null

  const canAcknowledge = incident.state === 'new'
  const canResolve = incident.state !== 'resolved'

  return (
    <main className="page-shell detail-shell">
      <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Incident center</button>
      <section className="detail-heading">
        <div className="detail-title">
          <span className="detail-database-icon"><ShieldCheck size={23} /></span>
          <div>
            <div className="eyebrow">INCIDENT <span className="eyebrow-dot">/</span> {incident.rule_id.toUpperCase()}</div>
            <h1>{incident.message}</h1>
            <div className="detail-subtitle">
              <button className="database-link" onClick={() => onOpenTarget(incident.target_id)}>{databaseName || 'Unknown database'}</button>
              <span className="bullet">·</span><span>First seen {relativeTime(incident.first_seen)}</span>
            </div>
          </div>
        </div>
        <div className="detail-status">
          <IncidentStatusBadge state={incident.state} />
          <SeverityBadge severity={incident.severity} />
          {onAskCopilot && (
            <button className="button button-secondary" type="button" onClick={() => onAskCopilot(incident.id)}>
              <MessageSquare size={14} /> Ask copilot
            </button>
          )}
        </div>
      </section>

      {actionError && <div className="inline-error" role="alert"><CircleAlert size={16} /> {actionError}</div>}
      {actionMessage && <div className="inline-success" role="status"><CheckCircle2 size={16} /> {actionMessage}</div>}

      <section className="panel incident-actions-panel">
        <div className="panel-heading"><div><h2><Eye size={18} /> Actions</h2><p>Acknowledge or resolve this incident</p></div></div>
        <div className="incident-action-grid">
          {canAcknowledge && <ActionForm label="Acknowledge" busy={busy} onSubmit={(reason) => { void runAction('acknowledge', reason) }} />}
          {canResolve && <ActionForm label="Resolve" busy={busy} onSubmit={(reason) => { void runAction('resolve', reason) }} />}
          {!canAcknowledge && !canResolve && <p className="empty-inline">This incident is resolved. No further actions are available.</p>}
        </div>
      </section>

      <InvestigationPanel incidentId={incident.id} />

      <div className="detail-grid lower-grid">
        <section className="panel">
          <div className="panel-heading"><div><h2>Incident details</h2></div></div>
          <ul className="incident-field-list">
            <li><span>Owner</span><strong>{incident.owner ?? 'Unassigned'}</strong></li>
            <li><span>Fingerprint</span><strong>{incident.fingerprint}</strong></li>
            <li><span>Root cause candidate</span><strong>{incident.root_cause_candidate ?? 'Unknown'}</strong></li>
            <li><span>Last seen</span><strong>{formatDate(incident.last_seen)}</strong></li>
          </ul>
        </section>
        <section className="panel">
          <div className="panel-heading"><div><h2>Evidence</h2></div></div>
          {Object.keys(incident.evidence ?? {}).length ? (
            <pre className="evidence-block">{JSON.stringify(incident.evidence, null, 2)}</pre>
          ) : <p className="empty-inline">No evidence was captured for this incident.</p>}
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading"><div><h2>Event history</h2><p>Chronological incident lifecycle events</p></div><span className="count-pill">{incident.events.length}</span></div>
        {incident.events.length ? (
          <ul className="incident-event-list">
            {incident.events.map((event) => (
              <li key={event.id}>
                <div className="incident-event-top">
                  <strong>{event.type.replaceAll('_', ' ')}</strong>
                  <span className="muted">{formatDate(event.timestamp)}</span>
                </div>
                <div className="incident-event-meta"><span>Actor: {event.actor}</span>{event.reason && <span>Reason: {event.reason}</span>}</div>
              </li>
            ))}
          </ul>
        ) : <p className="empty-inline">No events have been recorded for this incident.</p>}
      </section>
    </main>
  )
}

export function IncidentCenterPage({
  incidentId,
  onOpenIncident,
  onBack,
  onOpenTarget,
  onAskCopilot,
  refreshSeconds,
  onRefreshStatus,
  role,
  onCleared,
}: {
  incidentId: string | null
  onOpenIncident: (id: string) => void
  onBack: () => void
  onOpenTarget: (id: string) => void
  onAskCopilot?: (id: string) => void
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
  role?: string
  onCleared: () => void
}) {
  if (incidentId) {
    return (
      <IncidentDetailView
        key={incidentId}
        incidentId={incidentId}
        onBack={onBack}
        onOpenTarget={onOpenTarget}
        onAskCopilot={onAskCopilot}
        onRefreshStatus={onRefreshStatus}
      />
    )
  }
  return (
    <IncidentListView
      onOpenIncident={onOpenIncident}
      onOpenTarget={onOpenTarget}
      refreshSeconds={refreshSeconds}
      onRefreshStatus={onRefreshStatus}
      role={role}
      onCleared={onCleared}
    />
  )
}

export type { Incident }
