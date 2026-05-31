#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SESSIONS = Path(os.environ.get("OPENCLAW_MAIN_SESSIONS_FILE", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json")))
OPENCLAW_MAILBOX_ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
OPENCLAW_MAIN_WATCHER_STATE = os.environ.get(
    "OPENCLAW_MAIN_WATCHER_STATE_FILE",
    str(OPENCLAW_MAILBOX_ROOT / ".openclaw_main_watcher_state.json"),
)
OPENCLAW_AGENT_ROOM_STATUS = os.environ.get(
    "OPENCLAW_AGENT_ROOM_STATUS_FILE",
    str(OPENCLAW_MAILBOX_ROOT / "agent-room" / "agent_room_status.json"),
)
OPENCLAW_MODEL_QUOTA_SIGNAL = os.environ.get(
    "OPENCLAW_MODEL_QUOTA_SIGNAL_FILE",
    str(OPENCLAW_MAILBOX_ROOT / "agent-room" / "model_quota_signal.json"),
)
MAIN_TELEGRAM_KEY = os.environ.get("OPENCLAW_MAIN_SESSION_KEY", "")
MAIN_QUOTA_STATE_KEY = "main_quota_state"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ms_to_iso(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000, timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )


def load_sessions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("sessions"), list):
            return [item for item in data["sessions"] if isinstance(item, dict)]
        mapped: list[dict[str, Any]] = []
        for key, value in data.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("key", key)
                mapped.append(item)
        if mapped:
            return mapped
        for value in data.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def choose_main_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for session in sessions:
        if session.get("key") == MAIN_TELEGRAM_KEY:
            return session
    candidates = [
        session
        for session in sessions
        if (session.get("agentId") == "main" or str(session.get("key") or "").startswith("agent:main:"))
        and "telegram" in str(session.get("key") or "")
        and (session.get("kind") == "direct" or ":direct:" in str(session.get("key") or ""))
    ]
    if not candidates:
        candidates = [
            session
            for session in sessions
            if (session.get("agentId") == "main" or str(session.get("key") or "").startswith("agent:main:"))
            and (session.get("kind") == "direct" or ":direct:" in str(session.get("key") or ""))
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("updatedAt") or 0))


