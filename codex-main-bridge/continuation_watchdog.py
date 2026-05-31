#!/usr/bin/env python3
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

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT

ROOT = CODE_ROOT
MAILBOX = MAILBOX_ROOT
CONT_DIR = CODE_ROOT / "runtime-continuations"
EVENTS_DIR = CONT_DIR / "events"
WATCHDOG_LOG = CONT_DIR / "watchdog.jsonl"
LATEST_REPORT = CONT_DIR / "latest-watchdog-report.json"
TURN_FILE = MAILBOX / "turn.json"
FOREGROUND_NOTIFY = CODE_ROOT / "foreground_notify.py"
AGENT_ROOM_ACTIVE_RUNNERS = CODE_ROOT / "agent-room" / "active-runners"
SCHEMA = "openclaw.runtime.continuation_event.v0"
TERMINAL_STATES = {"completed", "failed", "blocked", "cancelled"}


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
    return dt


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def event_path(event_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in event_id).strip(".-")
    if not safe:
        raise ValueError("empty event id")
    return EVENTS_DIR / f"{safe}.json"


def load_event(event_id: str) -> dict[str, Any]:
    data = read_json(event_path(event_id), {}) or {}
    if not isinstance(data, dict):
        return {}
    return data


def save_event(event: dict[str, Any]) -> None:
    if not event.get("event_id"):
        raise ValueError("event missing event_id")
    event["updated_at"] = now_iso()
    write_json_atomic(event_path(str(event["event_id"])), event)


def list_events() -> list[dict[str, Any]]:
    if not EVENTS_DIR.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(EVENTS_DIR.glob("*.json")):
        data = read_json(path, {}) or {}
        if isinstance(data, dict) and data.get("event_id"):
            events.append(data)
    return events


def add_minutes(minutes: float) -> str:
    return (now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            item = part.strip()
            if item:
                out.append(item)
    return out


def pid_alive(pid: Any) -> bool:
    try:
        pid_value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid_value <= 0:
        return False
    stat_path = Path(f"/proc/{pid_value}/stat")
    if not stat_path.exists():
        return False
    try:
        stat_text = stat_path.read_text(encoding="utf-8", errors="replace")
        right_paren = stat_text.rfind(")")
        after_name = stat_text[right_paren + 2:].split() if right_paren != -1 else stat_text.split()[2:]
        if after_name and after_name[0] == "Z":
            return False
    except Exception:
        pass
    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def systemd_unit_alive(unit: Any) -> bool:
    unit_name = str(unit or "").strip()
    if not unit_name:
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", unit_name, "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "--no-pager"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    state: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key] = value
    # Liveness must be process-backed. A lingering/activating transient unit
    # with MainPID=0 is not proof that the Agent Room runner is still working.
    return pid_alive(state.get("MainPID"))


def inspect_agent_room_harvest() -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "agent_room_harvest",
        "status": "observed",
        "active_runners": 0,
        "alive_runners": 0,
        "result_pending_harvest": 0,
        "dead_or_missing_process": 0,
        "needs_harvest": 0,
    }
    if not AGENT_ROOM_ACTIVE_RUNNERS.exists():
        result["status"] = "missing_active_runner_dir"
        result["interpretation"] = "no_harvest_needed"
        return result
    for path in sorted(AGENT_ROOM_ACTIVE_RUNNERS.glob("*.json")):
        record = read_json(path, {}) or {}
        if not isinstance(record, dict):
            continue
        result["active_runners"] += 1
        runner_dir = Path(str(record.get("runner_dir") or ""))
        result_path = runner_dir / "result.json" if runner_dir else Path()
        result_ready = bool(runner_dir and result_path.exists())
        alive = systemd_unit_alive(record.get("systemd_unit")) or pid_alive(record.get("pid"))
        if alive:
            result["alive_runners"] += 1
        if result_ready:
            result["result_pending_harvest"] += 1
        if not alive:
            result["dead_or_missing_process"] += 1
        if result_ready or not alive:
            result["needs_harvest"] += 1
    result["interpretation"] = "harvest_needed" if result["needs_harvest"] else "no_harvest_needed"
    return result


