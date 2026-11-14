import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, CircleAlert, Database, MessageSquare } from 'lucide-react'
import { ApiError, getFleet, getGraphActivity, getGraphMemory, getGraphShell, getGraphStorage, getTarget, type GraphActivity, type GraphMemory, type GraphMeter, type GraphStorage, type StorageFile } from './api'
import { ActiveDatabase } from './components/ActiveDatabase'
import { HealthBadge } from './components/HealthBadge'
import { StatePanel } from './components/StatePanel'
import { StorageCylinder, percentLabel } from './components/StorageCylinder'
import type { Target, TargetDetails } from './types'

const NOT_FOUND = 'Database target not found'
const HOST_MISSING = 'The host did not return that statistic.'
const LOADING = 'Loading the current snapshot…'
const NEEDS_SAMPLE = 'Rate needs a second sample.'
const RATE_NOT_COLLECTED = 'Not collected.'
const RATE_CAPTION = 'Since the previous refresh on this page.'
const RATE_COUNTERS = [
  ['physical reads', 'Physical reads', 'blks/s'],
  ['redo entries', 'Redo entries', 'redo ents/s'],
  ['parse count (total)', 'Parse count', 'parse reqs/s'],
  ['execute count', 'Execute count', 'SQL exec/s'],
] as const
const PILLS = [
  ['DBW', 'Database writer'],
  ['RVWR', 'Recovery writer'],
  ['LGWR', 'Redo log writer'],
  ['ARCH', 'Archiver'],
] as const
const SGA_FRONT = ['buffer cache size', 'recycle pool size', 'keep pool size'] as const
const SGA_BACK = ['java pool size', 'large pool size', 'redo buffers', 'redo buffer size', 'shared pool size'] as const
const PROGRAM_NAME = /\(([A-Z0-9_]+)\)/i

type RateMeter = { label: string; unit: string; text: string; perSecond: number | null }
type RateSample = { id: string; at: number; values: Record<string, number | null> }

function needsSample(): RateMeter[] {
  return RATE_COUNTERS.map(([, label, unit]) => ({ label, unit, text: NEEDS_SAMPLE, perSecond: null }))
}

function formatRate(rate: number): string {
  const rounded = rate >= 100 ? Math.round(rate) : Math.round(rate * 10) / 10
  return `${String(rounded).replace(/\.0$/, '')} /s`
}

function rateMeters(previous: RateSample | null, next: RateSample): RateMeter[] {
  const seconds = previous && previous.id === next.id ? (next.at - previous.at) / 1000 : 0
  const first = !previous || previous.id !== next.id
  return RATE_COUNTERS.map(([name, label, unit]) => {
    const current = next.values[name]
    const prior = previous?.values[name]
    if (first) return { label, unit, text: NEEDS_SAMPLE, perSecond: null }
    if (seconds < 1 || current == null || prior == null || current < prior) {
      return { label, unit, text: RATE_NOT_COLLECTED, perSecond: null }
    }
    return { label, unit, text: formatRate((current - prior) / seconds), perSecond: (current - prior) / seconds }
  })
}

function percentText(value: number | null): string {
  if (value == null) return 'The used percent was not returned.'
  return percentLabel(value)
}

