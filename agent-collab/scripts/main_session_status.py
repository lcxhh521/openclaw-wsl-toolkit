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
MAIN_TELEGRAM_KEY = os.environ.get("OPENCLAW_MAIN_SESSION_KEY", "")


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


def build_status(path: Path) -> dict[str, Any]:
    start = time.perf_counter()
    sessions = load_sessions(path)
    session = choose_main_session(sessions)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    if not session:
        return {
            "schema": "openclaw.main_session_status.v0",
            "generated_at": now_iso(),
            "status": "unknown",
            "reason": "main telegram session not found",
            "read_elapsed_ms": elapsed_ms,
            "sessions_file": str(path),
        }

    context_tokens = session.get("contextTokens")
    total_tokens = session.get("totalTokens")
    context_ratio = None
    try:
        if context_tokens and total_tokens is not None:
            context_ratio = round(float(total_tokens) / float(context_tokens), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        context_ratio = None

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
        "quota_state": "unknown",
        "fallback_active": False,
        "recommended_action": "none",
        "read_elapsed_ms": elapsed_ms,
        "sessions_file": str(path),
    }


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
    fallback = "已启用" if status.get("fallback_active") else "未启用"
    return (
        f"main 模型：{provider}/{model}\n"
        f"上下文：{context}\n"
        f"额度状态：{quota}\n"
        f"Ark fallback：{fallback}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OpenClaw main session status.")
    parser.add_argument("--sessions-file", default=str(DEFAULT_SESSIONS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = build_status(Path(args.sessions_file))
    if args.text:
        print(to_text(status))
    if args.json or not args.text:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
