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
  compliance: { pass: number; fail: number; unknown: number; not_applicable: number; error: number }
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

export type ComplianceStatus = 'pass' | 'fail' | 'unknown' | 'not_applicable' | 'error'

export interface ComplianceResult {
  policy_id: string
  policy_version: string
  name: string
  result: ComplianceStatus
  severity: string
  observed_at: string | null
  evidence: Record<string, unknown>
  recommended_action: string
}

export interface DatabaseCompliance {
  target_id: string
  environment: string
  pass: number
  fail: number
  unknown: number
  not_applicable: number
  error: number
  items: ComplianceResult[]
}

export interface CompliancePolicyVersion {
  policy_id: string
  version: string
  name: string
  engine: string
  enabled: boolean
  approval_status: string
  severity: string
  applicability: Record<string, unknown>
  evaluation_config: Record<string, unknown>
  environment_overrides: Record<string, unknown>
  recommended_action: string
}

export interface CompliancePolicyList {
  items: CompliancePolicyVersion[]
}

export interface ForecastProjection {
  timestamp: string
  predicted_bytes: number
  lower_bytes: number
  upper_bytes: number
}

export interface CapacityForecast {
  resource: string
  status: 'unknown' | 'stable' | 'forecast'
  capacity_bytes: number | null
  data_points: number
  data_span_hours: number
  growth_rate_gb_per_day: number | null
  confidence_interval_gb_per_day: [number, number] | null
  days_to_exhaustion: number | null
  predicted_exhaustion_at: string | null
  early_warning: boolean
  prediction_horizon_days: number
  history: Array<{ timestamp: string; used_bytes: number }>
  projection: ForecastProjection[]
}

export interface CapacityForecastResponse {
  target_id: string
  generated_at: string
  items: CapacityForecast[]
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

export interface KnowledgeDocument {
  id: string
  file_name: string
  relative_path: string
  category: 'Knowledge' | 'Policy'
  version: number
  ingestion_status: 'indexed' | 'deleted' | 'quarantined'
  size_bytes: number
  page_count: number | null
  quality_score: number | null
  embedding_provider: string
  embedding_model: string
  embedding_disclosure: string
  indexed_at: string | null
  updated_at: string
  acl_roles: string[]
}

export interface KnowledgeDocumentList {
  items: KnowledgeDocument[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeCitation {
  document_id: string
  document_name: string
  relative_path: string
  category: string
  version: number
  page_start: number | null
  page_end: number | null
  heading: string
  chunk_index: number
}

export interface KnowledgePassage {
  chunk_id: string
  excerpt: string
  score: number
  authoritative_policy: boolean
  citation: KnowledgeCitation
}

export interface KnowledgeSearchResponse {
  query: string
  retrieval: 'hybrid'
  llm_used: false
  prioritization: string
  items: KnowledgePassage[]
}

export interface KnowledgeReindexResponse {
  scanned: number
  indexed: number
  updated: number
  unchanged: number
  deleted: number
  quarantined: number
  skipped: number
  errors: string[]
}

export interface GuidedCommand {
  label: string
  prompt: string
}

export interface ChatThread {
  id: string
  scope_type: 'fleet' | 'database' | 'incident'
  scope_id: string | null
  provider: string
  model: string
  llm_enabled: boolean
  disclosure: string
  guided_commands: GuidedCommand[]
}

export interface ChatCitation {
  kind: string
  label: string
  source: string
  detail: string
  target_id: string | null
  document_path: string | null
  heading: string | null
}

export interface ChatToolTrace {
  tool: string
  decision: 'allowed' | 'denied' | string
  summary: string
  reason: string
  duration_ms: number
}

export interface ChatMessage {
  id: string
  role: string
  content: string
  citations: ChatCitation[]
  tools: ChatToolTrace[]
  provider: string
  model: string
  llm_used: boolean
  provider_status?: string
  guided_commands?: GuidedCommand[]
}
