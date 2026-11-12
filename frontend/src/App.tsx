import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Database,
  FileText,
  HardDrive,
  Layers3,
  MessageSquare,
  Pause,
  Play,
  RefreshCw,
  Server,
  ShieldCheck,
  X,
} from 'lucide-react'
import { ApiError, getFleet, getFleetSummary, getMetricHistory, getSession, getTarget, logout, type AuthSession } from './api'
import { formatBytes, formatDate, formatMetric, relativeTime } from './format'
import { HealthBadge } from './components/HealthBadge'
import { MetricCard } from './components/MetricCard'
import { NotificationBell } from './components/NotificationBell'
import { DatabaseSidebar } from './components/DatabaseSidebar'
import { StatePanel } from './components/StatePanel'
import { TablespaceTable } from './components/TablespaceTable'
import { TimelineChart } from './components/TimelineChart'
import { CompliancePanel } from './components/CompliancePanel'
import { ForecastPanel } from './components/ForecastPanel'
import { EventsPage } from './EventsPage'
import { IncidentCenterPage } from './IncidentCenter'
import { DatabaseRegistrationPage } from './DatabaseRegistrationPage'
import { KnowledgePage } from './KnowledgePage'
import { CopilotPage } from './CopilotPage'
import { LoginPage } from './LoginPage'
import type { FleetHealthSummary, FleetResponse, Observation, Target, TargetDetails } from './types'

const PAGE_SIZE = 25
const REFRESH_OPTIONS = [0, 15, 30, 60]
const TIME_RANGES = {
  '1h': { label: 'Last hour', milliseconds: 60 * 60 * 1000 },
  '24h': { label: 'Last 24 hours', milliseconds: 24 * 60 * 60 * 1000 },
  '7d': { label: 'Last 7 days', milliseconds: 7 * 24 * 60 * 60 * 1000 },
} as const

type TimeRange = keyof typeof TIME_RANGES

type Route =
  | { page: 'fleet' }
  | { page: 'register' }
  | { page: 'database'; id: string }
  | { page: 'database-events'; id: string }
  | { page: 'incidents' }
  | { page: 'incident'; id: string }
  | { page: 'knowledge' }
  | { page: 'copilot' }
  | { page: 'copilot-database'; id: string }
  | { page: 'copilot-incident'; id: string }

