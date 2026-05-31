#!/usr/bin/env python3
"""Read-only status classifier for the Codex/OpenClaw mailbox bridge."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mailbox_paths import MAILBOX_ROOT, pointer_status


DEFAULT_MAILBOX = MAILBOX_ROOT


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # Keep the classifier useful even with partial state.
        return {"_read_error": str(exc)}


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_minutes(value: Any, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed.astimezone(now.tzinfo)).total_seconds() / 60.0)


def process_alive(pid: Any) -> bool | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if os.name == "posix":
        proc_path = Path(f"/proc/{pid_int}")
        if not proc_path.exists():
            return False
        stat_path = proc_path / "stat"
        try:
            fields = stat_path.read_text(encoding="utf-8", errors="replace").split()
            if len(fields) >= 3 and fields[2] == "Z":
                return False
        except OSError:
            pass
        return True
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid_int}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return None
    return str(pid_int) in result.stdout


def classify(
    mailbox: Path,
    stale_minutes: float,
    critical_minutes: float,
    trigger_grace_minutes: float,
    codex_state: Path | None,
) -> dict[str, Any]:
    now = datetime.now().astimezone()
    turn_path = mailbox / "turn.json"
    watcher_path = mailbox / ".openclaw_main_watcher_state.json"
    pointer = pointer_status()
    active_run_path = mailbox / "watch-runs" / "active-run.json"
    turn = read_json(turn_path)
    watcher = read_json(watcher_path) or {}
    active_run = read_json(active_run_path) or {}
    codex = read_json(codex_state) if codex_state else None

    if turn is None:
        return {
            "status": "missing_turn",
            "severity": "critical",
            "mailbox": str(mailbox),
        "turn_path": str(turn_path),
            "turn_path": str(turn_path),
            "checked_at": now.isoformat(timespec="seconds"),
        }
    if "_read_error" in turn:
        return {
            "status": "invalid_turn",
            "severity": "critical",
            "error": turn["_read_error"],
            "mailbox": str(mailbox),
            "checked_at": now.isoformat(timespec="seconds"),
        }

    seq = str(turn.get("seq", ""))
    owner = str(turn.get("needs_reply", "")).strip()
    turn_age = age_minutes(turn.get("updated_at"), now)
    age_value = turn_age if turn_age is not None else 999999.0

    severity = "ok"
    freshness = "fresh"
    if age_value >= critical_minutes:
        severity = "critical"
        freshness = "critical"
    elif age_value >= stale_minutes:
        severity = "warning"
        freshness = "stale"

    if owner in {"main", "codex"}:
        status = f"pending_{owner}_{freshness}"
        blocking_side = owner
    elif owner:
        status = f"pending_unknown_{freshness}"
        blocking_side = owner
        severity = "warning" if severity == "ok" else severity
    else:
        status = "idle"
        blocking_side = None
        severity = "ok"

    last_triggered_seq = str(watcher.get("last_triggered_seq", ""))
    last_triggered_at = watcher.get("last_triggered_at")
    trigger_age = age_minutes(last_triggered_at, now)
    attempts = None
    attempts_by_seq = watcher.get("attempts_by_seq")
    if isinstance(attempts_by_seq, dict):
        attempt_entry = attempts_by_seq.get(seq)
        if isinstance(attempt_entry, dict):
            attempts = attempt_entry.get("attempts")

    watcher_pid = watcher.get("last_triggered_pid")
    watcher_alive = process_alive(watcher_pid)

    if owner == "main" and last_triggered_seq == seq:
        if trigger_age is not None and trigger_age >= trigger_grace_minutes and not watcher_alive:
            status = "trigger_without_turn_advance"
            severity = "critical" if age_value >= critical_minutes else "warning"
        elif watcher_alive:
            status = "main_trigger_running"

    codex_state_status = None
    if isinstance(codex, dict):
        codex_state_status = codex.get("status")
        if str(codex.get("running_seq", "")) == seq and codex_state_status == "running":
            running_age = age_minutes(codex.get("running_started_at"), now)
            if running_age is not None and running_age >= critical_minutes:
                status = "stale_running_state"
                severity = "critical"

    return {
        "status": status,
        "severity": severity,
        "blocking_side": blocking_side,
        "waiting_on": blocking_side or "none",
        "seq": seq,
        "active_epoch": pointer.get("active_epoch"),
        "active_data_root": pointer.get("active_data_root"),
        "namespace_rollover_active": pointer.get("namespace_rollover_active"),
        "needs_reply": owner,
        "last_writer": turn.get("last_writer"),
        "turn_updated_at": turn.get("updated_at"),
        "turn_age_minutes": round(age_value, 1) if turn_age is not None else None,
        "checked_at": now.isoformat(timespec="seconds"),
        "mailbox": str(mailbox),
        "watcher": {
            "last_status": watcher.get("last_status"),
            "last_triggered_seq": last_triggered_seq or None,
            "last_triggered_at": last_triggered_at,
            "last_triggered_age_minutes": round(trigger_age, 1) if trigger_age is not None else None,
            "last_triggered_pid": watcher_pid,
            "last_triggered_pid_alive": watcher_alive,
            "last_triggered_session_id": watcher.get("last_triggered_session_id"),
            "last_run_log": watcher.get("last_run_log"),
            "last_observed_at": watcher.get("last_observed_at"),
            "last_trigger_returncode": watcher.get("last_trigger_returncode"),
            "last_trigger_timed_out": watcher.get("last_trigger_timed_out"),
            "last_trigger_duration_seconds": watcher.get("last_trigger_duration_seconds"),
            "last_trigger_stdout_bytes": watcher.get("last_trigger_stdout_bytes"),
            "last_trigger_stderr_bytes": watcher.get("last_trigger_stderr_bytes"),
            "last_post_trigger_status": watcher.get("last_post_trigger_status"),
            "attempts_for_seq": attempts,
            "active_run": {
                "status": active_run.get("status"),
                "seq": active_run.get("seq"),
                "pid": active_run.get("pid"),
                "pid_alive": process_alive(active_run.get("pid")),
                "started_at": active_run.get("started_at"),
                "finished_at": active_run.get("finished_at"),
                "duration_seconds": active_run.get("duration_seconds"),
                "returncode": active_run.get("returncode"),
                "timed_out": active_run.get("timed_out"),
                "run_log": active_run.get("run_log"),
            },
        },
        "codex_state": {
            "path": str(codex_state) if codex_state else None,
            "status": codex_state_status,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailbox", type=Path, default=DEFAULT_MAILBOX)
    parser.add_argument("--stale-minutes", type=float, default=10.0)
    parser.add_argument("--critical-minutes", type=float, default=20.0)
    parser.add_argument("--trigger-grace-minutes", type=float, default=3.0)
    parser.add_argument("--codex-state", type=Path)
    parser.add_argument("--write", type=Path, help="Optional JSON output path.")
    args = parser.parse_args()

    result = classify(
        mailbox=args.mailbox,
        stale_minutes=args.stale_minutes,
        critical_minutes=args.critical_minutes,
        trigger_grace_minutes=args.trigger_grace_minutes,
        codex_state=args.codex_state,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        write_text_atomic(args.write, output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
