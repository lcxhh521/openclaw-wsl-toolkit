#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TOOLS = ROOM / "tools"
DAEMON_ROOT = ROOM / "daemon-runs" / "telegram-agent-bridge"
HEARTBEAT = ROOM / "agent_room_bridge_daemon.status.json"
LIVE_APPROVAL = ROOM / "live_approval_epoch.json"
LIVE_HOLD = ROOM / "live_hold_epoch.json"
STOP = False
STANDING_AGENDA_TICK = TOOLS / "standing_agenda_tick.py"
COLLABORATION_STATUS = TOOLS / "collaboration_status.py"
CONTINUATION_WATCHDOG = ROOT.parent / "runtime-continuations" / "continuation_watchdog.py"
CONTINUATION_REGISTRY = ROOT.parent / "runtime-continuations" / "continuation_registry.py"
TASKS_DIR = ROOM / "tasks"

# Terminal states that mean a continuation event should be auto-closed.
_CONTINUATION_DECISION_AUTO_COMPLETE = "agent_room_task_harvested_auto_complete"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_read_error": type(exc).__name__ + ": " + str(exc)}


def has_valid_approval_provenance(approval: dict[str, Any]) -> bool:
    provenance = approval.get("approval_provenance")
    if not isinstance(provenance, list) or not provenance:
        return False
    has_exact = any(isinstance(item, dict) and str(item.get("exact_text") or "").strip() for item in provenance)
    scope = approval.get("approved_scope")
    effects = approval.get("approved_side_effects")
    return bool(has_exact and isinstance(scope, list) and scope and isinstance(effects, list) and effects)


def live_is_approved() -> tuple[bool, str, Any]:
    approval = read_json_or_none(LIVE_APPROVAL)
    if not isinstance(approval, dict):
        return False, "missing_live_approval_epoch", approval
    if approval.get("status") not in {"active", "approved"}:
        return False, "live_approval_epoch_not_active", approval
    if not approval.get("live_runtime_enabled", False):
        return False, "live_runtime_not_enabled_in_epoch", approval
    if not has_valid_approval_provenance(approval):
        return False, "missing_or_invalid_approval_provenance", approval
    hold = read_json_or_none(LIVE_HOLD)
    if isinstance(hold, dict) and hold.get("status", "active") in {"active", "hold"}:
        hold_at = str(hold.get("created_at") or hold.get("updated_at") or "")
        approved_at = str(approval.get("approved_at") or approval.get("created_at") or "")
        if not approved_at or hold_at >= approved_at:
            return False, "newer_or_active_live_hold_epoch", {"approval": approval, "hold": hold}
    return True, "approved", approval


def run_tick(
    run_dir: Path,
    mode: str,
    allow_send: bool,
    commit_offset: bool,
    timeout: int,
    limit_per_bot: int,
    room_id: str,
) -> dict[str, Any]:
    cmd = [
        "python3", str(TOOLS / "agent_room_resident_bridge.py"),
        "--mode", mode,
        "--timeout", str(timeout),
        "--limit-per-bot", str(limit_per_bot),
        "--room-id", room_id,
    ]
    if allow_send:
        cmd.append("--allow-send")
    if commit_offset:
        cmd.append("--commit-offset")
    env = None
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(600, timeout + 120), env=env)
    result = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    write_json(run_dir / "tick-subprocess.json", result)
    return result


def run_maintenance_harvest(run_dir: Path, room_id: str) -> dict[str, Any]:
    """Run a local harvest-only pass before secondary status/agenda readers.

    The resident tick harvests at its start, then may spend time polling,
    importing, and dispatching. Runners that exit in that gap should not leave
    active-runner files for standing agenda or status-card projection to treat
    as still running until the next full tick.
    """
    cmd = [
        "python3", str(TOOLS / "agent_room_resident_bridge.py"),
        "--mode", "harvest-only",
        "--timeout", "0",
        "--limit-per-bot", "0",
        "--room-id", room_id,
    ]
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    result: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        if isinstance(payload, dict):
            result["result"] = payload
            harvested = payload.get("harvested_runners")
            if isinstance(harvested, list):
                result["observed_runner_count"] = len(harvested)
                result["still_running_count"] = sum(
                    1 for item in harvested if isinstance(item, dict) and item.get("status") == "still_running"
                )
                result["harvested_runner_count"] = int(
                    payload.get("harvested_runner_count")
                    if payload.get("harvested_runner_count") is not None
                    else sum(1 for item in harvested if isinstance(item, dict) and item.get("status") != "still_running")
                )
    except Exception:
        pass
    write_json(run_dir / "maintenance-harvest-tick.json", result)
    return result


