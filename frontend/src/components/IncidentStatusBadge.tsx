import { CheckCircle2, CircleDot, Eye } from 'lucide-react'
import type { IncidentState } from '../types'

const states: Record<string, { label: string; icon: typeof CircleDot }> = {
  new: { label: 'New', icon: CircleDot },
  acknowledged: { label: 'Acknowledged', icon: Eye },
  resolved: { label: 'Resolved', icon: CheckCircle2 },
}

export function IncidentStatusBadge({ state }: { state: IncidentState | string }) {
  const normalized = state?.toLowerCase() || 'new'
  const info = states[normalized] ?? { label: normalized.replaceAll('_', ' '), icon: CircleDot }
  const Icon = info.icon
  return (
    <span className={`incident-status-badge incident-status-${normalized}`} aria-label={`Incident status: ${info.label}`}>
      <Icon aria-hidden="true" size={14} strokeWidth={2.2} />
      {info.label}
    </span>
  )
}
