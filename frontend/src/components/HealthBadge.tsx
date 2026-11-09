import { CircleHelp, CircleOff, CircleCheck, TriangleAlert, XCircle } from 'lucide-react'
import type { HealthStatus } from '../types'

const states: Record<string, { label: string; icon: typeof CircleCheck }> = {
  healthy: { label: 'Healthy', icon: CircleCheck },
  warning: { label: 'Warning', icon: TriangleAlert },
  critical: { label: 'Critical', icon: XCircle },
  unknown: { label: 'Unknown', icon: CircleHelp },
  offline: { label: 'Offline', icon: CircleOff },
}

export function HealthBadge({ status }: { status: HealthStatus }) {
  const normalized = status?.toLowerCase() || 'unknown'
  const state = states[normalized] ?? { label: normalized.replaceAll('_', ' '), icon: CircleHelp }
  const Icon = state.icon
  return (
    <span className={`health-badge health-${normalized}`} aria-label={`Health status: ${state.label}`}>
      <Icon aria-hidden="true" size={14} strokeWidth={2.2} />
      {state.label}
    </span>
  )
}