def build_status(
    path: Path,
    *,
    watcher_state_path: Path = Path(OPENCLAW_MAIN_WATCHER_STATE),
    agent_room_status_path: Path = Path(OPENCLAW_AGENT_ROOM_STATUS),
    model_quota_signal_path: Path = Path(OPENCLAW_MODEL_QUOTA_SIGNAL),
) -> dict[str, Any]:
    start = time.perf_counter()
    sessions = load_sessions(path)
    session = choose_main_session(sessions)
    watcher_state = load_json_optional(watcher_state_path)
    room_status = load_json_optional(agent_room_status_path)
    main_room_status = (((room_status.get("agents") or {}).get("openclaw-main")) or {})
    if not isinstance(main_room_status, dict):
        main_room_status = {}
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    if not session:
        return {
            "schema": "openclaw.main_session_status.v0",
            "generated_at": now_iso(),
            "status": "unknown",
            "reason": "main telegram session not found",
            "read_elapsed_ms": elapsed_ms,
            "sessions_file": str(path),
            "main_watcher_state_file": str(watcher_state_path),
            "agent_room_status_file": str(agent_room_status_path),
        }

    context_tokens = session.get("contextTokens")
    total_tokens = session.get("totalTokens")
    context_ratio = None
    try:
        if context_tokens and total_tokens is not None:
            context_ratio = round(float(total_tokens) / float(context_tokens), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        context_ratio = None

    main_quota_state = str(watcher_state.get(MAIN_QUOTA_STATE_KEY) or main_room_status.get("quota_state") or "unknown")
    fallback_active = (
        bool(main_room_status.get("fallback_active"))
        or main_quota_state in {"depleted", "depleted_ark_active", "fallback_active"}
        or watcher_state.get("last_post_trigger_status") == "ark_fallback_advanced"
    )
    active_model = str(
        watcher_state.get("main_ark_fallback_last_model")
        or main_room_status.get("active_model")
        or session.get("model")
        or ""
    )
    recommended_action = (
        "等待 GPT 额度恢复并自动回切"
        if fallback_active
        else "none"
    )
    quota_signal = {
        "mode": "reactive_failure_cooldown_and_success_recovery",
        "remaining_known": False,
        "remaining_percent": None,
        "remaining_units": None,
        "source": "main watcher state + agent_room_status model records",
        "proactive_switching_ready": False,
        "trusted_remaining_source": "session_status quota signal not wired into this reader",
        "quota_signal_contract_version": "0-no-numeric",
        "blocking_missing": ["per_model_remaining_units"],
        "limitation": "quota_state=available means no active recorded depletion/cooldown; it is not a numeric remaining-quota measurement",
    }
    trusted_signal = trusted_remaining_quota_signal(
        model_quota_signal_path,
        "openclaw-main",
        active_model or session.get("model") or "openai-codex/gpt-5.5",
    )
    if trusted_signal:
        quota_signal.update(trusted_signal)
        quota_signal["reactive_fallback_source"] = "main watcher state + agent_room_status model records"

    return {
        "schema": "openclaw.main_session_status.v0",
        "generated_at": now_iso(),
        "status": "ok",
        "session_key": session.get("key"),
        "session_id": session.get("sessionId"),
        "updated_at": ms_to_iso(session.get("updatedAt")),
        "model_provider": session.get("modelProvider") or session.get("provider"),
        "model": session.get("model"),
        "agent_id": session.get("agentId") or "main",
        "runtime": (session.get("agentRuntime") or {}).get("id") or session.get("agentHarnessId"),
        "thinking_level": session.get("thinkingLevel"),
        "input_tokens": session.get("inputTokens"),
        "output_tokens": session.get("outputTokens"),
        "total_tokens": total_tokens,
        "context_tokens": context_tokens,
        "context_ratio": context_ratio,
        "quota_state": main_quota_state,
        "quota_signal": quota_signal,
        "fallback_active": fallback_active,
        "active_model": active_model,
        "recommended_action": recommended_action,
        "main_watcher_state_file": str(watcher_state_path),
        "agent_room_status_file": str(agent_room_status_path),
        "model_quota_signal_file": str(model_quota_signal_path),
        "main_ark_fallback": {
            "last_model": watcher_state.get("main_ark_fallback_last_model"),
            "last_detail": watcher_state.get("main_ark_fallback_last_detail"),
            "candidate_models": watcher_state.get("main_ark_fallback_candidate_models"),
            "model_attempts": watcher_state.get("main_ark_fallback_model_attempts"),
            "skipped_models": watcher_state.get("main_ark_fallback_skipped_models"),
        },
        "read_elapsed_ms": elapsed_ms,
        "sessions_file": str(path),
    }


def load_json_optional(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def quota_signal_lookup_keys(model: str | None) -> list[str]:
    value = str(model or "").strip() or "unknown-model"
    keys = [value]
    if "/" in value:
        keys.append(value.rsplit("/", 1)[-1])
    else:
        keys.append(f"openai-codex/{value}")
    out: list[str] = []
    for key in [*keys, *[key.lower() for key in keys]]:
        if key and key not in out:
            out.append(key)
    return out


def normalize_trusted_quota_signal(record: dict[str, Any], default_source: str, signal_file: Path) -> dict[str, Any] | None:
    expires_raw = record.get("expires_at") if isinstance(record, dict) else None
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw))
            if expires_at.tzinfo is None:
                expires_at = expires_at.astimezone()
            if expires_at <= datetime.now(timezone.utc).astimezone():
                return None
        except ValueError:
            return None
    fields = ("remaining_percent", "remaining_units", "remaining_requests", "remaining_tokens", "remaining_messages", "per_model_remaining_units")
    if not (bool(record.get("remaining_known")) or any(record.get(key) is not None for key in fields)):
        return None
    per_model_units = record.get("per_model_remaining_units")
    if not isinstance(per_model_units, dict):
        per_model_units = {}
    if record.get("remaining_requests") is not None:
        per_model_units.setdefault("requests", record.get("remaining_requests"))
    if record.get("remaining_tokens") is not None:
        per_model_units.setdefault("tokens", record.get("remaining_tokens"))
    if record.get("remaining_messages") is not None:
        per_model_units.setdefault("messages", record.get("remaining_messages"))
    source = str(record.get("trusted_remaining_source") or record.get("source") or default_source or "model_quota_signal").strip()
    signal: dict[str, Any] = {
        "mode": "trusted_remaining_quota_signal",
        "remaining_known": True,
        "remaining_percent": record.get("remaining_percent"),
        "remaining_units": record.get("remaining_units"),
        "source": source,
        "trusted_remaining_source": source,
        "proactive_switching_ready": bool(record.get("proactive_switching_ready") or record.get("routing_ready")),
        "signal_file": str(signal_file),
        "quota_signal_contract_version": str(record.get("quota_signal_contract_version") or "1-trusted-remaining"),
        "blocking_missing": list(record.get("blocking_missing") or []),
        "limitation": str(record.get("limitation") or "trusted remaining quota is wired; routing thresholds remain a separate policy"),
    }
    if per_model_units:
        signal["per_model_remaining_units"] = per_model_units
    for key in ("remaining_requests", "remaining_tokens", "remaining_messages", "limit_requests", "limit_tokens", "used_percent", "reset_at", "estimated_reset_at", "observed_at", "updated_at", "expires_at", "quota_scope", "per_model_remaining_known", "per_model_remaining_units", "provider_quota_window_known", "blocking_missing", "provider", "provider_display_name", "plan", "windows", "primary_window_label", "primary_window_remaining_percent", "canonical_model"):
        if record.get(key) is not None:
            signal[key] = record.get(key)
    return signal


