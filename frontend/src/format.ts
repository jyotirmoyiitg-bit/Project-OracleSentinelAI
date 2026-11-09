export function formatDate(value: string | null | undefined): string {
  if (!value) return 'No telemetry received'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date) + ' UTC'
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return 'No telemetry received'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  const minutes = Math.round((date.getTime() - Date.now()) / 60_000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (Math.abs(minutes) < 60) return formatter.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return formatter.format(hours, 'hour')
  return formatter.format(Math.round(hours / 24), 'day')
}

export function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'Unavailable'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const unitIndex = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** unitIndex).toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`
}

export function formatMetric(value: number | null, unit: string | null): string {
  if (value == null || !Number.isFinite(value)) return 'Unavailable'
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1)
  return unit ? `${formatted} ${unit}` : formatted
}
