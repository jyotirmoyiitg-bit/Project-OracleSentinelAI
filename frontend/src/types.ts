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
  tns_alias: string | null
  engine: string
  owner: string
  poll_profile: Record<string, unknown>
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

export interface FleetHealthSummary {
  total: number
  healthy: number
  warning: number
  critical: number
  offline: number
  unknown: number
  disabled: number
  scheduler_enabled: boolean
}

export interface TnsAliasListResponse {
  items: Array<{ name: string; registered: boolean }>
  scheduler_enabled: boolean
}

export interface DatabaseRegistration {
  tns_alias: string
  display_name: string
  environment: string
  owner: string
}

export interface ConnectionTestResponse {
  status: string
  message: string
  version: string | null
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

export type IncidentState = 'new' | 'acknowledged' | 'resolved'

export interface Incident {
  id: string
  target_id: string
  rule_id: string
  fingerprint: string
  state: IncidentState
  severity: string
  message: string
  first_seen: string
  last_seen: string
  owner: string | null
  root_cause_candidate: string | null
  evidence: Record<string, unknown>
}

export interface IncidentListResponse {
  items: Incident[]
  total: number
  page: number
  page_size: number
}

export interface IncidentEvent {
  id: string
  type: string
  timestamp: string
  actor: string
  reason: string | null
  before: Record<string, unknown>
  after: Record<string, unknown>
}

export interface IncidentDetail extends Incident {
  events: IncidentEvent[]
}

export interface AlertLogEvent {
  id: string
  target_id: string
  source: string
  source_event_key: string
  occurred_at: string
  severity: string
  code: string | null
  message: string
  raw_payload: Record<string, unknown>
  incidents: Incident[]
}

export interface NotificationRecord {
  id: string
  incident_id: string
  channel: string
  recipient: string
  subject: string
  attempt_count: number
  status: string
  error_class: string | null
  created_at: string
  sent_at: string | null
}

export interface PortalNotification {
  id: string
  incident_id: string
  target_id: string
  target_name: string
  severity: string
  message: string
  status: 'unread' | 'read'
  created_at: string
}

export interface PortalNotificationListResponse {
  items: PortalNotification[]
  total: number
  unread_count: number
  page: number
  page_size: number
}
