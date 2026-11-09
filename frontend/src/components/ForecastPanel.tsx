import { useEffect, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CircleAlert, TrendingUp } from 'lucide-react'
import { ApiError, getDatabaseForecast } from '../api'
import type { CapacityForecast } from '../types'
import { formatBytes, formatDate } from '../format'

function ForecastChart({ forecast }: { forecast: CapacityForecast }) {
  const points = [
    ...forecast.history.map((item) => ({ timestamp: item.timestamp, observed: item.used_bytes })),
    ...forecast.projection.map((item) => ({
      timestamp: item.timestamp,
      predicted: item.predicted_bytes,
      lower: item.lower_bytes,
      band: Math.max(0, item.upper_bytes - item.lower_bytes),
    })),
  ].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  if (!points.length) return <p className="empty-inline">No capacity observations are available.</p>
  return (
    <div className="forecast-chart" role="img" aria-label={`${forecast.resource} capacity forecast`}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={{ top: 8, right: 12, left: 3, bottom: 3 }}>
          <CartesianGrid stroke="#e9edf4" strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="timestamp" tickFormatter={(value: string) => new Date(value).toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' })} tickLine={false} axisLine={false} tick={{ fill: '#8a95a8', fontSize: 10 }} minTickGap={24} />
          <YAxis tickFormatter={(value: number) => `${(value / 1024 ** 3).toFixed(0)} GB`} tickLine={false} axisLine={false} tick={{ fill: '#8a95a8', fontSize: 10 }} width={52} />
          <Tooltip labelFormatter={(value) => formatDate(String(value))} formatter={(value) => formatBytes(Number(value))} />
          {forecast.capacity_bytes != null && <ReferenceLine y={forecast.capacity_bytes} stroke="#d94b58" strokeDasharray="4 4" />}
          <Area dataKey="lower" stackId="confidence" stroke="none" fill="transparent" fillOpacity={0} />
          <Area dataKey="band" stackId="confidence" stroke="none" fill="#dce4ff" fillOpacity={0.45} />
          <Line dataKey="observed" name="Observed" type="monotone" stroke="#546ee8" strokeWidth={2} dot={false} connectNulls />
          <Line dataKey="predicted" name="Forecast" type="monotone" stroke="#d68c28" strokeWidth={2} strokeDasharray="5 4" dot={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

export function ForecastPanel({ targetId, refreshKey }: { targetId: string; refreshKey: number }) {
  const [forecasts, setForecasts] = useState<CapacityForecast[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    getDatabaseForecast(targetId)
      .then((response) => { if (active) { setForecasts(response.items); setError('') } })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason.message : 'Could not load capacity forecasts.')
      })
    return () => { active = false }
  }, [targetId, refreshKey])

  return (
    <section className="panel forecast-panel">
      <div className="panel-heading"><div><h2><TrendingUp size={18} /> Capacity forecasts</h2><p>Linear trend from retained tablespace and FRA observations</p></div></div>
      {error ? <div className="chart-error" role="alert"><CircleAlert size={16} /><span>{error}</span></div> : forecasts == null ? (
        <div className="chart-loading" role="status"><span className="spinner" /> Calculating capacity forecasts…</div>
      ) : forecasts.length === 0 ? <p className="empty-inline">No historical tablespace or FRA capacity measurements are available.</p> : (
        <div className="forecast-grid">
          {forecasts.map((forecast) => (
            <article className="forecast-card" key={forecast.resource}>
              <header><strong>{forecast.resource.replace('tablespace:', 'Tablespace · ')}</strong><span className={`forecast-status forecast-${forecast.early_warning ? 'warning' : forecast.status}`}>{forecast.early_warning ? 'Early warning' : forecast.status}</span></header>
              {forecast.early_warning && <p className="forecast-warning" role="alert"><CircleAlert size={14} /> Predicted exhaustion in {forecast.days_to_exhaustion?.toFixed(1)} days.</p>}
              {forecast.status === 'unknown' ? (
                <p className="empty-inline">Insufficient, stale, or irregular history ({forecast.data_points} points over {forecast.data_span_hours.toFixed(1)} hours). No exhaustion date is estimated.</p>
              ) : (
                <>
                  <ForecastChart forecast={forecast} />
                  <dl className="forecast-stats">
                    <div><dt>Growth</dt><dd>{forecast.growth_rate_gb_per_day?.toFixed(3) ?? '0'} GB/day</dd></div>
                    <div><dt>Capacity</dt><dd>{formatBytes(forecast.capacity_bytes)}</dd></div>
                    <div><dt>Predicted exhaustion</dt><dd>{forecast.predicted_exhaustion_at ? formatDate(forecast.predicted_exhaustion_at) : 'None at current trend'}</dd></div>
                  </dl>
                </>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