def effective_poll_timeout(requested_timeout: int) -> int:
    """Cap Telegram long-poll latency for interactive Agent Room turns.

    The installed systemd unit may pass an older explicit value. Keep a local
    cap so hotpath latency improves without requiring a unit-file edit.
    """
    try:
        requested = max(0, int(requested_timeout))
    except (TypeError, ValueError):
        requested = 0
    try:
        cap = max(0, int(os.environ.get("AGENT_ROOM_POLL_TIMEOUT_MAX", "5")))
    except ValueError:
        cap = 5
    if cap <= 0:
        return requested
    return min(requested, cap)


def on_signal(signum: int, frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Long-running Telegram Agent Room bridge daemon.")
    parser.add_argument("--mode", choices=["inspect-only", "consume-only", "live"], default=None)
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--commit-offset", action="store_true")
    parser.add_argument("--poll-timeout", type=int, default=5)
    parser.add_argument("--idle-sleep", type=int, default=2)
    parser.add_argument("--limit-per-bot", type=int, default=20)
    parser.add_argument("--max-ticks", type=int, default=0, help="0 means unlimited")
    parser.add_argument("--room-id", default="openclaw-evolution")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    if args.mode is None:
        mode = "live" if args.allow_send else ("consume-only" if args.commit_offset else "inspect-only")
    else:
        mode = args.mode
    allow_send = bool(args.allow_send and mode == "live")
    commit_offset = bool(args.commit_offset and mode in {"consume-only", "live"})

    if mode == "live":
        approved, approval_reason, approval_payload = live_is_approved()
        if not approved:
            write_json(HEARTBEAT, {
                "schema": "openclaw.agent_room.bridge_daemon_status.v0",
                "status": "refused_live_not_approved",
                "pid": os.getpid(),
                "updated_at": now_iso(),
                "mode": mode,
                "reason": approval_reason,
                "telegram_outbound_enabled": False,
                "offset_commit_enabled": False,
                "tokens_printed": False,
            })
            print(json.dumps({"ok": False, "status": "refused_live_not_approved", "reason": approval_reason}, ensure_ascii=False), file=sys.stderr)
            return 3
    else:
        approval_payload = None

    daemon_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = DAEMON_ROOT / daemon_id
    base.mkdir(parents=True, exist_ok=True)
    tick = 0
    write_json(HEARTBEAT, {
        "schema": "openclaw.agent_room.bridge_daemon_status.v0",
        "status": "starting",
        "daemon_id": daemon_id,
        "pid": os.getpid(),
        "updated_at": now_iso(),
        "mode": mode,
        "telegram_outbound_enabled": bool(allow_send),
        "offset_commit_enabled": bool(commit_offset),
        "approval_epoch_id": approval_payload.get("epoch_id") if isinstance(approval_payload, dict) else None,
    })
    while not STOP:
        tick += 1
        run_dir = base / f"tick-{tick:06d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        started = now_iso()
        try:
            tick_result = run_tick(
                run_dir,
                mode,
                allow_send,
                commit_offset,
                effective_poll_timeout(args.poll_timeout),
                args.limit_per_bot,
                args.room_id,
            )
            ok = tick_result.get("ok", False)
        except Exception as exc:
            tick_result = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
            ok = False
            write_json(run_dir / "tick-exception.json", tick_result)
        maintenance_harvest_result: dict[str, Any] = {"ok": True, "status": "not_run_for_mode"}
        if mode in {"consume-only", "live"}:
            try:
                maintenance_harvest_result = run_maintenance_harvest(run_dir, args.room_id)
            except Exception as exc:
                maintenance_harvest_result = {
                    "ok": False,
                    "status": "maintenance_harvest_failed",
                    "error": type(exc).__name__ + ": " + str(exc),
                }
                write_json(run_dir / "maintenance-harvest-exception.json", maintenance_harvest_result)
        # Standing agenda creation only runs after a successful consume/live
        # resident tick. Reconciliation is safe local maintenance and still runs
        # on tick errors so harvested/dead runners do not leave stale pending
        # standing tasks behind when Telegram polling is temporarily unavailable.
        # When harvest detected completed runners and the system is now idle,
        # bypass cooldown so the next standing task is injected immediately
        # instead of waiting up to 30 minutes.
        harvested_runners = maintenance_harvest_result.get("harvested_runner_count", 0) if isinstance(maintenance_harvest_result, dict) else 0
        remaining_active = maintenance_harvest_result.get("result", {}).get("active_runner_count_after_harvest") if isinstance(maintenance_harvest_result.get("result"), dict) else None
        agenda_bypass_cooldown = harvested_runners > 0 and (remaining_active is None or remaining_active == 0)
        agenda_tick_result: dict[str, Any] = {"ok": True, "status": "not_run_for_mode", "created": False}
        if mode in {"consume-only", "live"}:
            try:
                agenda_cmd = ["python3", str(STANDING_AGENDA_TICK), "--room-id", args.room_id]
                if not ok:
                    agenda_cmd.append("--reconcile-only")
                if agenda_bypass_cooldown and ok:
                    agenda_cmd.append("--bypass-cooldown")
                proc = subprocess.run(
                    agenda_cmd,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                )
                agenda_tick_result = {
                    "ok": proc.returncode == 0,
                    "exit_code": proc.returncode,
                    "stderr_tail": proc.stderr[-2000:],
                }
                if proc.stdout.strip():
                    agenda_tick_result["result"] = json.loads(proc.stdout)
            except Exception as exc:
                agenda_tick_result = {"ok": False, "status": "standing_agenda_tick_failed", "error": type(exc).__name__ + ": " + str(exc)}
        write_json(run_dir / "standing-agenda-tick.json", agenda_tick_result)
        # Continuation lifecycle: run watchdog and auto-complete resolved events.
        continuation_tick_result: dict[str, Any] = {"ok": True, "status": "not_run_no_scripts", "auto_completed": 0, "events": []}
        if CONTINUATION_WATCHDOG.exists() and CONTINUATION_REGISTRY.exists():
            try:
                wd_proc = subprocess.run(
                    ["python3", str(CONTINUATION_WATCHDOG), "--json"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                )
                wd_ok = wd_proc.returncode == 0
                if wd_ok and wd_proc.stdout.strip():
                    wd_report = json.loads(wd_proc.stdout)
                    wd_report_path = run_dir / "continuation-watchdog-report.json"
                    write_json(wd_report_path, wd_report)
                    auto_completed = 0
                    for ev in wd_report.get("events", []):
                        if ev.get("decision") == _CONTINUATION_DECISION_AUTO_COMPLETE:
                            event_id = ev.get("event_id", "")
                            if event_id:
                                complete_proc = subprocess.run(
                                    ["python3", str(CONTINUATION_REGISTRY), "complete",
                                     "--event-id", event_id,
                                     "--summary", "auto-completed by continuation lifecycle daemon tick: agent-room-task harvested",
                                     "--evidence", str(wd_report_path)],
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                                )
                                if complete_proc.returncode == 0:
                                    auto_completed += 1
                    continuation_tick_result = {
                        "ok": wd_ok,
                        "status": "completed",
                        "auto_completed": auto_completed,
                        "events": len(wd_report.get("events", [])),
                    }
                else:
                    continuation_tick_result = {"ok": wd_ok, "status": "watchdog_no_output", "events": 0}
            except Exception as exc:
                continuation_tick_result = {"ok": False, "status": "continuation_tick_failed", "error": type(exc).__name__ + ": " + str(exc)}
        write_json(run_dir / "continuation-tick.json", continuation_tick_result)
        collaboration_status_result: dict[str, Any] = {"ok": True, "status": "not_run_no_script"}
        if COLLABORATION_STATUS.exists():
            try:
                status_proc = subprocess.run(
                    ["python3", str(COLLABORATION_STATUS), "--include-background"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                )
                collaboration_status_result = {
                    "ok": status_proc.returncode == 0,
                    "exit_code": status_proc.returncode,
                    "stdout_tail": status_proc.stdout[-2000:],
                    "stderr_tail": status_proc.stderr[-2000:],
                }
                try:
                    collaboration_status_result["result"] = json.loads(status_proc.stdout) if status_proc.stdout.strip() else {}
                except Exception:
                    pass
            except Exception as exc:
                collaboration_status_result = {"ok": False, "status": "collaboration_status_failed", "error": type(exc).__name__ + ": " + str(exc)}
        write_json(run_dir / "collaboration-status-tick.json", collaboration_status_result)
        # Pinned status card live refresh: edit the pinned card in Telegram group.
        # Only runs in live mode with outbound enabled and a configured chat binding.
        PINNED_STATUS_CARD = TOOLS / "pinned_status_card.py"
        pinned_card_result: dict[str, Any] = {"ok": True, "status": "not_run"}
        if allow_send and PINNED_STATUS_CARD.exists():
            try:
                # Resolve chat_id from room bindings
                bindings_path = ROOM / "telegram-room-bindings.json"
                chat_id_for_card = ""
                if bindings_path.exists():
                    bindings_data = json.loads(bindings_path.read_text(encoding="utf-8"))
                    if isinstance(bindings_data, list):
                        for b in bindings_data:
                            if isinstance(b, dict) and b.get("room_id") == args.room_id:
                                chat_id_for_card = str(b.get("telegram_chat_id") or b.get("chat_id") or "")
                                break
                    elif isinstance(bindings_data, dict):
                        chat_id_for_card = str(bindings_data.get("telegram_chat_id") or bindings_data.get("chat_id") or "")
                if chat_id_for_card:
                    pc_cmd = [
                        "python3", str(PINNED_STATUS_CARD),
                        "--room-id", args.room_id,
                        "--chat-id", chat_id_for_card,
                        "--agent-id", "openclaw-main",
                        "--live",
                    ]
                    pc_proc = subprocess.run(
                        pc_cmd,
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                    )
                    pinned_card_result = {
                        "ok": pc_proc.returncode == 0,
                        "exit_code": pc_proc.returncode,
                        "stdout_tail": pc_proc.stdout[-2000:],
                        "stderr_tail": pc_proc.stderr[-2000:],
                    }
                    try:
                        pinned_card_result["result"] = json.loads(pc_proc.stdout) if pc_proc.stdout.strip() else {}
                    except Exception:
                        pass
                else:
                    pinned_card_result = {"ok": True, "status": "no_chat_id_in_bindings"}
            except Exception as exc:
                pinned_card_result = {"ok": False, "status": "pinned_card_failed", "error": type(exc).__name__ + ": " + str(exc)}
        write_json(run_dir / "pinned-card-tick.json", pinned_card_result)
        write_json(HEARTBEAT, {
            "schema": "openclaw.agent_room.bridge_daemon_status.v0",
            "status": "running" if ok else "tick_error",
            "daemon_id": daemon_id,
            "pid": os.getpid(),
            "mode": mode,
            "room_id": args.room_id,
            "tick": tick,
            "poll_timeout_requested": args.poll_timeout,
            "poll_timeout_effective": effective_poll_timeout(args.poll_timeout),
            "last_tick_started_at": started,
            "last_tick_finished_at": now_iso(),
            "last_tick_dir": str(run_dir),
            "last_tick_ok": ok,
            "telegram_outbound_enabled": bool(allow_send),
            "offset_commit_enabled": bool(commit_offset),
            "approval_epoch_id": approval_payload.get("epoch_id") if isinstance(approval_payload, dict) else None,
            "tokens_printed": False,
            "standing_agenda_tick": agenda_tick_result,
            "maintenance_harvest_tick": maintenance_harvest_result,
            "continuation_tick": continuation_tick_result,
            "collaboration_status_tick": collaboration_status_result,
            "pinned_card_tick": pinned_card_result,
        })
        if args.max_ticks and tick >= args.max_ticks:
            break
        time.sleep(max(0, args.idle_sleep))

    write_json(HEARTBEAT, {
        "schema": "openclaw.agent_room.bridge_daemon_status.v0",
        "status": "stopped",
        "daemon_id": daemon_id,
        "tick": tick,
        "updated_at": now_iso(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
