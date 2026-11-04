// ─── Auth ────────────────────────────────────────────────────────────────────
export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  updated_at?: string | null;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  full_name?: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  refresh_token?: string | null;
  user?: User | null;
}

// ─── Workflow ─────────────────────────────────────────────────────────────────
export type WorkflowStatus = "active" | "inactive" | "draft";
export type WorkflowNodeType = "input" | "assessor" | "tax" | "normalize" | "output";

export interface WorkflowNodeData {
  label?: string;
  required_params?: string[];
  optional_params?: string[];
  county_source_id?: string;
  recipe_id?: string;
  recipe_version_id?: string;
  allow_failure?: boolean;
  mappings?: Record<string, string>;
  support_partial_success?: boolean;
  output_format?: string;
  [key: string]: unknown;
}

export interface WorkflowDefinition {
  nodes: Array<{
    id: string;
    type: WorkflowNodeType;
    position: { x: number; y: number };
    data: WorkflowNodeData;
  }>;
  edges: Array<{
    id?: string;
    source: string;
    target: string;
    label?: string;
  }>;
  execution_config?: {
    mode?: string;
    allow_partial_success?: boolean;
    timeout_seconds?: number;
  };
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version_number: number;
  definition: WorkflowDefinition;
  changelog: string | null;
  is_current: boolean;
  created_at: string;
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  owner_id?: string;
  is_active: boolean;
  county_source_id?: string | null;
  current_version_id?: string | null;
  current_version_number?: number | null;
  created_at: string;
  updated_at?: string | null;
  versions?: WorkflowVersion[];
}

export interface WorkflowValidationResponse {
  is_valid: boolean;
  errors: string[];
  warnings: string[];
  execution_order: string[];
  node_count: number;
  edge_count: number;
  supports_assessor: boolean;
  supports_tax: boolean;
  supports_partial_success: boolean;
}

// ─── County Source ────────────────────────────────────────────────────────────
export type CountySourceStatus = "active" | "inactive" | "pending";
export type ConfigStatus = "unconfigured" | "partial" | "configured";
export type SourceType = "assessor" | "tax";

