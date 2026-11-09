import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, CircleAlert, FileText, RefreshCw, Search } from 'lucide-react'
import { ApiError, getAlertLogEvents } from './api'
import { formatDate } from './format'
import { SeverityBadge } from './components/SeverityBadge'
import { StatePanel } from './components/StatePanel'
import type { AlertLogEvent } from './types'

const TIME_RANGES = {
  '1h': { label: 'Last hour', milliseconds: 60 * 60 * 1000 },
  '24h': { label: 'Last 24 hours', milliseconds: 24 * 60 * 60 * 1000 },
  '7d': { label: 'Last 7 days', milliseconds: 7 * 24 * 60 * 60 * 1000 },
  '30d': { label: 'Last 30 days', milliseconds: 30 * 24 * 60 * 60 * 1000 },
} as const

type TimeRange = keyof typeof TIME_RANGES

export function EventsPage({
  targetId,
  targetLabel,
  onBack,
  onOpenIncident,
  refreshSeconds,
  onRefreshStatus,
}: {
  targetId: string
  targetLabel?: string
  onBack: () => void
  onOpenIncident: (id: string) => void
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [events, setEvents] = useState<AlertLogEvent[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [code, setCode] = useState('')
  const [severity, setSeverity] = useState('')
  const [timeRange, setTimeRange] = useState<TimeRange>('24h')
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const inFlight = useRef(false)

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setError('')
    const controller = new AbortController()
    const range = TIME_RANGES[timeRange]
    const end = new Date()
    const start = new Date(end.getTime() - range.milliseconds)
    try {
      const result = await getAlertLogEvents(targetId, {
        start: start.toISOString(),
        end: end.toISOString(),
        code: code || undefined,
        severity: severity || undefined,
        search: search || undefined,
        limit: 500,
      }, controller.signal)
      setEvents(result)
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading events.')
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }, [targetId, code, severity, search, timeRange])

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

  const items = events ?? []

  return (
    <main className="page-shell detail-shell">
      <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> {targetLabel ?? 'Database details'}</button>
      <section className="page-heading">
        <div>
          <div className="eyebrow"><FileText size={13} /> LOGS &amp; EVENTS</div>
          <h1>Alert-log events</h1>
          <p>{targetLabel ? `Events recorded for ${targetLabel}` : 'Events recorded for this database'}</p>
        </div>
        <div className="heading-meta">
          <span className="updated-note"><RefreshCw size={13} /> {lastUpdated ? `Updated ${formatDate(lastUpdated)}` : 'Waiting for data'}</span>
        </div>
      </section>

      <section className="panel fleet-panel">
        <div className="panel-heading fleet-panel-heading">
          <div>
            <h2>Events <span className="count-pill">{items.length}</span></h2>
            <p>Full-text search, ORA-code and severity filters</p>
          </div>
          <div className="filters events-filters">
            <label className="filter-control search-control">
              <Search size={13} aria-hidden="true" />
              <input
                type="text"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search message…"
                aria-label="Search events"
              />
            </label>
            <label className="filter-control search-control">
              <input
                type="text"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder="ORA-00257"
                aria-label="Filter by ORA code"
              />
            </label>
            <label className="filter-control">
              <span className="sr-only">Filter by severity</span>
              <select aria-label="Filter by severity" value={severity} onChange={(event) => setSeverity(event.target.value)}>
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            <label className="filter-control">
              <span className="sr-only">Time range</span>
              <select aria-label="Time range" value={timeRange} onChange={(event) => setTimeRange(event.target.value as TimeRange)}>
                {Object.entries(TIME_RANGES).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
          </div>
        </div>
        {error ? (
          <StatePanel
            tone="error"
            title="Could not load events"
            message={error}
            icon={<CircleAlert size={23} />}
            action={<button className="button button-primary" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>}
          />
        ) : loading && !events ? (
          <div className="loading-state" role="status"><span className="spinner" /> Loading events…</div>
        ) : items.length === 0 ? (
          <StatePanel
            title="No events match these filters"
            message="Try widening the time range or clearing the search and code filters."
            icon={<FileText size={24} />}
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table observation-table">
              <thead>
                <tr>
                  <th scope="col">Timestamp</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Code</th>
                  <th scope="col">Message</th>
                  <th scope="col">Incidents</th>
                </tr>
              </thead>
              <tbody>
                {items.map((event) => (
                  <tr key={event.id}>
                    <td className="muted">{formatDate(event.occurred_at)}</td>
                    <td><SeverityBadge severity={event.severity} /></td>
                    <td className="primary-cell">{event.code ?? '—'}</td>
                    <td>{event.message}</td>
                    <td>
                      {event.incidents.length === 0 ? <span className="muted">—</span> : (
                        <div className="event-incident-links">
                          {event.incidents.map((incident) => (
                            <button key={incident.id} className="event-incident-link" onClick={() => onOpenIncident(incident.id)}>
                              {incident.severity}
                            </button>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
