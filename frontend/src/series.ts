import type { Observation } from './types'

/** Chart option that draws every series of a labelled metric, one line each. */
export const ALL_SERIES = '__all__'

/** A label that says where a row came from, not which thing it measures. */
const SOURCE_LABELS = new Set(['source'])

export interface MetricSeries {
  key: string
  name: string
  labels: Record<string, string>
  value: number | null
  unit: string | null
  timestamp: string
  observation: Observation
}

/** The labels that tell one series of a metric from another, such as the tablespace name. */
export function seriesLabels(labels: Record<string, string> | null | undefined): Record<string, string> {
  return Object.fromEntries(
    Object.entries(labels ?? {})
      .filter(([key, value]) => !SOURCE_LABELS.has(key) && value !== '')
      .sort(([a], [b]) => a.localeCompare(b)),
  )
}

export function seriesKey(labels: Record<string, string> | null | undefined): string {
  const own = seriesLabels(labels)
  return Object.keys(own).length ? JSON.stringify(own) : ''
}

export function seriesName(labels: Record<string, string> | null | undefined): string {
  return Object.values(seriesLabels(labels)).join(' · ')
}

/** What the series of a metric are called, for the "All ..." option. */
export function seriesNoun(series: MetricSeries[]): string {
  const keys = new Set(series.flatMap((item) => Object.keys(item.labels)))
  if (keys.size === 1 && keys.has('tablespace_name')) return 'tablespaces'
  return 'series'
}

/** The latest value of each series of a metric, highest first, so the fullest tablespace leads. */
export function seriesFor(observations: Observation[], metric: string): MetricSeries[] {
  const latest = new Map<string, MetricSeries>()
  for (const item of observations) {
    if (item.metric_name !== metric) continue
    const key = seriesKey(item.labels)
    if (!key) continue
    const known = latest.get(key)
    if (known && known.timestamp >= item.timestamp) continue
    latest.set(key, {
      key,
      name: seriesName(item.labels),
      labels: seriesLabels(item.labels),
      value: item.value,
      unit: item.unit,
      timestamp: item.timestamp,
      observation: item,
    })
  }
  return [...latest.values()].sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity) || a.name.localeCompare(b.name))
}
