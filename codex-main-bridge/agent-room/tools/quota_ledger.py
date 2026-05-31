#!/usr/bin/env python3
"""Numeric quota ledger for API model quotas."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(WORKSPACE / "codex-main-bridge")))
QUOTA_LEDGER_FILE = ROOT / "agent-room" / "quota_ledger.json"
MODEL_QUOTA_SIGNAL_FILE = ROOT / "agent-room" / "model_quota_signal.json"
QUOTA_HEADER_OBSERVATIONS_FILE = ROOT / "agent-room" / "quota_header_observations.jsonl"
MODEL_QUOTA_SIGNAL_TTL_SECONDS = int(os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_TTL_SECONDS", "900"))

QUOTA_HEADER_CANDIDATES = {
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining",
    "x-quota-remaining",
    "x-ratelimit-remaining-tokens",
    "x-account-quota-remaining",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit",
    "x-quota-limit",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset",
    "x-ratelimit-reset-tokens",
    "retry-after",
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def read_quota_ledger():
    try:
        if QUOTA_LEDGER_FILE.is_file():
            raw = QUOTA_LEDGER_FILE.read_text(encoding="utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        pass
    return {}

def save_quota_ledger(ledger):
    QUOTA_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUOTA_LEDGER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(QUOTA_LEDGER_FILE)

def read_json_file(path):
    try:
        if path.is_file():
            raw = path.read_text(encoding="utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        pass
    return {}

def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def quota_header_observations_file():
    raw = str(
        os.environ.get("AGENT_ROOM_QUOTA_HEADER_OBSERVATIONS_FILE")
        or os.environ.get("OPENCLAW_QUOTA_HEADER_OBSERVATIONS_FILE")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else QUOTA_HEADER_OBSERVATIONS_FILE

def model_quota_signal_file():
    raw = str(
        os.environ.get("AGENT_ROOM_MODEL_QUOTA_SIGNAL_FILE")
        or os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_FILE")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else MODEL_QUOTA_SIGNAL_FILE

def quota_signal_from_record(model, record, *, source):
    remaining_known = record.get("requests_remaining") is not None or record.get("tokens_remaining") is not None
    if not remaining_known:
        return None
    updated_at = record.get("last_updated") or utc_now()
    try:
        updated_dt = datetime.fromisoformat(str(updated_at))
    except Exception:
        updated_dt = datetime.now(timezone.utc)
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    expires_at = (updated_dt + timedelta(seconds=max(60, MODEL_QUOTA_SIGNAL_TTL_SECONDS))).isoformat(timespec="seconds")
    per_model_units = {}
    if record.get("requests_remaining") is not None:
        per_model_units["requests"] = record.get("requests_remaining")
    if record.get("tokens_remaining") is not None:
        per_model_units["tokens"] = record.get("tokens_remaining")
    signal = {
        "mode": "trusted_remaining_quota_signal",
        "remaining_known": True,
        "remaining_percent": None,
        "remaining_units": None,
        "model": str(model),
        "quota_scope": "provider_header_model_or_account",
        "per_model_remaining_known": None,
        "per_model_remaining_units": per_model_units,
        "source": source,
        "trusted_remaining_source": source,
        "proactive_switching_ready": True,
        "routing_ready": True,
        "quota_signal_contract_version": "2-provider-header",
        "blocking_missing": [],
        "observed_at": updated_at,
        "updated_at": updated_at,
        "expires_at": expires_at,
        "signal_file": str(model_quota_signal_file()),
        "limitation": "Response headers provide numeric remaining quota, but header names may not prove whether the limit is per-model or provider/account-scoped; current routing only hard-skips a model when remaining is zero and reset_at is still in the future.",
    }
    for source_key, signal_key in (
        ("requests_remaining", "remaining_requests"),
        ("tokens_remaining", "remaining_tokens"),
        ("limit_requests", "limit_requests"),
        ("limit_tokens", "limit_tokens"),
        ("reset_at", "reset_at"),
    ):
        if record.get(source_key) is not None:
            signal[signal_key] = record.get(source_key)
    return signal

def update_model_quota_signal_from_record(model, record, *, agent_id=None, source="quota_ledger"):
    signal = quota_signal_from_record(model, record, source=source)
    if not signal:
        return None
    path = model_quota_signal_file()
    data = read_json_file(path)
    if not isinstance(data, dict):
        data = {}
    data["schema"] = "openclaw.agent_room.model_quota_signal.v2"
    data["source"] = data.get("source") or source
    data["updated_at"] = signal.get("updated_at") or utc_now()
    root = data.setdefault("signals", {})
    if not isinstance(root, dict):
        root = {}
        data["signals"] = root
    target_agent = str(agent_id or "direct-provider").strip() or "direct-provider"
    agent_signal = root.setdefault(target_agent, {})
    if not isinstance(agent_signal, dict):
        agent_signal = {}
        root[target_agent] = agent_signal
    models = agent_signal.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        agent_signal["models"] = models
    models[str(model)] = signal
    write_json_atomic(path, data)
    return signal

def get_model_quota(model):
    return read_quota_ledger().get("models", {}).get(model, {})

def ensure_model_quota(ledger, model):
    if "models" not in ledger:
        ledger["models"] = {}
    if model not in ledger["models"]:
        ledger["models"][model] = {
            "requests_remaining": None,
            "tokens_remaining": None,
            "limit_requests": None,
            "limit_tokens": None,
            "reset_at": None,
            "accumulated_tokens": 0,
            "accumulated_completion_tokens": 0,
            "accumulated_prompt_tokens": 0,
            "accumulated_calls": 0,
            "calls_since_reset": 0,
            "tokens_since_reset": 0,
            "source": None,
            "last_updated": None,
        }
    return ledger["models"][model]


def normalized_header_map(headers):
    raw = {}
    if hasattr(headers, "items"):
        for key, value in (headers.items() if hasattr(headers, "items") else []):
            raw[key.lower()] = str(value).strip()
    elif isinstance(headers, dict):
        for key, value in headers.items():
            raw[key.lower()] = str(value).strip()
    return raw

def record_quota_header_observation(model, headers, *, agent_id=None, source="response_header"):
    """Append safe header-name evidence for provider quota visibility.

    Values are intentionally not written here. Numeric remaining values still go
    through quota_ledger.json; this stream only proves whether a real provider
    response exposed quota/rate-limit header names at all.
    """
    raw = normalized_header_map(headers)
    if not raw:
        return None
    observed_names = sorted(str(key).lower().strip() for key in raw if str(key).strip())
    candidate_names = sorted(name for name in observed_names if name in QUOTA_HEADER_CANDIDATES)
    limit_like_names = sorted(
        name
        for name in observed_names
        if (
            name in QUOTA_HEADER_CANDIDATES
            or "ratelimit" in name
            or name.startswith("x-rate-limit")
            or name.startswith("x-quota")
            or name == "retry-after"
        )
    )
    event = {
        "schema": "openclaw.agent_room.quota_header_observation.v0",
        "observed_at": utc_now(),
        "agent_id": str(agent_id or "direct-provider"),
        "model": str(model),
        "source": source,
        "header_count": len(observed_names),
        "quota_headers_present": bool(candidate_names),
        "known_quota_header_names": candidate_names,
        "limit_like_header_names": limit_like_names,
        "limitation": "Header names only; values are not logged here. Absence of known quota headers means this response cannot populate per-model numeric remaining quota from headers.",
    }
    path = quota_header_observations_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return None
    return event

def extract_quota_from_headers(headers):
    """Extract numeric quota info from HTTP response headers.

    Accepts any object with .items() (http.client.HTTPMessage, urllib headers)
    or a plain dict. Returns a dict with keys: requests_remaining,
    tokens_remaining, limit_requests, limit_tokens, reset_at, source.
    """
    result = {
        "requests_remaining": None,
        "tokens_remaining": None,
        "limit_requests": None,
        "limit_tokens": None,
        "reset_at": None,
        "source": None,
    }

    raw = normalized_header_map(headers)
    if not raw:
        return result

    quota_map = {
        "requests_remaining": "",
        "tokens_remaining": "",
        "limit_requests": "",
        "limit_tokens": "",
        "reset_at": "",
    }

    for header_key, value in raw.items():
        hl = header_key.lower().strip()
        if hl in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining", "x-quota-remaining"):
            quota_map["requests_remaining"] = value
        elif hl in ("x-ratelimit-remaining-tokens", "x-account-quota-remaining"):
            quota_map["tokens_remaining"] = value
        elif hl in ("x-ratelimit-limit-requests", "x-ratelimit-limit", "x-quota-limit"):
            quota_map["limit_requests"] = value
        elif hl == "x-ratelimit-limit-tokens":
            quota_map["limit_tokens"] = value
        elif hl in ("x-ratelimit-reset-requests", "x-ratelimit-reset"):
            if not quota_map["reset_at"]:
                quota_map["reset_at"] = value
        elif hl == "x-ratelimit-reset-tokens" and not quota_map["reset_at"]:
            quota_map["reset_at"] = value

    for key in ("requests_remaining", "tokens_remaining", "limit_requests", "limit_tokens"):
        val = quota_map.get(key)
        if val:
            try:
                result[key] = int(val)
            except (ValueError, TypeError):
                pass

    reset_raw = quota_map.get("reset_at")
    if reset_raw:
        try:
            seconds = int(reset_raw)
            result["reset_at"] = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
        except (ValueError, TypeError):
            result["reset_at"] = reset_raw

    if any(v is not None for v in (result["requests_remaining"], result["tokens_remaining"])):
        result["source"] = "response_header"

    return result


def update_quota_ledger_from_headers(model, headers, *, agent_id=None):
    """Update quota ledger from HTTP response headers.

    Returns the updated model record.
    """
    record_quota_header_observation(model, headers, agent_id=agent_id)
    ledger = read_quota_ledger()
    record = ensure_model_quota(ledger, model)
    header_data = extract_quota_from_headers(headers)
    changed = False

    for key in ("requests_remaining", "tokens_remaining", "limit_requests", "limit_tokens", "reset_at"):
        new_val = header_data.get(key)
        if new_val is not None:
            record[key] = new_val
            changed = True
    if header_data.get("source"):
        record["source"] = header_data["source"]
        changed = True

    # Reset accumulated counters when we get fresh remaining data
    if header_data.get("requests_remaining") is not None or header_data.get("tokens_remaining") is not None:
        record["calls_since_reset"] = 0
        record["tokens_since_reset"] = 0

    if changed:
        now_stamp = utc_now()
        record["last_updated"] = now_stamp
        ledger["updated_at"] = now_stamp
        save_quota_ledger(ledger)
        if header_data.get("requests_remaining") is not None or header_data.get("tokens_remaining") is not None:
            update_model_quota_signal_from_record(
                model,
                record,
                agent_id=agent_id,
                source="quota_ledger.response_header",
            )

    return record


def update_quota_ledger_from_usage(model, usage):
    """Update quota ledger from per-call token usage data.

    This is the fallback path for calls where HTTP headers aren't available
    (e.g. native OpenClaw gateway subprocess). Accumulates token counts
    as a proxy for consumption.
    """
    if not usage or not isinstance(usage, dict):
        return {}

    ledger = read_quota_ledger()
    record = ensure_model_quota(ledger, model)
    now_stamp = utc_now()

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)

    if total_tokens > 0:
        record["accumulated_tokens"] = (record["accumulated_tokens"] or 0) + total_tokens
        record["accumulated_prompt_tokens"] = (record["accumulated_prompt_tokens"] or 0) + prompt_tokens
        record["accumulated_completion_tokens"] = (record["accumulated_completion_tokens"] or 0) + completion_tokens
        record["accumulated_calls"] = (record["accumulated_calls"] or 0) + 1
        record["tokens_since_reset"] = (record["tokens_since_reset"] or 0) + total_tokens
        record["calls_since_reset"] = (record["calls_since_reset"] or 0) + 1
        record["last_used_at"] = now_stamp

        if not record.get("source"):
            record["source"] = "usage_accumulation"

        record["last_updated"] = now_stamp
        ledger["updated_at"] = now_stamp
        save_quota_ledger(ledger)

    return record


def summarize_quota_for_decision(model):
    """Return a summary dict of quota state for a model.

    Used by agent_task_runner to surface remaining-quota visibility in
    room comments and model selection decisions.
    """
    record = get_model_quota(model)
    if not record:
        return {
            "available": True,
            "remaining_known": False,
            "requests_remaining": None,
            "tokens_remaining": None,
            "accumulated_calls": 0,
            "accumulated_tokens": 0,
            "reset_at": None,
            "source": None,
            "summary": "no quota data tracked yet",
        }

    remaining_known = record.get("requests_remaining") is not None or record.get("tokens_remaining") is not None
    source = record.get("source") or "unknown"
    parts = []

    if record.get("requests_remaining") is not None:
        total = record.get("limit_requests")
        if total:
            parts.append("{}/{} requests".format(record["requests_remaining"], total))
        else:
            parts.append("{} requests remaining".format(record["requests_remaining"]))

    if record.get("tokens_remaining") is not None:
        total = record.get("limit_tokens")
        if total:
            parts.append("{}/{} tokens".format(record["tokens_remaining"], total))
        else:
            parts.append("{} tokens remaining".format(record["tokens_remaining"]))

    if not remaining_known and record.get("accumulated_calls", 0) > 0:
        parts.append("{} calls tracked ({} tokens consumed)".format(
            record["accumulated_calls"], record["accumulated_tokens"]))

    if record.get("reset_at"):
        parts.append("reset: {}".format(record["reset_at"]))

    return {
        "available": True,
        "remaining_known": remaining_known,
        "requests_remaining": record.get("requests_remaining"),
        "tokens_remaining": record.get("tokens_remaining"),
        "accumulated_calls": record.get("accumulated_calls", 0),
        "accumulated_tokens": record.get("accumulated_tokens", 0),
        "reset_at": record.get("reset_at"),
        "source": source,
        "summary": "; ".join(parts) if parts else ("tracked via {}".format(source) if source else "no quota data"),
    }
