#!/usr/bin/env python3
"""Agent liveness at-a-glance: one compact line per agent.

Reads agent-presence + active-runners and outputs a Telegram-friendly
compact status. Designed to be called by the bridge daemon as a /status
command handler, or standalone for local debugging.

Usage:
    python3 agent_liveness_glance.py          # compact text
    python3 agent_liveness_glance.py --json   # structured JSON
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BRIDGE = Path(
    os.environ.get(
        "OPENCLAW_MAILBOX_ROOT",
        str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"),
    )
)
AGENT_ROOM = BRIDGE / "agent-room"
PRESENCE_DIR = AGENT_ROOM / "agent-presence" / "agents"
ACTIVE_DIR = AGENT_ROOM / "active-runners"
DAEMON_STATUS = AGENT_ROOM / "agent_room_bridge_daemon.status.json"

KNOWN_AGENTS = ("claude-code", "codex", "openclaw-main")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _seconds_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return max(0, int((datetime.now().astimezone() - dt).total_seconds()))


def _format_age(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"


def _agent_icon(alive: bool, state: str | None) -> str:
    if state == "idle" or state == "waiting":
        return "🟡"
    if alive:
        return "🟢"
    return "⚫"


def _collect_active_runners() -> dict[str, list[dict[str, Any]]]:
    """Group active-runner records by agent_id."""
    by_agent: dict[str, list[dict[str, Any]]] = {}
    if not ACTIVE_DIR.exists():
        return by_agent
    for path in sorted(ACTIVE_DIR.glob("*.json")):
        rec = _read_json(path)
        if not isinstance(rec, dict):
            continue
        aid = str(rec.get("agent_id") or "unknown")
        pid = int(rec.get("pid") or 0)
        alive = _pid_alive(pid)
        started = str(rec.get("started_at") or "")
        age = _seconds_since(started)
        run_id = str(rec.get("run_id") or "")
        by_agent.setdefault(aid, []).append({
            "run_id": run_id,
            "task_id": str(rec.get("task_id") or ""),
            "pid": pid or None,
            "alive": alive,
            "status": str(rec.get("status") or ""),
            "started_at": started or None,
            "age_seconds": age,
            "max_seconds": rec.get("max_seconds"),
            "stale": bool(
                alive
                and age is not None
                and rec.get("max_seconds")
                and age > int(rec.get("max_seconds"))
            ),
        })
    return by_agent


def _collect_presence() -> dict[str, dict[str, Any]]:
    """Read per-agent presence files."""
    by_agent: dict[str, dict[str, Any]] = {}
    if not PRESENCE_DIR.exists():
        return by_agent
    for path in PRESENCE_DIR.glob("*.json"):
        rec = _read_json(path)
        if not isinstance(rec, dict):
            continue
        aid = str(rec.get("agent_id") or path.stem)
        by_agent[aid] = {
            "state": str(rec.get("state") or ""),
            "work_item_id": str(rec.get("work_item_id") or ""),
            "pid": int(rec.get("pid") or 0) or None,
            "updated_at": str(rec.get("updated_at") or ""),
            "run_id": str(rec.get("run_id") or ""),
            "backend": str(rec.get("backend") or ""),
        }
    return by_agent


def _daemon_alive() -> dict[str, Any]:
    rec = _read_json(DAEMON_STATUS)
    if not isinstance(rec, dict):
        return {"status": "unknown", "pid": None, "alive": False}
    pid = int(rec.get("pid") or 0)
    return {
        "status": str(rec.get("status") or ""),
        "pid": pid or None,
        "alive": _pid_alive(pid),
        "tick": rec.get("tick"),
        "last_tick_at": str(rec.get("last_tick_finished_at") or ""),
    }


def glance() -> list[dict[str, Any]]:
    """Produce per-agent liveness records."""
    runners = _collect_active_runners()
    presence = _collect_presence()
    daemon = _daemon_alive()

    agent_ids = sorted(set(list(runners.keys()) + list(presence.keys()) + list(KNOWN_AGENTS)))

    results: list[dict[str, Any]] = []

    # openclaw-main is special: runs as the bridge daemon, not a spawned runner
    main_rec: dict[str, Any] = {
        "agent_id": "openclaw-main",
        "icon": _agent_icon(daemon["alive"], "running" if daemon["alive"] else None),
        "alive": daemon["alive"],
        "state": "running" if daemon["alive"] else "stopped",
        "pid": daemon.get("pid"),
        "run_id": None,
        "age_seconds": _seconds_since(daemon.get("last_tick_at")),
        "runners_count": 0,
        "detail": f"daemon tick {daemon.get('tick', '?')}" if daemon["alive"] else "daemon not running",
    }
    results.append(main_rec)

    for aid in agent_ids:
        if aid == "openclaw-main":
            continue
        agent_runners = runners.get(aid, [])
        agent_presence = presence.get(aid)
        # Use presence PID as the primary liveness indicator
        presence_pid = agent_presence.get("pid") if agent_presence else None
        presence_alive = _pid_alive(presence_pid) if presence_pid else False
        presence_state = agent_presence.get("state", "") if agent_presence else ""
        presence_updated = agent_presence.get("updated_at", "") if agent_presence else ""
        age = _seconds_since(presence_updated)

        # Also check if any active runner is alive
        any_runner_alive = any(r["alive"] for r in agent_runners)
        alive = presence_alive or any_runner_alive
        state = presence_state or ("running" if any_runner_alive else "idle")
        icon = _agent_icon(alive, state if not alive else None)

        # Pick the most recent runner for display
        primary_run = None
        if agent_runners:
            primary_run = max(agent_runners, key=lambda r: r.get("age_seconds") or 0)

        run_id_short = None
        if primary_run:
            rid = primary_run.get("run_id", "")
            run_id_short = rid[:16] + "…" if len(rid) > 16 else rid

        detail_parts = []
        if state == "invoking_agent_backend":
            detail_parts.append("执行中")
        elif state == "idle" or state == "waiting":
            detail_parts.append("空闲")
        elif alive:
            detail_parts.append("运行中")
        else:
            detail_parts.append("已停止")

        if presence_pid and alive:
            detail_parts.append(f"PID {presence_pid}")
        if run_id_short:
            detail_parts.append(run_id_short)
        if len(agent_runners) > 1:
            detail_parts.append(f"{len(agent_runners)}个runner")

        results.append({
            "agent_id": aid,
            "icon": icon,
            "alive": alive,
            "state": state,
            "pid": presence_pid,
            "run_id": agent_presence.get("run_id") if agent_presence else None,
            "age_seconds": age,
            "runners_count": len(agent_runners),
            "detail": " | ".join(detail_parts) if detail_parts else "unknown",
        })

    return results


def glance_text() -> str:
    """Compact one-line-per-agent text output."""
    lines = []
    for rec in glance():
        age_str = _format_age(rec.get("age_seconds"))
        lines.append(
            f"{rec['icon']} {rec['agent_id']}: {rec['detail']}"
            + (f" ({age_str})" if rec.get("age_seconds") is not None else "")
        )
    return "\n".join(lines)


def main() -> int:
    if "--json" in sys.argv:
        print(json.dumps(glance(), ensure_ascii=False, indent=2))
    else:
        print(glance_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