function parseHash(hash: string): Route {
  if (hash === '#/register') return { page: 'register' }
  let match = hash.match(/^#\/databases\/([^/]+)\/events$/)
  if (match) return { page: 'database-events', id: decodeURIComponent(match[1]) }
  match = hash.match(/^#\/databases\/([^/]+)$/)
  if (match) return { page: 'database', id: decodeURIComponent(match[1]) }
  match = hash.match(/^#\/incidents\/([^/]+)$/)
  if (match) return { page: 'incident', id: decodeURIComponent(match[1]) }
  if (hash.match(/^#\/incidents\/?$/)) return { page: 'incidents' }
  if (hash.match(/^#\/knowledge\/?$/)) return { page: 'knowledge' }
  match = hash.match(/^#\/copilot\/databases\/([^/]+)$/)
  if (match) return { page: 'copilot-database', id: decodeURIComponent(match[1]) }
  match = hash.match(/^#\/copilot\/incidents\/([^/]+)$/)
  if (match) return { page: 'copilot-incident', id: decodeURIComponent(match[1]) }
  if (hash.match(/^#\/copilot\/?$/)) return { page: 'copilot' }
  return { page: 'fleet' }
}

function usePageNavigation() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const update = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', update)
    if (!window.location.hash) window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/`)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  const navigate = useCallback((hash: string) => {
    window.location.hash = hash
    setRoute(parseHash(hash))
  }, [])
  return {
    route,
    navigateHome: () => navigate('#/'),
    navigateToDatabase: (id: string) => navigate(`#/databases/${encodeURIComponent(id)}`),
    navigateToDatabaseEvents: (id: string) => navigate(`#/databases/${encodeURIComponent(id)}/events`),
    navigateToIncidents: () => navigate('#/incidents'),
    navigateToIncident: (id: string) => navigate(`#/incidents/${encodeURIComponent(id)}`),
    navigateToRegistration: () => navigate('#/register'),
    navigateToKnowledge: () => navigate('#/knowledge'),
    navigateToCopilot: () => navigate('#/copilot'),
    navigateToDatabaseCopilot: (id: string) => navigate(`#/copilot/databases/${encodeURIComponent(id)}`),
    navigateToIncidentCopilot: (id: string) => navigate(`#/copilot/incidents/${encodeURIComponent(id)}`),
  }
}

function useRefreshPreference() {
  const [seconds, setSeconds] = useState(() => {
    const saved = window.localStorage.getItem('oracle-sentinel-refresh')
    return saved !== null && REFRESH_OPTIONS.includes(Number(saved)) ? Number(saved) : 30
  })
  const update = (value: number) => {
    setSeconds(value)
    window.localStorage.setItem('oracle-sentinel-refresh', String(value))
  }
  return [seconds, update] as const
}

function AppHeader({
  currentPage,
  onHome,
  onOpenIncidents,
  onOpenKnowledge,
  onOpenCopilot,
  onOpenIncident,
  notificationRefreshKey,
  refreshSeconds,
  onRefreshChange,
  onManualRefresh,
  refreshing,
  detailTitle,
  principal,
  onSignOut,
}: {
  currentPage: Route['page']
  onHome: () => void
  onOpenIncidents: () => void
  onOpenKnowledge: () => void
  onOpenCopilot: () => void
  onOpenIncident: (id: string) => void
  notificationRefreshKey: number
  refreshSeconds: number
  onRefreshChange: (seconds: number) => void
  onManualRefresh: () => void
  refreshing: boolean
  detailTitle?: string
  principal?: string
  onSignOut?: () => void
}) {
  return (
    <header className="topbar">
      <button className="brand" onClick={onHome} aria-label="Oracle Sentinel fleet overview">
        <span className="brand-mark"><Activity size={19} strokeWidth={2.5} /></span>
        <span className="brand-name">oracle<span>sentinel</span></span>
        <span className="brand-separator" />
        <span className="product-name">Fleet health</span>
      </button>
      <nav className="topbar-nav">
        <button className="nav-link" aria-current={currentPage === 'incidents' || currentPage === 'incident' ? 'page' : undefined} onClick={onOpenIncidents}><ShieldCheck size={15} /> Incidents</button>
        <button className="nav-link" aria-current={currentPage === 'knowledge' ? 'page' : undefined} onClick={onOpenKnowledge}><BookOpen size={15} /> Knowledge</button>
        <button className="nav-link" aria-current={currentPage === 'copilot' || currentPage === 'copilot-database' || currentPage === 'copilot-incident' ? 'page' : undefined} onClick={onOpenCopilot}><MessageSquare size={15} /> Copilot</button>
      </nav>
      <div className="topbar-actions">
        <NotificationBell onOpenIncident={onOpenIncident} refreshSeconds={refreshSeconds} refreshKey={notificationRefreshKey} />
        {detailTitle && <span className="topbar-target"><Database size={15} />{detailTitle}</span>}
        <label className="refresh-select">
          <Clock3 size={15} aria-hidden="true" />
          <span className="sr-only">Auto refresh interval</span>
          <select value={refreshSeconds} onChange={(event) => onRefreshChange(Number(event.target.value))} aria-label="Auto refresh interval">
            <option value={0}>Refresh off</option>
            <option value={15}>Every 15 sec</option>
            <option value={30}>Every 30 sec</option>
            <option value={60}>Every minute</option>
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </label>
        <button className={`icon-button refresh-button ${refreshing ? 'is-refreshing' : ''}`} onClick={onManualRefresh} aria-label="Refresh data" title="Refresh data">
          <RefreshCw size={17} />
        </button>
        {principal ? (
          <span className="signed-in">
            <span>{principal}</span>
            <button className="nav-link" type="button" onClick={onSignOut}>Sign out</button>
          </span>
        ) : <span className="user-avatar" aria-label="Oracle Sentinel monitoring">OS</span>}
      </div>
    </header>
  )
}

function FleetPage({
  onOpenTarget,
  onOpenIncidents,
  refreshSeconds,
  onRefreshStatus,
}: {
  onOpenTarget: (target: Target) => void
  onOpenIncidents: () => void
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [fleet, setFleet] = useState<FleetResponse | null>(null)
  const [summary, setSummary] = useState<FleetHealthSummary | null>(null)
  const [summaryError, setSummaryError] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [environment, setEnvironment] = useState('')
  const [health, setHealth] = useState('')
  const [page, setPage] = useState(1)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const inFlight = useRef(false)
  const query = useMemo(() => ({ page, pageSize: PAGE_SIZE, environment, healthStatus: health }), [page, environment, health])

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setError('')
    setSummaryError('')
    const controller = new AbortController()
    try {
      const [result, overview] = await Promise.allSettled([
        getFleet(query, controller.signal), getFleetSummary(controller.signal),
      ])
      if (overview.status === 'fulfilled') setSummary(overview.value)
      else setSummaryError(overview.reason instanceof ApiError ? overview.reason.message : 'Could not load fleet health summary.')
      if (result.status === 'rejected') throw result.reason
      setFleet(result.value)
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading databases.')
    } finally {
      inFlight.current = false
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

  const items = fleet?.items ?? []
  const pageCount = Math.max(1, Math.ceil((fleet?.total ?? 0) / PAGE_SIZE))
  const attentionCount = summary ? summary.warning + summary.critical + summary.offline : 0

  function changeEnvironment(value: string) {
    setEnvironment(value)
    setPage(1)
  }
  function changeHealth(value: string) {
    setHealth(value)
    setPage(1)
  }

  return (
    <main className="page-shell">
      <section className="page-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> LIVE MONITORING</div>
          <h1>Database fleet</h1>
          <p>Health and telemetry across your Oracle estate.</p>
        </div>
        <div className="heading-meta">
          <span className="updated-note"><RefreshCw size={13} /> {lastUpdated ? `Updated ${relativeTime(lastUpdated)}` : 'Waiting for data'}</span>
          <span className={`auto-state ${refreshSeconds ? 'auto-on' : ''}`}>{refreshSeconds ? <Play size={11} fill="currentColor" /> : <Pause size={11} fill="currentColor" />}{refreshSeconds ? `Auto · ${refreshSeconds}s` : 'Auto refresh off'}</span>
        </div>
      </section>

      <section className="metric-grid" aria-label="Fleet summary">
        <MetricCard label="Registered databases" value={!summaryError ? summary?.total ?? '—' : '—'} detail="Across all environments" icon={<Database size={18} />} />
        <MetricCard label="Healthy databases" value={!summaryError ? summary?.healthy ?? '—' : '—'} detail="Entire registered fleet" icon={<CheckCircle2 size={18} />} tone="good" />
        <MetricCard label="Needs attention" value={summary && !summaryError ? attentionCount : '—'} detail="Warning, critical or down" icon={<CircleAlert size={18} />} tone={attentionCount ? 'warning' : 'default'} />
        <div
          className="metric-card-link"
          role="link"
          tabIndex={0}
          onClick={onOpenIncidents}
          onKeyDown={(event) => { if (event.key === 'Enter') onOpenIncidents() }}
        >
          <MetricCard label="Critical incidents" value="View incidents" detail="Open the incident center" icon={<ShieldCheck size={18} />} />
        </div>
      </section>
      {summaryError && <div className="inline-error" role="alert">Fleet summary unavailable: {summaryError}</div>}
      {summary && !summaryError && (
        <section className="fleet-health-breakdown" aria-label="Fleet health breakdown">
          <span>Healthy <strong>{summary.healthy}</strong></span>
          <span>Warning <strong>{summary.warning}</strong></span>
          <span>Critical <strong>{summary.critical}</strong></span>
          <span>Down <strong>{summary.offline}</strong></span>
          <span>Unknown / stale <strong>{summary.unknown}</strong></span>
          <span>Disabled <strong>{summary.disabled}</strong></span>
        </section>
      )}
      {summary && !summary.scheduler_enabled && <p className="monitoring-warning">Automatic monitoring is off. Enable SCHEDULER_ENABLED=true in the backend .env and restart to collect data.</p>}

      <section className="panel fleet-panel" aria-labelledby="fleet-table-title">
        <div className="panel-heading fleet-panel-heading">
          <div>
            <h2 id="fleet-table-title">All databases <span className="count-pill">{fleet?.total ?? '—'}</span></h2>
            <p>Current status from the latest collected telemetry</p>
          </div>
          <div className="filters">
            <label className="filter-control">
              <span className="sr-only">Filter by environment</span>
              <select aria-label="Filter by environment" value={environment} onChange={(event) => changeEnvironment(event.target.value)}>
                <option value="">All environments</option>
                {(fleet?.environments ?? []).map((item) => <option value={item} key={item}>{item}</option>)}
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            <label className="filter-control">
              <span className="sr-only">Filter by health status</span>
              <select aria-label="Filter by health status" value={health} onChange={(event) => changeHealth(event.target.value)}>
                <option value="">All health states</option>
                <option value="healthy">Healthy</option>
                <option value="warning">Warning</option>
                <option value="critical">Critical</option>
                <option value="unknown">Unknown</option>
                <option value="offline">Offline</option>
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </label>
            {(environment || health) && (
              <button className="clear-filters" onClick={() => { changeEnvironment(''); changeHealth('') }}>
                <X size={13} /> Clear
              </button>
            )}
          </div>
        </div>
        {error ? (
          <StatePanel
            tone="error"
            title="Could not load fleet data"
            message={error}
            icon={<CircleAlert size={23} />}
            action={<button className="button button-primary" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>}
          />
        ) : loading && !fleet ? (
          <div className="loading-state" role="status"><span className="spinner" /> Loading registered databases…</div>
        ) : items.length === 0 ? (
          <StatePanel
            title={environment || health ? 'No databases match these filters' : 'No databases registered'}
            message={environment || health ? 'Try another environment or health status.' : 'Use Add database in the sidebar to register a TNS database alias.'}
            icon={<Database size={24} />}
            action={(environment || health) ? <button className="button button-secondary" onClick={() => { changeEnvironment(''); changeHealth('') }}>Clear filters</button> : undefined}
          />
        ) : (
          <>
            <div className="table-scroll">
              <table className="data-table fleet-table">
                <thead>
                  <tr>
                    <th scope="col">Database</th>
                    <th scope="col">Environment</th>
                    <th scope="col">Health status</th>
                    <th scope="col">Last seen</th>
                    <th scope="col">Critical incidents</th>
                    <th scope="col"><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((target) => (
                    <tr key={target.id} className="fleet-row" onClick={() => onOpenTarget(target)}>
                      <td>
                        <button className="database-link" onClick={(event) => { event.stopPropagation(); onOpenTarget(target) }}>
                          <span className="database-icon"><Database size={16} /></span>
                          <span><strong>{target.display_name}</strong><small>{target.tns_alias || target.engine || 'Oracle database'}</small></span>
                        </button>
                      </td>
                      <td><span className={`environment-tag env-${target.environment.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}>{target.environment || 'Unspecified'}</span></td>
                      <td><HealthBadge status={target.last_health_status} /></td>
                      <td><span className="last-seen">{relativeTime(target.last_seen)}</span>{target.last_seen && <small className="timestamp-sub">{formatDate(target.last_seen)}</small>}</td>
                      <td>{target.critical_incident_count == null ? <span className="unavailable-tag" title="Incident reporting is planned for Phase 3">Not available</span> : <span className="incident-count">{target.critical_incident_count}</span>}</td>
                      <td><button className="row-action" aria-label={`View ${target.display_name} details`} onClick={(event) => { event.stopPropagation(); onOpenTarget(target) }}><ArrowRight size={17} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-footer">
              <span>Showing <strong>{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, fleet?.total ?? 0)}</strong> of <strong>{fleet?.total ?? 0}</strong> databases</span>
              <div className="pagination">
                <button aria-label="Previous page" className="page-button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}><ArrowLeft size={15} /> Previous</button>
                <span>Page {page} of {pageCount}</span>
                <button aria-label="Next page" className="page-button" disabled={page >= pageCount} onClick={() => setPage((current) => Math.min(pageCount, current + 1))}>Next <ArrowRight size={15} /></button>
              </div>
            </div>
          </>
        )}
      </section>
      <footer className="page-footer"><span>Oracle Sentinel</span><span>Monitoring without guesswork.</span></footer>
    </main>
  )
}

function latestForMetric(observations: Observation[], metric: string): Observation | undefined {
  return [...observations].filter((item) => item.metric_name === metric).sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0]
}

function readableMetricName(metric: string): string {
  return metric.split('.').slice(-2).join(' ').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function FraGauge({ detail }: { detail: TargetDetails }) {
  const percentage = detail.fra.used_pct
  const clamped = percentage == null ? 0 : Math.min(100, Math.max(0, percentage))
  return (
    <div className="fra-layout">
      <div className={`fra-gauge ${percentage != null && percentage >= 90 ? 'fra-high' : percentage != null && percentage >= 75 ? 'fra-mid' : ''}`} style={{ '--gauge-value': `${clamped}%` } as CSSProperties} role="img" aria-label={percentage == null ? 'Recovery area usage unavailable' : `Recovery area ${percentage.toFixed(1)} percent used`}>
        <div className="fra-gauge-inner"><strong>{percentage == null ? '—' : `${percentage.toFixed(0)}%`}</strong><span>used</span></div>
      </div>
      <div className="fra-stats">
        <div><span>Used space</span><strong>{formatBytes(detail.fra.used_bytes)}</strong></div>
        <div><span>Total capacity</span><strong>{formatBytes(detail.fra.size_bytes)}</strong></div>
        <div><span>Free space</span><strong>{detail.fra.size_bytes != null && detail.fra.used_bytes != null ? formatBytes(Math.max(0, detail.fra.size_bytes - detail.fra.used_bytes)) : 'Unavailable'}</strong></div>
      </div>
    </div>
  )
}

function DetailPage({
  targetId,
  onBack,
  onOpenEvents,
  onAskCopilot,
  onRefreshStatus,
}: {
  targetId: string
  onBack: () => void
  onOpenEvents: (id: string) => void
  onAskCopilot: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [detail, setDetail] = useState<TargetDetails | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [timeRange, setTimeRange] = useState<TimeRange>('24h')
  const [selectedMetric, setSelectedMetric] = useState('storage.tablespace.used_pct')
  const [history, setHistory] = useState<Observation[]>([])
  const [historyError, setHistoryError] = useState('')
  const [historyLoading, setHistoryLoading] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [phase4RefreshKey, setPhase4RefreshKey] = useState(0)
  const inFlight = useRef(false)

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setError('')
    try {
      const result = await getTarget(targetId)
      setDetail(result)
      setPhase4RefreshKey((value) => value + 1)
      setLastUpdated(new Date().toISOString())
      const availableMetrics = [...new Set(result.latest_observations.filter((item) => item.value != null).map((item) => item.metric_name))]
      if (availableMetrics.length && !availableMetrics.includes(selectedMetric)) setSelectedMetric(availableMetrics[0])
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading database details.')
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }, [targetId, selectedMetric])

  useEffect(() => {
    void refresh(true)
  }, [refresh])
  useEffect(() => {
    onRefreshStatus(loading, () => { void refresh(false) })
  }, [loading, onRefreshStatus, refresh])

  useEffect(() => {
    if (!detail) return
    let active = true
    setHistoryLoading(true)
    setHistoryError('')
    const range = TIME_RANGES[timeRange]
    const end = new Date()
    const start = new Date(end.getTime() - range.milliseconds)
    getMetricHistory(targetId, selectedMetric, start, end)
      .then((result) => { if (active) setHistory(result) })
      .catch((reason: unknown) => {
        if (active) {
          setHistory([])
          setHistoryError(reason instanceof ApiError ? reason.message : 'Could not load metric history.')
        }
      })
      .finally(() => { if (active) setHistoryLoading(false) })
    return () => { active = false }
  }, [targetId, selectedMetric, timeRange, detail])

  useEffect(() => {
    onRefreshStatus(loading || historyLoading, () => { void refresh(false) })
  }, [historyLoading, loading, onRefreshStatus, refresh])

  if (loading && !detail) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Loading database details…</div></main>
  }
  if (error && !detail) {
    return (
      <main className="page-shell">
        <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Back to fleet</button>
        <StatePanel tone="error" title="Could not load database" message={error} icon={<CircleAlert size={23} />} action={<button className="button button-primary" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>} />
      </main>
    )
  }
  if (!detail) return null

  const numericMetrics = [...new Map(detail.latest_observations.filter((item) => item.value != null).map((item) => [item.metric_name, item])).values()]
  const storageMetric = latestForMetric(detail.latest_observations, 'storage.tablespace.used_pct')
  const seen = detail.last_seen
  const chartUnit = latestForMetric(detail.latest_observations, selectedMetric)?.unit ?? history.find((item) => item.unit)?.unit ?? null
  const chartLabel = readableMetricName(selectedMetric)
  const archiveErrors = detail.archive.destinations.filter((item) => item.quality.toLowerCase() === 'error' || (item.value_str ?? '').toLowerCase().includes('error'))
  const status = detail.last_health_status.toLowerCase()
  const staleTelemetry = detail.monitoring_status.toLowerCase().includes('stale')
    || detail.latest_observations.some((item) => item.quality.toLowerCase() === 'stale')
  const telemetryNotice = status === 'offline'
    ? 'This database is offline. Telemetry may be unavailable until connectivity is restored.'
    : status === 'unknown' || staleTelemetry
      ? 'Telemetry is stale or insufficient to confirm health. This database is not being reported as healthy.'
      : ''

  return (
    <main className="page-shell detail-shell">
      <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Fleet overview</button>
      <section className="detail-heading">
        <div className="detail-title">
          <span className="detail-database-icon"><Database size={23} /></span>
          <div>
            <div className="eyebrow">DATABASE OVERVIEW <span className="eyebrow-dot">/</span> {detail.environment.toUpperCase() || 'ENVIRONMENT UNSPECIFIED'}</div>
            <h1>{detail.display_name}</h1>
            <div className="detail-subtitle"><span>{detail.tns_alias || 'TNS alias unavailable'}</span><span className="bullet">·</span><span>{detail.engine || 'Oracle engine details unavailable'}</span></div>
          </div>
        </div>
        <div className="detail-status">
          <HealthBadge status={detail.last_health_status} />
          <span className="updated-note">Last seen {relativeTime(seen)}</span>
          <button className="button button-secondary events-link" onClick={() => onOpenEvents(targetId)}><FileText size={14} /> Logs &amp; events</button>
          <button className="button button-secondary" type="button" onClick={() => onAskCopilot(targetId)}><MessageSquare size={14} /> Ask copilot</button>
        </div>
      </section>

      {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> Details could not be refreshed: {error}<button onClick={() => { void refresh(false) }}>Retry</button></div>}
      {telemetryNotice && <div className={`telemetry-notice ${status === 'offline' ? 'telemetry-offline' : ''}`} role="status"><CircleAlert size={16} /><span><strong>{status === 'offline' ? 'Target offline' : 'Telemetry status requires attention'}</strong>{telemetryNotice}</span></div>}

      <section className="metric-grid detail-metric-grid" aria-label="Latest measurements">
        <MetricCard label="Health status" value={detail.last_health_status.replaceAll('_', ' ')} detail={`Monitoring ${detail.monitoring_status || 'status unavailable'}`} icon={<Activity size={18} />} tone={detail.last_health_status === 'healthy' ? 'good' : 'warning'} />
        <MetricCard label="Tablespace usage" value={storageMetric ? formatMetric(storageMetric.value, storageMetric.unit) : 'Unavailable'} detail={storageMetric ? `Updated ${relativeTime(storageMetric.timestamp)}` : 'No current measurement'} icon={<Layers3 size={18} />} tone={storageMetric?.value != null && storageMetric.value >= 85 ? 'warning' : 'default'} />
        <MetricCard label="Compliance checks" value={detail.compliance.pass + detail.compliance.fail + detail.compliance.unknown} detail={`${detail.compliance.pass} pass · ${detail.compliance.fail} fail · ${detail.compliance.unknown} unknown`} icon={<ShieldCheck size={18} />} tone={detail.compliance.fail ? 'warning' : 'default'} />
        <MetricCard label="Critical incidents" value={detail.critical_incident_count ?? 'Phase 3'} detail={detail.critical_incident_count == null ? 'Incident counts are not available yet' : 'Open critical incidents'} icon={<CircleAlert size={18} />} tone="default" />
      </section>

      <div className="detail-grid">
        <section className="panel history-panel">
          <div className="panel-heading detail-panel-heading">
            <div><h2><Activity size={18} /> Metric timeline</h2><p>{chartLabel} · chronological observations</p></div>
            <div className="chart-controls">
              {numericMetrics.length > 0 && (
                <label className="chart-select">
                  <span className="sr-only">Timeline metric</span>
                  <select value={selectedMetric} onChange={(event) => setSelectedMetric(event.target.value)} aria-label="Timeline metric">
                    {[...new Set(numericMetrics.map((item) => item.metric_name))].map((metric) => <option key={metric} value={metric}>{readableMetricName(metric)}</option>)}
                  </select><ChevronDown size={13} aria-hidden="true" />
                </label>
              )}
              <label className="chart-select range-select">
                <CalendarClock size={14} aria-hidden="true" />
                <span className="sr-only">Timeline time range</span>
                <select value={timeRange} onChange={(event) => setTimeRange(event.target.value as TimeRange)} aria-label="Timeline time range">
                  {Object.entries(TIME_RANGES).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
                </select><ChevronDown size={13} aria-hidden="true" />
              </label>
            </div>
          </div>
          {historyError ? <div className="chart-error" role="alert"><CircleAlert size={16} /><span>{historyError}</span></div> : historyLoading ? <div className="chart-loading" role="status"><span className="spinner" /> Loading metric history…</div> : <TimelineChart observations={history} unit={chartUnit} label={chartLabel} />}
          <div className="chart-footnote"><span><span className="legend-dot" /> Measured values</span><span>{history.length ? `${history.length} observations` : 'Bounded metric history'}</span></div>
        </section>

        <section className="panel fra-panel">
          <div className="panel-heading"><div><h2><HardDrive size={18} /> Recovery area</h2><p>Fast Recovery Area capacity</p></div><span className="section-icon"><HardDrive size={16} /></span></div>
          <FraGauge detail={detail} />
          <div className="fra-footer"><span>Recovery destination breakdown</span><span>{detail.fra.breakdown.length} observations</span></div>
        </section>
      </div>

      <section className="panel tablespace-panel">
        <div className="panel-heading">
          <div><h2><Layers3 size={18} /> Tablespaces</h2><p>Usage and storage configuration by tablespace</p></div>
          <span className="count-pill">{detail.tablespaces.length}</span>
        </div>
        <TablespaceTable tablespaces={detail.tablespaces} />
      </section>

      <CompliancePanel targetId={targetId} refreshKey={phase4RefreshKey} />
      <ForecastPanel targetId={targetId} refreshKey={phase4RefreshKey} />

      <div className="detail-grid lower-grid">
        <section className="panel archive-panel">
          <div className="panel-heading"><div><h2><Server size={18} /> Archive log</h2><p>Archive mode and destination health</p></div></div>
          <div className="archive-mode"><div className="archive-mode-icon"><Database size={17} /></div><div><span>Log mode</span><strong>{detail.archive.log_mode || 'Unavailable'}</strong></div></div>
          {detail.archive.destinations.length ? (
            <ul className="destination-list">
              {detail.archive.destinations.map((destination) => {
                const failed = destination.quality.toLowerCase() === 'error' || (destination.value_str ?? '').toLowerCase().includes('error')
                return <li key={destination.id}><span className={`destination-status ${failed ? 'destination-error' : ''}`}><span />{failed ? 'Attention' : 'Reported'}</span><span>{destination.labels.destination ?? destination.labels.name ?? destination.metric_name}</span><strong>{destination.value_str ?? formatMetric(destination.value, destination.unit)}</strong></li>
              })}
            </ul>
          ) : <p className="empty-inline">No archive destination observations available.</p>}
          {archiveErrors.length > 0 && <p className="archive-alert"><CircleAlert size={14} /> {archiveErrors.length} destination observation{archiveErrors.length === 1 ? '' : 's'} reported an error.</p>}
        </section>
        <section className="panel poll-panel">
          <div className="panel-heading"><div><h2><Activity size={18} /> Probe activity</h2><p>Latest collection results by probe</p></div></div>
          {detail.poll_status.length ? (
            <ul className="probe-list">
              {detail.poll_status.map((probe) => (
                <li key={probe.probe_type}>
                  <span className={`probe-dot probe-${probe.status.toLowerCase()}`} aria-hidden="true" />
                  <span className="probe-name">{probe.probe_type.replaceAll('_', ' ')}</span>
                  <span className={`probe-state probe-${probe.status.toLowerCase()}`}>{probe.status.replaceAll('_', ' ')}</span>
                  <span className="probe-time">{formatDate(probe.last_success_at ?? probe.last_attempt_at)}</span>
                  {probe.error_class && <span className="probe-error">{probe.error_class}</span>}
                </li>
              ))}
            </ul>
          ) : <p className="empty-inline">No probe status is available.</p>}
        </section>
      </div>

      <section className="panel observation-panel">
        <div className="panel-heading"><div><h2><Activity size={18} /> Latest observations</h2><p>Recent telemetry evidence from collection</p></div><span className="count-pill">{detail.latest_observations.length}</span></div>
        {detail.latest_observations.length ? (
          <div className="table-scroll">
            <table className="data-table observation-table">
              <thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">Quality</th><th scope="col">Source</th><th scope="col">Timestamp</th></tr></thead>
              <tbody>{detail.latest_observations.slice(0, 12).map((observation) => <tr key={observation.id}><td className="primary-cell">{observation.metric_name}</td><td>{observation.value_str ?? formatMetric(observation.value, observation.unit)}</td><td><span className={`quality-tag quality-${observation.quality.toLowerCase()}`}>{observation.quality}</span></td><td>{observation.source}</td><td className="muted">{formatDate(observation.timestamp)}</td></tr>)}</tbody>
            </table>
          </div>
        ) : <p className="empty-inline observation-empty">No observations have been collected for this database.</p>}
      </section>

      <footer className="page-footer"><span>Last detail refresh: {lastUpdated ? formatDate(lastUpdated) : '—'}</span><span>Telemetry timestamps are UTC.</span></footer>
    </main>
  )
}

export default function App() {
  const {
    route,
    navigateHome,
    navigateToDatabase,
    navigateToDatabaseEvents,
    navigateToIncidents,
    navigateToIncident,
    navigateToRegistration,
    navigateToKnowledge,
    navigateToCopilot,
    navigateToDatabaseCopilot,
    navigateToIncidentCopilot,
  } = usePageNavigation()
  const [refreshSeconds, setRefreshSeconds] = useRefreshPreference()
  const [refreshing, setRefreshing] = useState(false)
  const [notificationRefreshKey, setNotificationRefreshKey] = useState(0)
  const refreshAction = useRef<() => void>(() => undefined)
  const registerRefresh = useCallback((pending: boolean, action: () => void) => {
    setRefreshing(pending)
    refreshAction.current = action
  }, [])
  const onManualRefresh = useCallback(() => {
    refreshAction.current()
    setNotificationRefreshKey((value) => value + 1)
  }, [])
  const [session, setSession] = useState<AuthSession | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    getSession(controller.signal).then(setSession).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setSession({ required: false, authenticated: true, principal: '', role: 'operator', method: '' })
    })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (route.page === 'fleet' || route.page === 'register' || route.page.startsWith('copilot') || refreshSeconds === 0) return
    const timer = window.setInterval(() => refreshAction.current(), refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [route.page, refreshSeconds])

  if (!session) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Checking sign-in…</div></main>
  }
  if (session.required && !session.authenticated) {
    return <LoginPage onSuccess={setSession} />
  }

  return (
    <div className="app">
      <AppHeader
        currentPage={route.page}
        onHome={navigateHome}
        onOpenIncidents={navigateToIncidents}
        onOpenKnowledge={navigateToKnowledge}
        onOpenCopilot={navigateToCopilot}
        onOpenIncident={navigateToIncident}
        notificationRefreshKey={notificationRefreshKey}
        refreshSeconds={refreshSeconds}
        onRefreshChange={setRefreshSeconds}
        onManualRefresh={onManualRefresh}
        refreshing={refreshing}
        principal={session.required ? session.principal : undefined}
        onSignOut={() => {
          void logout().finally(() => setSession({ ...session, authenticated: false, principal: '', role: '', method: '' }))
        }}
      />
      <div className="database-workspace">
      <DatabaseSidebar
        selectedId={route.page === 'database' || route.page === 'database-events' || route.page === 'copilot-database' ? route.id : null}
        homeSelected={route.page === 'fleet'}
        onHome={navigateHome}
        onSelect={navigateToDatabase}
        onRegister={navigateToRegistration}
        refreshSeconds={refreshSeconds}
        refreshKey={notificationRefreshKey}
      />
      <div className="workspace-content">
      {route.page === 'register' ? (
        <DatabaseRegistrationPage onBack={navigateHome} onRefreshStatus={registerRefresh} onRegistered={(target) => {
          setNotificationRefreshKey((value) => value + 1)
          navigateToDatabase(target.id)
        }} />
      ) : route.page === 'database' ? (
        <DetailPage key={route.id} targetId={route.id} onBack={navigateHome} onOpenEvents={navigateToDatabaseEvents} onAskCopilot={navigateToDatabaseCopilot} onRefreshStatus={registerRefresh} />
      ) : route.page === 'database-events' ? (
        <EventsPage
          key={route.id}
          targetId={route.id}
          onBack={() => navigateToDatabase(route.id)}
          onOpenIncident={navigateToIncident}
          refreshSeconds={refreshSeconds}
          onRefreshStatus={registerRefresh}
        />
      ) : route.page === 'incidents' ? (
        <IncidentCenterPage
          incidentId={null}
          onOpenIncident={navigateToIncident}
          onBack={navigateToIncidents}
          onOpenTarget={navigateToDatabase}
          refreshSeconds={refreshSeconds}
          onRefreshStatus={registerRefresh}
        />
      ) : route.page === 'incident' ? (
        <IncidentCenterPage
          key={route.id}
          incidentId={route.id}
          onOpenIncident={navigateToIncident}
          onBack={navigateToIncidents}
          onOpenTarget={navigateToDatabase}
          onAskCopilot={navigateToIncidentCopilot}
          refreshSeconds={refreshSeconds}
          onRefreshStatus={registerRefresh}
        />
      ) : route.page === 'knowledge' ? (
        <KnowledgePage refreshSeconds={refreshSeconds} onRefreshStatus={registerRefresh} />
      ) : route.page === 'copilot' || route.page === 'copilot-database' || route.page === 'copilot-incident' ? (
        <CopilotPage
          key={`${route.page}:${'id' in route ? route.id : 'fleet'}`}
          scopeType={route.page === 'copilot-database' ? 'database' : route.page === 'copilot-incident' ? 'incident' : 'fleet'}
          scopeId={'id' in route ? route.id : null}
          onRefreshStatus={registerRefresh}
        />
      ) : (
        <FleetPage
          onOpenTarget={(target) => navigateToDatabase(target.id)}
          onOpenIncidents={navigateToIncidents}
          refreshSeconds={refreshSeconds}
          onRefreshStatus={registerRefresh}
        />
      )}
      </div>
      </div>
    </div>
  )
}