def inspect_waiting_on(waiting_on: str) -> dict[str, Any]:
    kind = (waiting_on or "").strip()
    result: dict[str, Any] = {"kind": kind or "none", "status": "unknown"}
    if not kind:
        result["status"] = "none"
        return result
    if kind.startswith("codex_mailbox") or kind == "codex":
        turn = read_json(TURN_FILE, {}) or {}
        result.update(
            {
                "status": "observed",
                "turn_seq": turn.get("seq"),
                "last_writer": turn.get("last_writer"),
                "needs_reply": turn.get("needs_reply"),
                "updated_at": turn.get("updated_at"),
            }
        )
        if turn.get("needs_reply") == "codex":
            result["interpretation"] = "waiting_for_codex"
        elif turn.get("needs_reply") == "main":
            result["interpretation"] = "main_must_reply_or_watcher_should_advance"
        elif turn.get("needs_reply") == "none":
            result["interpretation"] = "mailbox_idle"
        else:
            result["interpretation"] = "mailbox_unknown"
        return result
    if kind == "agent_room_harvest":
        return inspect_agent_room_harvest()
    if kind.startswith("pid:"):
        pid_raw = kind.split(":", 1)[1].strip()
        try:
            pid = int(pid_raw)
        except ValueError:
            result.update({"status": "invalid_pid", "pid": pid_raw})
            return result
        alive = Path(f"/proc/{pid}").exists()
        result.update({"status": "observed", "pid": pid, "alive": alive, "interpretation": "process_alive" if alive else "process_exited"})
        return result
    result["status"] = "not_inspected"
    return result


def evaluate(event: dict[str, Any]) -> dict[str, Any]:
    state = str(event.get("state") or "unknown")
    created = parse_time(event.get("created_at"))
    updated = parse_time(event.get("updated_at"))
    stale_at = parse_time(event.get("stale_at"))
    lease_deadline = parse_time(event.get("lease_deadline"))
    n = now()
    stale = bool(stale_at and n >= stale_at)
    lease_expired = bool(lease_deadline and n >= lease_deadline)
    terminal = state in TERMINAL_STATES
    waiting = inspect_waiting_on(str(event.get("waiting_on") or ""))
    severity = "ok"
    action = "none"
    if terminal:
        severity = "terminal"
        action = "none"
    elif lease_expired:
        severity = "critical"
        action = "mark_blocked_or_continue_now"
    elif stale:
        severity = "warning"
        interp = waiting.get("interpretation")
        if interp == "main_must_reply_or_watcher_should_advance":
            action = "run_main_watcher_or_reply"
        elif interp == "waiting_for_codex":
            action = "ask_or_wait_codex_with_deadline"
        elif interp == "harvest_needed":
            action = "run_agent_room_harvest_only"
        elif interp == "process_alive":
            action = "poll_process_or_emit_progress"
        elif interp == "process_exited":
            action = "collect_result_and_close"
        else:
            action = "emit_progress_or_reassess"
    return {
        "event_id": event.get("event_id"),
        "state": state,
        "terminal": terminal,
        "created_at": event.get("created_at"),
        "updated_at": event.get("updated_at"),
        "age_seconds": round((n - created).total_seconds(), 1) if created else None,
        "stale": stale,
        "lease_expired": lease_expired,
        "severity": severity,
        "action": action,
        "waiting_on": waiting,
        "goal": event.get("user_visible_goal"),
        "next_action": event.get("next_action"),
        "evidence_paths": event.get("evidence_paths") or [],
        "blocker_summary": event.get("blocker_summary") or "",
    }


