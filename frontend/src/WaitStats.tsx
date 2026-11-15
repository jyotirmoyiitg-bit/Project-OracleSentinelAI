import type { GraphWaitEvent, GraphWaits } from './api'

const NEEDS_SAMPLE = 'Needs a second sample.'
const NOT_COLLECTED = 'Not collected.'
const CPU = 'CPU time'
const CLASS_COLORS = ['#3b5bdb', '#f59f00', '#e8590c', '#2f9e44', '#ae3ec9', '#0c8599', '#c92a2a', '#5c7cfa', '#e67700', '#087f5b']

export interface WaitSample {
  id: string
  at: number
  events: GraphWaitEvent[]
  cpuMicro: number | null
}

export interface WaitPoint {
  label: string
  values: Record<string, number>
}

export interface WaitRow {
  event: string
  waitClass: string
  totalWaits: string
  waitsPerSecond: string
  waitPercent: string
  totalTime: string
  timePerSecond: string
  timePercent: string
  average: string
}

export function sampleFrom(picture: GraphWaits, id: string, at: number): WaitSample {
  return { id, at, events: picture.events, cpuMicro: picture.cpu_micro }
}

function secondsText(micro: number): string {
  return (micro / 1_000_000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function countText(value: number): string {
  return value.toLocaleString('en-US')
}

function percentText(part: number, whole: number): string {
  if (whole <= 0) return '—'
  return (part / whole * 100).toFixed(2)
}

function countRate(delta: number, gap: number): string {
  const rate = delta / gap
  const rounded = rate >= 100 ? Math.round(rate) : Math.round(rate * 10) / 10
  return String(rounded).replace(/\.0$/, '')
}

function timeRate(deltaMicro: number, gap: number): string {
  return (deltaMicro / 1_000_000 / gap).toFixed(2)
}

function priorEvent(previous: WaitSample | null, name: string): GraphWaitEvent | undefined {
  return previous?.events.find((item) => item.event === name)
}

function ratePair(
  current: number | null,
  prior: number | null | undefined,
  first: boolean,
  usable: boolean,
  format: (delta: number) => string,
): string {
  if (first) return NEEDS_SAMPLE
  if (!usable || current == null || prior == null || current < prior) return NOT_COLLECTED
  return format(current - prior)
}

export function waitView(previous: WaitSample | null, next: WaitSample): { rows: WaitRow[]; point: WaitPoint | null } {
  const same = previous != null && previous.id === next.id
  const gap = same && previous ? (next.at - previous.at) / 1000 : 0
  const first = !same
  const usable = Boolean(same && gap >= 1)
  const waitTotal = next.events.reduce((sum, item) => sum + (item.total_waits ?? 0), 0)
  const timeTotal = next.events.reduce((sum, item) => sum + (item.time_waited_micro ?? 0), 0) + (next.cpuMicro ?? 0)
  const rows: WaitRow[] = []
  const classMicros = new Map<string, { current: number; prior: number | null }>()

  if (next.cpuMicro != null) {
    rows.push({
      event: CPU,
      waitClass: 'CPU',
      totalWaits: '—',
      waitsPerSecond: '—',
      waitPercent: '—',
      totalTime: secondsText(next.cpuMicro),
      timePerSecond: ratePair(next.cpuMicro, previous?.cpuMicro, first, usable, (delta) => timeRate(delta, gap)),
      timePercent: percentText(next.cpuMicro, timeTotal),
      average: '—',
    })
    classMicros.set('CPU', { current: next.cpuMicro, prior: previous?.cpuMicro ?? null })
  }

  for (const item of next.events) {
    const earlier = priorEvent(same ? previous : null, item.event)
    const waits = item.total_waits
    const micro = item.time_waited_micro
    rows.push({
      event: item.event,
      waitClass: item.wait_class,
      totalWaits: waits == null ? NOT_COLLECTED : countText(waits),
      waitsPerSecond: ratePair(waits, earlier?.total_waits, first, usable, (delta) => countRate(delta, gap)),
      waitPercent: waits == null ? NOT_COLLECTED : percentText(waits, waitTotal),
      totalTime: micro == null ? NOT_COLLECTED : secondsText(micro),
      timePerSecond: ratePair(micro, earlier?.time_waited_micro, first, usable, (delta) => timeRate(delta, gap)),
      timePercent: micro == null ? NOT_COLLECTED : percentText(micro, timeTotal),
      average: waits == null || micro == null || waits <= 0 ? '—' : secondsText(micro / waits),
    })
    const bucket = classMicros.get(item.wait_class) ?? { current: 0, prior: same ? 0 : null }
    bucket.current += micro ?? 0
    if (same && earlier?.time_waited_micro != null && bucket.prior != null) bucket.prior += earlier.time_waited_micro
    else if (same) bucket.prior = null
    classMicros.set(item.wait_class, bucket)
  }

  let point: WaitPoint | null = null
  if (usable) {
    const values: Record<string, number> = {}
    for (const [name, bucket] of classMicros) {
      if (bucket.prior == null || bucket.current < bucket.prior) continue
      values[name] = (bucket.current - bucket.prior) / 1000 / gap
    }
    if (Object.keys(values).length > 0) {
      point = { label: new Date(next.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), values }
    }
  }
  return { rows, point }
}

export function WaitPanel({
  rows,
  points,
  limitations,
  caption,
  error,
}: {
  rows: WaitRow[]
  points: WaitPoint[]
  limitations: string[]
  caption: string
  error: string
}) {
  return (
    <section className="panel wait-panel" aria-labelledby="wait-stats-title">
      <div className="panel-heading">
        <div>
          <h2 id="wait-stats-title">Event waits (without idle waits)</h2>
          <p>{caption || 'Totals are since instance startup. Idle waits are not included.'}</p>
        </div>
      </div>
      {error ? <p className="memory-note">{error}</p> : (
        <>
          {limitations.map((note) => <p className="memory-note" key={note}>{note}</p>)}
          {rows.length === 0 ? <p className="empty-inline">No non-idle waits were returned.</p> : (
            <div className="table-scroll">
              <table className="data-table wait-table">
                <thead>
                  <tr>
                    <th scope="col">Event</th>
                    <th scope="col">Total waits</th>
                    <th scope="col">Waits/s</th>
                    <th scope="col">% total</th>
                    <th scope="col">Total wait time</th>
                    <th scope="col">Wait time/s</th>
                    <th scope="col">% total</th>
                    <th scope="col">Avg wait</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.event}>
                      <td>{row.event}</td>
                      <td>{row.totalWaits}</td>
                      <td>{row.waitsPerSecond}</td>
                      <td>{row.waitPercent}</td>
                      <td>{row.totalTime}</td>
                      <td>{row.timePerSecond}</td>
                      <td>{row.timePercent}</td>
                      <td>{row.average}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="wait-caption">Waits/s and wait time/s are since the previous sample on this page. This is not an Oracle historical chart.</p>
          <WaitChart points={points} />
        </>
      )}
    </section>
  )
}

function WaitChart({ points }: { points: WaitPoint[] }) {
  const classes = [...new Set(points.flatMap((point) => Object.keys(point.values)))]
  return (
    <div className="wait-chart" aria-label="Event waits">
      <h3>Event waits</h3>
      {points.length === 0 ? <p>The chart needs a second sample on this page.</p> : (
        <div className="wait-chart-body">
          <svg viewBox="0 0 640 180" role="img" aria-label="Milliseconds waited per second">
            <ChartLines points={points} classes={classes} />
          </svg>
          <ul className="wait-legend">
            {classes.map((name, index) => (
              <li key={name}><span style={{ background: CLASS_COLORS[index % CLASS_COLORS.length] }} />{name}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function ChartLines({ points, classes }: { points: WaitPoint[]; classes: string[] }) {
  const max = Math.max(1, ...points.flatMap((point) => Object.values(point.values)))
  const width = 600
  const height = 140
  const step = points.length === 1 ? 0 : width / (points.length - 1)
  return (
    <>
      {[0, 1, 2, 3].map((mark) => {
        const y = 16 + (height / 3) * mark
        return <line key={mark} x1="28" y1={y} x2="628" y2={y} stroke="#e6ebf2" />
      })}
      {classes.map((name, index) => {
        const color = CLASS_COLORS[index % CLASS_COLORS.length]
        const path = points.map((point, position) => {
          const value = point.values[name] ?? 0
          const x = 28 + step * position
          const y = 16 + height - (value / max) * height
          return `${position === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
        }).join(' ')
        return <path key={name} d={path} fill="none" stroke={color} strokeWidth="2" />
      })}
    </>
  )
}
