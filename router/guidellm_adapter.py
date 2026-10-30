"""GuideLLM 0.7.3 configuration and metric translation. No heavy imports."""

from urllib.parse import urlsplit


def scenario(config, target, model, api_key=None, dataset=None):
    o = config["options"]
    profile = {"kind": o["profile"]}
    if o["profile"] == "concurrent":
        profile["streams"] = [int(v) for v in o["values"]]
    if o["profile"] in ("constant", "poisson"):
        profile["rate"] = o["values"]
    if o["profile"] not in ("synchronous", "replay", "concurrent"):
        profile["max_concurrency"] = o["max_concurrency"]
    if o["profile"] not in ("synchronous", "replay"):
        profile["rampup_duration"] = o["rampup_seconds"]
    if o["profile"] == "sweep":
        profile["sweep_size"] = o["sweep_size"]
    if o["profile"] == "replay":
        profile["time_scale"] = o["time_scale"]
    body = {**o["extras"]}
    if o["ignore_eos"]:
        body["ignore_eos"] = True
    parts = urlsplit(target)
    backend = {
        "kind": "openai_http",
        "target": f"{parts.scheme}://{parts.netloc}",
        "model": model,
        "api_key": api_key,
        "timeout": None,
        "timeout_connect": 5,
        "verify": True,
        "validate_backend": False,
        "follow_redirects": False,
        "stream": True,
        "extras": {"body": body},
        "api_routes": {
            "/v1/chat/completions": parts.path.strip("/") + "/chat/completions",
            "/v1/models": parts.path.strip("/") + "/models",
        },
    }
    if o["data_kind"] == "synthetic":
        data = {
            "kind": "synthetic_text",
            **{
                k: o[k]
                for k in ("prompt_tokens", "output_tokens", "prompt_tokens_stdev", "output_tokens_stdev")
            },
        }
    elif o["data_kind"] == "huggingface":
        data = {
            "kind": "huggingface",
            "source": o["dataset_source"],
            "load_kwargs": {
                "split": o["dataset_split"],
                "revision": o["dataset_revision"],
                **({"name": o["dataset_config"]} if o["dataset_config"] else {}),
            },
        }
    else:
        data = {
            "kind": "trace_synthetic"
            if o["data_kind"] == "trace"
            else "csv_file"
            if dataset["extension"] == ".csv"
            else "json_file",
            "path": "/datasets/" + dataset["id"] + dataset["extension"],
        }
        if o["data_kind"] == "file":
            data["load_kwargs"] = {"split": "train"}
    if o["data_kind"] == "synthetic":
        data = {k: v for k, v in data.items() if not k.endswith("_stdev") or v > 0}
    constraints = [{"kind": "max_errors", "count": o["max_errors"], "stopping_scope": "all"}]
    if o["stop_kind"] == "requests":
        constraints.append({"kind": "max_requests", "count": o["requests"]})
    spec = {
        "backend": backend,
        "profile": profile,
        "data": [data],
        "data_preprocessors": [],
        "constraints": constraints,
        "tokenizer": {
            "kind": "huggingface_auto",
            "model": "/tokenizers/" + o["tokenizer"] if o["tokenizer_source"] == "local" else o["tokenizer"],
            "load_kwargs": {"revision": o["tokenizer_revision"], "trust_remote_code": False},
        },
        "data_loader": {
            "kind": "pytorch",
            "samples": (0 if o["stop_kind"] == "duration" else o["requests"])
            if o["data_kind"] == "synthetic"
            else o["samples"],
            "num_workers": 0,
        },
        "seed": {"kind": "static", "value": o["seed"]},
        "outputs": [],
        "metrics": {"kind": "generative", "sample_size": 0},
    }
    if o["data_kind"] in ("file", "huggingface"):
        mapping = {"text_column": o["prompt_column"]}
        if o["output_column"]:
            mapping["output_tokens_count_column"] = o["output_column"]
        else:
            backend["max_tokens"] = o["output_tokens"]
        spec["data_column_mapper"] = {"kind": "generative_column_mapper", "column_mappings": mapping}
    return {
        "scenario": spec,
        "duration_seconds": o["duration_seconds"] if o["stop_kind"] == "duration" else None,
        "max_errors": o["max_errors"],
        "output_tokens": o["output_tokens"],
    }


def normalize_stage(native):
    """Map GuideLLM's own distributions, preserving units and engine-specific data."""
    metrics = native["metrics"]

    def dist(key, factor=1):
        d = (metrics.get(key) or {}).get("successful")
        coverage_keys = {"prompt_token_count": "prompt_tokens", "output_token_count": "output_tokens"}
        coverage = native.get("coverage", {}).get(coverage_keys.get(key, key))
        if coverage is not None and coverage < metrics["request_totals"]["successful"]:
            return {"count": coverage, "mean": None, "p50": None, "p95": None, "p99": None}
        if not d or not d.get("count"):
            return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None}
        return {
            "count": d["count"],
            "mean": d["mean"] * factor,
            **{p: d["percentiles"][p] * factor for p in ("p50", "p95", "p99")},
        }

    counts = metrics["request_totals"]
    result = {
        "total": counts["total"],
        "completed": counts["successful"],
        "failed": counts["errored"],
        "cancelled": counts["incomplete"],
        "duration_seconds": native["duration"],
        "request_throughput": dist("requests_per_second")["mean"],
        "ttft_ms": dist("time_to_first_token_ms"),
        "e2e_ms": dist("request_latency", 1000),
        "tpot_ms": dist("time_per_output_token_ms"),
        "itl_ms": dist("inter_token_latency_ms"),
        "native": native,
        "not_sent": native.get("not_sent", 0),
        "errors": native.get("error_categories", {}),
        "metric_source": "GuideLLM: server usage preferred; tokenizer fallback",
        "input_distribution": dist("prompt_token_count"),
        "output_distribution": dist("output_token_count"),
    }
    sources = native.get("token_sources", {})
    for side, key in (("input", "prompt"), ("output", "output")):
        known = sources.get("server_" + side + "_known", 0)
        complete = known == counts["successful"] and known > 0
        result[side + "_known"] = known
        result[side + "_tokens"] = sources.get("server_" + side + "_total") if complete else None
        result[side + "_token_throughput"] = dist(key + "_tokens_per_second")["mean"] if complete else None
        if not complete:
            result[side + "_distribution"] = {
                "count": known,
                "mean": None,
                "p50": None,
                "p95": None,
                "p99": None,
            }
    if result["output_known"] < counts["successful"]:
        result["tpot_ms"] = {
            "count": result["output_known"],
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    for name in ("client_input", "requested_output"):
        result[name + "_mean"] = (
            sources[name + "_total"] / sources[name + "_known"] if sources.get(name + "_known") else None
        )
    result["metric_source"] = "Server usage only; GuideLLM supplemented metrics remain under native"
    return result
