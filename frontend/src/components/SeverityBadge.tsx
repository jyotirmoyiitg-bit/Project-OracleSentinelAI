import { CircleAlert, Info, OctagonAlert } from 'lucide-react'

const severities: Record<string, { label: string; icon: typeof CircleAlert }> = {
  critical: { label: 'Critical', icon: OctagonAlert },
  warning: { label: 'Warning', icon: CircleAlert },
  info: { label: 'Info', icon: Info },
}

export function SeverityBadge({ severity }: { severity: string }) {
  const normalized = severity?.toLowerCase() || 'info'
  const info = severities[normalized] ?? { label: normalized.replaceAll('_', ' '), icon: CircleAlert }
  const Icon = info.icon
  return (
    <span className={`severity-badge severity-${normalized}`} aria-label={`Severity: ${info.label}`}>
      <Icon aria-hidden="true" size={13} strokeWidth={2.2} />
      {info.label}
    </span>
  )
}