def foreground_notify_dry_run(report: dict[str, Any]) -> dict[str, Any] | None:
    if not FOREGROUND_NOTIFY.exists():
        return None
    stale = [r for r in report.get("events", []) if r.get("severity") in {"warning", "critical"}]
    if not stale:
        return None
    first = stale[0]
    title = f"Continuation watchdog: {first.get('event_id')}"
    summary = f"state={first.get('state')} action={first.get('action')} waiting={first.get('waiting_on', {}).get('interpretation')}"
    cmd = [
        sys.executable,
        str(FOREGROUND_NOTIFY),
        "--event-kind",
        "watchdog_stalled",
        "--severity",
        "warning" if first.get("severity") == "warning" else "error",
        "--title",
        title[:120],
        "--summary",
        summary[:300],
        "--affected-workflow",
        "runtime-continuation",
        "--artifact",
        str(LATEST_REPORT),
        "--dry-run",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
    return {"returncode": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}


def register(args: argparse.Namespace) -> dict[str, Any]:
    existing = load_event(args.event_id)
    created_at = existing.get("created_at") or now_iso()
    event = {
        **existing,
        "schema": SCHEMA,
        "event_id": args.event_id,
        "created_at": created_at,
        "state": args.state,
        "owner": args.owner,
        "owner_session": args.owner_session or existing.get("owner_session") or "",
        "source_chat_id": args.source_chat_id or existing.get("source_chat_id") or "",
        "source_message_id": args.source_message_id or existing.get("source_message_id") or "",
        "user_visible_goal": args.goal,
        "current_state_summary": args.summary or existing.get("current_state_summary") or "",
        "next_action": args.next_action,
        "waiting_on": args.waiting_on,
        "stale_after_minutes": args.stale_after_minutes,
        "lease_minutes": args.lease_minutes,
        "stale_at": add_minutes(args.stale_after_minutes),
        "lease_deadline": add_minutes(args.lease_minutes),
        "evidence_paths": split_values(args.evidence),
        "notify_policy": args.notify_policy,
        "terminal_condition": args.terminal_condition,
        "blocker_summary": args.blocker_summary or "",
        "history": [*(existing.get("history") if isinstance(existing.get("history"), list) else []), {"at": now_iso(), "action": "register", "state": args.state, "next_action": args.next_action}],
    }
    save_event(event)
    return event


def update(args: argparse.Namespace) -> dict[str, Any]:
    event = load_event(args.event_id)
    if not event:
        raise SystemExit(f"event not found: {args.event_id}")
    changes: dict[str, Any] = {}
    for key, value in {
        "state": args.state,
        "current_state_summary": args.summary,
        "next_action": args.next_action,
        "waiting_on": args.waiting_on,
        "blocker_summary": args.blocker_summary,
    }.items():
        if value is not None:
            event[key] = value
            changes[key] = value
    if args.evidence:
        current = event.get("evidence_paths") if isinstance(event.get("evidence_paths"), list) else []
        event["evidence_paths"] = [*current, *split_values(args.evidence)]
        changes["evidence_paths_added"] = split_values(args.evidence)
    if args.stale_after_minutes is not None:
        event["stale_after_minutes"] = args.stale_after_minutes
        event["stale_at"] = add_minutes(args.stale_after_minutes)
        changes["stale_at"] = event["stale_at"]
    if args.lease_minutes is not None:
        event["lease_minutes"] = args.lease_minutes
        event["lease_deadline"] = add_minutes(args.lease_minutes)
        changes["lease_deadline"] = event["lease_deadline"]
    history = event.get("history") if isinstance(event.get("history"), list) else []
    history.append({"at": now_iso(), "action": "update", "changes": changes})
    event["history"] = history[-80:]
    save_event(event)
    return event


def watchdog(args: argparse.Namespace) -> dict[str, Any]:
    events = [evaluate(event) for event in list_events()]
    report = {
        "schema": "openclaw.runtime.continuation_watchdog_report.v0",
        "generated_at": now_iso(),
        "event_count": len(events),
        "nonterminal_count": sum(1 for e in events if not e["terminal"]),
        "stale_count": sum(1 for e in events if e["stale"] and not e["terminal"]),
        "critical_count": sum(1 for e in events if e["severity"] == "critical"),
        "events": events,
    }
    write_json_atomic(LATEST_REPORT, report)
    append_jsonl(WATCHDOG_LOG, report)
    if args.notify_dry_run:
        report["foreground_notify_dry_run"] = foreground_notify_dry_run(report)
        write_json_atomic(LATEST_REPORT, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw runtime continuation event registry/watchdog.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register")
    p.add_argument("--event-id", required=True)
    p.add_argument("--owner", default="main")
    p.add_argument("--owner-session", default="")
    p.add_argument("--source-chat-id", default="")
    p.add_argument("--source-message-id", default="")
    p.add_argument("--goal", required=True)
    p.add_argument("--summary", default="")
    p.add_argument("--state", default="running")
    p.add_argument("--next-action", required=True)
    p.add_argument("--waiting-on", default="")
    p.add_argument("--stale-after-minutes", type=float, default=10)
    p.add_argument("--lease-minutes", type=float, default=45)
    p.add_argument("--evidence", action="append")
    p.add_argument("--notify-policy", default="low_noise_dry_run_until_verified")
    p.add_argument("--terminal-condition", default="completed/failed/blocked with evidence artifact")
    p.add_argument("--blocker-summary", default="")

    u = sub.add_parser("update")
    u.add_argument("--event-id", required=True)
    u.add_argument("--state")
    u.add_argument("--summary")
    u.add_argument("--next-action")
    u.add_argument("--waiting-on")
    u.add_argument("--stale-after-minutes", type=float)
    u.add_argument("--lease-minutes", type=float)
    u.add_argument("--evidence", action="append")
    u.add_argument("--blocker-summary")

    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")

    w = sub.add_parser("watchdog")
    w.add_argument("--notify-dry-run", action="store_true")
    w.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "register":
        result = register(args)
    elif args.command == "update":
        result = update(args)
    elif args.command == "status":
        result = {"generated_at": now_iso(), "events": [evaluate(e) for e in list_events()]}
    elif args.command == "watchdog":
        result = watchdog(args)
    else:
        return 2
    if getattr(args, "json", False) or args.command in {"register", "update", "watchdog"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result.get("events", []):
            print(f"{item['event_id']} state={item['state']} severity={item['severity']} action={item['action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
