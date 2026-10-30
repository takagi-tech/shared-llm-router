"""Versioned engine boundary: configuration, form metadata, and result mapping."""

import math
import re
from typing import Literal

from pydantic import Field, model_validator

from .guidellm_adapter import normalize_stage, scenario
from .schemas import StrictModel

GUIDELLM_VERSION = "0.7.3"


class GuideLLMConfig(StrictModel):
    tokenizer_source: Literal["huggingface", "local"] = "huggingface"
    tokenizer: str = Field(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_./-]+$")
    tokenizer_revision: str = Field(default="main", max_length=100, pattern=r"^[A-Za-z0-9_./-]+$")
    data_kind: Literal["synthetic", "file", "huggingface", "trace"] = "synthetic"
    dataset_id: str | None = None
    dataset_source: str = Field(default="", max_length=300, pattern=r"^[A-Za-z0-9_./-]*$")
    dataset_split: str = Field(default="train", max_length=100)
    dataset_config: str = Field(default="", max_length=100)
    dataset_revision: str = Field(default="main", max_length=100)
    prompt_column: str = Field(default="prompt", max_length=100)
    output_column: str = Field(default="", max_length=100)
    prompt_tokens: int = Field(default=256, ge=1, le=1000000)
    output_tokens: int = Field(default=128, ge=1, le=1000000)
    prompt_tokens_stdev: int = Field(default=0, ge=0, le=1000000)
    output_tokens_stdev: int = Field(default=0, ge=0, le=1000000)
    samples: int = Field(default=100, ge=1, le=100000)
    seed: int = Field(default=42, ge=0, le=2147483647)
    ignore_eos: bool = False
    profile: Literal["synchronous", "concurrent", "constant", "poisson", "throughput", "sweep", "replay"] = (
        "concurrent"
    )
    values: list[float] = Field(default_factory=lambda: [1, 2, 4], min_length=1, max_length=20)
    max_concurrency: int = Field(default=16, ge=1, le=10000)
    sweep_size: int = Field(default=5, ge=3, le=30)
    rampup_seconds: float = Field(default=0, ge=0, le=3600)
    time_scale: float = Field(default=1, gt=0, le=1000)
    stop_kind: Literal["requests", "duration"] = "requests"
    requests: int = Field(default=20, ge=1, le=1000000)
    duration_seconds: float = Field(default=60, gt=0, le=86400)
    max_errors: int = Field(default=1, ge=1, le=100000)
    extras: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self):
        if self.tokenizer_source == "local" and not re.fullmatch(r"[a-f0-9-]{36}", self.tokenizer):
            raise ValueError("登録済みのローカルTokenizerを選択してください")
        if self.tokenizer.startswith(("/", ".")) or ".." in self.tokenizer.split("/"):
            raise ValueError("TokenizerはHugging FaceのリポジトリIDで指定してください")
        if any(not 0 < v <= 10000 for v in self.values):
            raise ValueError("負荷値は0より大きく10000以下です")
        if self.profile == "concurrent" and any(v != int(v) or v > self.max_concurrency for v in self.values):
            raise ValueError("同時実行数は整数で、最大同時実行数以下にしてください")
        if self.data_kind in ("file", "trace") and not self.dataset_id:
            raise ValueError("登録済みデータを選択してください")
        if self.data_kind == "huggingface" and (
            not self.dataset_source or self.dataset_source.startswith(("/", "."))
        ):
            raise ValueError("データセットのリポジトリIDを指定してください")
        if (self.profile == "replay") != (self.data_kind == "trace"):
            raise ValueError("Replayとトレースデータは組み合わせて指定してください")
        allowed = {
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "seed",
            "repetition_penalty",
            "presence_penalty",
            "frequency_penalty",
            "chat_template_kwargs",
        }
        if set(self.extras) - allowed:
            raise ValueError("許可されていない生成パラメータです")
        for key, value in self.extras.items():
            if key == "chat_template_kwargs":
                if (
                    not isinstance(value, dict)
                    or set(value) - {"enable_thinking"}
                    or any(type(v) is not bool for v in value.values())
                ):
                    raise ValueError("chat_template_kwargsはenable_thinkingの真偽値に対応します")
            elif type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError("生成パラメータは数値で指定してください")
        return self


class TrialInput(StrictModel):
    engine: str = "guidellm"
    schema_version: Literal[1] = 1
    name: str = Field(default="GuideLLM試験", min_length=1, max_length=100)
    notes: str = Field(default="", max_length=1000)
    alias: str = Field(min_length=1, max_length=200)
    mode: Literal["direct", "router"] = "direct"
    options: dict

    @model_validator(mode="after")
    def engine_config(self):
        engine = ENGINES.get(self.engine)
        if not engine:
            raise ValueError("未対応の試験エンジンです")
        self.options = engine["config"].model_validate(self.options).model_dump()
        return self


ENGINES = {
    "guidellm": {
        "name": "GuideLLM",
        "version": GUIDELLM_VERSION,
        "config": GuideLLMConfig,
        "form": "guidellm-v1",
        "prepare": scenario,
        "normalize": normalize_stage,
    },
}


def catalog():
    return [
        {
            "id": key,
            "name": value["name"],
            "version": value["version"],
            "form": value["form"],
            "schema_version": 1,
            "schema": value["config"].model_json_schema(),
        }
        for key, value in ENGINES.items()
    ]
