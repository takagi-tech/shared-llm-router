BEGIN;
CREATE TABLE IF NOT EXISTS admins (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, admin_id TEXT NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    system INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL, created_at REAL NOT NULL,
    last_used_at REAL, revoked_at REAL
);
CREATE TABLE IF NOT EXISTS servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, hostname TEXT NOT NULL,
    gpu_notes TEXT NOT NULL DEFAULT '', metrics_url TEXT, hardware_metrics TEXT NOT NULL DEFAULT '{}',
    metrics_checked_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS endpoints (
    id TEXT PRIMARY KEY, server_id TEXT NOT NULL REFERENCES servers(id), name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL, backend TEXT NOT NULL DEFAULT 'vllm', credential TEXT,
    enabled INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
    recovery_required INTEGER NOT NULL DEFAULT 0,
    capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity > 0),
    queue_timeout_seconds REAL, health TEXT NOT NULL DEFAULT 'unknown',
    consecutive_failures INTEGER NOT NULL DEFAULT 0, consecutive_successes INTEGER NOT NULL DEFAULT 0,
    last_checked_at REAL, last_error TEXT, observed_models TEXT NOT NULL DEFAULT '[]',
    metrics_url TEXT, native_metrics TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    id TEXT PRIMARY KEY, alias TEXT NOT NULL UNIQUE,
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id), upstream_model TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id TEXT PRIMARY KEY, endpoint_id TEXT NOT NULL REFERENCES endpoints(id), alias TEXT NOT NULL,
    admin_id TEXT NOT NULL REFERENCES admins(id), status TEXT NOT NULL,
    config TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, completed_at REAL,
    current_step INTEGER NOT NULL DEFAULT 0, completed_requests INTEGER NOT NULL DEFAULT 0,
    error_type TEXT
);
CREATE TABLE IF NOT EXISTS benchmark_steps (
    id TEXT PRIMARY KEY, benchmark_id TEXT NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    step INTEGER NOT NULL, concurrency INTEGER, arrival_rate REAL,
    started_at REAL, completed_at REAL, status TEXT NOT NULL, results TEXT NOT NULL DEFAULT '{}',
    UNIQUE(benchmark_id, step)
);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, client_request_id TEXT, user_id TEXT NOT NULL REFERENCES users(id),
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id), alias TEXT NOT NULL, upstream_model TEXT NOT NULL,
    status TEXT NOT NULL, streaming INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'normal',
    benchmark_id TEXT REFERENCES benchmarks(id) ON DELETE SET NULL,
    requested_at REAL NOT NULL, started_at REAL, first_token_at REAL, completed_at REAL,
    queue_ms REAL, upstream_ms REAL, ttft_ms REAL, e2e_ms REAL,
    input_tokens INTEGER, output_tokens INTEGER, output_tokens_per_second REAL,
    http_status INTEGER, error_type TEXT,
    UNIQUE(user_id, client_request_id)
);
CREATE INDEX IF NOT EXISTS requests_time ON requests(requested_at);
CREATE INDEX IF NOT EXISTS requests_endpoint ON requests(endpoint_id, requested_at);
CREATE INDEX IF NOT EXISTS requests_user ON requests(user_id, requested_at);
CREATE INDEX IF NOT EXISTS requests_benchmark ON requests(benchmark_id);
CREATE TABLE IF NOT EXISTS metric_minutes (
    bucket INTEGER NOT NULL, endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    user_id TEXT NOT NULL REFERENCES users(id), alias TEXT NOT NULL, source TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(bucket, endpoint_id, user_id, alias, source)
);
CREATE INDEX IF NOT EXISTS metric_minutes_time ON metric_minutes(bucket);
CREATE TABLE IF NOT EXISTS health_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    observed_at REAL NOT NULL, healthy INTEGER NOT NULL, latency_ms REAL, error_type TEXT
);
CREATE INDEX IF NOT EXISTS health_samples_time ON health_samples(observed_at);
CREATE TABLE IF NOT EXISTS resource_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id TEXT NOT NULL,
    observed_at REAL NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_samples_time ON resource_samples(observed_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
PRAGMA user_version=1;
COMMIT;
