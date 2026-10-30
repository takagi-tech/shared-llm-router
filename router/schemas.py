import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def clean_url(value: str | None, api=False):
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("認証情報を含まないhttp(s) URLを指定してください")
    if parts.query or parts.fragment:
        raise ValueError("URLにクエリやフラグメントは指定できません")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("ポートが不正です") from exc
    path = parts.path.rstrip("/")
    if api and not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ServerInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=255)
    gpu_notes: str = Field(default="", max_length=1000)
    metrics_url: str | None = None

    @field_validator("metrics_url")
    @classmethod
    def url(cls, value):
        return clean_url(value)


class EndpointInput(StrictModel):
    server_id: str
    name: str = Field(min_length=1, max_length=100)
    base_url: str
    backend: str = "vllm"
    api_key: str | None = None  # null/omitted keeps the old secret on update; empty clears it
    enabled: bool = False
    capacity: int = Field(default=1, ge=1, le=100000)
    queue_timeout_seconds: float | None = Field(default=None, gt=0)
    metrics_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def base(cls, value):
        result = clean_url(value, api=True)
        if not result:
            raise ValueError("Base URLを指定してください")
        return result

    @field_validator("metrics_url")
    @classmethod
    def metrics(cls, value):
        return clean_url(value)

    @field_validator("backend")
    @classmethod
    def backend_supported(cls, value):
        if value not in ("vllm", "llamacpp"):
            raise ValueError("vLLMまたはllama.cppを選択してください")
        return value


class CapacityInput(StrictModel):
    capacity: int = Field(ge=1, le=100000)


class AliasInput(StrictModel):
    alias: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    endpoint_id: str
    upstream_model: str = Field(min_length=1, max_length=1000)
    enabled: bool = True


class UserInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class KeyInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class SettingsInput(StrictModel):
    logo_text: str = Field(default="llm", min_length=1, max_length=12, pattern=r"^\S(?:[^\r\n]*\S)?$")
    organization_name: str = Field(default="Lab", min_length=1, max_length=60, pattern=r"^\S(?:[^\r\n]*\S)?$")
    workspace_description: str = Field(default="研究室の推論基盤", max_length=100, pattern=r"^[^\r\n]*$")
    health_interval_seconds: int = Field(ge=0, le=3600)
    health_failure_threshold: int = Field(ge=1, le=100)
    health_recovery_threshold: int = Field(ge=1, le=100)
    ui_refresh_seconds: int = Field(ge=1, le=300)
    request_retention_days: int = Field(ge=1, le=3650)
    metric_retention_days: int = Field(ge=1, le=3650)
    default_queue_timeout_seconds: float | None = Field(default=None, gt=0)
    discovery_hosts: list[str] = Field(default=[], max_length=256)
    discovery_port_start: int = Field(default=8000, ge=1, le=65535)
    discovery_port_end: int = Field(default=8100, ge=1, le=65535)
    discovery_concurrency: int = Field(default=10, ge=1, le=50)

    @field_validator("discovery_hosts")
    @classmethod
    def hosts(cls, value):
        result = []
        for host in (h.strip() for h in value):
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?", host):
                raise ValueError("IPアドレスまたはホスト名を1行に1つずつ指定してください")
            if host not in result:
                result.append(host)
        return result
