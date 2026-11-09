import type { ReactNode } from 'react'

export function MetricCard({
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: {
  label: string
  value: string | number
  detail?: string
  icon: ReactNode
  tone?: 'default' | 'good' | 'warning'
}) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-card-top">
        <span className="metric-label">{label}</span>
        <span className="metric-icon" aria-hidden="true">{icon}</span>
      </div>
      <strong className="metric-value">{value}</strong>
      {detail && <span className="metric-detail">{detail}</span>}
    </article>
  )
}
