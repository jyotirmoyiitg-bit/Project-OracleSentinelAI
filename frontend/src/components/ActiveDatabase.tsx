import type { Target } from '../types'
import { HealthBadge } from './HealthBadge'
import { StorageCylinder } from './StorageCylinder'

/** The database a page is scoped to: cylinder mark, name, environment, monitoring status, and health. */
export function ActiveDatabase({ target, caption = 'Active database' }: { target: Target; caption?: string }) {
  return (
    <div className="graph-active" aria-label={caption}>
      <span className="graph-active-mark" aria-hidden="true"><StorageCylinder percent={100} /></span>
      <span className="graph-active-text">
        <span>{caption}</span>
        <strong>{target.display_name}</strong>
        <small>{target.tns_alias || 'TNS alias unavailable'} · {target.environment || 'environment unknown'} · {target.monitoring_status || 'monitoring status unavailable'}</small>
      </span>
      <HealthBadge status={target.last_health_status} />
    </div>
  )
}
