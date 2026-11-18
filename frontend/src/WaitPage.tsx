import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, CircleAlert, Database, MessageSquare } from 'lucide-react'
import { ApiError, getFleet, getGraphHistory, getGraphWaits, getTarget, getWaitActivity, type GraphHistorySample, type GraphWaits, type WaitActivity, type WaitBlocking, type WaitLock } from './api'
import { ActiveDatabase } from './components/ActiveDatabase'
import { StatePanel } from './components/StatePanel'
import type { Target, TargetDetails } from './types'
import { PieChart, SeriesChart } from './WaitCharts'
import { sampleFrom, waitView, WaitPanel, type WaitPoint, type WaitRow, type WaitSample } from './WaitStats'

const NOT_FOUND = 'Database target not found'
const NEEDS_SAMPLE = 'Needs a second sample.'
const NOT_COLLECTED = 'Not collected.'
const LIVE_CAPTION = 'Rates and charts are since the previous sample on this page. This is not a stored history.'
/** A second reading soon after the page opens, so live rates do not wait for the refresh interval. */
export const QUICK_SAMPLE_MS = 3000
const COUNT_ROWS = [
  ['parse count (total)', 'Parse count'],
  ['parse count (hard)', 'Hard parses'],
  ['parse count (failures)', 'Parse failures'],
] as const
const TIME_ROWS = [
  ['parse time elapsed', 'Parse time'],
  ['hard parse elapsed time', 'Hard parse time'],
  ['failed parse elapsed time', 'Failed parse time'],
] as const
const BLOCK_SERIES = [
  ['db block changes', 'Block changes'],
  ['physical reads', 'Physical reads'],
  ['physical reads direct', 'Direct reads'],
  ['consistent gets', 'Consistent gets'],
  ['physical writes direct', 'Direct writes'],
  ['physical writes', 'Physical writes'],
] as const
const TABS = [
  ['summary', 'Summary'],
  ['events', 'Event waits'],
  ['blocking', 'Blocking locks'],
  ['locks', 'Lock activity'],
  ['buffer', 'Buffer busy waits'],
] as const

type Tab = (typeof TABS)[number][0]
type IoSample = {
  id: string
  at: number
  blocks: Record<string, number | null>
  reads: number | null
  writes: number | null
  readCs: number | null
  writeCs: number | null
}
type LockSample = { id: string; at: number; rows: WaitLock[] }

type Named = { name: string; value: number | null }
type ParseSample = { id: string; at: number; counts: Record<string, number | null>; times: Record<string, number | null> }

function namedMap(rows: Named[]): Record<string, number | null> {
  return Object.fromEntries(rows.map((item) => [item.name, item.value]))
}

function countText(value: number): string {
  return value.toLocaleString('en-US')
}

function secondsText(micro: number): string {
  return `${(micro / 1_000_000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} s`
}

function countRate(delta: number, gap: number): string {
  const rate = delta / gap
  const rounded = rate >= 100 ? Math.round(rate) : Math.round(rate * 10) / 10
  return `${String(rounded).replace(/\.0$/, '')} /s`
}

function timeRate(deltaMicro: number, gap: number): string {
  return `${(deltaMicro / 1_000_000 / gap).toFixed(2)} /s`
}

function rateText(
  current: number | null | undefined,
  prior: number | null | undefined,
  first: boolean,
  usable: boolean,
  format: (delta: number) => string,
): string {
  if (first) return NEEDS_SAMPLE
  if (!usable || current == null || prior == null || current < prior) return NOT_COLLECTED
  return format(current - prior)
}

function percent(part: number | null | undefined, whole: number | null | undefined): string {
  if (part == null || whole == null || whole <= 0) return NOT_COLLECTED
  return `${(part / whole * 100).toFixed(2)}%`
}

function share(part: number | null, whole: number): string {
  if (part == null || whole <= 0) return '—'
  return (part / whole * 100).toFixed(2)
}

function plainRate(delta: number, gap: number): string {
  const rate = delta / gap
  const rounded = rate >= 100 ? Math.round(rate) : Math.round(rate * 10) / 10
  return String(rounded).replace(/\.0$/, '')
}

