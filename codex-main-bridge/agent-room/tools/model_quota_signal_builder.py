#!/usr/bin/env python3
"""Build Agent Room quota signal from trusted local usage sources.

This deliberately separates two concepts that were previously conflated:

- provider/account quota windows that OpenClaw can read (for example Codex 5h
  and Week usage windows);
- per-model numeric quota, which is only known if a provider response/header or a
  dedicated provider endpoint exposes it.

The output is consumed by status readers. It must never invent per-model
remaining amounts from cooldown/success state.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
BRIDGE = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(WORKSPACE / "codex-main-bridge")))
ROOM = BRIDGE / "agent-room"
DEFAULT_OUTPUT = ROOM / "model_quota_signal.json"
DEFAULT_LEDGER = ROOM / "quota_ledger.json"
DEFAULT_TOKEN_CHANNEL_CONFIG = ROOM / "token_channel_config.json"
DEFAULT_TOKEN_CHANNEL_USAGE = ROOM / "token_channel_usage.json"
DEFAULT_ARK_OPENAPI_STATUS = ROOM / "volcengine_ark_usage_openapi.status.json"
DEFAULT_QUOTA_HEADER_OBSERVATIONS = ROOM / "quota_header_observations.jsonl"
DEFAULT_CODEX_STATE = ROOM / "codex_model_state.json"
DEFAULT_CLAUDE_STATE = ROOM / "claude_model_state.json"
DEFAULT_WATCHER_STATE = BRIDGE / ".openclaw_main_watcher_state.json"
DEFAULT_TTL_SECONDS = int(os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_TTL_SECONDS", "900"))
ARK_CONSOLE_SNAPSHOT_MAX_AGE_SECONDS = int(os.environ.get("OPENCLAW_ARK_CONSOLE_SNAPSHOT_MAX_AGE_SECONDS", "1800"))
OPENAI_CODEX_PROVIDER = "openai-codex"
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local" / "bin" / "openclaw"))

OFFICIAL_API_USAGE_SOURCES = {
    "volcengine_openapi_get_usage",
    "volcengine_openapi_get_inference_usage",
    "volcengine_official_api",
    "ark_coding_plan_official_api",
    "ark_coding_plan_official_usage_snapshot",
}
CONSOLE_SNAPSHOT_USAGE_SOURCES = {
    "volcengine_console_dom_bridge",
    "volcengine_console_snapshot_dom_fallback",
    "volcengine_console_usage_snapshot",
    "manual_official_console_snapshot",
}
LOCAL_OBSERVED_USAGE_SOURCES = {
    "local_observed_requests_plus_configured_plan_limit",
    "local_observed_requests_no_plan_limit_configured",
}

# These are model identifiers used in the Agent Room Codex/GPT chain. They share
# the OpenAI Codex account/provider usage window unless a future provider signal
# proves a more specific per-model quota.
CODEX_GPT_MODELS = [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "gpt-5.4-mini",
]


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def ms_to_iso(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def parse_json_object_with_preamble(text: str) -> dict[str, Any]:
    """Parse the first JSON object from command output that may contain warnings.

    Some OpenClaw CLI paths still print config warnings before the requested
    --json payload. Treat those warnings as transport preamble instead of
    dropping the quota signal entirely.
    """
    if not text:
        return {}
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else {}
    return {}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def pct_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(100.0, number)


def model_aliases(model: str) -> list[str]:
    value = str(model or "").strip()
    if not value:
        return []
    aliases = [value]
    if value.startswith("openai-codex/"):
        aliases.append(value.split("/", 1)[1])
    elif value.startswith("codex/"):
        bare = value.split("/", 1)[1]
        aliases.extend([bare, f"openai-codex/{bare}"])
    elif value.startswith("gpt-"):
        aliases.extend([f"openai-codex/{value}", f"codex/{value}"])
    out: list[str] = []
    for item in aliases:
        if item and item not in out:
            out.append(item)
    return out


def collect_models_from_state(path: Path) -> list[str]:
    data = read_json(path)
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return [str(key) for key in models.keys() if str(key)]


def openclaw_status_usage(timeout_seconds: int) -> tuple[dict[str, Any], str | None]:
    """Return `openclaw status --usage --json` output.

    This is an explicit refresh path, not something to call on every dispatch.
    """
    try:
        proc = subprocess.run(
            [OPENCLAW_BIN, "status", "--usage", "--json"],
            cwd=str(WORKSPACE),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {}, f"openclaw status --usage --json timed out after {timeout_seconds}s"
    except Exception as exc:
        return {}, f"openclaw status --usage --json failed: {exc}"
    data = parse_json_object_with_preamble(proc.stdout or "")
    if not data:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["empty output"]
        return {}, "could not parse openclaw status usage JSON: " + detail[0]
    if proc.returncode != 0:
        return data if isinstance(data, dict) else {}, f"openclaw status returned {proc.returncode}"
    return data if isinstance(data, dict) else {}, None


def provider_windows(status_json: dict[str, Any], provider_name: str) -> dict[str, Any] | None:
    usage = status_json.get("usage") if isinstance(status_json.get("usage"), dict) else {}
    providers = usage.get("providers") if isinstance(usage.get("providers"), list) else []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if str(provider.get("provider") or "") == provider_name:
            return provider
    return None


def week_start(dt: datetime) -> datetime:
    local = dt.astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=start.weekday())


def month_start(dt: datetime) -> datetime:
    local = dt.astimezone()
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        pass
    return rows


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def window_from_provider(item: dict[str, Any]) -> dict[str, Any]:
    label = str(item.get("label") or "window")
    used = pct_number(item.get("used_percent") if "used_percent" in item else item.get("usedPercent"))
    remaining = pct_number(item.get("remaining_percent"))
    if remaining is None:
        remaining = None if used is None else max(0.0, 100.0 - used)
    return {
        "label": label,
        "used_known": used is not None,
        "used_percent": used,
        "remaining_known": remaining is not None,
        "remaining_percent": remaining,
        "reset_at": item.get("reset_at") or ms_to_iso(item.get("resetAt")),
        "source": "openclaw status --usage --json",
    }


def count_ark_observed_requests(path: Path, generated: datetime) -> dict[str, int]:
    five_hours_ago = generated - timedelta(hours=5)
    week = week_start(generated)
    month = month_start(generated)
    counts = {"5h": 0, "Week": 0, "Month": 0}
    for event in read_jsonl(path):
        model = str(event.get("model") or "")
        source = str(event.get("source") or "")
        agent_id = str(event.get("agent_id") or "")
        if not model and "ark" not in source.lower() and "direct-provider" not in agent_id:
            continue
        observed = parse_time(event.get("observed_at"))
        if observed is None:
            continue
        if observed >= five_hours_ago:
            counts["5h"] += 1
        if observed >= week:
            counts["Week"] += 1
        if observed >= month:
            counts["Month"] += 1
    return counts


def configured_limit(config: dict[str, Any], channel_id: str, label: str) -> int | None:
    channels = config.get("channels") if isinstance(config.get("channels"), dict) else {}
    channel = channels.get(channel_id) if isinstance(channels.get(channel_id), dict) else {}
    windows = channel.get("windows") if isinstance(channel.get("windows"), dict) else {}
    raw = None
    if isinstance(windows.get(label), dict):
        raw = windows[label].get("limit_requests", windows[label].get("limit"))
    elif isinstance(windows.get(label.lower()), dict):
        raw = windows[label.lower()].get("limit_requests", windows[label.lower()].get("limit"))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def normalize_window_label(label: Any) -> str:
    key = str(label or "").strip().lower()
    if key in {"5h", "5hr", "5hour", "5hours"}:
        return "5h"
    if key in {"week", "weekly", "7d"}:
        return "Week"
    if key in {"month", "monthly"}:
        return "Month"
    return str(label or "window").strip() or "window"


def channel_usage_windows(usage: dict[str, Any], channel_id: str) -> dict[str, dict[str, Any]]:
    channels = usage.get("channels") if isinstance(usage.get("channels"), dict) else {}
    channel = channels.get(channel_id) if isinstance(channels.get(channel_id), dict) else {}
    raw_windows = channel.get("windows")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw_windows, dict):
        for label, row in raw_windows.items():
            if isinstance(row, dict):
                out[normalize_window_label(label)] = row
    elif isinstance(raw_windows, list):
        for row in raw_windows:
            if isinstance(row, dict):
                out[normalize_window_label(row.get("label"))] = row
    return out


def int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def usage_source_kind(source: Any) -> str:
    value = str(source or "").strip()
    if value in OFFICIAL_API_USAGE_SOURCES or value.startswith("volcengine_openapi_"):
        return "official_api"
    if value in CONSOLE_SNAPSHOT_USAGE_SOURCES or "console" in value or "dom_bridge" in value:
        return "console_snapshot"
    if value in LOCAL_OBSERVED_USAGE_SOURCES or value.startswith("local_observed_"):
        return "local_observed"
    return "unknown"


def observation_age_seconds(row: dict[str, Any], generated: datetime) -> int | None:
    observed = parse_time(row.get("observed_at"))
    if observed is None:
        return None
    return max(0, int((generated - observed).total_seconds()))


def build_used_window(label: str, row: dict[str, Any], limit: int | None, source: str, generated: datetime) -> dict[str, Any]:
    used_requests = int_or_none(row.get("used_requests", row.get("used", row.get("observed_used_requests"))))
    used_percent = pct_number(row.get("used_percent", row.get("usedPercent")))
    actual_source = str(row.get("source") or source)
    source_kind = usage_source_kind(actual_source)
    age_seconds = observation_age_seconds(row, generated)
    stale = (
        source_kind == "console_snapshot"
        and (age_seconds is None or age_seconds > ARK_CONSOLE_SNAPSHOT_MAX_AGE_SECONDS)
    )
    limit = int_or_none(row.get("limit_requests", row.get("limit"))) or limit
    item: dict[str, Any] = {
        "label": label,
        "used_known": used_requests is not None or used_percent is not None,
        "used_requests": used_requests,
        "used_percent": used_percent,
        "remaining_known": False,
        "source": actual_source,
        "source_kind": source_kind,
        "api_first": True,
        "live_api": source_kind == "official_api",
        "authoritative_usage": source_kind == "official_api",
        "current_quota_basis": source_kind == "official_api",
        "historical_only": source_kind == "console_snapshot",
        "stale": stale,
        "display_in_quota_card": source_kind == "official_api",
        "reset_at": row.get("reset_at") or row.get("resets_at"),
    }
    if row.get("observed_at"):
        item["observed_at"] = row.get("observed_at")
    if age_seconds is not None:
        item["age_seconds"] = age_seconds
    if row.get("reset_text"):
        item["reset_text"] = row.get("reset_text")
    if limit:
        item["limit_requests"] = limit

    can_compute_live_remaining = source_kind == "official_api"
    if used_percent is not None:
        snapshot_remaining = round(max(0.0, 100.0 - used_percent), 2)
        if can_compute_live_remaining:
            item["remaining_known"] = True
            item["remaining_percent"] = snapshot_remaining
        else:
            item["remaining_percent_snapshot"] = snapshot_remaining
            item["limitation"] = "Ark Coding Plan usage is from a console snapshot; keep it as historical evidence only, not as current quota. Prefer Volcengine OpenAPI usage when configured."
        if limit:
            estimated_used = round(limit * used_percent / 100)
            item["used_requests_estimated_from_percent"] = estimated_used
            if can_compute_live_remaining:
                item["remaining_requests_estimated_from_percent"] = max(0, limit - estimated_used)
            else:
                item["remaining_requests_snapshot_estimated_from_percent"] = max(0, limit - estimated_used)
    elif used_requests is not None and limit and source_kind == "official_api":
        remaining = max(0, limit - used_requests)
        item["remaining_known"] = True
        item["remaining_requests"] = remaining
        item["remaining_percent"] = round(remaining / limit * 100, 2)
    elif used_requests is not None:
        item["limitation"] = "Ark Coding Plan usage reports used amount, but remaining quota needs an official plan limit or percentage source."
    else:
        item["limitation"] = "No Ark Coding Plan usage amount is available for this window yet."
    return item


def build_token_channels(
    *,
    provider_signal: dict[str, Any] | None,
    agent_state: dict[str, Any],
    codex_state: dict[str, Any],
    config: dict[str, Any],
    usage_snapshot: dict[str, Any],
    quota_observations_path: Path,
    generated: datetime,
    generated_at: str,
    expires_at: str,
) -> dict[str, Any]:
    channels: dict[str, Any] = {}

    codex_windows: list[dict[str, Any]] = []
    if provider_signal:
        for item in provider_signal.get("windows") or []:
            if isinstance(item, dict):
                codex_windows.append(window_from_provider(item))
    codex_models = codex_state.get("models") if isinstance(codex_state.get("models"), dict) else {}
    codex_total = 0
    codex_available = 0
    for row in codex_models.values():
        if not isinstance(row, dict):
            continue
        codex_total += 1
        if str(row.get("status") or "").lower() in {"available", "recovered"}:
            codex_available += 1
    codex_status = "available" if codex_available > 0 else ("unavailable" if codex_total > 0 else "unknown")
    channels["codex"] = {
        "id": "codex",
        "display_name": "Codex 额度",
        "provider_keys": ["openai-codex", "codex"],
        "base_urls": ["https://chatgpt.com/backend-api"],
        "consumers": ["openclaw-main", "codex", "daily-writer", "telegram"],
        "remaining_known": any(w.get("remaining_known") for w in codex_windows),
        "availability_known": codex_total > 0,
        "available_models": codex_available,
        "total_models": codex_total,
        "status": codex_status,
        "windows": codex_windows,
        "source": "openclaw status --usage --json" if codex_windows else "codex_model_state + pending provider usage refresh",
        "limitation": None if codex_windows else "Codex model availability is known from local success/failure state, but provider 5h/week usage windows need the heavier OpenClaw status refresh.",
        "updated_at": generated_at,
        "expires_at": expires_at,
    }

    agents = agent_state.get("agents") if isinstance(agent_state.get("agents"), dict) else {}
    claude = agents.get("claude-code") if isinstance(agents.get("claude-code"), dict) else {}
    models = claude.get("models") if isinstance(claude.get("models"), dict) else {}
    available = 0
    total = 0
    for row in models.values():
        if not isinstance(row, dict):
            continue
        total += 1
        if str(row.get("status") or "").lower() in {"available", "recovered"}:
            available += 1

    observed = count_ark_observed_requests(quota_observations_path, generated)
    official_usage = channel_usage_windows(usage_snapshot, "ark-coding-plan")
    ark_openapi_status = read_json(DEFAULT_ARK_OPENAPI_STATUS)
    ark_usage_api_status = str(ark_openapi_status.get("status") or "").strip()
    ark_usage_api_credential_accepted = bool(ark_openapi_status.get("credential_accepted"))
    ark_usage_api_host = str(ark_openapi_status.get("host") or "").strip()
    ark_windows: list[dict[str, Any]] = []
    labels = ["5h", "Week"]
    for extra in ("Month",):
        if extra in official_usage:
            labels.append(extra)
    for label in labels:
        limit = configured_limit(config, "ark-coding-plan", label)
        if label in official_usage:
            ark_windows.append(build_used_window(label, official_usage[label], limit, "ark_coding_plan_official_usage_snapshot", generated))
            continue
        used = observed.get(label, 0)
        if limit:
            remaining_requests = max(0, limit - used)
            ark_windows.append({
                "label": label,
                "used_known": False,
                "observed_only": True,
                "display_in_quota_card": False,
                "estimated_remaining_requests": remaining_requests,
                "limit_requests": limit,
                "observed_used_requests": used,
                "estimated_remaining_percent": round(remaining_requests / limit * 100, 2),
                "source": "local_observed_requests_plus_configured_plan_limit",
                "limitation": "Local observed requests are diagnostic only and may miss console-side usage; do not display them as authoritative Ark Coding Plan quota.",
            })
        else:
            ark_windows.append({
                "label": label,
                "used_known": False,
                "observed_only": True,
                "display_in_quota_card": False,
                "remaining_known": False,
                "observed_used_requests": used,
                "source": "local_observed_requests_no_plan_limit_configured",
                "limitation": "Local observed requests are diagnostic only and not the console's authoritative Ark Coding Plan usage.",
            })

    live_usage_known = any(w.get("live_api") and w.get("used_known") for w in ark_windows)
    fallback_snapshot_present = any(w.get("source_kind") == "console_snapshot" for w in ark_windows)
    snapshot_stale = any(w.get("source_kind") == "console_snapshot" and w.get("stale") for w in ark_windows)
    current_quota_basis = "volcengine_openapi" if live_usage_known else None
    if live_usage_known:
        ark_limitation = None
    elif ark_usage_api_status == "authorized_empty_or_unbound_plan":
        ark_limitation = "Volcengine OpenAPI accepted the AK/SK, but GetAFPUsage returned an empty PlanType and zero AFP windows. This usually means the AK/SK is not attached to the Coding Plan seat/account that has quota; the model API key may still work for calls."
    elif ark_usage_api_status:
        ark_limitation = f"Volcengine OpenAPI usage refresh status: {ark_usage_api_status}. Runtime API-key availability is separate from quota visibility."
    else:
        ark_limitation = "No fresh Volcengine OpenAPI Ark Coding Plan usage source is configured yet; console/DOM snapshots are fallback evidence only."
    channels["ark-coding-plan"] = {
        "id": "ark-coding-plan",
        "display_name": "Ark Coding Plan",
        "provider_keys": ["volcengine-plan", "ark-coding-plan"],
        "base_urls": [
            "https://ark.cn-beijing.volces.com/api/coding",
            "https://ark.cn-beijing.volces.com/api/coding/v3",
        ],
        "consumers": ["claude-code", "codex-ark-fallback", "openclaw-main-ark-fallback"],
        "available_models": available,
        "total_models": total,
        "remaining_known": any(w.get("remaining_known") for w in ark_windows),
        "live_usage_known": live_usage_known,
        "fallback_snapshot_present": fallback_snapshot_present,
        "snapshot_stale": snapshot_stale,
        "api_first": True,
        "runtime_api_key_known": available > 0,
        "usage_api_status": ark_usage_api_status,
        "usage_api_credential_accepted": ark_usage_api_credential_accepted,
        "usage_api_host": ark_usage_api_host,
        "current_quota_basis": current_quota_basis,
        "console_snapshot_is_current_basis": False,
        "windows": ark_windows,
        "source": "volcengine_openapi_usage_preferred + console_snapshot_fallback + quota_header_observations + optional token_channel_config",
        "updated_at": generated_at,
        "expires_at": expires_at,
        "limitation": ark_limitation,
    }

    configured = config.get("channels") if isinstance(config.get("channels"), dict) else {}
    for channel_id, channel_config in configured.items():
        if channel_id in channels or not isinstance(channel_config, dict):
            continue
        channels[str(channel_id)] = {
            "id": str(channel_id),
            "display_name": str(channel_config.get("display_name") or channel_id),
            "provider_keys": channel_config.get("provider_keys") or [],
            "base_urls": channel_config.get("base_urls") or [],
            "consumers": channel_config.get("consumers") or [],
            "remaining_known": False,
            "windows": [],
            "source": "token_channel_config",
            "updated_at": generated_at,
            "expires_at": expires_at,
        }
    return channels


def signal_from_provider_windows(provider: dict[str, Any], *, generated_at: str, expires_at: str) -> dict[str, Any] | None:
    raw_windows = provider.get("windows") if isinstance(provider.get("windows"), list) else []
    windows: list[dict[str, Any]] = []
    remaining_values: list[float] = []
    used_values: list[float] = []
    for window in raw_windows:
        if not isinstance(window, dict):
            continue
        used = pct_number(window.get("usedPercent"))
        if used is None:
            continue
        remaining = max(0.0, 100.0 - used)
        used_values.append(used)
        remaining_values.append(remaining)
        label = str(window.get("label") or "window")
        windows.append(
            {
                "label": label,
                "used_percent": used,
                "remaining_percent": remaining,
                "reset_at": ms_to_iso(window.get("resetAt")),
            }
        )
    if not windows:
        return None
    bottleneck_remaining = min(remaining_values)
    bottleneck_used = max(used_values)
    primary = windows[0]
    summary = "; ".join(
        f"{item['label']} {item['remaining_percent']:.0f}% left" for item in windows
    )
    return {
        "mode": "trusted_provider_usage_window",
        "remaining_known": True,
        "remaining_percent": bottleneck_remaining,
        "remaining_units": summary,
        "used_percent": bottleneck_used,
        "quota_scope": "provider_account_shared",
        "per_model_remaining_known": False,
        "provider_quota_window_known": True,
        "provider": str(provider.get("provider") or OPENAI_CODEX_PROVIDER),
        "provider_display_name": provider.get("displayName"),
        "plan": provider.get("plan"),
        "windows": windows,
        "primary_window_label": primary.get("label"),
        "primary_window_remaining_percent": primary.get("remaining_percent"),
        "reset_at": primary.get("reset_at"),
        "observed_at": generated_at,
        "updated_at": generated_at,
        "expires_at": expires_at,
        "source": "openclaw status --usage --json",
        "trusted_remaining_source": "openclaw status --usage --json provider window",
        "quota_signal_contract_version": "2-provider-window",
        "routing_ready": False,
        "proactive_switching_ready": False,
        "blocking_missing": ["per_model_remaining_units", "approved_routing_threshold_policy"],
        "limitation": "This is a provider/account window shared by OpenAI Codex models, not an independent per-model remaining quota. It is visibility/admission evidence until a routing threshold policy is explicitly approved.",
    }


def signal_from_ledger_record(model: str, record: dict[str, Any], *, generated_at: str, expires_at: str) -> dict[str, Any] | None:
    requests_remaining = record.get("requests_remaining")
    tokens_remaining = record.get("tokens_remaining")
    if requests_remaining is None and tokens_remaining is None:
        return None
    signal: dict[str, Any] = {
        "mode": "trusted_provider_response_header",
        "remaining_known": True,
        "remaining_percent": None,
        "remaining_units": None,
        "remaining_requests": requests_remaining,
        "remaining_tokens": tokens_remaining,
        "limit_requests": record.get("limit_requests"),
        "limit_tokens": record.get("limit_tokens"),
        "quota_scope": "provider_header_model_or_account",
        "per_model_remaining_known": None,
        "model": model,
        "source": "quota_ledger response headers",
        "trusted_remaining_source": "provider response headers captured by quota_ledger",
        "quota_signal_contract_version": "2-provider-header",
        "routing_ready": True,
        "proactive_switching_ready": True,
        "reset_at": record.get("reset_at"),
        "observed_at": record.get("last_updated") or generated_at,
        "updated_at": generated_at,
        "expires_at": expires_at,
        "limitation": "Header names do not always declare whether the quota is per-model or provider/account-scoped; route conservatively.",
    }
    if requests_remaining is not None and record.get("limit_requests"):
        try:
            signal["remaining_percent"] = round(float(requests_remaining) / float(record["limit_requests"]) * 100, 2)
        except Exception:
            pass
    elif tokens_remaining is not None and record.get("limit_tokens"):
        try:
            signal["remaining_percent"] = round(float(tokens_remaining) / float(record["limit_tokens"]) * 100, 2)
        except Exception:
            pass
    return signal


def put_signal(signals: dict[str, Any], agent_id: str, model: str, signal: dict[str, Any]) -> None:
    agent = signals.setdefault(agent_id, {"models": {}})
    models = agent.setdefault("models", {})
    for alias in model_aliases(model):
        item = dict(signal)
        item.setdefault("model", alias)
        item["canonical_model"] = model
        models[alias] = item


def build_signal(
    *,
    include_openclaw_status: bool,
    timeout_seconds: int,
    ttl_seconds: int,
    ledger_path: Path,
    codex_state_path: Path,
    claude_state_path: Path,
    watcher_state_path: Path,
    token_channel_config_path: Path,
    token_channel_usage_path: Path,
    quota_observations_path: Path,
) -> tuple[dict[str, Any], int]:
    generated = now()
    generated_at = iso(generated)
    expires_at = iso(generated + timedelta(seconds=max(60, ttl_seconds)))
    signals: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []

    provider_signal: dict[str, Any] | None = None
    if include_openclaw_status:
        status_json, error = openclaw_status_usage(timeout_seconds)
        if error:
            diagnostics.append({"source": "openclaw status --usage --json", "status": "error", "detail": error})
        provider = provider_windows(status_json, OPENAI_CODEX_PROVIDER) if status_json else None
        provider_signal = signal_from_provider_windows(provider, generated_at=generated_at, expires_at=expires_at) if provider else None
        if provider_signal:
            codex_models = CODEX_GPT_MODELS[:]
            for model in collect_models_from_state(codex_state_path):
                if model not in codex_models:
                    codex_models.append(model)
            watcher = read_json(watcher_state_path)
            main_candidates = watcher.get("main_gpt_candidate_models") or watcher.get("main_codex_candidate_models") or []
            if isinstance(main_candidates, list):
                for model in main_candidates:
                    model = str(model)
                    if model and model not in codex_models:
                        codex_models.append(model)
            for model in codex_models:
                if model.startswith("gpt-") or model.startswith("openai-codex/") or model.startswith("codex/"):
                    put_signal(signals, "codex", model, provider_signal)
                    put_signal(signals, "openclaw-main", model, provider_signal)
        elif include_openclaw_status:
            diagnostics.append({"source": "openclaw status --usage --json", "status": "no_openai_codex_provider_window"})

    ledger = read_json(ledger_path)
    models = ledger.get("models") if isinstance(ledger.get("models"), dict) else {}
    codex_state_models = set(collect_models_from_state(codex_state_path))
    claude_state_models = set(collect_models_from_state(claude_state_path))
    for model, record in models.items():
        if not isinstance(record, dict):
            continue
        header_signal = signal_from_ledger_record(str(model), record, generated_at=generated_at, expires_at=expires_at)
        if not header_signal:
            continue
        target_agents: list[str] = []
        bare = str(model).split("/", 1)[-1]
        if model in codex_state_models or bare in codex_state_models or str(model).startswith(("gpt-", "openai-codex/", "codex/")):
            target_agents.extend(["codex", "openclaw-main"])
        if model in claude_state_models or bare in claude_state_models:
            target_agents.append("claude-code")
        if not target_agents:
            target_agents.append("openclaw-main")
        for agent_id in target_agents:
            put_signal(signals, agent_id, str(model), header_signal)

    agent_state = read_json(ROOM / "agent_quota_state.json")
    codex_state = read_json(codex_state_path)
    token_channel_config = read_json(token_channel_config_path)
    token_channel_usage = read_json(token_channel_usage_path)
    token_channels = build_token_channels(
        provider_signal=provider_signal,
        agent_state=agent_state,
        codex_state=codex_state,
        config=token_channel_config,
        usage_snapshot=token_channel_usage,
        quota_observations_path=quota_observations_path,
        generated=generated,
        generated_at=generated_at,
        expires_at=expires_at,
    )

    output = {
        "schema": "openclaw.agent_room.model_quota_signal.v2",
        "source": "openclaw status --usage --json + quota_ledger response headers",
        "generated_at": generated_at,
        "updated_at": generated_at,
        "expires_at": expires_at,
        "ttl_seconds": max(60, ttl_seconds),
        "signals": signals,
        "token_channels": token_channels,
        "diagnostics": diagnostics,
        "safety": {
            "does_not_invent_per_model_quota": True,
            "provider_windows_are_provider_account_scoped": True,
            "unknown_remaining_must_remain_unknown": True,
        },
    }
    return output, 0 if signals else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Agent Room model quota signal JSON.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--codex-state", default=str(DEFAULT_CODEX_STATE))
    parser.add_argument("--claude-state", default=str(DEFAULT_CLAUDE_STATE))
    parser.add_argument("--watcher-state", default=str(DEFAULT_WATCHER_STATE))
    parser.add_argument("--token-channel-config", default=str(DEFAULT_TOKEN_CHANNEL_CONFIG))
    parser.add_argument("--token-channel-usage", default=str(DEFAULT_TOKEN_CHANNEL_USAGE))
    parser.add_argument("--quota-observations", default=str(DEFAULT_QUOTA_HEADER_OBSERVATIONS))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_REFRESH_TIMEOUT", "90")))
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--no-openclaw-status", action="store_true", help="Only build from quota_ledger response headers.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data, status = build_signal(
        include_openclaw_status=not args.no_openclaw_status,
        timeout_seconds=max(5, args.timeout),
        ttl_seconds=args.ttl_seconds,
        ledger_path=Path(args.ledger),
        codex_state_path=Path(args.codex_state),
        claude_state_path=Path(args.claude_state),
        watcher_state_path=Path(args.watcher_state),
        token_channel_config_path=Path(args.token_channel_config),
        token_channel_usage_path=Path(args.token_channel_usage),
        quota_observations_path=Path(args.quota_observations),
    )
    if args.dry_run:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        write_json(Path(args.output), data)
        print(json.dumps({"status": "ok" if status == 0 else "no_signals", "output": args.output, "signals": sorted(data.get("signals", {}).keys())}, ensure_ascii=False))
    return 0 if status in {0, 2} else status


if __name__ == "__main__":
    raise SystemExit(main())
