export interface Session {
  username: string;
  csrf: string;
  open?: boolean;
}
export interface Server {
  id: string;
  name: string;
  hostname: string;
  gpu_notes: string;
  metrics_url: string | null;
  endpoint_count: number;
  hardware_metrics: ResourceMetrics;
}
export interface ObservedModel {
  id: string;
  owned_by?: string;
  max_model_len?: number;
}
export interface Endpoint {
  id: string;
  server_id: string;
  server_name?: string;
  name: string;
  base_url: string;
  backend: string;
  enabled: number;
  capacity: number;
  queue_timeout_seconds: number | null;
  health: string;
  recovery_required: number;
  last_checked_at: number | null;
  last_error: string | null;
  observed_models: ObservedModel[];
  running: number;
  queued: number;
  direct_test_running: number;
  has_credential: boolean;
  benchmark_id: string | null;
  metrics_url: string | null;
  native_metrics: ResourceMetrics;
  upstream: UpstreamLoad;
}
export interface UpstreamLoad {
  running: number | null;
  queued: number | null;
}
export interface Alias {
  id: string;
  alias: string;
  endpoint_id: string;
  endpoint_name: string;
  upstream_model: string;
  enabled: number;
}
export interface User {
  id: string;
  name: string;
  enabled: number;
  created_at: number;
  active_keys: number;
}
export interface APIKey {
  id: string;
  name: string;
  prefix: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
}
export interface RequestRow {
  id: string;
  client_request_id: string | null;
  user_id: string;
  user_name?: string;
  endpoint_name?: string;
  endpoint_id: string;
  alias: string;
  upstream_model: string;
  status: string;
  streaming: number;
  source: string;
  requested_at: number;
  started_at: number | null;
  completed_at: number | null;
  queue_ms: number | null;
  ttft_ms: number | null;
  e2e_ms: number | null;
  upstream_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  error_type: string | null;
  http_status: number | null;
}
export interface Distribution {
  count: number;
  mean: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}
export interface MetricSummary {
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  input_tokens: number;
  output_tokens: number;
  input_known: number;
  output_known: number;
  queue_ms: Distribution;
  ttft_ms: Distribution;
  e2e_ms: Distribution;
  percentiles_approximate: boolean;
}
export interface Metrics {
  summary: MetricSummary;
  live: { running: number; queued: number };
  bucket_seconds: number;
  series: (MetricSummary & { time: number })[];
  endpoints: (MetricSummary & { id: string })[];
  users: (MetricSummary & { id: string })[];
  since: number;
  until: number;
}
export interface ResourceMetrics {
  observed_at?: number;
  error?: string | null;
  gauges?: Record<string, { labels: Record<string, string>; value: number }[]>;
}
export interface PublicEndpoint {
  name: string;
  backend: string;
  status: string;
  models: string[];
  aliases: string[];
  running: number | null;
  queued: number | null;
  last_checked_at: number | null;
}
export interface PublicStatus {
  servers: {
    name: string;
    hardware: ResourceMetrics;
    endpoints: PublicEndpoint[];
  }[];
}
export interface Config {
  logo_text: string;
  organization_name: string;
  workspace_description: string;
  health_interval_seconds: number;
  health_failure_threshold: number;
  health_recovery_threshold: number;
  ui_refresh_seconds: number;
  request_retention_days: number;
  metric_retention_days: number;
  default_queue_timeout_seconds: number | null;
  discovery_hosts: string[];
  discovery_port_start: number;
  discovery_port_end: number;
  discovery_concurrency: number;
}
export interface DiscoveryResult {
  running: boolean;
  started_at: number | null;
  completed_at: number | null;
  hosts: {
    host: string;
    found: {
      port: number;
      models: ObservedModel[];
      endpoint_id: string;
      new: boolean;
    }[];
  }[];
}
export interface BenchmarkConfig {
  engine?: string;
  engine_version?: string;
  name?: string;
  notes?: string;
  options?: import("./pages/BenchmarkForm").GuideOptions;
  dataset?: { name: string; sha256: string };
  tokenizer_asset?: { name: string; sha256: string };
  alias: string;
  mode: string;
  load_mode: string;
  concurrency: number[];
  arrival_rates: number[];
  requests_per_step: number;
  input_words: number;
  max_output_tokens: number;
  tool_calling: boolean;
  ignore_eos?: boolean;
  capacity_at_start?: number;
  upstream_model?: string;
}
export interface BenchmarkResult {
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  duration_seconds: number;
  request_throughput: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  input_known: number;
  output_known: number;
  input_token_throughput: number | null;
  output_token_throughput: number | null;
  queue_ms: Distribution;
  ttft_ms: Distribution;
  e2e_ms: Distribution;
  tpot_estimate_ms: Distribution;
  tpot_ms: Distribution;
  itl_ms: Distribution;
  input_distribution: Distribution;
  output_distribution: Distribution;
  metric_source?: string;
  native?: { strategy?: Record<string, unknown> };
  errors: Record<string, number>;
  submission_errors: number;
  not_sent?: number;
  client_input_mean?: number;
  requested_output_mean?: number;
}
export interface BenchmarkStep {
  id: string;
  step: number;
  concurrency: number | null;
  arrival_rate: number | null;
  status: string;
  results: Partial<BenchmarkResult>;
}
export interface Benchmark {
  id: string;
  endpoint_id: string;
  endpoint_name?: string;
  alias: string;
  status: string;
  config: BenchmarkConfig;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
  current_step: number;
  completed_requests: number;
  error_type: string | null;
  steps?: BenchmarkStep[];
  progress?: {
    counts?: {
      successful: number;
      errored: number;
      incomplete: number;
      total: number;
    };
  };
}
