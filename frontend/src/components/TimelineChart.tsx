import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Observation } from '../types'
import { formatMetric } from '../format'

export function TimelineChart({
  observations,
  unit,
  label,
}: {
  observations: Observation[]
  unit: string | null
  label: string
}) {
  const data = useMemo(
    () => observations
      .filter((item) => item.value != null && Number.isFinite(item.value))
      .map((item) => ({
        time: new Date(item.timestamp).getTime(),
        value: item.value as number,
        timestamp: item.timestamp,
      }))
      .filter((item) => Number.isFinite(item.time))
      .sort((a, b) => a.time - b.time),
    [observations],
  )

  if (data.length === 0) {
    return (
      <div className="chart-empty" role="status">
        <span className="chart-empty-mark" aria-hidden="true">⌁</span>
        <strong>No history available</strong>
        <span>Measurements for this period will appear here when collected.</span>
      </div>
    )
  }

  return (
    <div className="chart-frame" role="img" aria-label={`${label} timeline with ${data.length} measurements`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 12, left: 0, bottom: 4 }}>
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
            formatter={(value) => [formatMetric(Number(value), unit), label]}
            contentStyle={{
              border: '1px solid #e8edf4',
              borderRadius: 12,
              boxShadow: '0 8px 24px rgba(27, 45, 75, .1)',
              fontSize: 12,
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#546ee8"
            strokeWidth={2.5}
            dot={data.length < 24 ? { r: 3, fill: '#546ee8', strokeWidth: 0 } : false}
            activeDot={{ r: 5, fill: '#546ee8', stroke: '#fff', strokeWidth: 2 }}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
