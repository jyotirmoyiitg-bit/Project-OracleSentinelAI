import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, Database, MessageSquare } from 'lucide-react'
import { ApiError, getTarget } from '../api'
import type { TargetDetails } from '../types'
import { ActiveDatabase } from './ActiveDatabase'
import { StatePanel } from './StatePanel'

const NOT_FOUND = 'Database target not found'

/** A time the API stamped in UTC, shown in the browser's own time. */
export function clockTime(iso: string | undefined): string {
  if (!iso) return ''
  const stamped = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const date = new Date(stamped)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** Bytes as megabytes with two decimals, the unit the tablespace and SGA tables use. */
export function megabytes(value: number | null | undefined): string {
  if (value == null) return '—'
  return (value / 1024 / 1024).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export interface Bar {
  label: string
  note?: string
  segments: Array<{ name: string; value: number; tone: string }>
}

/** Horizontal bars on one shared scale. Each bar is split into named segments, such as used and free. */
export function StackedBars({ title, unit, bars, legend }: {
  title: string
  unit: string
  bars: Bar[]
  legend: Array<{ name: string; tone: string }>
}) {
  const peak = Math.max(1, ...bars.map((bar) => bar.segments.reduce((sum, item) => sum + item.value, 0)))
  return (
    <div className="section-bars" role="img" aria-label={`${title}, ${unit}`}>
      <ul className="section-legend">
        {legend.map((item) => <li key={item.name}><span className={`section-swatch section-tone-${item.tone}`} />{item.name}</li>)}
      </ul>
      <ul className="section-bar-list">
        {bars.map((bar) => {
          const total = bar.segments.reduce((sum, item) => sum + item.value, 0)
          return (
            <li key={bar.label}>
              <span className="section-bar-label" title={bar.label}>{bar.label}</span>
              <span className="section-bar-track">
                {bar.segments.filter((item) => item.value > 0).map((item) => (
                  <span
                    key={item.name}
                    className={`section-bar-fill section-tone-${item.tone}`}
                    style={{ width: `${(item.value / peak) * 100}%` }}
                    title={`${item.name}: ${megabytes(item.value)} MB`}
                  />
                ))}
              </span>
              <span className="section-bar-value">{bar.note ?? `${megabytes(total)} MB`}</span>
            </li>
          )
        })}
      </ul>
      <p className="section-axis">{unit}. The longest bar is {megabytes(peak)} MB.</p>
    </div>
  )
}

/** A value between 0 and 100 as a thin meter with its number. */
export function PercentBar({ percent }: { percent: number | null }) {
  if (percent == null) return <span className="section-muted">—</span>
  const tone = percent >= 95 ? 'bad' : percent >= 85 ? 'warn' : 'good'
  return (
    <span className="section-percent">
      <span className="section-percent-track"><span className={`section-percent-fill section-percent-${tone}`} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} /></span>
      <span>{percent.toFixed(percent % 1 === 0 ? 0 : 1)}%</span>
    </span>
  )
}

/**
 * The frame every database section shares: it loads the database, then the section's picture, and
 * shows the heading, the loading and error states, and when the picture was collected.
 */
export function DatabaseView<T extends { collected_at?: string; limitation?: string | null; detail_note?: string | null }>({
  databaseId,
  eyebrow,
  lead,
  load,
  onAskCopilot,
  onOpenDatabase,
  onRefreshStatus,
  children,
}: {
  databaseId: string
  eyebrow: string
  lead: string
  load: (id: string, signal: AbortSignal) => Promise<T>
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
  children: (picture: T, target: TargetDetails) => ReactNode
}) {
  const [target, setTarget] = useState<TargetDetails | null>(null)
  const [picture, setPicture] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [state, setState] = useState<'ok' | 'forbidden' | 'missing' | 'error'>('ok')
  const [error, setError] = useState('')
  const [pictureError, setPictureError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const loader = useRef(load)
  loader.current = load

  const refresh = useCallback(() => setReloadKey((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setPending(true)
    const run = async () => {
      try {
        const detail = target && target.id === databaseId ? target : await getTarget(databaseId, controller.signal)
        if (!active) return
        if (!detail.enabled) {
          setState('missing')
          return
        }
        setTarget(detail)
        setState('ok')
        try {
          const next = await loader.current(detail.id, controller.signal)
          if (!active) return
          setPicture(next)
          setPictureError('')
        } catch (reason) {
          if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
          if (reason instanceof ApiError && reason.status === 403) setState('forbidden')
          else if (reason instanceof ApiError && reason.status === 404) setState('missing')
          // A failed refresh keeps the picture that is already on the page.
          else setPictureError(reason instanceof ApiError ? reason.message : 'Could not read this section.')
        }
      } catch (reason) {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        if (reason instanceof ApiError && reason.status === 403) setState('forbidden')
        else if (reason instanceof ApiError && reason.status === 404) setState('missing')
        else {
          setState('error')
          setError(reason instanceof ApiError ? reason.message : 'Could not open this section.')
        }
      } finally {
        if (active) {
          setLoading(false)
          setPending(false)
        }
      }
    }
    void run()
    return () => {
      active = false
      controller.abort()
    }
    // The database is loaded once; a refresh reads the picture again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databaseId, reloadKey])

  useEffect(() => {
    onRefreshStatus(loading || pending, refresh)
  }, [loading, onRefreshStatus, pending, refresh])

  if (loading && !target && state === 'ok') {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Loading {eyebrow}…</div></main>
  }
  if (state === 'forbidden') {
    return <main className="page-shell"><StatePanel tone="error" title={`${eyebrow} is not available`} message="Authorization failed." icon={<CircleAlert size={23} />} /></main>
  }
  if (state === 'missing') {
    return <main className="page-shell"><StatePanel tone="error" title="Could not load database" message={NOT_FOUND} icon={<CircleAlert size={23} />} /></main>
  }
  if (state === 'error' || !target) {
    return (
      <main className="page-shell">
        <StatePanel
          tone="error" title={`Could not open ${eyebrow}`} message={error || 'Could not open this section.'}
          icon={<CircleAlert size={23} />}
          action={<button className="button button-primary" onClick={refresh}>Try again</button>}
        />
      </main>
    )
  }

  return (
    <main className="page-shell graph-page section-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">{eyebrow.toUpperCase()}</div>
          <h1>{target.display_name}</h1>
          <p>{lead}</p>
          <ActiveDatabase target={target} />
        </div>
        <div className="graph-actions">
          {picture?.collected_at && <span className="section-collected">Collected {clockTime(picture.collected_at)}</span>}
          <button className="button button-secondary" type="button" onClick={() => onOpenDatabase(target.id)}><Database size={14} /> Database detail</button>
          <button className="button button-primary" type="button" onClick={() => onAskCopilot(target.id)}><MessageSquare size={14} /> Ask copilot</button>
        </div>
      </section>
      {pictureError && <p className="memory-note section-note" role="alert">{pictureError}</p>}
      {!picture && !pictureError && <div className="loading-state" role="status"><span className="spinner" /> Reading the database…</div>}
      {picture?.limitation && <p className="memory-note section-note">{picture.limitation}</p>}
      {picture?.detail_note && <p className="section-detail-note">{picture.detail_note}</p>}
      {picture && children(picture, target)}
    </main>
  )
}

/** Tabs inside one section, such as SGA utilization and Shared pool. */
export function SectionTabs<Id extends string>({ label, tabs, active, onChange }: {
  label: string
  tabs: ReadonlyArray<readonly [Id, string]>
  active: Id
  onChange: (id: Id) => void
}) {
  return (
    <div className="wait-tabs" role="tablist" aria-label={label}>
      {tabs.map(([id, name]) => (
        <button key={id} type="button" role="tab" aria-selected={active === id} onClick={() => onChange(id)}>{name}</button>
      ))}
    </div>
  )
}