def trusted_remaining_quota_signal(path: Path, agent_id: str, model: str | None = None) -> dict[str, Any] | None:
    data = load_json_optional(path)
    root = data.get("signals") if isinstance(data.get("signals"), dict) else data.get("agents")
    if not isinstance(root, dict):
        root = data
    agent_signal = root.get(agent_id) if isinstance(root.get(agent_id), dict) else None
    if agent_signal is None:
        alt = agent_id.replace("-", "_")
        agent_signal = root.get(alt) if isinstance(root.get(alt), dict) else None
    if not isinstance(agent_signal, dict):
        return None
    models = agent_signal.get("models")
    record = agent_signal
    if isinstance(models, dict):
        record = {}
        for key in quota_signal_lookup_keys(model):
            value = models.get(key)
            if isinstance(value, dict):
                record = value
                break
        if not record:
            lower = {str(key).lower(): value for key, value in models.items() if isinstance(value, dict)}
            for key in quota_signal_lookup_keys(model):
                value = lower.get(key.lower())
                if isinstance(value, dict):
                    record = value
                    break
    return normalize_trusted_quota_signal(record if isinstance(record, dict) else {}, str(data.get("source") or "model_quota_signal"), path)


def to_text(status: dict[str, Any]) -> str:
    if status.get("status") != "ok":
        return f"main 状态：未知（{status.get('reason')}）"
    provider = status.get("model_provider") or "unknown"
    model = status.get("model") or "unknown"
    ratio = status.get("context_ratio")
    context = "未知"
    if ratio is not None:
        context = f"{status.get('total_tokens')}/{status.get('context_tokens')} ({ratio:.0%})"
    quota = status.get("quota_state") or "unknown"
    quota_signal = status.get("quota_signal") if isinstance(status.get("quota_signal"), dict) else {}
    remaining = "未知"
    if quota_signal.get("remaining_known"):
        percent = quota_signal.get("remaining_percent")
        if percent is not None:
            try:
                value = float(percent)
                remaining = f"{value * 100:.0f}%" if 0 <= value <= 1 else f"{value:.0f}%"
            except (TypeError, ValueError):
                remaining = str(percent)
        elif quota_signal.get("remaining_units") is not None:
            remaining = str(quota_signal.get("remaining_units"))
        elif quota_signal.get("remaining_requests") is not None:
            remaining = f"{quota_signal.get('remaining_requests')} requests"
        elif quota_signal.get("remaining_tokens") is not None:
            remaining = f"{quota_signal.get('remaining_tokens')} tokens"
        elif quota_signal.get("remaining_messages") is not None:
            remaining = f"{quota_signal.get('remaining_messages')} messages"
        else:
            remaining = "known"
        if quota_signal.get("quota_scope") == "provider_account_shared":
            remaining += "（provider共享，不是单模型独立额度）"
    fallback = "已启用" if status.get("fallback_active") else "未启用"
    active = status.get("active_model") or model
    ark = status.get("main_ark_fallback") if isinstance(status.get("main_ark_fallback"), dict) else {}
    candidates = ark.get("candidate_models") or []
    candidate_text = ""
    if isinstance(candidates, list) and candidates:
        candidate_text = "\nArk候选：" + ", ".join(str(item) for item in candidates[:5])
    return (
        f"main 模型：{provider}/{model}\n"
        f"当前生效模型：{active}\n"
        f"上下文：{context}\n"
        f"额度状态：{quota}\n"
        f"剩余额度：{remaining}\n"
        f"Ark fallback：{fallback}"
        f"{candidate_text}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OpenClaw main session status.")
    parser.add_argument("--sessions-file", default=str(DEFAULT_SESSIONS))
    parser.add_argument("--watcher-state", default=str(OPENCLAW_MAIN_WATCHER_STATE))
    parser.add_argument("--agent-room-status", default=str(OPENCLAW_AGENT_ROOM_STATUS))
    parser.add_argument("--model-quota-signal", default=str(OPENCLAW_MODEL_QUOTA_SIGNAL))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = build_status(
        Path(args.sessions_file),
        watcher_state_path=Path(args.watcher_state),
        agent_room_status_path=Path(args.agent_room_status),
        model_quota_signal_path=Path(args.model_quota_signal),
    )
    if args.text:
        print(to_text(status))
    if args.json or not args.text:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
