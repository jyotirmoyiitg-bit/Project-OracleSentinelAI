export type HealthStatus = 'healthy' | 'warning' | 'critical' | 'unknown' | 'offline' | string

export interface Observation {
  id: string
  target_id: string
  metric_name: string
  timestamp: string
  value: number | null
  value_str: string | null
  unit: string | null
  labels: Record<string, string>
  quality: string
  source: string
}

export interface Target {
  id: string
  display_name: string
  environment: string
  enabled: boolean
  tns_alias: string
  engine: string
  owner: string
  poll_profile: string
  capability_snapshot: Record<string, unknown>
  last_connection_at: string | null
  last_health_status: HealthStatus
  created_at: string
  updated_at: string
  last_seen: string | null
  monitoring_status: string
  critical_incident_count: number | null
  trend_snapshots: Observation[]
}

export interface FleetResponse {
  items: Target[]
  total: number
  page: number
  page_size: number
  environments: string[]
}

export interface PollStatus {
  probe_type: string
  status: string
  last_success_at: string | null
  last_attempt_at: string | null
  error_class: string | null
}

export interface TargetDetails extends Target {
  latest_observations: Observation[]
  poll_status: PollStatus[]
  compliance: { pass: number; fail: number; unknown: number }
  tablespaces: Array<{
    name: string
    used_pct: number | null
    size_bytes: number | null
    autoextend: boolean | null
    timestamp: string
  }>
  fra: {
    size_bytes: number | null
    used_bytes: number | null
    used_pct: number | null
    breakdown: Observation[]
  }
  archive: {
    log_mode: string | null
    destinations: Observation[]
  }
}
