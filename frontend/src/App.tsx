import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Database,
  HardDrive,
  Layers3,
  Pause,
  Play,
  RefreshCw,
  Server,
  ShieldCheck,
  X,
} from 'lucide-react'
import { ApiError, getFleet, getMetricHistory, getTarget } from './api'
import { formatBytes, formatDate, formatMetric, relativeTime } from './format'
import { HealthBadge } from './components/HealthBadge'
import { MetricCard } from './components/MetricCard'
import { StatePanel } from './components/StatePanel'
import { TablespaceTable } from './components/TablespaceTable'
import { TimelineChart } from './components/TimelineChart'
import type { FleetResponse, Observation, Target, TargetDetails } from './types'

const PAGE_SIZE = 25
const REFRESH_OPTIONS = [0, 15, 30, 60]
const TIME_RANGES = {
  '1h': { label: 'Last hour', milliseconds: 60 * 60 * 1000 },
  '24h': { label: 'Last 24 hours', milliseconds: 24 * 60 * 60 * 1000 },
  '7d': { label: 'Last 7 days', milliseconds: 7 * 24 * 60 * 60 * 1000 },
} as const

type TimeRange = keyof typeof TIME_RANGES

function readHashTarget(): string | null {
  const match = window.location.hash.match(/^#\/databases\/([^/]+)$/)
  return match ? decodeURIComponent(match[1]) : null
}

function targetUrl(id: string | null): string {
  return id ? `#/databases/${encodeURIComponent(id)}` : '#/'
}

function usePageNavigation() {
  const [targetId, setTargetId] = useState<string | null>(readHashTarget)
  useEffect(() => {
    const update = () => setTargetId(readHashTarget())
    window.addEventListener('hashchange', update)
    if (!window.location.hash) window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/`)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  const navigate = useCallback((id: string | null) => {
    window.location.hash = targetUrl(id)
    setTargetId(id)
  }, [])
  return { targetId, navigate }
}

function useRefreshPreference() {
  const [seconds, setSeconds] = useState(() => {
    const saved = Number(window.localStorage.getItem('oracle-sentinel-refresh'))
    return REFRESH_OPTIONS.includes(saved) ? saved : 30
  })
  const update = (value: number) => {
    setSeconds(value)
    window.localStorage.setItem('oracle-sentinel-refresh', String(value))
  }
  return [seconds, update] as const
}

function AppHeader({
  onHome,
  refreshSeconds,
  onRefreshChange,
  onManualRefresh,
  refreshing,
  detailTitle,
}: {
  onHome: () => void
  refreshSeconds: number
  onRefreshChange: (seconds: number) => void
  onManualRefresh: () => void
  refreshing: boolean
  detailTitle?: string
}) {
  return (
    <header className="topbar">
      <button className="brand" onClick={onHome} aria-label="Oracle Sentinel fleet overview">
        <span className="brand-mark"><Activity size={19} strokeWidth={2.5} /></span>
        <span className="brand-name">oracle<span>sentinel</span></span>
        <span className="brand-separator" />
        <span className="product-name">Fleet health</span>
      </button>
      <div className="topbar-actions">
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
        <span className="user-avatar" aria-label="Oracle Sentinel monitoring">OS</span>
      </div>
    </header>
  )
}

function FleetPage({
  onOpenTarget,
  refreshSeconds,
  onRefreshStatus,
}: {
  onOpenTarget: (target: Target) => void
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [fleet, setFleet] = useState<FleetResponse | null>(null)
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
    const controller = new AbortController()
    try {
      const result = await getFleet(query, controller.signal)
      setFleet(result)
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
  const healthyCount = items.filter((target) => target.last_health_status === 'healthy').length
  const attentionCount = items.filter((target) => ['critical', 'warning', 'offline'].includes(target.last_health_status.toLowerCase())).length

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
        <MetricCard label="Registered databases" value={fleet?.total ?? '—'} detail="Across all environments" icon={<Database size={18} />} />
        <MetricCard label="Healthy on this page" value={fleet ? healthyCount : '—'} detail="Fresh telemetry and passing probes" icon={<CheckCircle2 size={18} />} tone="good" />
        <MetricCard label="Needs attention" value={fleet ? attentionCount : '—'} detail="Warning, critical or offline" icon={<CircleAlert size={18} />} tone={attentionCount ? 'warning' : 'default'} />
        <MetricCard label="Critical incidents" value="Phase 3" detail="Incident counts are not available yet" icon={<ShieldCheck size={18} />} />
      </section>

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
            message={environment || health ? 'Try another environment or health status.' : 'Databases will appear here after they are registered through the API.'}
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
  onRefreshStatus,
}: {
  targetId: string
  onBack: () => void
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
  const inFlight = useRef(false)

  const refresh = useCallback(async (initial = false) => {
    if (inFlight.current) return
    inFlight.current = true
    if (initial) setLoading(true)
    setError('')
    try {
      const result = await getTarget(targetId)
      setDetail(result)
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
        <div className="detail-status"><HealthBadge status={detail.last_health_status} /><span className="updated-note">Last seen {relativeTime(seen)}</span></div>
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
  const { targetId, navigate } = usePageNavigation()
  const [refreshSeconds, setRefreshSeconds] = useRefreshPreference()
  const [refreshing, setRefreshing] = useState(false)
  const refreshAction = useRef<() => void>(() => undefined)
  const registerRefresh = useCallback((pending: boolean, action: () => void) => {
    setRefreshing(pending)
    refreshAction.current = action
  }, [])
  const onManualRefresh = useCallback(() => refreshAction.current(), [])

  useEffect(() => {
    if (!targetId || refreshSeconds === 0) return
    const timer = window.setInterval(() => refreshAction.current(), refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [targetId, refreshSeconds])

  return (
    <div className="app">
      <AppHeader
        onHome={() => navigate(null)}
        refreshSeconds={refreshSeconds}
        onRefreshChange={setRefreshSeconds}
        onManualRefresh={onManualRefresh}
        refreshing={refreshing}
      />
      {targetId ? (
        <DetailPage key={targetId} targetId={targetId} onBack={() => navigate(null)} onRefreshStatus={registerRefresh} />
      ) : (
        <FleetPage
          onOpenTarget={(target) => navigate(target.id)}
          refreshSeconds={refreshSeconds}
          onRefreshStatus={registerRefresh}
        />
      )}
    </div>
  )
}