function clockTime(iso: string | undefined): string {
  if (!iso) return ''
  // The API stamps UTC. A value without an offset is still UTC, not browser-local time.
  const stamped = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`
  const date = new Date(stamped)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function engineLabel(engine: string | undefined): string {
  if (!engine) return 'Oracle'
  return engine.toLowerCase() === 'oracle' ? 'Oracle' : engine
}

export function GraphPage({
  databaseId,
  onChoose,
  onBack,
  onAskCopilot,
  onOpenDatabase,
  onRefreshStatus,
}: {
  databaseId: string | null
  onChoose: (id: string) => void
  onBack: () => void
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [databases, setDatabases] = useState<Target[]>([])
  const [target, setTarget] = useState<TargetDetails | null>(null)
  const [memory, setMemory] = useState<GraphMemory | null>(null)
  const [memoryError, setMemoryError] = useState('')
  const [storage, setStorage] = useState<GraphStorage | null>(null)
  const [storageError, setStorageError] = useState('')
  const [activity, setActivity] = useState<GraphActivity | null>(null)
  const [activityError, setActivityError] = useState('')
  const [rates, setRates] = useState<RateMeter[]>(needsSample)
  const [readingsPending, setReadingsPending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [readingKey, setReadingKey] = useState(0)
  const rateSample = useRef<RateSample | null>(null)
  const shownId = useRef<string | null>(null)

  const refresh = useCallback(() => {
    if (shownId.current) setReadingKey((value) => value + 1)
    else setReloadKey((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true)
    setForbidden(false)
    setMissing(false)
    setError('')
    setTarget(null)
    getGraphShell(controller.signal)
      .then(async () => {
        if (!active) return
        const fleet = await getFleet({ page: 1, pageSize: 100, environment: '', healthStatus: '' }, controller.signal)
        if (!active) return
        const enabled = fleet.items.filter((item) => item.enabled)
        setDatabases(enabled)
        if (!databaseId) return
        const detail = await getTarget(databaseId, controller.signal)
        if (!active) return
        if (!detail.enabled) {
          setMissing(true)
          return
        }
        shownId.current = detail.id
        setTarget(detail)
      })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        if (reason instanceof ApiError && reason.status === 403) {
          setForbidden(true)
          setDatabases([])
          setTarget(null)
          return
        }
        if (reason instanceof ApiError && reason.status === 404) {
          setMissing(true)
          return
        }
        setError(reason instanceof ApiError ? reason.message : 'Could not open GraphDB.')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => {
      active = false
      controller.abort()
    }
  }, [databaseId, reloadKey])

  useEffect(() => {
    if (!target) return
    const controller = new AbortController()
    let active = true
    const id = target.id
    if (shownId.current !== id) {
      shownId.current = id
      rateSample.current = null
      setMemory(null)
      setStorage(null)
      setActivity(null)
      setMemoryError('')
      setStorageError('')
      setActivityError('')
      setRates(needsSample())
    }
    setReadingsPending(true)
    const memoryPromise = getGraphMemory(id, controller.signal)
      .then((picture) => { if (active) { setMemory(picture); setMemoryError('') } })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setMemory(null)
        setMemoryError(reason instanceof ApiError ? reason.message : 'Could not read memory structures.')
      })
    const storagePromise = getGraphStorage(id, controller.signal)
      .then((picture) => { if (active) { setStorage(picture); setStorageError('') } })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setStorage(null)
        setStorageError(reason instanceof ApiError ? reason.message : 'Could not read disk storage.')
      })
    const activityPromise = getGraphActivity(id, controller.signal)
      .then((picture) => {
        if (!active) return
        const sample: RateSample = {
          id,
          at: Date.now(),
          values: Object.fromEntries((picture.counters ?? []).map((item) => [item.name, item.value])),
        }
        setRates(rateMeters(rateSample.current, sample))
        rateSample.current = sample
        setActivity(picture)
        setActivityError('')
      })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setActivity(null)
        setActivityError(reason instanceof ApiError ? reason.message : 'Could not read service activity.')
        setRates(RATE_COUNTERS.map(([, label, unit]) => ({ label, unit, text: RATE_NOT_COLLECTED, perSecond: null })))
      })
    void Promise.allSettled([memoryPromise, storagePromise, activityPromise]).then(() => {
      if (active) setReadingsPending(false)
    })
    return () => {
      active = false
      controller.abort()
    }
  }, [target, readingKey])

  useEffect(() => {
    onRefreshStatus(loading || readingsPending, refresh)
  }, [loading, onRefreshStatus, readingsPending, refresh])

  if (loading && !forbidden && !missing && !target && !error) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Loading GraphDB…</div></main>
  }
  if (forbidden) {
    return (
      <main className="page-shell">
        <StatePanel tone="error" title="GraphDB is not available" message="Authorization failed." icon={<CircleAlert size={23} />} />
      </main>
    )
  }
  if (error && !target) {
    return (
      <main className="page-shell">
        <StatePanel
          tone="error"
          title="Could not open GraphDB"
          message={error}
          icon={<CircleAlert size={23} />}
          action={<button className="button button-primary" onClick={refresh}>Try again</button>}
        />
      </main>
    )
  }

  const collectedAt = [activity?.collected_at, memory?.collected_at, storage?.collected_at].filter(Boolean).sort().pop()

  return (
    <main className="page-shell graph-page">
      {databaseId && (
        <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Database choice</button>
      )}
      <section className="page-heading">
        <div>
          <div className="eyebrow">GRAPHDB</div>
          <h1>{target ? target.display_name : 'GraphDB'}</h1>
          <p>{target ? (target.tns_alias || 'TNS alias unavailable') : 'Choose one registered database. This page does not change Oracle.'}</p>
          {target && <ActiveDatabase target={target} />}
        </div>
        {target && (
          <div className="graph-actions">
            {databases.length > 1 && (
              <label className="filter-control graph-switch">
                <span className="sr-only">Graph database</span>
                <select aria-label="Graph database" value={target.id} onChange={(event) => onChoose(event.target.value)}>
                  {databases.map((item) => (
                    <option key={item.id} value={item.id}>{item.id === target.id ? `● ${item.display_name} (active)` : item.display_name}</option>
                  ))}
                </select>
              </label>
            )}
            <button className="button button-secondary" type="button" onClick={() => onOpenDatabase(target.id)}><Database size={14} /> Database detail</button>
            <button className="button button-primary" type="button" onClick={() => onAskCopilot(target.id)}><MessageSquare size={14} /> Ask copilot</button>
          </div>
        )}
      </section>
      {missing && (
        <StatePanel tone="error" title="Could not load database" message={NOT_FOUND} icon={<CircleAlert size={23} />} />
      )}
      {!databaseId && databases.length === 0 && !missing && (
        <StatePanel title="No databases registered" message="Register a database before opening GraphDB." icon={<Database size={24} />} />
      )}
      {!databaseId && databases.length > 0 && (
        <section className="panel" aria-label="Registered databases">
          <div className="panel-heading"><div><h2>Choose a database</h2><p>One database is shown at a time</p></div></div>
          <ul className="graph-choices">
            {databases.map((item) => (
              <li key={item.id}>
                <button className="database-link" type="button" onClick={() => onChoose(item.id)}>
                  <span className="database-icon"><Database size={16} /></span>
                  <span><strong>{item.display_name}</strong><small>{item.tns_alias || 'TNS alias unavailable'}</small></span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {target && (
        <>
          <section className="graph-board" aria-label="Graph columns">
            <div className="graph-column">
              <ServiceCard activity={activity} error={activityError} />
              <HostCard activity={activity} error={activityError} />
            </div>
            <ServerProcesses memory={memory} error={memoryError} activity={activity} activityError={activityError} />
            <div className="graph-column">
              <RateStrip meters={rates} />
              <SgaCard memory={memory} error={memoryError} />
            </div>
            <BackgroundCard memory={memory} error={memoryError} />
            <DiskStorage storage={storage} error={storageError} />
          </section>
          <footer className="graph-footer">
            <span>{engineLabel(target.engine)}</span>
            <span className="bullet">·</span>
            <span>{target.tns_alias || 'TNS alias unavailable'}</span>
            <span className="bullet">·</span>
            <span>{memory?.snapshot ?? 'Current instance snapshot.'}</span>
            {collectedAt && <><span className="bullet">·</span><span>Collected {clockTime(collectedAt)}</span></>}
          </footer>
        </>
      )}
    </main>
  )
}

function CardNote({ text }: { text: string | null | undefined }) {
  if (!text) return null
  return <p className="memory-note">{text}</p>
}

function Facts({ items, tone }: { items: Array<{ label: string; value: string; key?: string }>; tone?: 'good' | 'high' }) {
  return (
    <ul className={tone ? `graph-facts graph-facts-${tone}` : 'graph-facts'}>
      {items.map((item) => (
        <li key={item.key ?? item.label}><span>{item.label}</span><strong>{item.value}</strong></li>
      ))}
    </ul>
  )
}

function ServiceCard({ activity, error }: { activity: GraphActivity | null; error: string }) {
  const lines = activity?.service ?? []
  const identity = lines.filter((line) => ['Uptime', 'Container database', 'Pluggable database'].includes(line.label))
  const load = lines.filter((line) => !['Uptime', 'Container database', 'Pluggable database'].includes(line.label))
  return (
    <section className="panel graph-card" aria-labelledby="graph-Service">
      <div className="panel-heading"><h2 id="graph-Service">Service</h2></div>
      <CardNote text={error} />
      {!activity && !error && <p className="memory-note">{LOADING}</p>}
      {activity && (
        <>
          <Facts items={identity} />
          <hr className="graph-rule" />
          <Facts items={load} />
        </>
      )}
    </section>
  )
}

function HostCard({ activity, error }: { activity: GraphActivity | null; error: string }) {
  const host = activity?.host
  const cpu = host?.cpu_percent
  const queue = host?.run_queue == null ? null : Number.parseFloat(host.run_queue)
  const queueWidth = queue == null || Number.isNaN(queue) ? 0 : Math.min(100, queue * 25)
  return (
    <section className="panel graph-card" aria-labelledby="graph-Host">
      <div className="panel-heading"><h2 id="graph-Host">Host</h2></div>
      <CardNote text={error} />
      {!activity && !error && <p className="memory-note">{LOADING}</p>}
      {host && (
        <>
          <p className="graph-label">Total CPU usage</p>
          <div className={cpu == null ? 'cpu-donut cpu-donut-empty' : 'cpu-donut'} style={cpu == null ? undefined : { ['--cpu' as string]: `${cpu}%` }}>
            <span>{percentLabel(cpu ?? null)}</span>
          </div>
          <p className="memory-note">{host.cpu_caption}</p>
          <hr className="graph-rule" />
          <p className="graph-label">Run queue</p>
          <div className="graph-meter-row">
            <span className="graph-meter-track" aria-hidden="true"><span className="graph-meter-fill" style={{ width: `${queueWidth}%` }} /></span>
            <strong>{host.run_queue ?? '—'}</strong>
          </div>
          {host.run_queue == null && <p className="memory-note">{HOST_MISSING}</p>}
          <hr className="graph-rule" />
          <p className="graph-label">Memory</p>
          <Facts items={[{ label: 'Free', value: host.free_memory ?? '—' }]} />
          {host.free_memory == null && <p className="memory-note">{HOST_MISSING}</p>}
        </>
      )}
    </section>
  )
}

function Oval({ name, count, limitation }: { name: string; count: number | null; limitation: string | null }) {
  return (
    <li className={count == null ? 'graph-oval graph-oval-empty' : 'graph-oval'} title={count == null ? limitation ?? HOST_MISSING : undefined}>
      <span>{name}</span>
      <strong aria-label={count == null ? `${name} ${limitation ?? HOST_MISSING}` : undefined}>{count == null ? '—' : count}</strong>
    </li>
  )
}

function ServerProcesses({
  memory,
  error,
  activity,
  activityError,
}: {
  memory: GraphMemory | null
  error: string
  activity: GraphActivity | null
  activityError: string
}) {
  const target = memory?.pga.items.find((item) => item.name === 'Target')
  const used = memory?.pga.items.find((item) => item.name === 'Used')
  const ovals = (activity?.server_processes ?? []).filter((item) => item.name !== 'Total')
  const total = activity?.server_processes.find((item) => item.name === 'Total')
  return (
    <section className="panel graph-card" aria-labelledby="graph-processes">
      <div className="panel-heading"><h2 id="graph-processes">Server processes</h2></div>
      <CardNote text={error} />
      {!memory && !error && <p className="memory-note">{LOADING}</p>}
      {memory && (
        <>
          <p className="graph-label">PGA</p>
          <CardNote text={memory.pga.limitation} />
          <Facts
            tone={memory.pga.high ? 'high' : undefined}
            items={[
              { label: 'Target', value: target?.display ?? '—' },
              { label: 'Used', value: used?.display ?? '—' },
            ]}
          />
          <hr className="graph-rule" />
        </>
      )}
      <CardNote text={activityError} />
      {activity && (
        <>
          <ul className="graph-ovals">
            {ovals.map((item) => <Oval key={item.name} name={item.name} count={item.count} limitation={item.limitation} />)}
          </ul>
          {total && (
            <div className="graph-total">
              <span>Total</span>
              <strong>{total.count == null ? total.limitation ?? HOST_MISSING : total.count}</strong>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function RateStrip({ meters }: { meters: RateMeter[] }) {
  const widest = Math.max(0, ...meters.map((item) => item.perSecond ?? 0))
  const measured = meters.some((item) => item.perSecond != null)
  return (
    <section className="panel graph-card rate-strip" aria-label="Rate meters">
      <ul>
        {meters.map((item) => {
          const share = item.perSecond == null ? 0 : widest > 0 ? (item.perSecond / widest) * 100 : 0
          return (
            <li key={item.label} className={item.perSecond == null ? 'rate-pending' : undefined}>
              <span className="rate-head"><span>{item.label}</span><small>{item.unit}</small></span>
              <span className="rate-flow" aria-hidden="true">
                <span className="rate-track" />
                {item.perSecond != null && <span className="rate-fill" style={{ width: `${Math.max(share > 0 ? 6 : 0, share)}%` }} />}
              </span>
              <strong>{item.text}</strong>
            </li>
          )
        })}
      </ul>
      <p>{measured ? RATE_CAPTION : 'Rates come from two refreshes of this page.'}</p>
    </section>
  )
}

function PercentMeter({ label, meter }: { label: string; meter: GraphMeter }) {
  const percent = meter.percent
  const width = percent == null || percent <= 0 ? 0 : Math.min(100, percent)
  return (
    <div className="graph-percent">
      <span className="graph-label">{label}</span>
      <span className="graph-meter-track" aria-hidden="true">
        {percent != null && <span className="graph-meter-fill" style={{ width: `${width === 0 ? 0 : Math.max(4, width)}%` }} />}
      </span>
      <strong>{percent == null ? meter.caption : percentLabel(percent)}</strong>
      {percent != null && <small className="meter-caption">{meter.caption}</small>}
    </div>
  )
}

function poolItems(memory: GraphMemory, names: readonly string[]) {
  return names
    .map((name) => memory.sga.items.find((item) => item.name.toLowerCase() === name))
    .filter((item): item is GraphMemory['sga']['items'][number] => Boolean(item))
}

function SgaCard({ memory, error }: { memory: GraphMemory | null; error: string }) {
  const front = memory ? poolItems(memory, SGA_FRONT) : []
  const back = memory ? poolItems(memory, SGA_BACK) : []
  const named = new Set([...front, ...back].map((item) => item.name))
  const other = memory ? memory.sga.items.filter((item) => !named.has(item.name)) : []
  const widest = Math.max(1, ...(memory?.sga.items.map((item) => item.bytes ?? 0) ?? [1]))
  const management = memory?.memory_management.lines ?? []
  const auto = management.find((line) => line.startsWith('Automatic Shared Memory Management'))
  return (
    <section className="panel graph-card" aria-labelledby="graph-sga">
      <div className="panel-heading">
        <div>
          <h2 id="graph-sga">SGA</h2>
          <p>{memory?.snapshot ?? 'Current instance snapshot.'}</p>
        </div>
      </div>
      <CardNote text={error} />
      {!memory && !error && <p className="memory-note">{LOADING}</p>}
      {memory && (
        <div className="memory-block">
          <CardNote text={memory.sga.limitation} />
          <PoolBars items={front} widest={widest} />
          <PercentMeter label="Buffer cache hit ratio" meter={memory.hit_ratio} />
          <div className="graph-percent">
            <span className="graph-label">In-memory usage</span>
            <span className="graph-meter-track" aria-hidden="true">{memory.in_memory.in_use && <span className="graph-meter-fill" style={{ width: '100%' }} />}</span>
            <strong>{memory.in_memory.limitation ?? memory.in_memory.display}</strong>
          </div>
          <hr className="graph-rule" />
          <p className="graph-label">Current SGA</p>
          <p className="memory-caption">{memory.sga.caption}</p>
          <PoolBars items={back} widest={widest} />
          <PoolBars items={other} widest={widest} />
          <PercentMeter label="Shared pool used" meter={memory.shared_pool_used} />
          <hr className="graph-rule" />
          <p className="memory-note">Result cache {memory.result_cache}</p>
          <CardNote text={memory.memory_management.limitation} />
          {management.filter((line) => line !== auto).map((line) => <p className="memory-note" key={line}>{line}</p>)}
          <div className="graph-card-foot">{auto ?? 'Automatic Shared Memory Management was not returned.'}</div>
        </div>
      )}
    </section>
  )
}

function PoolBars({ items, widest }: { items: GraphMemory['sga']['items']; widest: number }) {
  if (items.length === 0) return null
  return (
    <ul className="memory-bars">
      {items.map((item) => (
        <li key={item.name}>
          <span>{item.name}</span>
          <strong>{item.display}</strong>
          <span className="memory-track" aria-hidden="true">
            <span className="memory-fill" style={{ width: `${Math.max(4, ((item.bytes ?? 0) / widest) * 100)}%` }} />
          </span>
        </li>
      ))}
    </ul>
  )
}

function shortName(program: string): string {
  const match = PROGRAM_NAME.exec(program)
  return match ? match[1].toUpperCase() : program
}

function BackgroundCard({ memory, error }: { memory: GraphMemory | null; error: string }) {
  const processes = memory?.background_processes ?? []
  const processNotes = (memory?.limitations ?? []).filter((item) => item.includes('V_$SESSION') || item.includes('first 80'))
  return (
    <section className="panel graph-card" aria-labelledby="graph-background">
      <div className="panel-heading"><h2 id="graph-background">Background processes</h2></div>
      <CardNote text={error} />
      {!memory && !error && <p className="memory-note">{LOADING}</p>}
      {memory && (
        <>
          <ul className="graph-pills">
            {PILLS.map(([mark, label]) => {
              const matches = processes.filter((item) => item.program.toUpperCase().includes(mark))
              const names = Array.from(new Set(matches.map((item) => shortName(item.program))))
              return (
                <li key={mark}>
                  <span className="graph-label">{label}</span>
                  {names.length === 0
                    ? <span className="graph-pill graph-pill-empty" title="No background session with this name was returned.">{mark}</span>
                    : names.map((name) => <span className="graph-pill" key={name}>{name}</span>)}
                </li>
              )
            })}
          </ul>
          <hr className="graph-rule" />
          <p className="graph-label">Data Guard</p>
          <CardNote text={memory.data_guard.limitation} />
          {memory.data_guard.stats.length > 0 && (
            <Facts items={memory.data_guard.stats.map((stat) => ({ label: stat.name, value: stat.value }))} />
          )}
          <hr className="graph-rule" />
          <p className="graph-label">All background sessions</p>
          {processNotes.map((item) => <p className="memory-note" key={item}>{item}</p>)}
          <ul className="memory-processes">
            {processes.map((item) => (
              <li key={`${item.program}-${item.status}-${item.state}`}>
                <span>{item.program}</span>
                <span>{item.status}</span>
                <span>{item.state}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function PathName({ value }: { value: string }) {
  const parts = value.split('\\')
  return (
    <span className="storage-path">
      {parts.map((part, index) => (
        <span key={`${index}-${part}`}>
          {index > 0 && <>\<wbr /></>}
          {part}
        </span>
      ))}
    </span>
  )
}

function FileList({ files, temp = false }: { files: StorageFile[]; temp?: boolean }) {
  return (
    <ul className="storage-files storage-file-list storage-mono">
      {files.map((file) => (
        <li key={`${file.tablespace}-${file.name}`}>
          <PathName value={file.name} />
          <span className="storage-meta">
            {temp ? 'Temp' : file.tablespace}{' '}{file.display}
            {!file.online && <> <strong className="storage-offline">Offline</strong></>}
          </span>
        </li>
      ))}
    </ul>
  )
}

function StorageBlock({
  id,
  title,
  percent,
  empty,
  facts,
  note,
}: {
  id: string
  title: string
  percent: number | null
  empty?: boolean
  facts: Array<{ label: string; value: string }>
  note?: string | null
}) {
  return (
    <div className="storage-block" aria-labelledby={id}>
      <h3 id={id} className="graph-label">{title}</h3>
      <div className="storage-head">
        <StorageCylinder percent={percent} empty={empty} />
        <div className="storage-summary">
          {facts.map((fact) => (
            <div key={fact.label}><span>{fact.label}</span><strong>{fact.value}</strong></div>
          ))}
        </div>
      </div>
      <CardNote text={note} />
    </div>
  )
}

function DiskStorage({ storage, error }: { storage: GraphStorage | null; error: string }) {
  const data = storage?.data_files
  const redo = storage?.redo_logs
  const recovery = storage?.recovery_area
  const archive = storage?.archive
  const backup = storage?.backup
  const detail = storage?.detail !== false
  return (
    <section className="panel graph-card storage-card" aria-labelledby="graph-storage">
      <div className="panel-heading"><h2 id="graph-storage">Disk storage</h2></div>
      <CardNote text={error} />
      {!storage && !error && <p className="memory-note">{LOADING}</p>}
      {storage && data && redo && recovery && archive && backup && (
        <>
          <StorageBlock
            id="graph-database-files"
            title="Database files"
            percent={data.used_percent}
            empty={data.files.length === 0 && data.used_percent == null}
            facts={[
              { label: 'Online files', value: String(data.online_files) },
              { label: 'Online tablespaces', value: String(data.online_tablespaces) },
              { label: 'Used', value: data.used_display ? `${data.used_display} ${percentText(data.used_percent)}` : percentText(data.used_percent) },
              { label: 'Control files', value: String(storage.control_files.count) },
              { label: 'Flash cache', value: data.flash_cache },
            ]}
            note={data.limitation}
          />
          <hr className="graph-rule" />
          <StorageBlock
            id="graph-recovery"
            title="Fast recovery area"
            percent={recovery.used_percent}
            empty={!recovery.present}
            facts={recovery.present
              ? [
                  { label: 'Size', value: recovery.limit_display || '—' },
                  { label: 'Used', value: recovery.used_display ? `${recovery.used_display} ${percentText(recovery.used_percent)}` : percentText(recovery.used_percent) },
                ]
              : [{ label: 'Size', value: '—' }, { label: 'Used', value: '—' }]}
            note={recovery.limitation}
          />
          {recovery.usage.length > 0 && (
            <ul className="storage-files storage-usage">
              {recovery.usage.map((item) => (
                <li key={item.file_type}>
                  <span>{item.file_type}</span>
                  <span className="storage-meta">{item.percent_space_used == null ? '' : `${item.percent_space_used}%`} {item.number_of_files ?? ''} files</span>
                </li>
              ))}
            </ul>
          )}
          <hr className="graph-rule" />
          <StorageBlock
            id="graph-redo"
            title="Redo logs"
            percent={redo.active_percent}
            empty={redo.groups === 0}
            facts={[
              { label: 'Groups', value: String(redo.groups) },
              { label: 'Size', value: redo.total_display || '—' },
            ]}
            note={redo.limitation}
          />
          <hr className="graph-rule" />
          <StorageBlock
            id="graph-archive"
            title="Archive log"
            percent={null}
            empty
            facts={[
              { label: 'Log mode', value: archive.log_mode || '—' },
              { label: 'Latest', value: archive.latest || '—' },
              { label: 'Free (min)', value: archive.free_minutes },
              { label: 'Time to failure', value: archive.time_to_failure },
            ]}
            note={archive.limitation}
          />
          {archive.destinations.length > 0 && (
            <ul className="storage-files storage-file-list storage-mono">
              {archive.destinations.map((item) => (
                <li key={`${item.destination}-${item.status}`}>
                  <span className="storage-path">{item.destination}</span>
                  <span className="storage-meta">{item.status}</span>
                </li>
              ))}
            </ul>
          )}
          <hr className="graph-rule" />
          <div className="storage-block" aria-labelledby="graph-backup">
            <h3 id="graph-backup" className="graph-label">Backup</h3>
            <CardNote text={backup.limitation} />
            {backup.status && (
              <p className="memory-note storage-backup">
                {backup.input_type} {backup.status}
                {backup.started ? ` ${backup.started}` : ''}
                {backup.ended ? ` ${backup.ended}` : ''}
                {backup.failed && <> <HealthBadge status="critical" /></>}
              </p>
            )}
          </div>
          {detail ? <StorageListings storage={storage} /> : <div className="graph-card-foot">{storage.detail_note}</div>}
        </>
      )}
    </section>
  )
}

function StorageListings({ storage }: { storage: GraphStorage }) {
  const data = storage.data_files
  const redo = storage.redo_logs
  return (
    <div className="storage-listings">
      <hr className="graph-rule" />
      <details className="graph-details">
        <summary>Data files <span className="count-pill">{data.files.length}</span></summary>
        <FileList files={data.files} />
      </details>
      <details className="graph-details">
        <summary>Temp files <span className="count-pill">{storage.temp_files.files.length}</span></summary>
        <CardNote text={storage.temp_files.limitation} />
        <FileList files={storage.temp_files.files} temp />
      </details>
      <details className="graph-details">
        <summary>Control files <span className="count-pill">{storage.control_files.files.length}</span></summary>
        <CardNote text={storage.control_files.limitation} />
        <ul className="storage-files storage-file-list storage-mono">
          {storage.control_files.files.map((file) => (
            <li key={file.name}><PathName value={file.name} /><span className="storage-meta">{file.status}</span></li>
          ))}
        </ul>
      </details>
      <details className="graph-details">
        <summary>Redo members <span className="count-pill">{redo.members.length}</span></summary>
        <ul className="storage-files storage-file-list storage-mono">
          {redo.members.map((member) => (
            <li key={`${member.group}-${member.path}`}>
              <PathName value={member.path} />
              <span className="storage-meta">
                {member.display} {member.status}
                {member.current && <> <strong className="storage-current">Current group</strong></>}
              </span>
            </li>
          ))}
        </ul>
      </details>
      <details className="graph-details">
        <summary>Largest segments <span className="count-pill">{storage.segments.rows.length}</span></summary>
        <p className="memory-caption">These are the 20 largest segments.</p>
        <CardNote text={storage.segments.limitation} />
        <ul className="storage-files storage-mono">
          {storage.segments.rows.map((row) => (
            <li key={`${row.owner}-${row.name}-${row.segment_type}`}>
              <span className="storage-path">{row.owner} {row.name}</span>
              <span className="storage-meta">{row.segment_type} {row.tablespace} {row.display}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  )
}
