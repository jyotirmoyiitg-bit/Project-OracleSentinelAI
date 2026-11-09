import { useEffect, useState } from 'react'
import { Database, LayoutDashboard, Plus, RefreshCw } from 'lucide-react'
import { ApiError, getFleet } from '../api'
import type { Target } from '../types'

export function DatabaseSidebar({
  selectedId,
  homeSelected,
  onHome,
  onSelect,
  onRegister,
  refreshSeconds,
  refreshKey,
}: {
  selectedId: string | null
  homeSelected: boolean
  onHome: () => void
  onSelect: (id: string) => void
  onRegister: () => void
  refreshSeconds: number
  refreshKey: number
}) {
  const [targets, setTargets] = useState<Target[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let inFlight = false
    async function refresh() {
      if (inFlight) return
      inFlight = true
      setLoading(true)
      setError('')
      try {
        const all: Target[] = []
        let page = 1
        let total: number
        do {
          const response = await getFleet({ page, pageSize: 100, environment: '', healthStatus: '' }, controller.signal)
          all.push(...response.items)
          total = response.total
          if (response.items.length === 0 && all.length < total) {
            throw new ApiError('The database list changed while loading. Please refresh it.')
          }
          page += 1
        } while (all.length < total)
        if (!controller.signal.aborted) setTargets([...new Map(all.map((target) => [target.id, target])).values()])
      } catch (reason) {
        if (controller.signal.aborted) return
        setError(reason instanceof ApiError ? reason.message : 'Could not load registered databases.')
      } finally {
        inFlight = false
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void refresh()
    const timer = refreshSeconds > 0 ? window.setInterval(() => { void refresh() }, refreshSeconds * 1000) : undefined
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [refreshSeconds, refreshKey, retry])

  return (
    <aside className="database-sidebar" aria-label="Registered databases">
      <div className="sidebar-heading"><h2>Databases</h2><span className="count-pill">{targets?.length ?? '-'}</span></div>
      <button className="button button-primary sidebar-add" onClick={onRegister}><Plus size={15} /> Add database</button>
      <nav aria-label="Database navigation">
        <button className={`sidebar-home ${homeSelected ? 'sidebar-selected' : ''}`} aria-current={homeSelected ? 'page' : undefined} onClick={onHome}>
          <LayoutDashboard size={17} /> Fleet overview
        </button>
        {loading && !targets && <p className="sidebar-note" role="status">Loading databases...</p>}
        {error && (
          <div className="sidebar-error" role="alert">
            <p>{error}</p>
            <button className="button button-secondary" onClick={() => setRetry((value) => value + 1)}><RefreshCw size={14} /> Retry database list</button>
          </div>
        )}
        {!error && targets?.length === 0 && <p className="sidebar-note">No databases registered. Add a database to begin monitoring.</p>}
        <ul className="sidebar-databases">
          {targets?.map((target) => {
            const health = target.enabled ? target.last_health_status.toLowerCase() : 'disabled'
            const label = health === 'healthy' ? 'Healthy' : health === 'offline' ? 'Down' : health === 'critical'
              ? 'Critical' : health === 'warning' ? 'Warning' : health === 'disabled' ? 'Disabled' : 'Unknown / stale'
            return (
              <li key={target.id}>
                <button
                  className={`sidebar-target ${selectedId === target.id ? 'sidebar-selected' : ''}`}
                  aria-label={`Select ${target.display_name}`}
                  aria-current={selectedId === target.id ? 'page' : undefined}
                  onClick={() => onSelect(target.id)}
                >
                  <Database size={16} aria-hidden="true" />
                  <span className="sidebar-target-name"><strong>{target.display_name}</strong><small>{target.tns_alias || 'No TNS alias'}</small></span>
                  <span className={`sidebar-health sidebar-health-${health}`} aria-label={`${target.display_name}: ${label}`} title={label} />
                </button>
              </li>
            )
          })}
        </ul>
      </nav>
      <p className="sidebar-note">Green: healthy. Yellow: warning. Red: critical or down. Gray: unknown, stale or disabled.</p>
      <p className="sidebar-note">Registrations are saved in PostgreSQL and reloaded after restart.</p>
    </aside>
  )
}
