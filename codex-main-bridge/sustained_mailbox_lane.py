#!/usr/bin/env python3
"""Inspect the main↔Codex sustained mailbox lane.

P0 is intentionally read-only by default: it classifies the active mailbox lane,
soft gates, and recommended owner/action without writing mailbox turns or sending
Telegram messages.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT as ROOT, pointer_status

TURN = ROOT / "turn.json"
MAIN_STATE = ROOT / ".openclaw_main_watcher_state.json"
CODEX_STATE = ROOT / ".codex_mailbox_replier_state.json"
ACTIVE_RUN = ROOT / "watch-runs" / "active-run.json"
ALERT = ROOT / "sustained-lane-alert.json"
REPORT = ROOT / "sustained-lane-state.json"
ARCHIVE = ROOT / "archive" / "mailbox-turns.jsonl"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"

SCHEMA = "openclaw.codex_main.sustained_mailbox_lane.v0"
DEFAULT_STALE_SECONDS = int(os.environ.get("OPENCLAW_SUSTAINED_LANE_STALE_SECONDS", "600"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        return {"_read_error": f"{type(exc).__name__}: {exc}", "path": str(path)}
    return value


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return ""


SILENT_WAIT_MARKERS = (
    "status: waiting_approval_silent",
    "status: acknowledged_silent_wait",
    "status: waiting_silent_ack",
    "keep-waiting/noop",
    "keep-waiting / noop",
    "protocol keep-waiting",
    "继续静默等待",
    "不要回复下一轮 keep-waiting",
)


def is_silent_wait_noop(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in SILENT_WAIT_MARKERS)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_seconds(value: Any) -> int | None:
    dt = parse_time(value)
    if dt is None:
        return None
    return max(0, int((datetime.now(tz=dt.tzinfo) - dt).total_seconds()))


def pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if sys.platform.startswith("linux"):
        proc = Path(f"/proc/{pid_int}")
        if not proc.exists():
            return False
        try:
            stat = (proc / "stat").read_text(errors="replace")
            # zombie processes have state Z in /proc/<pid>/stat
            parts = stat.split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        except OSError:
            pass
        return True
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def compact_gate(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    return {
        "epoch": record.get("epoch"),
        "mailbox_root": record.get("mailbox_root"),
        "seq": record.get("seq"),
        "gate_kind": record.get("gate_kind"),
        "class": record.get("class"),
        "count": record.get("count"),
        "first_at": record.get("first_at"),
        "last_at": record.get("last_at"),
        "age_seconds": record.get("age_seconds"),
        "threshold_reached": record.get("threshold_reached"),
        "recommended_action": record.get("recommended_action"),
        "last_detail": record.get("last_detail"),
    }


def latest_gate(*states: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        gate = compact_gate(state.get("sustained_lane_last_gate"))
        if gate:
            candidates.append(gate)
        gates = state.get("sustained_lane_gates")
        if isinstance(gates, dict):
            for value in gates.values():
                gate = compact_gate(value)
                if gate:
                    candidates.append(gate)
    if not candidates:
        return None
    return sorted(candidates, key=lambda g: str(g.get("last_at") or ""))[-1]


def infer_state(turn: dict[str, Any], main_state: dict[str, Any], codex_state: dict[str, Any], active: dict[str, Any]) -> tuple[str, str, str]:
    needs = str(turn.get("needs_reply") or "")
    seq = str(turn.get("seq") or "")
    updated_age = age_seconds(turn.get("updated_at"))
    last_gate = latest_gate(main_state, codex_state)
    gate_matches = bool(last_gate and (not seq or str(last_gate.get("seq")) in {seq, "unknown"}))
    gate_threshold = bool(last_gate and last_gate.get("threshold_reached") and gate_matches)

    if needs == "none":
        return "COMPLETED_OR_EXPLICIT_STOP", "none", "mailbox_idle"

    if needs == "main" and is_silent_wait_noop(read_text(CODEX_FILE)):
        return "SILENT_WAIT_NOOP", "none", "no_main_reply_needed"
    if needs == "codex" and is_silent_wait_noop(read_text(MAIN_FILE)):
        return "SILENT_WAIT_NOOP", "none", "close_noop_turn_without_model"

    active_status = str(active.get("status") or "") if isinstance(active, dict) else ""
    active_seq = str(active.get("seq") or "") if isinstance(active, dict) else ""
    if active_status == "running" and active_seq == seq:
        if pid_alive(active.get("pid")):
            return "RUNNING_MAIN", "openclaw-main-mailbox-watch", "wait_for_main_run"
        return "SOFT_STALLED", "openclaw-main-mailbox-watch", "dead_main_run_needs_harvest_or_retry"

    if gate_threshold:
        owner = "codex-mailbox-replier" if needs == "codex" else "openclaw-main-mailbox-watch" if needs == "main" else "unknown"
        return "SOFT_STALLED", owner, "review_soft_gate_alert"

    if updated_age is not None and updated_age >= DEFAULT_STALE_SECONDS and needs in {"codex", "main"}:
        owner = "codex-mailbox-replier" if needs == "codex" else "openclaw-main-mailbox-watch"
        return "SOFT_STALLED", owner, "trigger_or_review_stale_pending_turn"

    if needs == "codex":
        return "WAITING_CODEX", "codex-mailbox-replier", "trigger_codex_replier_or_wait_min_age"
    if needs == "main":
        return "WAITING_MAIN", "openclaw-main-mailbox-watch", "trigger_main_watcher_or_wait_gate"
    if not needs:
        return "IDLE", "none", "no_needs_reply"
    return "UNKNOWN", "unknown", "inspect_turn_json"


def inspect() -> dict[str, Any]:
    turn = read_json(TURN, {}) or {}
    main_state = read_json(MAIN_STATE, {}) or {}
    codex_state = read_json(CODEX_STATE, {}) or {}
    active = read_json(ACTIVE_RUN, {}) or {}
    alert = read_json(ALERT, {}) or {}
    state, owner, action = infer_state(turn, main_state, codex_state, active)
    last_gate = latest_gate(main_state, codex_state)
    archive_exists = ARCHIVE.exists()
    blocks_transport = state in {"HARD_BLOCKED", "CONVERGENCE_ANOMALY"}
    status = state.lower()
    return {
        "schema": SCHEMA,
        "generated_at": now_iso(),
        "mailbox_root": str(ROOT),
        "code_root": str(CODE_ROOT),
        "pointer": pointer_status(),
        "turn": {
            "seq": turn.get("seq"),
            "last_writer": turn.get("last_writer"),
            "needs_reply": turn.get("needs_reply"),
            "updated_at": turn.get("updated_at"),
            "age_seconds": age_seconds(turn.get("updated_at")),
            "context_epoch": turn.get("context_epoch"),
            "context_turns_since_rollover": turn.get("context_turns_since_rollover"),
        },
        "state": state,
        "status": status,
        "blocks_transport": blocks_transport,
        "owner": owner,
        "recommended_action": action,
        "derived_cache": True,
        "telegram_send_performed": False,
        "last_gate": last_gate,
        "last_alert": alert if isinstance(alert, dict) and alert else None,
        "main_watcher": {
            "last_status": main_state.get("last_status"),
            "last_deferred_seq": main_state.get("last_deferred_seq"),
            "last_deferred_at": main_state.get("last_deferred_at"),
            "last_deferred_reason": main_state.get("last_deferred_reason"),
            "last_post_trigger_status": main_state.get("last_post_trigger_status"),
            "last_triggered_seq": main_state.get("last_triggered_seq"),
            "last_run_log": main_state.get("last_run_log"),
        },
        "codex_replier": {
            "last_status": codex_state.get("last_status"),
            "last_replied_seq": codex_state.get("last_replied_seq"),
            "last_run_dir": codex_state.get("last_run_dir"),
            "updated_at": codex_state.get("updated_at"),
        },
        "active_run": {
            "status": active.get("status"),
            "seq": active.get("seq"),
            "pid": active.get("pid"),
            "pid_alive": pid_alive(active.get("pid")) if active.get("pid") else None,
            "run_log": active.get("run_log"),
        },
        "archive": {
            "exists": archive_exists,
            "path": str(ARCHIVE),
        },
        "p0_boundaries": {
            "inspect_only_default": True,
            "writes_mailbox_turns": False,
            "sends_telegram": False,
            "diagnostic_turn_requires_second_package": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect main↔Codex sustained mailbox lane state.")
    parser.add_argument("--inspect", action="store_true", help="Inspect lane state (default action).")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write-report", action="store_true", help="Write sustained-lane-state.json cache/report.")
    args = parser.parse_args()
    result = inspect()
    if args.write_report:
        write_json(REPORT, result)
    if args.json or args.inspect:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['state']}: seq={result['turn'].get('seq')} owner={result['owner']} action={result['recommended_action']}")
    if result["state"] == "HARD_BLOCKED":
        return 3
    if result["state"] == "SOFT_STALLED":
        return 2
    if result["state"] == "UNKNOWN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
