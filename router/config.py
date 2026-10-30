from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_ROUTER_", env_file=".env", extra="ignore")

    data_dir: Path = Path(".local/data")
    host: str = "127.0.0.1"
    port: int = 18080
    secure_cookies: bool = False
    public_origin: str | None = None
    encryption_key: str | None = None
    web_dist: Path = Path(__file__).resolve().parent.parent / "web" / "dist"
    session_hours: int = 12
    testing: bool = False
    runner_url: str = "http://127.0.0.1:18083"
    runner_key_file: Path = Path(".local/runner.key")
    benchmark_router_url: str = "http://127.0.0.1:18080/v1"
    # Lab mode: everyone on the network can view and change settings without logging in.
    open_access: bool = True

    @property
    def database_path(self) -> Path:
        return self.data_dir / "router.sqlite3"


DEFAULT_SETTINGS = {
    "logo_text": "llm",
    "organization_name": "Lab",
    "workspace_description": "研究室の推論基盤",
    # 0 = check only when someone presses 更新, to keep load on lab machines predictable.
    "health_interval_seconds": 0,
    "health_failure_threshold": 1,
    "health_recovery_threshold": 1,
    "ui_refresh_seconds": 5,
    "request_retention_days": 30,
    "metric_retention_days": 90,
    "default_queue_timeout_seconds": None,
    "discovery_hosts": [],
    "discovery_port_start": 8000,
    "discovery_port_end": 8100,
    "discovery_concurrency": 10,
}
