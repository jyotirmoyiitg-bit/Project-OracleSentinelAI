import { useMemo } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Observation } from '../types'
import { formatMetric } from '../format'
import { seriesKey, seriesName } from '../series'

const SERIES_COLOURS = ['#546ee8', '#d9822b', '#2a9d8f', '#c2417a', '#7a5cc7', '#5f8f2f', '#b5543c', '#3d7fb8']

type Row = { time: number } & Record<string, number>

export function TimelineChart({
  observations,
  unit,
  label,
}: {
  observations: Observation[]
  unit: string | null
  label: string
}) {
  // Rows of different series, such as different tablespaces, are never joined into one line.
  const { rows, series, count } = useMemo(() => {
    const names = new Map<string, string>()
    const byTime = new Map<number, Row>()
    let total = 0
    for (const item of observations) {
      const time = new Date(item.timestamp).getTime()
      if (item.value == null || !Number.isFinite(item.value) || !Number.isFinite(time)) continue
      const key = seriesKey(item.labels)
      if (!names.has(key)) names.set(key, seriesName(item.labels) || label)
      const field = `s${[...names.keys()].indexOf(key)}`
      const row = byTime.get(time) ?? { time }
      row[field] = item.value
      byTime.set(time, row)
      total += 1
    }
    return {
      rows: [...byTime.values()].sort((a, b) => a.time - b.time),
      series: [...names.values()].map((name, index) => ({ field: `s${index}`, name })),
      count: total,
    }
  }, [observations, label])

  if (count === 0) {
    return (
      <div className="chart-empty" role="status">
        <span className="chart-empty-mark" aria-hidden="true">⌁</span>
        <strong>No history available</strong>
        <span>Measurements for this period will appear here when collected.</span>
      </div>
    )
  }

  const several = series.length > 1
  const description = several
    ? `${label} timeline with ${count} measurements across ${series.length} series`
    : `${label} timeline with ${count} measurements`

  return (
    <div className={`chart-frame${several ? ' chart-frame-legend' : ''}`} role="img" aria-label={description}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 10, right: 12, left: 0, bottom: 4 }}>
          <CartesianGrid stroke="#e9edf4" strokeDasharray="3 5" vertical={false} />
          <XAxis
            dataKey="time"
            tickFormatter={(value: number) => new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' })}
            tickLine={false}
            axisLine={false}
            tick={{ fill: '#8a95a8', fontSize: 11 }}
            minTickGap={30}
          />
          <YAxis
            width={42}
            tickLine={false}
            axisLine={false}
            tick={{ fill: '#8a95a8', fontSize: 11 }}
            tickFormatter={(value: number) => `${value}${unit === '%' ? '%' : ''}`}
          />
          <Tooltip
            labelFormatter={(value) => new Date(Number(value)).toLocaleString([], { timeZone: 'UTC', timeZoneName: 'short' })}
            formatter={(value, name) => [formatMetric(Number(value), unit), String(name)]}
            contentStyle={{
              border: '1px solid #e8edf4',
              borderRadius: 12,
              boxShadow: '0 8px 24px rgba(27, 45, 75, .1)',
              fontSize: 12,
            }}
          />
          {several && <Legend iconType="circle" iconSize={7} wrapperStyle={{ fontSize: 11, color: '#5d6879' }} />}
          {series.map((item, index) => {
            const colour = SERIES_COLOURS[index % SERIES_COLOURS.length]
            return (
              <Line
                key={item.field}
                type="monotone"
                dataKey={item.field}
                name={item.name}
                stroke={colour}
                strokeWidth={several ? 2 : 2.5}
                dot={rows.length < 24 ? { r: 3, fill: colour, strokeWidth: 0 } : false}
                activeDot={{ r: 5, fill: colour, stroke: '#fff', strokeWidth: 2 }}
                connectNulls
              />
            )
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
