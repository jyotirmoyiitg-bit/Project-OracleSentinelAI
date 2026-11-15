import type { WaitPoint } from './WaitStats'

export const CHART_COLORS = ['#4c6ef5', '#f59f00', '#2f9e44', '#e8590c', '#ae3ec9', '#0c8599', '#e03131', '#f08c00', '#1c7ed6', '#82c91e']

export function SeriesChart({
  title,
  unit,
  points,
  stacked = false,
}: {
  title: string
  unit: string
  points: WaitPoint[]
  stacked?: boolean
}) {
  const names = [...new Set(points.flatMap((point) => Object.keys(point.values)))]
  return (
    <section className="panel wait-card" aria-label={title}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{unit}</p>
        </div>
      </div>
      {points.length === 0 ? <p className="wait-chart-empty">The chart needs a second sample on this page.</p> : (
        <div className="wait-chart-body">
          <svg viewBox="0 0 640 180" role="img" aria-label={`${title}, ${unit}`}>
            <ChartSeries points={points} names={names} stacked={stacked} />
          </svg>
          <ul className="wait-legend">
            {names.map((name, index) => (
              <li key={name}><span style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />{name}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function ChartSeries({ points, names, stacked }: { points: WaitPoint[]; names: string[]; stacked: boolean }) {
  const width = 600
  const height = 140
  const step = points.length === 1 ? 0 : width / (points.length - 1)
  const totals = points.map((point) => names.reduce((sum, name) => sum + (point.values[name] ?? 0), 0))
  const peak = Math.max(1, ...(stacked ? totals : points.flatMap((point) => Object.values(point.values))))
  const yFor = (value: number) => 16 + height - (value / peak) * height
  return (
    <>
      {[0, 1, 2, 3].map((mark) => {
        const y = 16 + (height / 3) * mark
        return <line key={mark} x1="20" y1={y} x2="620" y2={y} stroke="#e6ebf2" />
      })}
      {names.map((name, index) => {
        const color = CHART_COLORS[index % CHART_COLORS.length]
        if (!stacked) {
          const path = points.map((point, position) => {
            const x = 20 + step * position
            const y = yFor(point.values[name] ?? 0)
            return `${position === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
          }).join(' ')
          return <path key={name} d={path} fill="none" stroke={color} strokeWidth="2" />
        }
        const upper = points.map((point, position) => {
          const below = names.slice(0, index).reduce((sum, item) => sum + (point.values[item] ?? 0), 0)
          return { x: 20 + step * position, y: yFor(below + (point.values[name] ?? 0)) }
        })
        const lower = points.map((point, position) => {
          const below = names.slice(0, index).reduce((sum, item) => sum + (point.values[item] ?? 0), 0)
          return { x: 20 + step * position, y: yFor(below) }
        }).reverse()
        const path = [...upper, ...lower].map((point, position) => `${position === 0 ? 'M' : 'L'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' ')
        return <path key={name} d={`${path} Z`} fill={color} fillOpacity="0.82" stroke={color} strokeWidth="1" />
      })}
    </>
  )
}

export function PieChart({
  title,
  slices,
  caption,
}: {
  title: string
  slices: Array<{ name: string; value: number }>
  caption: string
}) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)
  return (
    <section className="panel wait-card" aria-label={title}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{caption}</p>
        </div>
      </div>
      {total <= 0 ? <p className="wait-chart-empty">No data</p> : (
        <div className="wait-chart-body wait-pie-body">
          <svg viewBox="0 0 180 180" role="img" aria-label={title}>
            <PieSlices slices={slices} total={total} />
          </svg>
          <ul className="wait-legend">
            {slices.map((slice, index) => (
              <li key={slice.name}><span style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />{slice.name}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function PieSlices({ slices, total }: { slices: Array<{ name: string; value: number }>; total: number }) {
  let cursor = -Math.PI / 2
  return (
    <>
      {slices.map((slice, index) => {
        const angle = (slice.value / total) * Math.PI * 2
        const start = cursor
        cursor += angle
        return <path key={slice.name} d={piePath(90, 90, 68, start, cursor)} fill={CHART_COLORS[index % CHART_COLORS.length]} />
      })}
    </>
  )
}

function piePath(cx: number, cy: number, radius: number, start: number, end: number): string {
  if (end - start >= Math.PI * 2 - 0.001) {
    return `M ${cx} ${cy - radius} A ${radius} ${radius} 0 1 1 ${cx - 0.01} ${cy - radius} Z`
  }
  const x1 = cx + radius * Math.cos(start)
  const y1 = cy + radius * Math.sin(start)
  const x2 = cx + radius * Math.cos(end)
  const y2 = cy + radius * Math.sin(end)
  const large = end - start > Math.PI ? 1 : 0
  return `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`
}
