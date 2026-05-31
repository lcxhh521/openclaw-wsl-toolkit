#!/usr/bin/env python3
"""Read Ark usage through Volcengine OpenAPI and merge safe quota-cache fields.

This is the API-first path for the Agent Room/Control Center quota card. It does
not use Chrome, DOM scraping, Telegram, OpenClaw gateway, or model calls. It only
uses Volcengine OpenAPI AK/SK credentials from local environment/secret files and
writes a bounded local cache when the response shape is parseable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ROOM = Path(os.environ.get("OPENCLAW_ROOM_AGENT_DIR", str(Path.home() / ".openclaw/workspace/codex-main-bridge/agent-room")))
USAGE_FILE = ROOM / "token_channel_usage.json"
STATUS_FILE = ROOM / "volcengine_ark_usage_openapi.status.json"
RAW_META_FILE = ROOM / "volcengine_ark_usage_openapi.last-meta.json"
SECRET_FILES = [
    Path.home() / ".openclaw/secrets/volcengine-openapi.env",
    Path.home() / ".openclaw/secrets/volcengine.env",
]
HOST = os.environ.get("VOLCENGINE_ARK_OPENAPI_HOST", "open.volcengineapi.com")
SERVICE = os.environ.get("VOLCENGINE_ARK_OPENAPI_SERVICE", "ark")
VERSION = "2024-01-01"
CONTENT_TYPE = "application/json; charset=UTF-8"
SOURCE = "volcengine_openapi_get_afp_usage"


def now_local() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone()


def iso(value: dt.datetime | None = None) -> str:
    return (value or now_local()).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_env_files() -> None:
    for path in SECRET_FILES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def pick_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def credentials() -> tuple[str | None, str | None, list[str]]:
    load_env_files()
    ak_names = [
        "VOLCENGINE_ACCESS_KEY_ID",
        "VOLCENGINE_ACCESS_KEY",
        "VOLCANO_ENGINE_ACCESS_KEY_ID",
        "VOLCANO_ENGINE_ACCESS_KEY",
        "ARK_OPENAPI_ACCESS_KEY_ID",
        "ARK_OPENAPI_ACCESS_KEY",
        "VOLCENGINE_AK",
    ]
    sk_names = [
        "VOLCENGINE_SECRET_ACCESS_KEY",
        "VOLCENGINE_SECRET_KEY",
        "VOLCANO_ENGINE_SECRET_ACCESS_KEY",
        "VOLCANO_ENGINE_SECRET_KEY",
        "ARK_OPENAPI_SECRET_ACCESS_KEY",
        "ARK_OPENAPI_SECRET_KEY",
        "VOLCENGINE_SK",
    ]
    return pick_env(*ak_names), pick_env(*sk_names), ak_names + sk_names


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def canonical_query(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)


def auth_headers(*, ak: str, sk: str, region: str, action: str, body: bytes, request_time: dt.datetime) -> dict[str, str]:
    x_date = request_time.strftime("%Y%m%dT%H%M%SZ")
    short_date = request_time.strftime("%Y%m%d")
    payload_hash = sha256_hex(body)
    query = canonical_query({"Action": action, "Version": VERSION})
    signed_headers = "host;x-content-sha256;x-date"
    canonical_headers = "".join([
        f"host:{HOST}\n",
        f"x-content-sha256:{payload_hash}\n",
        f"x-date:{x_date}\n",
    ])
    canonical_request = "\n".join([
        "POST",
        "/",
        query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = "/".join([short_date, region, SERVICE, "request"])
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        x_date,
        credential_scope,
        sha256_hex(canonical_request.encode("utf-8")),
    ])
    k_date = hmac_sha256(sk.encode("utf-8"), short_date)
    k_region = hmac_sha256(k_date, region)
    k_service = hmac_sha256(k_region, SERVICE)
    k_signing = hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Content-Type": CONTENT_TYPE,
        "Host": HOST,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": f"HMAC-SHA256 Credential={ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}",
    }


def post_openapi(*, ak: str, sk: str, region: str, action: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_time = dt.datetime.now(dt.timezone.utc)
    headers = auth_headers(ak=ak, sk=sk, region=region, action=action, body=body, request_time=request_time)
    url = f"https://{HOST}/?{canonical_query({'Action': action, 'Version': VERSION})}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except Exception:
            body = {"raw_body": raw[:1000]}
        if not isinstance(body, dict):
            body = {"raw_type": type(body).__name__}
        body["_http_status"] = exc.code
        body["_http_reason"] = exc.reason
        return body
    data = json.loads(raw) if raw.strip() else {}
    return data if isinstance(data, dict) else {"raw_type": type(data).__name__}


def number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def label_from_key(key: Any) -> str | None:
    value = str(key or "").strip().lower()
    if value in {"5h", "5hr", "five_hour", "rolling_5h"}:
        return "5h"
    if value in {"week", "weekly", "7d"}:
        return "Week"
    if value in {"month", "monthly"}:
        return "Month"
    return None




def ms_to_iso(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return dt.datetime.fromtimestamp(number / 1000, dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_afp_usage(response: dict[str, Any], observed_at: str) -> dict[str, dict[str, Any]]:
    result = response.get("Result") if isinstance(response.get("Result"), dict) else response
    mapping = {
        "AFPFiveHour": "5h",
        "AFPDaily": "Day",
        "AFPWeekly": "Week",
        "AFPMonthly": "Month",
    }
    windows: dict[str, dict[str, Any]] = {}
    if not isinstance(result, dict):
        return windows
    for key, label in mapping.items():
        row = result.get(key)
        if not isinstance(row, dict):
            continue
        quota = number(row.get("Quota"))
        used = number(row.get("Used"))
        if quota is None or quota <= 0 or used is None:
            continue
        used_percent = min(100.0, round(used / quota * 100, 2))
        windows[label] = {
            "source": SOURCE,
            "host": HOST,
            "service": SERVICE,
            "observed_at": observed_at,
            "used_percent": used_percent,
            "unit": "AFP",
            "used_afp": used,
            "quota_afp": quota,
            "subscribe_at": ms_to_iso(row.get("SubscribeTime")),
            "reset_at": ms_to_iso(row.get("ResetTime")),
        }
    return windows

def afp_response_looks_unbound(response: dict[str, Any]) -> bool:
    """Return true when OpenAPI auth succeeded but no Agent/Coding Plan seat is visible.

    The official GetAFPUsage shape can return zeroed AFP windows with an empty
    PlanType for credentials that authenticate but do not map to a readable plan.
    Treat that as "unknown/unbound", not as a real 0% remaining quota.
    """
    result = response.get("Result") if isinstance(response.get("Result"), dict) else response
    if not isinstance(result, dict):
        return False
    if str(result.get("PlanType") or "").strip():
        return False
    keys = ("AFPFiveHour", "AFPDaily", "AFPWeekly", "AFPMonthly")
    saw_window = False
    for key in keys:
        row = result.get(key)
        if not isinstance(row, dict):
            continue
        saw_window = True
        try:
            quota = float(row.get("Quota") or 0)
            used = float(row.get("Used") or 0)
        except (TypeError, ValueError):
            return False
        if quota != 0 or used != 0:
            return False
    return saw_window


def normalize_windows(response: dict[str, Any], observed_at: str) -> dict[str, dict[str, Any]]:
    result = response.get("Result") if isinstance(response.get("Result"), dict) else response
    candidates: list[Any] = []
    for key in ("windows", "Windows", "WindowDetails", "UsageWindows", "QuotaWindows", "Data"):
        value = result.get(key) if isinstance(result, dict) else None
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            for label, row in value.items():
                if isinstance(row, dict):
                    merged = dict(row)
                    merged.setdefault("label", label)
                    candidates.append(merged)
    windows: dict[str, dict[str, Any]] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        label = label_from_key(row.get("label") or row.get("Label") or row.get("window") or row.get("Window"))
        if not label:
            continue
        used_percent = number(row.get("used_percent", row.get("UsedPercent", row.get("usage_percent", row.get("UsagePercent")))))
        used_requests = number(row.get("used_requests", row.get("UsedRequests", row.get("requests", row.get("RequestCount")))))
        window: dict[str, Any] = {
            "source": SOURCE,
            "observed_at": observed_at,
        }
        if used_percent is not None:
            window["used_percent"] = min(100.0, used_percent)
        if used_requests is not None:
            window["used_requests"] = int(used_requests)
        for src, dst in (("reset_at", "reset_at"), ("ResetAt", "reset_at"), ("resets_at", "resets_at"), ("ResetTime", "reset_at")):
            if row.get(src):
                window[dst] = str(row.get(src))
        if "used_percent" in window or "used_requests" in window:
            windows[label] = window
    return windows


def merge_usage(windows: dict[str, dict[str, Any]], observed_at: str, *, dry_run: bool) -> dict[str, Any]:
    existing = read_json(USAGE_FILE)
    channels = existing.setdefault("channels", {})
    channel = channels.setdefault("ark-coding-plan", {})
    channel["source"] = SOURCE
    channel["source_kind"] = "official_api"
    channel["observed_at"] = observed_at
    channel["note"] = "Official Volcengine OpenAPI usage response normalized for local quota display; no Chrome/DOM source used."
    current_windows = channel.setdefault("windows", {})
    current_windows.update(windows)
    if not dry_run:
        write_json(USAGE_FILE, existing)
    return existing


def safe_response_meta(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("Result") if isinstance(response.get("Result"), dict) else {}
    metadata = response.get("ResponseMetadata") if isinstance(response.get("ResponseMetadata"), dict) else {}
    return {
        "response_metadata_keys": sorted(metadata.keys()),
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "data_count": result.get("DataCount") if isinstance(result, dict) else None,
        "has_error": bool(response.get("Error")) or bool(metadata.get("Error")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Ark usage from Volcengine OpenAPI and update local token channel cache.")
    parser.add_argument("--action", default="GetAFPUsage")
    parser.add_argument("--region", default=os.environ.get("VOLCENGINE_REGION", "cn-beijing"))
    parser.add_argument("--query-interval", default="Hour", choices=["Hour", "Day"])
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--plan-types", default="", help="Comma-separated Agent Plan type enum values, e.g. 1,2,3,4. Leave empty to query all visible plan details.")
    parser.add_argument("--show-window-detail", action="store_true", help="Deprecated compatibility flag; GetUsageDetails now uses Filter-shaped payload.")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--stdin-response", action="store_true", help="Read an already captured JSON response from stdin and normalize it without network. Defaults to dry-run unless --allow-stdin-write is set.")
    parser.add_argument("--allow-stdin-write", action="store_true", help="Allow --stdin-response to mutate local usage cache; intended only for controlled fixture installation.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.stdin_response and not args.allow_stdin_write:
        args.dry_run = True

    observed_at = iso()
    end = now_local().replace(microsecond=0)
    start = end - dt.timedelta(hours=5)
    if args.action == "GetAFPUsage":
        payload = {}
    else:
        # GetUsageDetails requires StartTime/EndTime under Filter and uses
        # date-only values. Keep this path as usage-detail evidence only; true
        # remaining AFP quota still comes from GetAFPUsage.
        filter_obj: dict[str, Any] = {
            "StartTime": args.start_time or start.strftime("%Y-%m-%d"),
            "EndTime": args.end_time or end.strftime("%Y-%m-%d"),
        }
        if args.plan_types:
            filter_obj["PlanType"] = [int(x.strip()) for x in args.plan_types.split(",") if x.strip()]
        payload = {
            "QueryInterval": args.query_interval,
            "Filter": filter_obj,
        }

    if args.stdin_response:
        response = json.loads(sys.stdin.read() or "{}")
    else:
        ak, sk, names = credentials()
        if not ak or not sk:
            status = {
                "updated_at": observed_at,
                "status": "blocked_missing_credentials",
                "required_any_env": names,
                "secret_files_checked": [str(p) for p in SECRET_FILES],
                "network_called": False,
            }
            write_json(STATUS_FILE, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 2
        try:
            response = post_openapi(ak=ak, sk=sk, region=args.region, action=args.action, payload=payload, timeout=args.timeout)
        except Exception as exc:
            status = {
                "updated_at": observed_at,
                "status": "openapi_request_failed",
                "reason": str(exc)[:500],
                "network_called": True,
                "action": args.action,
            }
            write_json(STATUS_FILE, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 1

    windows = normalize_afp_usage(response, observed_at) if args.action == "GetAFPUsage" else normalize_windows(response, observed_at)
    meta = safe_response_meta(response)
    http_status = response.get("_http_status")
    response_error = response.get("Error") or (response.get("ResponseMetadata") or {}).get("Error") if isinstance(response.get("ResponseMetadata"), dict) else response.get("Error")
    unbound_plan = args.action == "GetAFPUsage" and not http_status and not windows and afp_response_looks_unbound(response)
    mapped_status = "ok" if windows else ("openapi_http_error" if http_status else ("authorized_empty_or_unbound_plan" if unbound_plan else "response_shape_unmapped"))
    status = {
        "updated_at": observed_at,
        "status": mapped_status,
        "source": SOURCE,
        "host": HOST,
        "service": SERVICE,
        "credential_accepted": bool(not http_status),
        "action": args.action,
        "payload": {k: payload[k] for k in sorted(payload)},
        "normalized_windows": sorted(windows),
        "response_meta": meta,
        "http_status": http_status,
        "http_reason": response.get("_http_reason"),
        "response_error": response_error,
        "dry_run": bool(args.dry_run),
    }
    if windows:
        merge_usage(windows, observed_at, dry_run=args.dry_run)
    if not args.dry_run:
        write_json(STATUS_FILE, status)
        write_json(RAW_META_FILE, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if windows else 3


if __name__ == "__main__":
    raise SystemExit(main())