function clockLabel(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function remember(current: WaitPoint[], point: WaitPoint | null): WaitPoint[] {
  return point ? [...current, point].slice(-24) : current
}

function changed(current: number | null | undefined, prior: number | null | undefined, gap: number): number | null {
  if (current == null || prior == null || current < prior || gap < 1) return null
  return (current - prior) / gap
}

function dropEmpty(values: Record<string, number | null>): Record<string, number> {
  return Object.fromEntries(Object.entries(values).filter((entry): entry is [string, number] => entry[1] != null))
}

function latency(
  currentCs: number | null,
  priorCs: number | null,
  currentCount: number | null,
  priorCount: number | null,
): number | null {
  if (currentCs == null || priorCs == null || currentCount == null || priorCount == null) return null
  if (currentCs < priorCs || currentCount <= priorCount) return null
  return ((currentCs - priorCs) * 10) / (currentCount - priorCount)
}

type IoPoints = { blocks: Record<string, number>; files: Record<string, number>; delays: Record<string, number> }

/** Rates between two I/O samples of one database. Null when there is no usable earlier sample. */
function ioPoints(prior: IoSample | null, next: IoSample): IoPoints | null {
  if (!prior || prior.id !== next.id) return null
  const gap = (next.at - prior.at) / 1000
  if (gap < 1) return null
  return {
    blocks: Object.fromEntries(BLOCK_SERIES.flatMap(([name, labelName]) => {
      const rate = changed(next.blocks[name], prior.blocks[name], gap)
      return rate == null ? [] : [[labelName, rate]]
    })),
    files: dropEmpty({
      'Data file read': changed(next.reads, prior.reads, gap),
      'Data file write': changed(next.writes, prior.writes, gap),
    }),
    delays: dropEmpty({
      'Data file read': latency(next.readCs, prior.readCs, next.reads, prior.reads),
      'Data file write': latency(next.writeCs, prior.writeCs, next.writes, prior.writes),
    }),
  }
}

function storedIo(sample: GraphHistorySample, id: string, at: number): IoSample {
  return {
    id,
    at,
    blocks: namedMap(sample.io),
    reads: sample.files.reads,
    writes: sample.files.writes,
    readCs: sample.files.read_time_cs,
    writeCs: sample.files.write_time_cs,
  }
}

function topPoint(previous: WaitSample | null, next: WaitSample, gap: number): WaitPoint | null {
  if (!previous || previous.id !== next.id || gap < 1) return null
  const values: Record<string, number> = {}
  if (next.cpuMicro != null && previous.cpuMicro != null && next.cpuMicro >= previous.cpuMicro) {
    values['CPU time'] = (next.cpuMicro - previous.cpuMicro) / 1000 / gap
  }
  for (const event of next.events) {
    const earlier = previous.events.find((item) => item.event === event.event)
    const rate = changed(event.time_waited_micro, earlier?.time_waited_micro, gap)
    if (rate != null) values[event.event] = rate / 1000
  }
  const top = Object.entries(values).sort((left, right) => right[1] - left[1]).slice(0, 5)
  return top.length ? { label: clockLabel(next.at), values: Object.fromEntries(top) } : null
}

export function WaitPage({
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
  const [waitRows, setWaitRows] = useState<WaitRow[]>([])
  const [waitPoints, setWaitPoints] = useState<WaitPoint[]>([])
  const [waitLimitations, setWaitLimitations] = useState<string[]>([])
  const [waitCaption, setWaitCaption] = useState('')
  const [waitError, setWaitError] = useState('')
  const [activity, setActivity] = useState<WaitActivity | null>(null)
  const [activityError, setActivityError] = useState('')
  const [parseRates, setParseRates] = useState<Record<string, string>>({})
  const [bufferRows, setBufferRows] = useState<WaitRow[]>([])
  const [tab, setTab] = useState<Tab>('summary')
  const [blockPoints, setBlockPoints] = useState<WaitPoint[]>([])
  const [topPoints, setTopPoints] = useState<WaitPoint[]>([])
  const [filePoints, setFilePoints] = useState<WaitPoint[]>([])
  const [latencyPoints, setLatencyPoints] = useState<WaitPoint[]>([])
  const [lockPoints, setLockPoints] = useState<WaitPoint[]>([])
  const [lockRates, setLockRates] = useState<Record<string, { gets: string; waits: string; time: string }>>({})
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [readingKey, setReadingKey] = useState(0)
  const [pending, setPending] = useState(false)
  const [historyCaption, setHistoryCaption] = useState('')
  const waitSample = useRef<WaitSample | null>(null)
  const openedId = useRef<string | null>(null)
  const quickId = useRef<string | null>(null)
  const parseSample = useRef<ParseSample | null>(null)
  const bufferSample = useRef<WaitSample | null>(null)
  const ioSample = useRef<IoSample | null>(null)
  const lockSample = useRef<LockSample | null>(null)
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
    getFleet({ page: 1, pageSize: 100, environment: '', healthStatus: '' }, controller.signal)
      .then(async (fleet) => {
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
        setError(reason instanceof ApiError ? reason.message : 'Could not open WaitDB.')
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
      waitSample.current = null
      parseSample.current = null
      bufferSample.current = null
      setWaitRows([])
      setWaitPoints([])
      setWaitLimitations([])
      setWaitCaption('')
      setWaitError('')
      setActivity(null)
      setActivityError('')
      setParseRates({})
      setBufferRows([])
      setBlockPoints([])
      setTopPoints([])
      setFilePoints([])
      setLatencyPoints([])
      setLockPoints([])
      setLockRates({})
      ioSample.current = null
      lockSample.current = null
      setHistoryCaption('')
    }
    // The first reading of a database replays its stored samples, so the charts open filled.
    const opening = openedId.current !== id
    let quickTimer: number | undefined
    const historyPromise = !opening ? Promise.resolve() : getGraphHistory(id, controller.signal)
      .then((history) => {
        if (!active || openedId.current === id) return
        openedId.current = id
        waitSample.current = null
        ioSample.current = null
        const fetchedAt = Date.now()
        const waits: WaitPoint[] = []
        const tops: WaitPoint[] = []
        const blocks: WaitPoint[] = []
        const files: WaitPoint[] = []
        const delays: WaitPoint[] = []
        for (const stored of history.samples) {
          const at = fetchedAt - stored.age_seconds * 1000
          const label = clockLabel(at)
          const sample: WaitSample = { id, at, events: stored.events, cpuMicro: stored.cpu_micro }
          const previous = waitSample.current
          const view = waitView(previous, sample)
          if (view.point) waits.push(view.point)
          const top = topPoint(previous, sample, previous ? (at - previous.at) / 1000 : 0)
          if (top) tops.push(top)
          waitSample.current = sample
          const ioNext = storedIo(stored, id, at)
          const io = ioPoints(ioSample.current, ioNext)
          if (io && Object.keys(io.blocks).length > 0) blocks.push({ label, values: io.blocks })
          if (io && Object.keys(io.files).length > 0) files.push({ label, values: io.files })
          if (io && Object.keys(io.delays).length > 0) delays.push({ label, values: io.delays })
          ioSample.current = ioNext
        }
        setWaitPoints(waits.slice(-24))
        setTopPoints(tops.slice(-24))
        setBlockPoints(blocks.slice(-24))
        setFilePoints(files.slice(-24))
        setLatencyPoints(delays.slice(-24))
        setHistoryCaption(history.samples.length > 0 ? history.caption : '')
      })
      .catch(() => { /* An aborted request leaves the page as it was. */ })
    setPending(true)
    const eventsPromise = Promise.all([getGraphWaits(id, controller.signal), historyPromise])
      .then(([picture]: [GraphWaits, void]) => {
        if (!active) return
        const at = Date.now()
        const previous = waitSample.current
        const sample = sampleFrom(picture, id, at)
        const view = waitView(previous, sample)
        const gap = previous && previous.id === id ? (at - previous.at) / 1000 : 0
        waitSample.current = sample
        setWaitRows(view.rows)
        if (view.point) setWaitPoints((current) => remember(current, view.point))
        setTopPoints((current) => remember(current, topPoint(previous, sample, gap)))
        setWaitLimitations(picture.limitations)
        setWaitCaption(picture.caption)
        setWaitError('')
      })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setWaitRows([])
        setTopPoints([])
        setWaitError(reason instanceof ApiError ? reason.message : 'Could not read wait statistics.')
      })
    const activityPromise = Promise.all([getWaitActivity(id, controller.signal), historyPromise])
      .then(([picture]) => {
        if (!active) return
        const at = Date.now()
        const next: ParseSample = {
          id,
          at,
          counts: namedMap(picture.parsing.counters),
          times: namedMap(picture.parsing.times),
        }
        const previous = parseSample.current
        const same = previous != null && previous.id === id
        const gap = same && previous ? (at - previous.at) / 1000 : 0
        const usable = Boolean(same && gap >= 1)
        const rates: Record<string, string> = {}
        for (const [name] of COUNT_ROWS) {
          rates[name] = rateText(next.counts[name], previous?.counts[name], !same, usable, (delta) => countRate(delta, gap))
        }
        for (const [name] of TIME_ROWS) {
          rates[name] = rateText(next.times[name], previous?.times[name], !same, usable, (delta) => timeRate(delta, gap))
        }
        parseSample.current = next
        setParseRates(rates)
        const ioNext: IoSample = {
          id,
          at,
          blocks: namedMap(picture.io.counters),
          reads: picture.io.files.reads,
          writes: picture.io.files.writes,
          readCs: picture.io.files.read_time_cs,
          writeCs: picture.io.files.write_time_cs,
        }
        const io = ioPoints(ioSample.current, ioNext)
        if (io) {
          const label = clockLabel(at)
          if (Object.keys(io.blocks).length > 0) setBlockPoints((current) => remember(current, { label, values: io.blocks }))
          if (Object.keys(io.files).length > 0) setFilePoints((current) => remember(current, { label, values: io.files }))
          if (Object.keys(io.delays).length > 0) setLatencyPoints((current) => remember(current, { label, values: io.delays }))
        }
        ioSample.current = ioNext
        const lockPrior = lockSample.current
        const lockSame = lockPrior != null && lockPrior.id === id
        const lockGap = lockSame && lockPrior ? (at - lockPrior.at) / 1000 : 0
        const ratesByName: Record<string, { gets: string; waits: string; time: string }> = {}
        const lockValues: Record<string, number> = {}
        for (const row of picture.locking.locks) {
          const earlier = lockSame ? lockPrior?.rows.find((item) => item.name === row.name) : undefined
          ratesByName[row.name] = {
            gets: rateText(row.gets, earlier?.gets, !lockSame, lockSame && lockGap >= 1, (delta) => plainRate(delta, lockGap)),
            waits: rateText(row.waits, earlier?.waits, !lockSame, lockSame && lockGap >= 1, (delta) => plainRate(delta, lockGap)),
            time: rateText(row.wait_time_ms, earlier?.wait_time_ms, !lockSame, lockSame && lockGap >= 1, (delta) => (delta / lockGap).toFixed(2)),
          }
          const timeRateValue = changed(row.wait_time_ms, earlier?.wait_time_ms, lockGap)
          if (timeRateValue != null) lockValues[row.name] = timeRateValue
        }
        if (Object.keys(lockValues).length > 0) {
          setLockPoints((current) => remember(current, { label: clockLabel(at), values: lockValues }))
        }
        lockSample.current = { id, at, rows: picture.locking.locks }
        setLockRates(ratesByName)
        const buffer = sampleFrom({
          events: picture.buffer_busy.events,
          cpu_micro: null,
          collected_at: picture.collected_at,
          limitations: picture.buffer_busy.limitations,
          caption: picture.buffer_busy.caption,
        }, id, at)
        setBufferRows(waitView(bufferSample.current, buffer).rows)
        bufferSample.current = buffer
        setActivity(picture)
        setActivityError('')
      })
      .catch((reason: unknown) => {
        if (!active || (reason instanceof DOMException && reason.name === 'AbortError')) return
        setActivity(null)
        setParseRates({})
        setBufferRows([])
        setBlockPoints([])
        setFilePoints([])
        setLatencyPoints([])
        setLockPoints([])
        setLockRates({})
        setActivityError(reason instanceof ApiError ? reason.message : 'Could not read wait activity.')
      })
    void Promise.allSettled([eventsPromise, activityPromise]).then(() => {
      if (!active) return
      setPending(false)
      if (quickId.current !== id) {
        quickId.current = id
        quickTimer = window.setTimeout(() => setReadingKey((value) => value + 1), QUICK_SAMPLE_MS)
      }
    })
    return () => {
      active = false
      if (quickTimer !== undefined) window.clearTimeout(quickTimer)
      controller.abort()
    }
  }, [target, readingKey])

  useEffect(() => {
    onRefreshStatus(loading || pending, refresh)
  }, [loading, onRefreshStatus, pending, refresh])

  if (loading && !forbidden && !missing && !target && !error) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Loading WaitDB…</div></main>
  }
  if (forbidden) {
    return (
      <main className="page-shell">
        <StatePanel tone="error" title="WaitDB is not available" message="Authorization failed." icon={<CircleAlert size={23} />} />
      </main>
    )
  }
  if (error && !target) {
    return (
      <main className="page-shell">
        <StatePanel
          tone="error"
          title="Could not open WaitDB"
          message={error}
          icon={<CircleAlert size={23} />}
          action={<button className="button button-primary" onClick={refresh}>Try again</button>}
        />
      </main>
    )
  }

  return (
    <main className="page-shell graph-page">
      {databaseId && (
        <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Database choice</button>
      )}
      <section className="page-heading">
        <div>
          <div className="eyebrow">WAITDB</div>
          <h1>{target ? target.display_name : 'WaitDB'}</h1>
          <p>{target ? (target.tns_alias || 'TNS alias unavailable') : 'Choose one registered database. This page does not change Oracle.'}</p>
          {target && <ActiveDatabase target={target} />}
        </div>
        {target && (
          <div className="graph-actions">
            {databases.length > 1 && (
              <label className="filter-control graph-switch">
                <span className="sr-only">Wait database</span>
                <select aria-label="Wait database" value={target.id} onChange={(event) => onChoose(event.target.value)}>
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
        <StatePanel title="No databases registered" message="Register a database before opening WaitDB." icon={<Database size={24} />} />
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
          <div className="wait-tabs" role="tablist" aria-label="WaitDB sections">
            {TABS.map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          {tab === 'summary' && (
            <div className="wait-grid">
              <SeriesChart title="Block activity" unit="blocks/s" points={blockPoints} stacked />
              <SeriesChart title="Event waits" unit="ms/s" points={waitPoints} />
              <SeriesChart title="Top waits" unit="ms/s" points={topPoints} />
              <SeriesChart title="Data file requests" unit="requests/s" points={filePoints} />
              <SeriesChart title="Data file latency" unit="ms per request" points={latencyPoints} />
              <ParseCard activity={activity} rates={parseRates} error={activityError} />
            </div>
          )}
          {tab === 'events' && (
            <WaitPanel rows={waitRows} points={waitPoints} limitations={waitLimitations} caption={waitCaption} error={waitError} />
          )}
          {tab === 'blocking' && <BlockingTable picture={activity?.blocking ?? null} error={activityError} />}
          {tab === 'locks' && (
            <LockTable locks={activity?.locking.locks ?? []} rates={lockRates} points={lockPoints} caption={activity?.locking.caption ?? ''} limitation={activity?.locking.limitation ?? null} error={activityError} />
          )}
          {tab === 'buffer' && <BufferBoard rows={bufferRows} activity={activity} error={activityError} />}
          <p className="wait-caption">{historyCaption || LIVE_CAPTION}</p>
        </>
      )}
    </main>
  )
}

function ParseCard({ activity, rates, error }: { activity: WaitActivity | null; rates: Record<string, string>; error: string }) {
  const parsing = activity?.parsing
  const counts = namedMap(parsing?.counters ?? [])
  const times = namedMap(parsing?.times ?? [])
  const gets = (parsing?.library ?? []).reduce((sum, item) => sum + (item.gets ?? 0), 0)
  const hits = (parsing?.library ?? []).reduce((sum, item) => sum + (item.gethits ?? 0), 0)
  const libraryKnown = (parsing?.library ?? []).some((item) => item.gets != null && item.gethits != null)
  return (
    <section className="panel wait-card" aria-labelledby="parsing-activity">
      <div className="panel-heading">
        <div>
          <h2 id="parsing-activity">Parsing</h2>
          <p>{parsing?.caption || 'Since instance startup'}</p>
        </div>
      </div>
      {error ? <p className="memory-note">{error}</p> : (
        <ul className="wait-stats">
          {COUNT_ROWS.map(([name, label]) => (
            <li key={name}><span>{label}</span><strong>{counts[name] == null ? NOT_COLLECTED : countText(counts[name])}</strong><small>{rates[name] || NEEDS_SAMPLE}</small></li>
          ))}
          <li><span>Hard parse share</span><strong>{percent(counts['parse count (hard)'], counts['parse count (total)'])}</strong><small>since startup</small></li>
          {TIME_ROWS.slice(0, 1).map(([name, label]) => (
            <li key={name}><span>{label}</span><strong>{times[name] == null ? NOT_COLLECTED : secondsText(times[name])}</strong><small>{rates[name] || NEEDS_SAMPLE}</small></li>
          ))}
          <li><span>Library cache hit</span><strong>{libraryKnown ? percent(hits, gets) : NOT_COLLECTED}</strong><small>since startup</small></li>
        </ul>
      )}
    </section>
  )
}

function BlockingTable({ picture, error }: { picture: WaitBlocking | null; error: string }) {
  return (
    <section className="panel wait-panel" aria-labelledby="blocking-locks">
      <div className="panel-heading">
        <div>
          <h2 id="blocking-locks">Blocking locks</h2>
          <p>{picture?.caption || 'These are current sessions waiting for a lock.'}</p>
        </div>
      </div>
      {error ? <p className="memory-note">{error}</p> : (
        <>
          {picture?.limitation && <p className="memory-note">{picture.limitation}</p>}
          {!picture && <p className="memory-note">Loading the current snapshot…</p>}
          {picture && picture.locks.length === 0 && !picture.limitation && <p className="wait-empty">{picture.empty}</p>}
          {picture && picture.locks.length > 0 && (
            <div className="table-scroll">
              <table className="data-table wait-table">
                <thead>
                  <tr>
                    <th scope="col">SID</th>
                    <th scope="col">Serial #</th>
                    <th scope="col">User name</th>
                    <th scope="col">PID</th>
                    <th scope="col">Lock type</th>
                    <th scope="col">Mode wanted</th>
                    <th scope="col">Object wanted</th>
                  </tr>
                </thead>
                <tbody>
                  {picture.locks.map((row) => (
                    <tr key={`${row.sid}-${row.serial_no}-${row.lock_type}`}>
                      <td>{row.sid ?? '—'}</td>
                      <td>{row.serial_no ?? '—'}</td>
                      <td>{row.username || '—'}</td>
                      <td>{row.pid || '—'}</td>
                      <td>{row.lock_type || '—'}</td>
                      <td>{row.mode_wanted || '—'}</td>
                      <td>{row.object_name || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function LockTable({
  locks, rates, points, caption, limitation, error,
}: {
  locks: WaitLock[]
  rates: Record<string, { gets: string; waits: string; time: string }>
  points: WaitPoint[]
  caption: string
  limitation: string | null
  error: string
}) {
  const getsTotal = locks.reduce((sum, row) => sum + (row.gets ?? 0), 0)
  const waitTotal = locks.reduce((sum, row) => sum + (row.wait_time_ms ?? 0), 0)
  return (
    <div className="wait-stack">
      <section className="panel wait-panel" aria-labelledby="lock-activity">
        <div className="panel-heading">
          <div>
            <h2 id="lock-activity">Lock activity</h2>
            <p>{caption || 'Gets, waits, and wait time are since instance startup.'}</p>
          </div>
        </div>
        {error ? <p className="memory-note">{error}</p> : (
          <>
            {limitation && <p className="memory-note">{limitation}</p>}
            {locks.length === 0 && !limitation && <p className="wait-empty">No data</p>}
            {locks.length > 0 && (
              <div className="table-scroll">
                <table className="data-table wait-table wait-numbers">
                  <thead>
                    <tr>
                      <th scope="col">Lock name</th>
                      <th scope="col">Gets</th>
                      <th scope="col">Waits</th>
                      <th scope="col">Gets/s</th>
                      <th scope="col">% gets</th>
                      <th scope="col">Waits/s</th>
                      <th scope="col">% wait</th>
                      <th scope="col">Wait ms/s</th>
                      <th scope="col">Wait time (ms)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {locks.map((row) => (
                      <tr key={row.name}>
                        <td>{row.name}</td>
                        <td>{row.gets == null ? NOT_COLLECTED : countText(row.gets)}</td>
                        <td>{row.waits == null ? NOT_COLLECTED : countText(row.waits)}</td>
                        <td>{rates[row.name]?.gets || NEEDS_SAMPLE}</td>
                        <td>{share(row.gets, getsTotal)}</td>
                        <td>{rates[row.name]?.waits || NEEDS_SAMPLE}</td>
                        <td>{share(row.wait_time_ms, waitTotal)}</td>
                        <td>{rates[row.name]?.time || NEEDS_SAMPLE}</td>
                        <td>{row.wait_time_ms == null ? NOT_COLLECTED : countText(row.wait_time_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>
      <SeriesChart title="Lock activity" unit="ms/s" points={points} />
    </div>
  )
}

function BufferBoard({ rows, activity, error }: { rows: WaitRow[]; activity: WaitActivity | null; error: string }) {
  const picture = activity?.buffer_busy
  const classes = (picture?.classes ?? []).filter((item) => (item.time_cs ?? 0) > 0).map((item) => ({ name: item.name, value: item.time_cs ?? 0 }))
  const spaces = (picture?.tablespaces ?? []).filter((item) => (item.sessions ?? 0) > 0).map((item) => ({ name: item.name, value: item.sessions ?? 0 }))
  return (
    <div className="wait-stack">
      {error ? <p className="memory-note">{error}</p> : (
        <>
          {picture?.limitations.map((note) => <p className="memory-note" key={note}>{note}</p>)}
          <div className="wait-grid">
            <PieChart title="Tablespace" caption="Sessions waiting now" slices={spaces} />
            <PieChart title="Block class" caption="Since instance startup" slices={classes} />
          </div>
          <section className="panel wait-panel" aria-labelledby="buffer-users">
            <div className="panel-heading"><h2 id="buffer-users">User buffer busy</h2></div>
            {!picture && <p className="memory-note">Loading the current snapshot…</p>}
            {picture && picture.users.length === 0 && <p className="wait-empty">No data</p>}
            {picture && picture.users.length > 0 && (
              <div className="table-scroll">
                <table className="data-table wait-table wait-numbers">
                  <thead>
                    <tr>
                      <th scope="col">User name</th>
                      <th scope="col">SID</th>
                      <th scope="col">Total waits</th>
                      <th scope="col">Waited (ms)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {picture.users.map((row) => (
                      <tr key={`${row.sid}-${row.username}`}>
                        <td>{row.username || '—'}</td>
                        <td>{row.sid ?? '—'}</td>
                        <td>{row.total_waits == null ? NOT_COLLECTED : countText(row.total_waits)}</td>
                        <td>{row.waited_ms == null ? NOT_COLLECTED : countText(row.waited_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="panel wait-panel" aria-labelledby="buffer-busy-waits">
            <div className="panel-heading">
              <div>
                <h2 id="buffer-busy-waits">Buffer busy waits</h2>
                <p>{picture?.caption || 'Totals are since instance startup.'}</p>
              </div>
            </div>
            {rows.length === 0 ? <p className="wait-empty">No data</p> : (
              <div className="table-scroll">
                <table className="data-table wait-table">
                  <thead>
                    <tr>
                      <th scope="col">Event</th>
                      <th scope="col">Total waits</th>
                      <th scope="col">Waits/s</th>
                      <th scope="col">Total wait time</th>
                      <th scope="col">Wait time/s</th>
                      <th scope="col">Avg wait</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.event}>
                        <td>{row.event}</td>
                        <td>{row.totalWaits}</td>
                        <td>{row.waitsPerSecond}</td>
                        <td>{row.totalTime}</td>
                        <td>{row.timePerSecond}</td>
                        <td>{row.average}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