export interface SourceConfiguration {
  id: string;
  county_source_id: string;
  source_type: SourceType;
  website_url: string;
  search_page_url?: string | null;
  search_method?: string | null;
  search_fields: Record<string, unknown>;
  search_button: Record<string, unknown>;
  result_page: Record<string, unknown>;
  field_mappings: Record<string, unknown>;
  pagination_settings: Record<string, unknown>;
  navigation_config: Record<string, unknown>;
  template_version_id?: string | null;
  status: string;
  is_active: boolean;
  created_by?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface CountySource {
  id: string;
  name: string;
  state: string;
  county: string;
  country: string;
  description?: string | null;
  assessor_url: string | null;
  tax_url: string | null;
  assessor_status: string;
  tax_status: string;
  supported_search_types: string[];
  status: CountySourceStatus;
  config_status: ConfigStatus;
  is_active: boolean;
  notes: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at?: string | null;
  configurations?: SourceConfiguration[];
}

export interface CountySourceListResponse {
  items: CountySource[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

// ─── Recipe & Template Management ─────────────────────────────────────────────
export type RecordType = "assessor" | "tax";
export type RecipeLifecycleStatus = "draft" | "testing" | "validated" | "approved" | "published" | "deprecated";
export type RecipeStatus = RecipeLifecycleStatus; // alias for backwards compatibility

export interface UITemplate {
  id: string;
  name: string;
  description: string | null;
  pattern_type: string;
  supported_source_types: string[];
  supported_search_methods: string[];
  required_configuration_fields: string[];
  default_config_schema: Record<string, unknown>;
  current_version_id: string | null;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
  versions?: TemplateVersion[];
}

export interface TemplateVersion {
  id: string;
  template_id: string;
  version_number: number;
  definition: Record<string, unknown>;
  changelog: string | null;
  is_current: boolean;
  created_by: string | null;
  created_at: string;
}

export interface RecipeVersion {
  id: string;
  recipe_id: string;
  template_version_id: string | null;
  version_number: number;
  definition: Record<string, unknown>;
  changelog: string | null;
  status: RecipeLifecycleStatus;
  is_current: boolean;
  is_published: boolean;
  created_by: string | null;
  created_at: string;
  published_at: string | null;
}

export interface ValidationCase {
  id: string;
  recipe_id: string;
  recipe_version_id: string | null;
  name: string;
  description: string | null;
  input_data: Record<string, unknown>;
  expected_output: Record<string, unknown>;
  last_run_at: string | null;
  last_run_status: string;
  last_error: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface Recipe {
  id: string;
  county_source_id: string;
  source_configuration_id: string | null;
  ui_template_id: string | null;
  name: string;
  description: string | null;
  record_type: RecordType;
  status: RecipeLifecycleStatus;
  current_version_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
  versions?: RecipeVersion[];
  validation_cases?: ValidationCase[];
}

export interface CompatibilityCheckRequest {
  template_id: string;
  source_configuration_id: string;
  template_version_id?: string | null;
}

export interface CompatibilityCheckResponse {
  compatible: boolean;
  missing_fields: string[];
  warnings: string[];
  details: Record<string, unknown>;
}

// ─── Execution ────────────────────────────────────────────────────────────────
export type RunStatus =
  | "PENDING"
  | "QUEUED"
  | "RUNNING"
  | "RECOVERING"
  | "COMPLETED"
  | "FAILED"
  | "PARTIALLY_COMPLETED"
  | "CANCEL_REQUESTED"
  | "CANCELLED"
  | "pending"
  | "queued"
  | "running"
  | "recovering"
  | "completed"
  | "failed"
  | "cancelled";

export type NodeStatus =
  | "PENDING"
  | "WAITING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "SKIPPED"
  | "CANCELLED";

export type NodeType = "input" | "assessor" | "tax" | "normalize" | "output";

export interface NodeRun {
  id: string;
  workflow_run_id: string;
  node_id: string;
  node_type: string;
  status: string;
  input_data: Record<string, unknown>;
  output_data: Record<string, unknown>;
  error_message: string | null;
  retry_count: number;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  workflow_name?: string | null;
  version_number?: number | null;
  triggered_by: string;
  celery_task_id: string | null;
  status: RunStatus | string;
  input_data: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  execution_metadata: Record<string, unknown>;
  worker_id?: string | null;
  fencing_token?: number;
  worker_lease_expires_at?: string | null;
  recovery_attempt_count?: number;
  last_recovery_at?: string | null;
  recovery_reason?: string | null;
  last_heartbeat_at?: string | null;
  created_at: string;
  node_runs?: NodeRun[];
}

export interface ExecutionResultsResponse {
  workflow_run_id: string;
  workflow_id: string;
  workflow_version_id: string;
  status: string;
  duration_ms: number | null;
  is_partial_success: boolean;
  assessor_branch_status?: "SUCCEEDED" | "FAILED" | "RUNNING" | "NOT_CONFIGURED" | "SKIPPED" | string;
  tax_branch_status?: "SUCCEEDED" | "FAILED" | "RUNNING" | "NOT_CONFIGURED" | "SKIPPED" | string;
  has_assessor_branch?: boolean;
  has_tax_branch?: boolean;
  has_normalize_branch?: boolean;
  assessor_records_count?: number;
  tax_records_count?: number;
  assessor_result: Record<string, unknown> | null;
  tax_result: Record<string, unknown> | null;
  normalized_result: Record<string, unknown> | null;
  final_output: Record<string, unknown> | null;
  errors: string[];
}

// ─── Security ─────────────────────────────────────────────────────────────────
export type SecurityEventType = "cloudflare" | "captcha" | "turnstile" | "rate_limit" | "blocked" | "other";

export interface SecurityEvent {
  id: string;
  workflow_run_id: string | null;
  county_source_id: string | null;
  event_type: SecurityEventType;
  url: string;
  details: Record<string, unknown>;
  detected_at: string;
  created_at: string;
}

// ─── Documents ────────────────────────────────────────────────────────────────
export type DocumentStatus = "discovered" | "queued" | "downloading" | "validating" | "downloaded" | "validated" | "completed" | "retry_pending" | "failed" | "cancelled" | "duplicate";

export interface DocumentArtifact {
  id: string;
  workflow_run_id: string | null;
  node_run_id: string | null;
  property_record_id: string | null;
  document_type: string;
  original_filename: string;
  stored_filename: string;
  source_url: string;
  source_page_url: string | null;
  storage_key: string;
  mime_type: string;
  file_size: number;
  checksum_sha256: string;
  page_count: number | null;
  pdf_title: string | null;
  pdf_author: string | null;
  document_date: string | null;
  recording_number: string | null;
  status: DocumentStatus | string;
  error_message: string | null;
  is_duplicate: boolean;
  duplicate_of_id: string | null;
  etag?: string | null;
  last_modified?: string | null;
  downloaded_bytes?: number;
  download_attempts?: number;
  last_attempt_at?: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DocumentDownloadAttempt {
  id: string;
  document_artifact_id: string;
  attempt_number: number;
  status: string;
  downloaded_bytes: number;
  total_bytes: number | null;
  etag: string | null;
  range_resumed: boolean;
  resumed_from_byte: number;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface BrowserActivityItem {
  id: string;
  timestamp: string;
  node_id?: string | null;
  step_index?: number | null;
  action?: string | null;
  description: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | string;
  url?: string | null;
  error?: string | null;
  duration_ms?: number;
  completed_at?: string | null;
}

export interface BrowserSession {
  id: string;
  session_id: string;
  workflow_run_id: string | null;
  worker_id: string | null;
  browser_type: string;
  state: "INITIALIZING" | "READY" | "BUSY" | "DISCONNECTED" | "CRASHED" | "CLOSING" | "CLOSED" | string;
  is_healthy: boolean;
  pages_created: number;
  contexts_created: number;
  failure_count: number;
  last_failure_reason: string | null;
  current_url?: string | null;
  current_node?: string | null;
  current_step?: string | null;
  current_action?: string | null;
  activity_log?: BrowserActivityItem[];
  started_at: string;
  last_activity_at: string;
  closed_at: string | null;
}

export interface BrowserActivityResponse {
  workflow_run_id: string;
  session_id: string | null;
  browser_state: string;
  current_url: string | null;
  current_node: string | null;
  current_step: string | null;
  current_action: string | null;
  is_healthy: boolean;
  activities: BrowserActivityItem[];
}

export interface ExecutionDownloadStatus {
  workflow_run_id: string;
  total_documents: number;
  completed: number;
  failed: number;
  in_progress: number;
  duplicate: number;
  total_bytes_stored: number;
  documents: Array<{
    id: string;
    filename: string;
    status: string;
    file_size: number;
    download_attempts: number;
    is_duplicate: boolean;
    error_message: string | null;
  }>;
}

// ─── Phase 3D.3 Worker, Circuit Breaker & Recovery ──────────────────────────────
export type WorkerStatus = "online" | "busy" | "unresponsive" | "offline";
export type CircuitState = "closed" | "open" | "half_open";
export type RecoveryStatus = "initiated" | "completed" | "failed" | "skipped";

export interface WorkerRecord {
  worker_id: string;
  hostname: string | null;
  pid: number | null;
  status: WorkerStatus;
  queues: string[];
  active_task_count: number;
  current_task_id: string | null;
  heartbeat_count: number;
  last_heartbeat_at: string | null;
  registered_at: string;
  heartbeat_age_seconds: number | null;
}

export interface WorkerHealthResponse {
  overall_status: "HEALTHY" | "DEGRADED" | "UNHEALTHY";
  total_workers: number;
  online_workers: number;
  busy_workers: number;
  unresponsive_workers: number;
  offline_workers: number;
  active_tasks: number;
  workers: WorkerRecord[];
}

export interface CircuitBreakerState {
  dependency_key: string;
  dependency_type: string;
  state: CircuitState;
  failure_count: number;
  success_count: number;
  consecutive_successes: number;
  last_failure_at: string | null;
  last_success_at: string | null;
  last_state_change_at: string | null;
  open_until: string | null;
  failure_threshold: number;
  open_duration_seconds: number;
  half_open_max_probes: number;
  last_failure_reason: string | null;
}

export interface RecoveryAuditRecord {
  id: string;
  workflow_run_id: string;
  recovery_attempt: number;
  previous_worker_id: string | null;
  recovery_worker_id: string | null;
  recovery_reason: string;
  fencing_token: number;
  status: RecoveryStatus;
  resumed_at_node_id: string | null;
  started_at: string;
  completed_at: string | null;
  details: Record<string, unknown>;
}

// ─── Health ───────────────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string;
  version: string;
  environment: string;
  database: string;
  redis: string;
  timestamp: string;
}

// ─── API Response wrappers ────────────────────────────────────────────────────
export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}
