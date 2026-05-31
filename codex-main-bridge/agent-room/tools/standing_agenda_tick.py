#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TOOL_DIR = Path(__file__).resolve().parent
CONFIG = ROOM / "config" / "standing-agenda.json"
STATE = ROOM / "standing-agenda-state.json"
TASKS_JSONL = ROOM / "tasks.jsonl"
ACTIVE_RUNNERS = ROOM / "active-runners"
AUTONOMY_EVOLUTION_LEDGER = ROOM / "artifacts" / "mainline-autonomy-evolution-ledger.jsonl"
LOCAL_RUNTIME_AGENTS = {"codex", "claude-code"}
ACTIVE_COLLAB_CLAIM_STATUSES = {"active", "claimed", "running", "handoff"}
GOVERNANCE_CONTRACT_PATH = "agent-room/methodology/mainline-governance-contract-20260528.md"
MAINLINE_ACTIVE_STATUSES = {"open", "in_progress", "in_review", "blocked"}
MAINLINE_TERMINAL_STATUSES = {"done", "accepted", "closed", "superseded", "cancelled", "wont_do"}
MAINLINE_ALLOWED_STATUSES = MAINLINE_ACTIVE_STATUSES | MAINLINE_TERMINAL_STATUSES
RETRYABLE_STANDING_STATUSES = {"blocked", "failed", "partial", "partial_failed", "stale"}
TASK_TERMINAL_STATUSES = {"completed", "blocked", "failed", "partial", "partial_failed", "cancelled", "stale", "merged"}
GOVERNANCE_STATES = {"intake", "triage", "plan", "execute", "review", "integrate", "close", "needs_alex", "blocked", "stale", "failed", "retry", "merged"}
GOVERNANCE_TERMINAL_STATES = {"close", "needs_alex", "blocked", "stale", "failed", "merged"}
INTERNAL_TRANSPORTS = {
    "agent-room-collab-followup",
    "agent-room-runtime-takeover",
    "agent-room-proactive-mainline",
    "agent-room-standing-mainline",
}


def mainline_attention_rank(mainline_status: str) -> tuple[int, str]:
    status = str(mainline_status or "").strip()
    if status == "blocked":
        return 1, "mainline_blocked"
    return 0, ""


def now_dt() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        return {"_read_error": type(exc).__name__ + ": " + str(exc)}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def compact_slug(value: Any, default: str = "item") -> str:
    out: list[str] = []
    for ch in str(value).lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")[:80] or default


def evidence_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return raw
    return raw


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def first_text(*values: Any) -> str:
    for value in values:
        text = compact_text(value)
        if text:
            return text
    return ""


def autonomy_self_evolution_policy(config: dict[str, Any]) -> dict[str, Any]:
    policy = config.get("autonomy_improvement_policy")
    if not isinstance(policy, dict):
        return {}
    raw = policy.get("self_evolution")
    return raw if isinstance(raw, dict) else {}


def autonomy_self_evolution_enabled(policy: dict[str, Any]) -> bool:
    if not isinstance(policy, dict):
        return False
    raw = policy.get("enabled")
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def autonomy_self_evolution_snapshot_path(policy: dict[str, Any]) -> Path:
    raw = str(policy.get("snapshot_path") or "agent-room/artifacts/mainline-autonomy-evolution-ledger.jsonl").strip()
    if not raw:
        return AUTONOMY_EVOLUTION_LEDGER
    path = Path(raw)
    if path.is_absolute():
        return path
    return ROOT / raw


def record_autonomy_evolution_snapshot(
    room_id: str,
    config: dict[str, Any],
    state: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any] | None:
    policy = autonomy_self_evolution_policy(config)
    if not autonomy_self_evolution_enabled(policy):
        return None
    selected_item_id = str((selection.get("selected_item_id") or "")).strip()
    selected_record = next(
        (
            record
            for record in (selection.get("considered_items") or [])
            if isinstance(record, dict) and str(record.get("item_id") or "") == selected_item_id
        ),
        {},
    )
    threshold_raw = policy.get("repeat_failure_threshold")
    try:
        repeat_threshold = int(threshold_raw) if threshold_raw is not None else 2
    except Exception:
        repeat_threshold = 2
    window_hours_raw = policy.get("repeat_failure_window_hours")
    try:
        repeat_window_hours = int(window_hours_raw) if window_hours_raw is not None else 24
    except Exception:
        repeat_window_hours = 24
    repeat_window_hours = max(1, repeat_window_hours)
    recent_stats = standing_item_task_stats(
        selected_item_id,
        since=now_dt() - timedelta(hours=repeat_window_hours),
    ) if selected_item_id else {}
    recent_retryable = int(recent_stats.get("retryable") or 0) if isinstance(recent_stats, dict) else 0
    recent_material = int(recent_stats.get("material_completed") or 0) if isinstance(recent_stats, dict) else 0
    recent_total = int(recent_stats.get("total") or 0) if isinstance(recent_stats, dict) else 0
    recent_repeat_signal = selected_item_id and recent_retryable >= repeat_threshold
    failure_state = selected_record.get("failure_recovery_state") if isinstance(selected_record, dict) else {}
    if not isinstance(failure_state, dict):
        failure_state = {}
    selected_retryable = int(failure_state.get("retryable") or 0) if failure_state else 0
    selected_unresolved = int(failure_state.get("unresolved") or 0) if failure_state else 0
    snapshot = {
        "schema": "openclaw.agent_room.mainline_autonomy_evolution_snapshot.v0",
        "room_id": room_id,
        "captured_at": now_iso(),
        "selected_item_id": selected_item_id,
        "selected_reason": str(selected_record.get("reason") or selection.get("selected_reason") or ""),
        "selected_selection_class": str(selected_record.get("selection_class") or selection.get("selected_selection_class") or ""),
        "selected_due": bool(selected_record.get("due")),
        "max_silence_seconds": selected_record.get("max_silence_seconds"),
        "recent_window_hours": repeat_window_hours,
        "recent_retryable": recent_retryable,
        "recent_total": recent_total,
        "recent_material_completed": recent_material,
        "selected_retryable_history": selected_retryable,
        "selected_unresolved": selected_unresolved,
        "degraded_rounds": int(failure_state.get("degraded_rounds") or 0),
        "evolution_signal": "repeat_failure_pressure" if recent_repeat_signal else "monitor_only",
        "signals": {
            "recent_repeat_pressure": recent_repeat_signal,
            "repeat_failure_threshold": repeat_threshold,
            "global_cooldown_active": bool(selected_record.get("global_cooldown_active")),
            "due_item_count": len(selection.get("due_item_ids") or []),
            "selected_mainline_attention": int(selected_record.get("mainline_attention") or 0),
        },
    }
    if recent_repeat_signal:
        snapshot["next_step"] = "evolution_iteration_needed"
    else:
        snapshot["next_step"] = "observe_only"
    snapshot_path = autonomy_self_evolution_snapshot_path(policy)
    try:
        append_jsonl(snapshot_path, [snapshot])
        return snapshot
    except Exception:
        return None


def approval_gate_value(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        required = bool(raw.get("required"))
        reason = first_text(raw.get("reason"), raw.get("why"))
        return {
            "required": required,
            "reason": reason or ("explicit item gate" if required else "safe local Agent Room task; external/destructive/secrets gates remain closed"),
        }
    text = compact_text(raw).lower()
    required = text not in {"", "none", "no", "false", "not_required", "local_only"}
    return {
        "required": required,
        "reason": compact_text(raw) if required else "safe local Agent Room task; external/destructive/secrets gates remain closed",
    }


def build_task_governance(
    room_id: str,
    item: dict[str, Any],
    active_item: dict[str, Any],
    targets: list[str],
    dedupe_key: str,
) -> dict[str, Any]:
    mainline_id = first_text(
        item.get("mainline_id"),
        active_item.get("mainline_id"),
        item.get("mainline_item_id"),
        active_item.get("id"),
        "agent_room_infrastructure",
    )
    participants = list(dict.fromkeys(["openclaw-main", *targets]))
    problem_statement = first_text(
        item.get("problem_statement"),
        item.get("description"),
        active_item.get("work_item"),
        f"Advance {mainline_id} with bounded Agent Room work.",
    )
    raw_done = item.get("definition_of_done") or item.get("acceptance_evidence") or active_item.get("acceptance_evidence")
    if isinstance(raw_done, list):
        definition_of_done = [str(entry).strip() for entry in raw_done if str(entry).strip()]
    else:
        definition_of_done = [first_text(raw_done)]
    if not definition_of_done:
        definition_of_done = ["Produce a patch, artifact, smoke result, RCA, accepted blocker, or verified state transition."]
    return {
        "schema": "openclaw.agent_room.mainline_governance.v0",
        "mainline_id": mainline_id,
        "problem_statement": problem_statement,
        "expected_user_value": first_text(
            item.get("expected_user_value"),
            item.get("user_value"),
            active_item.get("user_value"),
            f"{mainline_id} moves toward visible reliability or recovery improvement without extra Alex coordination.",
        ),
        "owner": first_text(item.get("owner"), active_item.get("owner"), "openclaw-main"),
        "participants": participants,
        "definition_of_done": definition_of_done,
        "approval_gate": approval_gate_value(item.get("approval_gate")),
        "dedupe_key": dedupe_key,
        "next_action": first_text(
            item.get("next_action"),
            active_item.get("work_item"),
            item.get("description"),
            f"Produce the next bounded evidence item for {mainline_id}.",
        ),
        "state": "execute",
        "drift_check_passed": True,
    }


def env_disabled() -> bool:
    raw = os.environ.get("AGENT_ROOM_STANDING_AGENDA_ENABLED")
    if raw is None:
        raw = os.environ.get("AGENT_ROOM_STANDING_MAINLINE_DISCUSSION")
    if raw is None:
        return False
    return raw.strip().lower() not in {"1", "true", "yes", "on"}


def room_chat_id(room_id: str) -> str | None:
    room = read_json(ROOM / "rooms" / room_id / "room.json", {})
    if isinstance(room, dict) and room.get("telegram_chat_id"):
        return str(room.get("telegram_chat_id"))
    bindings = read_json(ROOT / "telegram-room-bindings.json", {})
    for binding in bindings.get("bindings") or []:
        if isinstance(binding, dict) and str(binding.get("room_id") or "") == room_id:
            chat_id = binding.get("telegram_chat_id")
            return str(chat_id) if chat_id else None
    return None


def reply_artifact_exists(agent_id: str, run_id: str) -> bool:
    reply_path = ROOM / "telegram-agent-reply" / f"{agent_id}-{run_id}.json"
    if reply_path.exists():
        data = read_json(reply_path, {})
        if isinstance(data, dict) and (data.get("sent") or data.get("suppressed_reason")):
            return True
    finished_path = ROOM / "finished-runners" / f"{agent_id}-{run_id}.json"
    finished = read_json(finished_path, {})
    if not isinstance(finished, dict) or finished.get("status") != "finished":
        return False
    reply_result = finished.get("reply_result") if isinstance(finished.get("reply_result"), dict) else {}
    return bool(
        reply_result.get("sent")
        or reply_result.get("suppressed_reason")
        or finished.get("telegram_projection_suppressed_reason")
        or finished.get("reply_delivery_state") in {"delivered", "suppressed", "failed_visible_layer"}
    )


def source_transport(task: dict[str, Any]) -> str:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return str(source.get("transport") or task.get("transport") or "")


def task_handled(task_id: str, run_id: str, targets: list[str]) -> bool:
    if not task_id or not run_id or not targets:
        return False
    return all(reply_artifact_exists(agent_id, run_id) for agent_id in targets)


def task_age_seconds(task: dict[str, Any]) -> float | None:
    created = parse_dt(task.get("created_at") or task.get("updated_at"))
    if not created:
        return None
    return (now_dt() - created).total_seconds()


def fresh_user_task_count(max_age_seconds: int) -> int:
    count = 0
    pending_dir = ROOM / "pending-tasks"
    if pending_dir.exists():
        for path in pending_dir.glob("*.json"):
            task = read_json(path, {})
            if isinstance(task, dict) and source_transport(task) not in INTERNAL_TRANSPORTS:
                age = task_age_seconds(task)
                if age is not None and age > max_age_seconds:
                    continue
                count += 1
    for task in reversed(read_jsonl(TASKS_JSONL)[-50:]):
        if source_transport(task) != "telegram":
            continue
        age = task_age_seconds(task)
        if age is not None and age > max_age_seconds:
            continue
        run_id = str(task.get("run_id") or task.get("task_id") or "")
        targets = [str(agent_id) for agent_id in (task.get("target_agents") or []) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
        if run_id and targets and not task_handled(str(task.get("task_id") or ""), run_id, targets):
            count += 1
    return count


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
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
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def process_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        if not raw:
            return []
        return [part.decode(errors="replace") for part in raw.split(b"\x00") if part]
    except Exception:
        return []


def process_looks_like_runner_process(pid: int, record: dict[str, Any]) -> bool:
    cmd = process_cmdline(pid)
    if not cmd:
        return True
    joined = " ".join(cmd).lower()
    if record.get("systemd_unit"):
        return "runner-systemd-entrypoint.sh" in joined
    runner_cmd = record.get("cmd")
    if isinstance(runner_cmd, list) and runner_cmd:
        expected_tokens: set[str] = set()
        for part in runner_cmd:
            if not isinstance(part, str):
                continue
            part = part.strip().lower()
            if not part:
                continue
            expected_tokens.add(Path(part).name.lower())
        if expected_tokens:
            for token in expected_tokens:
                if token and token not in joined:
                    return False
            return True
    return True


def systemd_unit_alive(unit: str) -> bool:
    if not unit:
        return False
    return systemd_unit_process_backed_alive(unit, {})


def systemd_unit_process_backed_alive(unit: str, record: dict[str, Any]) -> bool:
    if not unit:
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "--no-pager"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except Exception:
        return False
    state: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key] = value
    if not systemd_state_process_backed_alive(state):
        return False
    try:
        pid = int(state.get("MainPID") or 0)
    except Exception:
        pid = 0
    if not pid:
        return False
    return process_looks_like_runner_process(pid, record)


def systemd_state_process_backed_alive(state: dict[str, str]) -> bool:
    """Standing agenda liveness must match resident runner liveness.

    A lingering transient unit with ActiveState=active/activating but no live
    MainPID is not proof that an agent is still working; counting it as live
    suppresses standing tasks behind fake-running active-runner files.
    """
    try:
        main_pid = int(state.get("MainPID") or 0)
    except Exception:
        main_pid = 0
    return bool(main_pid and process_alive(main_pid))


def active_runner_process_backed_alive(record: dict[str, Any]) -> bool:
    runner_dir = Path(str(record.get("runner_dir") or ""))
    if runner_dir and (runner_dir / ".runner-exit-marker").exists():
        return False
    unit = str(record.get("systemd_unit") or "")
    if unit:
        return systemd_unit_process_backed_alive(unit, record)
    try:
        pid = int(record.get("pid") or 0)
    except Exception:
        pid = 0
    if not process_alive(pid):
        return False
    return process_looks_like_runner_process(pid, record)


def active_runner_count() -> int:
    if not ACTIVE_RUNNERS.exists():
        return 0
    count = 0
    for path in ACTIVE_RUNNERS.glob("*.json"):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        runner_dir = Path(str(record.get("runner_dir") or ""))
        result_path = runner_dir / "result.json" if runner_dir else None
        if result_path and result_path.exists() and runner_result_status_from_payload(read_json(result_path, {})):
            continue
        if active_runner_process_backed_alive(record):
            count += 1
    return count


def material_stall_active_runner_info() -> dict[str, Any]:
    """Identify active runners that are alive but have no material progress.

    Returns a dict with:
    - stall_count: number of material-stalled active runners
    - progress_count: number of active runners with material progress
    - stall_runners: list of (agent_id, run_id) for stalled runners
    - stall_task_ids: set of task_ids that have stalled runners

    This feeds into the tick() gate: if all active runners are stalled,
    the autonomy loop should not be fully blocked from creating recovery work.
    """
    if not ACTIVE_RUNNERS.exists():
        return {"stall_count": 0, "progress_count": 0, "stall_runners": [], "stall_task_ids": set(), "stalled_runner_ages": {}}
    stall_runners: list[tuple[str, str]] = []
    progress_runners: list[tuple[str, str]] = []
    stall_task_ids: set[str] = set()
    stalled_runner_ages: dict[str, float] = {}
    for path in ACTIVE_RUNNERS.glob("*.json"):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        agent_id = str(record.get("agent_id") or path.stem.split("-", 1)[0])
        run_id = str(record.get("run_id") or record.get("task_id") or "")
        runner_dir = Path(str(record.get("runner_dir") or ""))
        result_path = runner_dir / "result.json" if runner_dir else None
        if result_path and result_path.exists() and runner_result_status_from_payload(read_json(result_path, {})):
            continue
        if not active_runner_process_backed_alive(record):
            continue
        # Check material progress via the collaboration ledger written by
        # collaboration_ledger.py and read by collaboration_status.py.
        task_id = str(record.get("task_id") or run_id)
        ledger_path, _archive_path = collaboration_ledger_paths(task_id)
        if not ledger_path.exists():
            ledger_path = ROOM / "collaboration-ledger" / f"{task_id}.json"
        ledger = read_json(ledger_path, {})
        has_material = False
        if isinstance(ledger, dict):
            points = ledger.get("points") if isinstance(ledger.get("points"), list) else []
            for point in points:
                if isinstance(point, dict) and str(point.get("agent_id") or "") == agent_id:
                    has_material = True
                    break
        if has_material:
            progress_runners.append((agent_id, run_id))
        else:
            stall_runners.append((agent_id, run_id))
            if run_id:
                stall_task_ids.add(run_id)
            age = active_runner_age_seconds(record)
            if age is not None:
                stalled_runner_ages[f"{agent_id}:{run_id}"] = age
    return {
        "stall_count": len(stall_runners),
        "progress_count": len(progress_runners),
        "stall_runners": stall_runners,
        "progress_runners": progress_runners,
        "stall_task_ids": stall_task_ids,
        "stalled_runner_ages": stalled_runner_ages,
    }


def material_stall_info_for_output(info: dict[str, Any]) -> dict[str, Any]:
    output = dict(info)
    stall_task_ids = output.get("stall_task_ids")
    if isinstance(stall_task_ids, set):
        output["stall_task_ids"] = sorted(stall_task_ids)
    for key in ("stall_runners", "progress_runners"):
        runners = output.get(key)
        if isinstance(runners, list):
            output[key] = [list(item) if isinstance(item, tuple) else item for item in runners]
    return output


def active_runners_stalled_past_threshold(info: dict[str, Any], min_age_seconds: int) -> bool:
    """Return True when every recorded stalled runner has been alive longer than threshold."""
    if int(info.get("progress_count") or 0) > 0:
        return False
    stall_count = int(info.get("stall_count") or 0)
    if stall_count <= 0:
        return False
    if min_age_seconds <= 0:
        return True
    ages = info.get("stalled_runner_ages")
    if not isinstance(ages, dict):
        return False
    if not ages:
        return False
    if len(ages) < stall_count:
        return False
    for age in ages.values():
        try:
            age_seconds = float(age)
        except Exception:
            return False
        if age_seconds < float(min_age_seconds):
            return False
    return True


def pending_standing_task(state: dict[str, Any]) -> tuple[bool, str | None]:
    pending = state.get("pending_task")
    if not isinstance(pending, dict):
        return False, None
    task_id = str(pending.get("task_id") or "")
    run_id = str(pending.get("run_id") or task_id)
    targets = [str(x) for x in (pending.get("target_agents") or []) if str(x) in LOCAL_RUNTIME_AGENTS]
    manifest_path = ROOM / "tasks" / task_id / "manifest.json"
    manifest = read_json(manifest_path, {})
    if isinstance(manifest, dict):
        status = str(manifest.get("status") or "").strip().lower()
        if status in {"completed", "blocked", "failed", "partial", "partial_failed", "cancelled", "stale", "merged"}:
            return False, task_id
    if task_handled(task_id, run_id, targets):
        return False, task_id
    if not manifest_path.exists():
        # If manifest files disappear but a runner is still alive, keep waiting.
        # Otherwise, treat as resolved pending work to avoid hard deadlock on
        # orphaned pending_task state.
        if any(active_runner_record_is_alive(agent_id, run_id) for agent_id in targets):
            return True, task_id
        return False, task_id
    return True, task_id


def finished_runner_path(agent_id: str, run_id: str) -> Path:
    return ROOM / "finished-runners" / f"{agent_id}-{run_id}.json"


def active_runner_path(agent_id: str, run_id: str) -> Path:
    return ROOM / "active-runners" / f"{agent_id}-{run_id}.json"


def active_runner_record_is_alive(agent_id: str, run_id: str) -> bool:
    record = read_json(active_runner_path(agent_id, run_id), {})
    if not isinstance(record, dict):
        return False
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    if result_path and result_path.exists() and runner_result_status_from_payload(read_json(result_path, {})):
        return False
    return active_runner_process_backed_alive(record)


def active_runner_age_seconds(record: dict[str, Any]) -> float | None:
    started = parse_dt(record.get("started_at"))
    if not started:
        return None
    return (now_dt() - started).total_seconds()


def runner_result_status_from_payload(runner_result: dict[str, Any]) -> str | None:
    if not isinstance(runner_result, dict) or not runner_result:
        return None
    results = runner_result.get("results")
    if isinstance(results, list):
        if not results:
            return "completed"
        saw_result = False
        for result in results:
            if not isinstance(result, dict):
                continue
            saw_result = True
            comment = result.get("comment") if isinstance(result.get("comment"), dict) else {}
            if comment.get("blocked_reason") or comment.get("blockers"):
                return "blocked"
            sub_result = result.get("result") if isinstance(result.get("result"), dict) else {}
            if result.get("executed") and sub_result and not sub_result.get("ok", True):
                return "failed"
        return "completed" if saw_result else "completed"
    status = str(runner_result.get("status") or "").strip().lower()
    if status == "completed":
        return "completed"
    if status == "blocked":
        return "blocked"
    if status in {"failed", "partial_failed", "cancelled", "stale"}:
        return "failed"
    if runner_result.get("ok") is False:
        return "failed"
    if runner_result.get("ok") is True:
        return "completed"
    return None


def active_runner_terminal_or_dead_status(agent_id: str, run_id: str, grace_seconds: int) -> str | None:
    """Classify an active-runner record without waiting for resident harvest.

    The resident bridge remains the canonical harvester. This projection exists
    for standing-agenda liveness only: dead active-runner files should not keep a
    standing task in `running`/pending state for hours when the process is gone.
    """
    path = active_runner_path(agent_id, run_id)
    record = read_json(path, {})
    if not isinstance(record, dict):
        return None
    try:
        pid = int(record.get("pid") or 0)
    except Exception:
        pid = 0
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    runner_status = runner_result_status_from_payload(read_json(result_path, {}) if result_path and result_path.exists() else {})
    if runner_status:
        return runner_status
    if active_runner_process_backed_alive(record):
        return None
    age = active_runner_age_seconds(record)
    if grace_seconds > 0 and age is not None and age < grace_seconds:
        return None
    return "failed"


def archive_dead_missing_result_active_runner(
    agent_id: str,
    run_id: str,
    *,
    at: str,
    reason: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    """Archive a dead active-runner file that cannot be harvested for output.

    This is intentionally narrower than resident harvest. If a terminal
    result.json exists, the resident bridge must still harvest it so comments,
    followups, and projection decisions are preserved. Standing reconcile only
    cleans the fake-running lock for dead runners with no result artifact.
    """
    path = active_runner_path(agent_id, run_id)
    record = read_json(path, {})
    if not isinstance(record, dict):
        return None
    try:
        pid = int(record.get("pid") or 0)
    except Exception:
        pid = 0
    if active_runner_process_backed_alive(record):
        return None
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    runner_status = runner_result_status_from_payload(read_json(result_path, {}) if result_path and result_path.exists() else {})
    if runner_status:
        return None
    finished = dict(record)
    finished.update(
        {
            "status": "finished",
            "finished_at": at,
            "standing_reconcile_archive": True,
            "archive_reason": reason,
            "missing_process": True,
            "missing_result_json": True,
            "comments": 0,
            "collab_followups": [],
            "runtime_takeovers": [],
        }
    )
    if not dry_run:
        write_json(finished_runner_path(agent_id, run_id), finished)
        path.unlink(missing_ok=True)
    return {
        "agent_id": agent_id,
        "active_runner": str(path),
        "finished_runner": str(finished_runner_path(agent_id, run_id)),
        "pid": pid,
        "status": "archived_dead_missing_result" if not dry_run else "would_archive_dead_missing_result",
        "reason": reason,
        "missing_process": True,
        "missing_result_json": True,
        "dry_run": dry_run,
    }


def finished_agent_status(agent_id: str, run_id: str) -> str | None:
    record = read_json(finished_runner_path(agent_id, run_id), {})
    if not isinstance(record, dict) or record.get("status") != "finished":
        return None
    if (
        record.get("stale_runner")
        or record.get("missing_process")
        or record.get("orphan_harvest")
        or record.get("missing_result_json")
    ):
        return "failed"
    runner_result = record.get("runner_result") if isinstance(record.get("runner_result"), dict) else {}
    for result in runner_result.get("results") or []:
        if not isinstance(result, dict):
            continue
        comment = result.get("comment") if isinstance(result.get("comment"), dict) else {}
        if comment.get("blocked_reason") or comment.get("blockers"):
            return "blocked"
        sub_result = result.get("result") if isinstance(result.get("result"), dict) else {}
        if result.get("executed") and sub_result and not sub_result.get("ok", True):
            return "failed"
    return "completed"


def collaboration_ledger_paths(task_id: str) -> tuple[Path, Path]:
    return ROOM / "collaboration-ledgers" / f"{task_id}.json", ROOM / "collaboration-ledgers" / f"{task_id}.jsonl"


def runner_result_is_terminal(runner_result: dict[str, Any]) -> bool:
    if not isinstance(runner_result, dict) or not runner_result:
        return False
    if isinstance(runner_result.get("results"), list):
        return True
    status = str(runner_result.get("status") or "").strip().lower()
    if status in {"completed", "failed", "blocked", "partial", "partial_failed", "cancelled", "stale"}:
        return True
    return "ok" in runner_result and ("exit_code" in runner_result or "runner_status" in runner_result)


def collaboration_claim_expired(claim: dict[str, Any], now: datetime | None = None) -> bool:
    if str(claim.get("status") or "").strip() not in ACTIVE_COLLAB_CLAIM_STATUSES:
        return False
    expiry = parse_dt(claim.get("lease_expiry"))
    if expiry is None:
        return False
    return (now or now_dt()) > expiry


def collaboration_claim_has_live_or_result_runner(ledger: dict[str, Any], claim: dict[str, Any]) -> bool:
    agent_id = str(claim.get("agent_id") or "").strip()
    run_id = str(ledger.get("run_id") or ledger.get("task_id") or "").strip()
    if agent_id not in LOCAL_RUNTIME_AGENTS or not run_id:
        return False
    record = read_json(active_runner_path(agent_id, run_id), {})
    if not isinstance(record, dict) or not record:
        return False
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    if result_path and result_path.exists() and runner_result_is_terminal(read_json(result_path, {})):
        return True
    return active_runner_process_backed_alive(record)


def parse_command_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = str(proc.stdout or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def load_python_tool(path: Path, module_name: str) -> Any | None:
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def normalize_standing_artifact_hooks(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw_hooks = item.get("standing_artifact_hooks") or item.get("artifact_hooks") or []
    if isinstance(raw_hooks, (str, dict)):
        raw_hooks = [raw_hooks]
    hooks: list[dict[str, Any]] = []
    for raw in raw_hooks if isinstance(raw_hooks, list) else []:
        if isinstance(raw, str):
            hook = {"type": raw}
        elif isinstance(raw, dict):
            hook = dict(raw)
        else:
            continue
        hook_type = str(hook.get("type") or hook.get("name") or "").strip()
        if hook_type:
            hook["type"] = hook_type
            hooks.append(hook)
    return hooks


def standing_artifact_hook_out_dir(hook: dict[str, Any], hook_type: str) -> Path:
    raw = str(hook.get("out_dir") or "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else ROOT / path
    if hook_type == "model_routing_reliability_snapshot":
        return ROOM / "artifacts" / f"model-routing-reliability-{now_dt().strftime('%Y%m%d')}"
    return ROOM / "artifacts" / "standing-artifact-hooks" / hook_type


def run_model_routing_reliability_hook(hook: dict[str, Any], produced_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    module = load_python_tool(TOOL_DIR / "model_routing_reliability.py", "standing_model_routing_reliability")
    if module is None:
        return [], [{"type": "model_routing_reliability_snapshot", "error": "tool_load_failed"}]
    module.ROOT = ROOT
    module.ROOM = ROOM
    module.COMMENT_ROOT = ROOT / "agent-comments"
    module.ACTIVE_RUNNERS = ROOM / "active-runners"
    module.POLICY_FILE = ROOM / "config" / "claude-code-model-policy.json"
    try:
        since_hours = float(hook.get("since_hours") if hook.get("since_hours") is not None else 24.0)
    except Exception:
        since_hours = 24.0
    raw_focus_models = hook.get("focus_models")
    focus_models: list[str] = []
    if isinstance(raw_focus_models, list):
        focus_models = [str(item).strip() for item in raw_focus_models if str(item).strip()]
    elif isinstance(raw_focus_models, str):
        focus_models = [item.strip() for item in raw_focus_models.split(",") if item.strip()]
    try:
        report = module.build_report(since_hours, focus_models=focus_models)
        artifacts = module.write_artifacts(report, standing_artifact_hook_out_dir(hook, "model_routing_reliability_snapshot"))
    except Exception as exc:
        return [], [
            {
                "type": "model_routing_reliability_snapshot",
                "error": type(exc).__name__ + ": " + str(exc)[:240],
            }
        ]
    latest_markdown = evidence_path(artifacts.get("latest_markdown"))
    latest_json = evidence_path(artifacts.get("latest_json"))
    if not latest_markdown:
        return [], [{"type": "model_routing_reliability_snapshot", "error": "missing_latest_markdown"}]
    return [
        {
            "id": "standing-hook-model-routing-reliability",
            "type": "model_routing_reliability_snapshot",
            "title": "24h model-routing reliability snapshot",
            "path": latest_markdown,
            "metadata_paths": {
                "latest_json": latest_json,
                "markdown": evidence_path(artifacts.get("markdown")),
                "json": evidence_path(artifacts.get("json")),
            },
            "produced_by": "standing_agenda_tick.py",
            "produced_at": produced_at,
            "source": {
                "tool": "model_routing_reliability.py",
                "since_hours": since_hours,
                "focus_models": focus_models,
            },
        }
    ], []


def create_standing_artifact_hooks(item: dict[str, Any], produced_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for hook in normalize_standing_artifact_hooks(item):
        hook_type = str(hook.get("type") or "")
        if hook_type == "model_routing_reliability_snapshot":
            produced, hook_errors = run_model_routing_reliability_hook(hook, produced_at)
            artifacts.extend(produced)
            errors.extend(hook_errors)
        else:
            errors.append({"type": hook_type or "unknown", "error": "unsupported_standing_artifact_hook"})
    return artifacts, errors


def reconcile_expired_collaboration_claims(limit: int = 50, *, dry_run: bool = False) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    ledger_dir = ROOM / "collaboration-ledgers"
    if not ledger_dir.exists():
        return []
    reconciled: list[dict[str, Any]] = []
    now = now_dt()
    for state_file in sorted(ledger_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        if len(reconciled) >= limit:
            break
        ledger = read_json(state_file, {})
        if not isinstance(ledger, dict) or ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
            continue
        task_id = str(ledger.get("task_id") or "").strip()
        archive_file = state_file.with_suffix(".jsonl")
        claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
        for claim in claims:
            if len(reconciled) >= limit:
                break
            if not isinstance(claim, dict) or not collaboration_claim_expired(claim, now):
                continue
            if collaboration_claim_has_live_or_result_runner(ledger, claim):
                continue
            work_item_id = str(claim.get("work_item_id") or "").strip()
            agent_id = str(claim.get("agent_id") or "").strip()
            if not work_item_id or agent_id not in LOCAL_RUNTIME_AGENTS:
                continue
            if dry_run:
                reconciled.append(
                    {
                        "task_id": task_id,
                        "work_item_id": work_item_id,
                        "agent_id": agent_id,
                        "state_file": str(state_file),
                        "status": "would_block_expired_claim",
                        "dry_run": True,
                    }
                )
                continue
            proc = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_DIR / "collaboration_ledger.py"),
                    "--state-file", str(state_file),
                    "--archive-file", str(archive_file),
                    "release-expired",
                    "--work-item-id", work_item_id,
                    "--agent-id", agent_id,
                    "--mode", "block",
                    "--reason", "claim_lease_expired_no_live_runner",
                    "--detail",
                    "Claim lease expired and no live/result-pending runner exists; closing as blocked so the room loop can continue with explicit degraded-quorum evidence.",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            payload = parse_command_json(proc)
            if proc.returncode == 0 and payload.get("ok") and int(payload.get("released_count") or 0) > 0:
                reconciled.append(
                    {
                        "task_id": task_id,
                        "work_item_id": work_item_id,
                        "agent_id": agent_id,
                        "state_file": str(state_file),
                        "status": "blocked_expired_claim",
                        "released_count": payload.get("released_count"),
                        "dry_run": False,
                    }
                )
            elif proc.returncode != 0 or payload.get("ok") is False:
                reconciled.append(
                    {
                        "task_id": task_id,
                        "work_item_id": work_item_id,
                        "agent_id": agent_id,
                        "state_file": str(state_file),
                        "status": "reconcile_failed",
                        "error": (payload.get("error") if isinstance(payload, dict) else None) or str(proc.stderr or "")[-400:],
                        "dry_run": False,
                    }
                )
    return reconciled


def terminal_collaboration_status(task_status: str) -> str | None:
    status = task_status.strip().lower()
    if status == "completed":
        return "completed"
    if status in {"blocked", "failed", "partial_failed", "cancelled", "stale"}:
        return "blocked"
    return None


def standing_closure_evidence_paths(task: dict[str, Any]) -> list[str]:
    """Return concrete produced evidence, not planned acceptance criteria."""
    evidence: list[str] = []
    candidates: list[Any] = [
        task.get("result_paths"),
        task.get("evidence_paths"),
        task.get("artifacts"),
    ]
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    candidates.extend([
        collaboration.get("evidence_paths"),
        collaboration.get("artifacts"),
    ])
    for raw in candidates:
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = raw
        else:
            continue
        for item in raw_items:
            if isinstance(item, dict):
                value = first_text(item.get("path"), item.get("artifact"), item.get("file"))
            else:
                value = compact_text(item)
            normalized = path_like_evidence(value)
            if normalized and normalized not in evidence:
                evidence.append(normalized)
    return evidence[:12]


def work_item_has_material_marker(item: dict[str, Any]) -> bool:
    for key in ("result_paths", "evidence_paths", "artifacts", "artifact", "path", "file"):
        value = item.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    if first_text(item.get("blocker"), item.get("blocked_reason"), item.get("reason")):
        return True
    return str(item.get("acceptance") or "").strip() in {"accepted", "rejected", "superseded"}


def standing_closure_material_markers(task: dict[str, Any]) -> int:
    count = 0
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    for key in ("points", "material_points", "blockers"):
        value = collaboration.get(key)
        if isinstance(value, list):
            count += len([item for item in value if isinstance(item, dict)])
    work_items = collaboration.get("work_items")
    if isinstance(work_items, list):
        count += len([item for item in work_items if isinstance(item, dict) and work_item_has_material_marker(item)])
    return count


def build_standing_closure(
    task: dict[str, Any],
    *,
    terminal_status: str,
    reason: str,
    at: str,
    targets: list[str],
    agent_statuses: dict[str, str],
) -> dict[str, Any]:
    evidence = standing_closure_evidence_paths(task)
    material_markers = standing_closure_material_markers(task)
    failed_agents = sorted(agent_id for agent_id, value in agent_statuses.items() if value == "failed")
    blocked_agents = sorted(agent_id for agent_id, value in agent_statuses.items() if value == "blocked")
    completed_agents = sorted(agent_id for agent_id, value in agent_statuses.items() if value == "completed")
    owner = (failed_agents or blocked_agents or targets or ["openclaw-main"])[0]

    if terminal_status == "completed":
        if evidence or material_markers or completed_agents:
            outcome = "completed_with_evidence"
            summary = "standing task completed with recorded runner/material evidence"
            recovery_action = "summarize result and advance/close the mainline item if acceptance criteria are met"
        else:
            outcome = "degraded_no_progress"
            owner = "openclaw-main"
            summary = "standing task reached completed state but no material evidence was recorded"
            recovery_action = "record artifact, smoke, RCA, blocker, or reopen as failed_with_rca before reporting progress"
    elif failed_agents or "failed" in reason or "dead_active_runner" in reason:
        outcome = "failed_with_rca"
        summary = "standing task failed or lost runner evidence; RCA/recovery owner required"
        recovery_action = "write root cause, impact, recovery action, and unblock/retry owner before creating more autonomy work"
    else:
        outcome = "blocked_with_owner"
        summary = "standing task is blocked and needs an explicit owner/recovery step"
        recovery_action = "name the blocker owner, missing evidence, and safe next retry/implementation step"

    return {
        "status": terminal_status,
        "outcome": outcome,
        "reason": reason,
        "owner": owner,
        "targets": targets,
        "completed_agents": completed_agents,
        "blocked_agents": blocked_agents,
        "failed_agents": failed_agents,
        "evidence_paths": evidence,
        "material_marker_count": material_markers,
        "telegram_safe_summary": summary,
        "recovery_action": recovery_action,
        "reconciled_at": at,
        "source": "standing_agenda_tick.py",
    }


def reconcile_collaboration_terminal_state(
    collaboration: dict[str, Any],
    *,
    terminal_status: str,
    reason: str,
    at: str,
) -> bool:
    changed = False
    if str(collaboration.get("status") or "").strip().lower() != terminal_status:
        collaboration["status"] = terminal_status
        changed = True
    work_items = collaboration.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status in {"completed", "blocked", "cancelled"}:
                continue
            item["status"] = "completed" if terminal_status == "completed" else "blocked"
            item["updated_at"] = at
            changed = True
    if terminal_status == "blocked":
        blockers = collaboration.get("blockers")
        if not isinstance(blockers, list):
            blockers = []
            collaboration["blockers"] = blockers
            changed = True
        existing = {
            (str(blocker.get("work_item_id") or ""), str(blocker.get("reason") or ""))
            for blocker in blockers
            if isinstance(blocker, dict)
        }
        for item in work_items or []:
            if not isinstance(item, dict):
                continue
            work_item_id = str(item.get("id") or "")
            if not work_item_id or (work_item_id, reason) in existing:
                continue
            blockers.append(
                {
                    "id": f"standing-closure-{len(blockers) + 1:03d}",
                    "work_item_id": work_item_id,
                    "agent_id": "agent-room-standing-agenda",
                    "reason": reason,
                    "detail": "Standing agenda reconciled a terminal task whose collaboration state had not recorded a material artifact or blocker.",
                    "blocked_at": at,
                    "status": "closed_by_reconciliation",
                }
            )
            existing.add((work_item_id, reason))
            changed = True
    if changed:
        collaboration["updated_at"] = at
    return changed


def reconcile_ledger_terminal_state(task: dict[str, Any], terminal_status: str, reason: str, at: str, *, dry_run: bool) -> bool:
    task_id = str(task.get("task_id") or "")
    if not task_id:
        return False
    state_path, archive_path = collaboration_ledger_paths(task_id)
    ledger = read_json(state_path, {})
    if not isinstance(ledger, dict) or ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return False
    if str(ledger.get("task_id") or "") != task_id:
        return False
    before = json.dumps(ledger, ensure_ascii=False, sort_keys=True)
    changed = reconcile_collaboration_terminal_state(ledger, terminal_status=terminal_status, reason=reason, at=at)
    if not changed:
        return False
    ledger["updated_at"] = at
    after = json.dumps(ledger, ensure_ascii=False, sort_keys=True)
    if before == after:
        return False
    if dry_run:
        return True
    write_json(state_path, ledger)
    append_jsonl(
        archive_path,
        [
            {
                "schema": "openclaw.agent_room.collaboration_event.v0",
                "event_type": "standing_closure_reconcile",
                "at": at,
                "room_id": ledger.get("room_id"),
                "task_id": task_id,
                "run_id": ledger.get("run_id") or task.get("run_id") or task_id,
                "turn_seq": ledger.get("turn_seq"),
                "payload": {
                    "terminal_status": terminal_status,
                    "reason": reason,
                    "source": "standing_agenda_tick.py",
                },
            }
        ],
    )
    return True


def standing_task_mainline_item_id(task: dict[str, Any]) -> str:
    standing_mainline = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
    candidates = [
        standing_mainline.get("linked_mainline_item_id"),
        task.get("mainline_id"),
        governance.get("mainline_id"),
    ]
    standing_item_id = str(standing.get("item_id") or "").strip()
    if standing_item_id:
        config = read_json(CONFIG, {})
        config_items = config.get("items") if isinstance(config, dict) else []
        for item in config_items or []:
            if not isinstance(item, dict) or str(item.get("id") or "") != standing_item_id:
                continue
            candidates.extend([
                item.get("mainline_item_id"),
                item.get("mainline_agenda_item_id"),
                item.get("mainline_id"),
            ])
            break
        candidates.append(standing_item_id)
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text != "agent_room_infrastructure":
            return text
    return ""


def path_like_evidence(value: Any) -> str:
    text = evidence_path(value)
    if not text:
        return ""
    if "/" in text or text.startswith("."):
        return text
    if text.endswith((".md", ".json", ".jsonl", ".py", ".txt", ".log")):
        return text
    return ""


def standing_task_closure_evidence_paths(task: dict[str, Any], manifest: Path, closure: dict[str, Any]) -> list[str]:
    evidence: list[str] = []

    def add(raw: Any) -> None:
        if isinstance(raw, list):
            for entry in raw:
                add(entry)
            return
        if isinstance(raw, dict):
            add(first_text(raw.get("path"), raw.get("artifact"), raw.get("file")))
            return
        normalized = path_like_evidence(raw)
        if normalized and normalized not in evidence:
            evidence.append(normalized)

    add(str(manifest))
    add(task.get("brief_path"))
    add(task.get("result_paths"))
    add(task.get("evidence_paths"))
    add(task.get("artifacts"))
    add(closure.get("evidence_paths"))
    task_id = str(task.get("task_id") or "")
    if task_id:
        ledger_path, archive_path = collaboration_ledger_paths(task_id)
        if ledger_path.exists():
            add(str(ledger_path))
        if archive_path.exists():
            add(str(archive_path))
    return evidence[:20]


def standing_config_item_for_task(task: dict[str, Any]) -> dict[str, Any]:
    standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
    standing_item_id = str(standing.get("item_id") or "").strip()
    if not standing_item_id:
        return {}
    config = read_json(CONFIG, {})
    config_items = config.get("items") if isinstance(config, dict) else []
    for item in config_items or []:
        if isinstance(item, dict) and str(item.get("id") or "") == standing_item_id:
            return item
    return {}


def standing_closure_mainline_status(closure: dict[str, Any]) -> str:
    outcome = str(closure.get("outcome") or "").strip()
    if outcome == "completed_with_evidence":
        return "in_review"
    return "blocked"


def canonical_mainline_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    chars: list[str] = []
    previous_sep = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            previous_sep = False
        else:
            if not previous_sep:
                chars.append("_")
            previous_sep = True
    return "".join(chars).strip("_")


def mainline_lookup_values(raw: Any) -> list[str]:
    values: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for entry in value:
                add(entry)
            return
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
        canonical = canonical_mainline_id(text)
        if canonical and canonical not in values:
            values.append(canonical)
        if text:
            for variant in (text.replace("_", "-"), text.replace("-", "_")):
                variant = variant.strip()
                if variant and variant not in values:
                    values.append(variant)
                canonical_variant = canonical_mainline_id(variant)
                if canonical_variant and canonical_variant not in values:
                    values.append(canonical_variant)

    add(raw)
    return values


def find_mainline_item(active_items: Any, item_id: str) -> dict[str, Any] | None:
    wanted = set(mainline_lookup_values(item_id))
    if not wanted or not isinstance(active_items, list):
        return None
    for candidate in active_items:
        if not isinstance(candidate, dict):
            continue
        candidate_values = set(
            mainline_lookup_values(
                [
                    candidate.get("id"),
                    candidate.get("legacy_ids"),
                    candidate.get("aliases"),
                    candidate.get("standing_item_ids"),
                ]
            )
        )
        if wanted & candidate_values:
            return candidate
    return None


def sync_mainline_item_for_standing_closure(
    room_id: str,
    task: dict[str, Any],
    manifest: Path,
    closure: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    item_id = standing_task_mainline_item_id(task)
    if not item_id:
        return {"ok": False, "status": "missing_mainline_item_id", "tokens_printed": False}
    agenda_path = ROOM / "rooms" / room_id / "mainline_agenda.json"
    agenda = read_json(agenda_path, {})
    active_items = agenda.get("active_items") if isinstance(agenda, dict) else []
    item = find_mainline_item(active_items, item_id)
    if item is None:
        return {"ok": False, "status": "mainline_item_not_found", "item_id": item_id, "tokens_printed": False}
    current_status = str(item.get("status") or "open").strip()
    if current_status in MAINLINE_TERMINAL_STATUSES:
        return {"ok": True, "status": "skipped_mainline_terminal", "item_id": item_id, "tokens_printed": False}
    standing_item = standing_config_item_for_task(task)
    standing_item_blocked = str(standing_item.get("status") or "").strip() == "blocked"
    next_status = "blocked" if standing_item_blocked else standing_closure_mainline_status(closure)
    evidence = standing_task_closure_evidence_paths(task, manifest, closure)
    if standing_item_blocked:
        for entry in standing_item.get("pause_evidence") or []:
            normalized = path_like_evidence(entry)
            if normalized and normalized not in evidence:
                evidence.append(normalized)
    existing_evidence = item.get("evidence_paths") if isinstance(item.get("evidence_paths"), list) else []
    existing = {evidence_path(entry) for entry in existing_evidence}
    missing = [entry for entry in evidence if entry not in existing]
    if standing_item_blocked:
        blocked_reason = first_text(
            standing_item.get("blocked_reason"),
            standing_item.get("resume_condition"),
            "standing agenda item is blocked",
        )
        note = f"standing agenda item blocked: {blocked_reason}"
    else:
        note = f"standing closure {closure.get('outcome') or closure.get('status')}: {closure.get('reason')}"
    if current_status == next_status and not missing and str(item.get("status_note") or "") == note:
        return {"ok": True, "status": "already_synced", "item_id": item_id, "tokens_printed": False}
    if dry_run:
        return {
            "ok": True,
            "status": "would_advance_mainline_item",
            "item_id": item_id,
            "mainline_status": next_status,
            "evidence_paths_added": missing,
            "tokens_printed": False,
        }
    return advance_mainline_item(
        room_id,
        item_id,
        status=next_status,
        evidence_paths=evidence,
        note=note,
        source={
            "tool": "standing_agenda_tick.py",
            "task_id": task.get("task_id"),
            "run_id": task.get("run_id") or task.get("task_id"),
            "standing_closure_outcome": closure.get("outcome"),
        },
    )


def reconcile_standing_task_statuses(
    max_age_seconds: int = 7200,
    limit: int = 50,
    *,
    dry_run: bool = False,
    dead_runner_grace_seconds: int | None = None,
) -> list[dict[str, Any]]:
    """Close stale standing manifests and keep collaboration ledgers terminal.

    Standing agenda only works as a loop if each task reaches a clear terminal
    state. This reconciles two gaps: old `running` tasks whose runners are gone,
    and terminal manifests whose collaboration ledger still says `open`.
    """
    if dead_runner_grace_seconds is None:
        try:
            dead_runner_grace_seconds = int(os.environ.get("AGENT_ROOM_STANDING_DEAD_RUNNER_GRACE_SECONDS", "60"))
        except Exception:
            dead_runner_grace_seconds = 60
    dead_runner_grace_seconds = max(0, int(dead_runner_grace_seconds))
    changed: list[dict[str, Any]] = []
    task_root = ROOM / "tasks"
    if not task_root.exists():
        return changed
    open_statuses = {"queued", "running", "deferred"}
    terminal_statuses = {"completed", "blocked", "failed", "partial_failed", "cancelled", "stale"}
    for manifest in sorted(task_root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(changed) >= limit:
            break
        task = read_json(manifest, {})
        if not isinstance(task, dict):
            continue
        if source_transport(task) != "agent-room-standing-mainline" and not task.get("standing_agenda"):
            continue
        status = str(task.get("status") or "queued").strip().lower()
        if status not in open_statuses | terminal_statuses:
            continue
        run_id = str(task.get("run_id") or task.get("task_id") or "")
        targets = [str(agent_id) for agent_id in (task.get("target_agents") or []) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
        if not run_id or not targets:
            continue
        agent_statuses: dict[str, str] = {}
        agent_status_sources: dict[str, str] = {}
        new_status: str | None = None
        reason = f"manifest_{status}"
        if status in open_statuses:
            # Live active runners own their state until harvest/timeout; dead
            # active-runner files must not keep standing work fake-running.
            if any(active_runner_record_is_alive(agent_id, run_id) for agent_id in targets):
                continue
            for agent_id in targets:
                agent_status = finished_agent_status(agent_id, run_id)
                agent_source = "finished_runner"
                if agent_status is None and reply_artifact_exists(agent_id, run_id):
                    agent_status = "completed"
                    agent_source = "reply_artifact"
                if agent_status is None:
                    agent_status = active_runner_terminal_or_dead_status(agent_id, run_id, dead_runner_grace_seconds)
                    agent_source = "dead_active_runner_projection"
                if agent_status:
                    agent_statuses[agent_id] = agent_status
                    agent_status_sources[agent_id] = agent_source
            age = task_age_seconds(task)
            if set(agent_statuses) >= set(targets):
                if any(v == "failed" for v in agent_statuses.values()):
                    new_status = "failed"
                elif any(v == "blocked" for v in agent_statuses.values()):
                    new_status = "blocked"
                else:
                    new_status = "completed"
                if any(source == "dead_active_runner_projection" for source in agent_status_sources.values()):
                    reason = "dead_active_runner_evidence"
                else:
                    reason = "finished_runner_evidence"
            elif age is not None and age > max_age_seconds:
                new_status = "stale"
                reason = "standing_task_unharvested_or_missing_runner_terminal_state"
                task["blocked_reason"] = reason
            else:
                continue
        else:
            new_status = status
        terminal_status = terminal_collaboration_status(new_status)
        if not terminal_status:
            continue
        at = now_iso()
        existing_terminal_at = iso_or_none(standing_task_terminal_at(task) or parse_dt(task.get("updated_at") or task.get("created_at")))
        before = json.dumps(task, ensure_ascii=False, sort_keys=True)
        manifest_status_changed = status in open_statuses and new_status != status
        if manifest_status_changed or agent_statuses:
            summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
            summary["completed_agents"] = sorted(agent_id for agent_id, v in agent_statuses.items() if v == "completed")
            summary["blocked_agents"] = sorted(agent_id for agent_id, v in agent_statuses.items() if v == "blocked")
            summary["failed_agents"] = sorted(agent_id for agent_id, v in agent_statuses.items() if v == "failed")
            summary["targets"] = targets
            summary["agent_status_sources"] = agent_status_sources
            summary["reconciled_at"] = at
            summary["reconcile_source"] = "standing_agenda_tick.reconcile_standing_task_statuses"
            summary["reconcile_reason"] = reason
            archived_dead_runners: list[dict[str, Any]] = []
            if reason == "dead_active_runner_evidence":
                for archive_agent_id, source in sorted(agent_status_sources.items()):
                    if source != "dead_active_runner_projection" or agent_statuses.get(archive_agent_id) != "failed":
                        continue
                    archived = archive_dead_missing_result_active_runner(
                        archive_agent_id,
                        run_id,
                        at=at,
                        reason=reason,
                        dry_run=dry_run,
                    )
                    if archived:
                        archived_dead_runners.append(archived)
                if archived_dead_runners:
                    summary["archived_dead_active_runners"] = archived_dead_runners
            task["runner_summary"] = summary
        if manifest_status_changed:
            task["status"] = new_status
            task["terminal_state_at"] = at
        elif status in terminal_statuses and not task.get("terminal_state_at") and existing_terminal_at:
            task["terminal_state_at"] = existing_terminal_at
        next_closure = build_standing_closure(
            task,
            terminal_status=terminal_status,
            reason=reason,
            at=at,
            targets=targets,
            agent_statuses=agent_statuses,
        )
        closure = task.get("standing_closure") if isinstance(task.get("standing_closure"), dict) else {}
        if closure != next_closure:
            task["standing_closure"] = next_closure
        collaboration = task.get("collaboration")
        if isinstance(collaboration, dict):
            reconcile_collaboration_terminal_state(collaboration, terminal_status=terminal_status, reason=reason, at=at)
            task["collaboration"] = collaboration
        after = json.dumps(task, ensure_ascii=False, sort_keys=True)
        ledger_changed = reconcile_ledger_terminal_state(task, terminal_status, reason, at, dry_run=dry_run)
        mainline_sync = sync_mainline_item_for_standing_closure(
            str(task.get("room_id") or "openclaw-evolution"),
            task,
            manifest,
            next_closure,
            dry_run=dry_run,
        )
        if before == after and not ledger_changed:
            if mainline_sync.get("status") in {"already_synced", "skipped_mainline_terminal", "missing_mainline_item_id", "mainline_item_not_found"}:
                continue
        if before != after:
            task["updated_at"] = at
            task.setdefault("heartbeat", {})["last_seen_at"] = at
            after = json.dumps(task, ensure_ascii=False, sort_keys=True)
        if not dry_run and before != after:
            write_json(manifest, task)
        changed.append(
            {
                "task_id": task.get("task_id"),
                "status": new_status,
                "terminal_status": terminal_status,
                "reason": reason,
                "manifest": str(manifest),
                "ledger_updated": ledger_changed,
                "mainline_sync": mainline_sync,
                "dry_run": dry_run,
            }
        )
    return changed


def cooldown_remaining_seconds(config: dict[str, Any], state: dict[str, Any]) -> int:
    interval = int(config.get("proactive_tick_interval_seconds") or 3600)
    if interval <= 0:
        return 0
    last_injected = parse_dt(state.get("last_injected_at"))
    if not last_injected:
        return 0
    elapsed = (now_dt() - last_injected).total_seconds()
    if elapsed >= interval:
        return 0
    return max(1, int(interval - elapsed))


def item_max_rounds(config: dict[str, Any], item: dict[str, Any]) -> int:
    raw = item.get("max_rounds") if "max_rounds" in item else config.get("max_rounds", 1)
    try:
        return int(raw)
    except Exception:
        return 1


def standing_item_round_count(item_id: str, state: dict[str, Any]) -> int:
    count = 0
    state_items = state.get("items") if isinstance(state.get("items"), dict) else {}
    item_state = state_items.get(item_id) if isinstance(state_items, dict) else {}
    if isinstance(item_state, dict):
        for key in ("rounds_created", "created_count", "injected_count"):
            try:
                count = max(count, int(item_state.get(key) or 0))
            except Exception:
                pass

    task_ids: set[str] = set()
    for task in read_jsonl(TASKS_JSONL):
        standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
        mainline = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
        if str(standing.get("item_id") or mainline.get("item_id") or "") != item_id:
            continue
        if source_transport(task) != "agent-room-standing-mainline":
            continue
        task_id = str(task.get("task_id") or task.get("run_id") or "")
        if task_id:
            task_ids.add(task_id)
    return max(count, len(task_ids))


def dt_sort_value(value: datetime | None) -> float:
    if not value:
        return 0.0
    return value.timestamp()


def iso_or_none(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat(timespec="seconds")


def latest_dt(values: list[datetime | None]) -> datetime | None:
    parsed = [value for value in values if value is not None]
    if not parsed:
        return None
    return max(parsed)


def standing_task_attempt_at(task: dict[str, Any]) -> datetime | None:
    return parse_dt(task.get("created_at") or task.get("canonical_imported_at") or task.get("updated_at"))


def standing_task_terminal_at(task: dict[str, Any]) -> datetime | None:
    explicit = parse_dt(
        task.get("terminal_state_at")
        or task.get("terminal_at")
        or task.get("status_terminal_at")
        or task.get("status_changed_at")
    )
    if explicit:
        return explicit
    status = str(task.get("status") or "").strip().lower()
    if status not in TASK_TERMINAL_STATUSES:
        return None
    candidates: list[datetime | None] = []
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    for item in collaboration.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(parse_dt(item.get("completed_at") or item.get("blocked_at") or item.get("failed_at")))
    for blocker in collaboration.get("blockers") or []:
        if isinstance(blocker, dict):
            candidates.append(parse_dt(blocker.get("blocked_at")))
    runner_summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
    candidates.append(parse_dt(runner_summary.get("reconciled_at")))
    closure = task.get("standing_closure") if isinstance(task.get("standing_closure"), dict) else {}
    candidates.append(parse_dt(closure.get("reconciled_at")))
    return latest_dt(candidates) or parse_dt(task.get("updated_at") or task.get("created_at"))


def standing_item_task_stats(item_id: str, *, since: datetime | None = None) -> dict[str, Any]:
    """Return current manifest-backed standing-task stats for one item.

    The append-only tasks.jsonl ledger is creation history; the per-task
    manifest is the operational truth after async runners finish.  Counting raw
    creations as "rounds consumed" made a single blocked standing task suppress
    future repair attempts forever.  Use manifests so blocked/failed/running can
    be diagnosed or retried while completed rounds still cap self-amplifying
    loops.
    """
    rows: list[dict[str, Any]] = []
    saw_manifest_for_item = False
    task_root = ROOM / "tasks"
    if task_root.exists():
        for manifest in task_root.glob("*/manifest.json"):
            task = read_json(manifest, {})
            if not isinstance(task, dict):
                continue
            standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
            mainline = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
            if str(standing.get("item_id") or mainline.get("item_id") or "") != item_id:
                continue
            if source_transport(task) != "agent-room-standing-mainline":
                continue
            saw_manifest_for_item = True
            attempt_at = standing_task_attempt_at(task)
            terminal_at = standing_task_terminal_at(task)
            if since is not None:
                task_at = attempt_at or terminal_at or parse_dt(task.get("updated_at"))
                if task_at is not None and task_at < since:
                    continue
            task = dict(task)
            task["_manifest_path"] = str(manifest)
            task["_attempt_at"] = iso_or_none(attempt_at)
            task["_terminal_at"] = iso_or_none(terminal_at)
            rows.append(task)
    if not rows and not saw_manifest_for_item:
        for task in read_jsonl(TASKS_JSONL):
            standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
            mainline = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
            if str(standing.get("item_id") or mainline.get("item_id") or "") != item_id:
                continue
            if source_transport(task) != "agent-room-standing-mainline":
                continue
            if since is not None:
                task_at = standing_task_attempt_at(task) or standing_task_terminal_at(task) or parse_dt(task.get("updated_at"))
                if task_at is not None and task_at < since:
                    continue
            task = dict(task)
            task["_attempt_at"] = iso_or_none(standing_task_attempt_at(task))
            task["_terminal_at"] = iso_or_none(standing_task_terminal_at(task))
            rows.append(task)
    statuses = [str(task.get("status") or "queued") for task in rows]
    terminal_success = {"completed"}
    unresolved = {"queued", "running", "deferred"}
    retryable_failure = {"blocked", "failed", "partial", "partial_failed", "stale"}
    # Material completed: rounds that produced evidence, not just reached a
    # completed manifest status without artifacts.  A degraded_no_progress
    # closure means the round produced nothing and should not suppress further
    # autonomy work via the max_rounds safety fuse.
    DEGRADED_NO_PROGRESS = "degraded_no_progress"
    def _is_material_completed(task: dict[str, Any]) -> bool:
        if str(task.get("status") or "").strip().lower() != "completed":
            return False
        closure = task.get("standing_closure") if isinstance(task.get("standing_closure"), dict) else None
        if closure is not None:
            outcome = str(closure.get("outcome") or "").strip()
            if outcome == DEGRADED_NO_PROGRESS:
                return False
            # Any other closure outcome (completed_with_evidence, etc.) counts.
            return True
        # No closure recorded yet — check for evidence paths as a heuristic.
        evidence = task.get("evidence_paths") or task.get("acceptance_evidence")
        if isinstance(evidence, (list, str)) and evidence:
            return True
        artifacts = task.get("artifacts")
        if isinstance(artifacts, list) and any(isinstance(a, dict) and a.get("path") for a in artifacts):
            return True
        # No closure and no evidence: conservatively treat as non-material.
        return False
    material_completed = sum(1 for task in rows if _is_material_completed(task))
    latest = None
    if rows:
        latest = sorted(
            rows,
            key=lambda task: (
                dt_sort_value(parse_dt(task.get("_attempt_at"))),
                dt_sort_value(parse_dt(task.get("_terminal_at"))),
                dt_sort_value(parse_dt(task.get("updated_at"))),
            ),
            reverse=True,
        )[0]
    latest_retryable = None
    retryable_rows = [task for task in rows if str(task.get("status") or "").strip().lower() in retryable_failure]
    if retryable_rows:
        latest_retryable = sorted(
            retryable_rows,
            key=lambda task: (
                dt_sort_value(parse_dt(task.get("_terminal_at"))),
                dt_sort_value(parse_dt(task.get("_attempt_at"))),
                dt_sort_value(parse_dt(task.get("updated_at"))),
            ),
            reverse=True,
        )[0]
    latest_material_completed = None
    material_completed_rows = [task for task in rows if _is_material_completed(task)]
    if material_completed_rows:
        latest_material_completed = sorted(
            material_completed_rows,
            key=lambda task: (
                dt_sort_value(parse_dt(task.get("_terminal_at"))),
                dt_sort_value(parse_dt(task.get("_attempt_at"))),
                dt_sort_value(parse_dt(task.get("updated_at"))),
            ),
            reverse=True,
        )[0]
    return {
        "total": len(rows),
        "completed": sum(1 for status in statuses if status in terminal_success),
        "material_completed": material_completed,
        "retryable": sum(1 for status in statuses if status in retryable_failure),
        "unresolved": sum(1 for status in statuses if status in unresolved),
        "latest_task_id": latest.get("task_id") if isinstance(latest, dict) else None,
        "latest_status": latest.get("status") if isinstance(latest, dict) else None,
        "latest_updated_at": latest.get("updated_at") if isinstance(latest, dict) else None,
        "latest_created_at": latest.get("created_at") if isinstance(latest, dict) else None,
        "latest_attempt_at": latest.get("_attempt_at") if isinstance(latest, dict) else None,
        "latest_terminal_at": latest.get("_terminal_at") if isinstance(latest, dict) else None,
        "latest_manifest_path": latest.get("_manifest_path") if isinstance(latest, dict) else None,
        "latest_retryable_task_id": latest_retryable.get("task_id") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_status": latest_retryable.get("status") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_updated_at": latest_retryable.get("updated_at") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_created_at": latest_retryable.get("created_at") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_attempt_at": latest_retryable.get("_attempt_at") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_terminal_at": latest_retryable.get("_terminal_at") if isinstance(latest_retryable, dict) else None,
        "latest_retryable_manifest_path": latest_retryable.get("_manifest_path") if isinstance(latest_retryable, dict) else None,
        "latest_material_completed_task_id": latest_material_completed.get("task_id") if isinstance(latest_material_completed, dict) else None,
        "latest_material_completed_updated_at": latest_material_completed.get("updated_at") if isinstance(latest_material_completed, dict) else None,
        "latest_material_completed_created_at": latest_material_completed.get("created_at") if isinstance(latest_material_completed, dict) else None,
        "latest_material_completed_attempt_at": latest_material_completed.get("_attempt_at") if isinstance(latest_material_completed, dict) else None,
        "latest_material_completed_terminal_at": latest_material_completed.get("_terminal_at") if isinstance(latest_material_completed, dict) else None,
        "latest_material_completed_manifest_path": latest_material_completed.get("_manifest_path") if isinstance(latest_material_completed, dict) else None,
        "latest_is_material_completed": bool(isinstance(latest, dict) and _is_material_completed(latest)),
    }


def max_rounds_reached(config: dict[str, Any], item: dict[str, Any], state: dict[str, Any]) -> bool:
    """Return whether a standing item is temporarily capped.

    `max_rounds` is a per-burst safety fuse, not a permanent brake on the
    OpenClaw evolution standing agenda.  Alex clarified that automatic next
    rounds must continue after idle/cooldown unless an item is explicitly moved
    to a terminal status.  Therefore historical completed rounds no longer cap
    future epochs; only unresolved work or too many starts inside the current
    round window suppresses another task.

    Rounds that completed without material evidence (degraded_no_progress) do
    NOT count toward the max_rounds cap, because they represent a wasted round
    rather than genuine progress.  Counting them would allow empty rounds to
    suppress further autonomy work — exactly the opposite of what the autonomy
    loop requires.
    """
    max_rounds = item_max_rounds(config, item)
    if max_rounds < 0:
        return False
    item_id = str(item.get("id") or "")
    all_stats = standing_item_task_stats(item_id)
    if int(all_stats.get("unresolved") or 0) > 0:
        return True
    try:
        window_seconds = int(
            item.get("max_round_window_seconds")
            or config.get("max_round_window_seconds")
            or item.get("max_silence_seconds")
            or config.get("proactive_tick_interval_seconds")
            or 1800
        )
    except Exception:
        window_seconds = 1800
    window_seconds = max(60, window_seconds)
    since = now_dt() - timedelta(seconds=window_seconds)
    stats = standing_item_task_stats(item_id, since=since)
    if int(stats.get("material_completed") or 0) >= max_rounds:
        return True
    max_attempts_raw = item.get("max_attempts") if "max_attempts" in item else config.get("max_attempts_per_item")
    try:
        max_attempts = int(max_attempts_raw if max_attempts_raw is not None else max(max_rounds + 2, max_rounds))
    except Exception:
        max_attempts = max(max_rounds + 2, max_rounds)
    if max_attempts >= 0 and int(stats.get("total") or 0) >= max_attempts:
        return True
    return False


def max_round_capped_item_ids(config: dict[str, Any], state: dict[str, Any]) -> list[str]:
    capped: list[str] = []
    for item in config.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "open") not in {"open", "in_progress"}:
            continue
        if max_rounds_reached(config, item, state):
            capped.append(str(item.get("id") or ""))
    return capped


def advance_mainline_item(
    room_id: str,
    item_id: str,
    *,
    status: str | None = None,
    evidence_paths: list[str] | None = None,
    note: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agenda_path = ROOM / "rooms" / room_id / "mainline_agenda.json"
    agenda = read_json(agenda_path, {})
    if not isinstance(agenda, dict):
        return {"ok": False, "status": "agenda_read_error", "agenda_path": str(agenda_path), "tokens_printed": False}

    normalized_status = str(status or "").strip()
    if normalized_status and normalized_status not in MAINLINE_ALLOWED_STATUSES:
        return {
            "ok": False,
            "status": "invalid_mainline_status",
            "allowed_statuses": sorted(MAINLINE_ALLOWED_STATUSES),
            "tokens_printed": False,
        }

    active_items = agenda.get("active_items")
    if not isinstance(active_items, list):
        return {"ok": False, "status": "agenda_missing_active_items", "tokens_printed": False}

    item = find_mainline_item(active_items, item_id)
    if item is None:
        return {"ok": False, "status": "mainline_item_not_found", "item_id": item_id, "tokens_printed": False}

    changed_at = now_iso()
    previous_status = str(item.get("status") or "open")
    if normalized_status:
        item["status"] = normalized_status
    current_status = str(item.get("status") or previous_status or "open")

    existing_evidence = item.get("evidence_paths")
    if not isinstance(existing_evidence, list):
        existing_evidence = []
    deduped_evidence: list[str] = []
    for entry in existing_evidence:
        normalized = evidence_path(entry)
        if normalized and normalized not in deduped_evidence:
            deduped_evidence.append(normalized)

    added_evidence: list[str] = []
    for entry in evidence_paths or []:
        normalized = evidence_path(entry)
        if normalized and normalized not in deduped_evidence:
            deduped_evidence.append(normalized)
            added_evidence.append(normalized)
    item["evidence_paths"] = deduped_evidence
    item["updated_at"] = changed_at
    if note:
        item["status_note"] = note

    history = item.get("status_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "at": changed_at,
            "previous_status": previous_status,
            "status": current_status,
            "evidence_paths": added_evidence,
            "note": note,
            "source": source or {},
        }
    )
    item["status_history"] = history[-20:]
    agenda["updated_at"] = changed_at
    write_json(agenda_path, agenda)
    return {
        "ok": True,
        "status": "mainline_item_advanced",
        "room_id": room_id,
        "item_id": item_id,
        "mainline_status": current_status,
        "previous_status": previous_status,
        "evidence_paths_added": added_evidence,
        "agenda_path": str(agenda_path),
        "tokens_printed": False,
    }


def item_last_acceptance(item_id: str, state: dict[str, Any]) -> str | None:
    """Return the latest acceptance verdict for a standing agenda item, if any.

    Checks the collaboration ledger for the last task associated with this
    agenda item and reads the work-item acceptance status.
    Returns one of: 'accepted', 'rejected', 'superseded', 'pending', or None.
    """
    state_items = state.get("items") if isinstance(state.get("items"), dict) else {}
    item_state = state_items.get(item_id) if isinstance(state_items, dict) else {}
    if not isinstance(item_state, dict):
        return None
    last_task_id = str(item_state.get("last_discussed_task_id") or "")
    if not last_task_id:
        return None
    # Look for the collaboration ledger for this task
    ledger_dir = ROOM / "collaboration-ledgers"
    ledger_path = ledger_dir / f"{last_task_id}.json"
    if not ledger_path.exists():
        return None
    ledger = read_json(ledger_path)
    if not isinstance(ledger, dict):
        return None
    work_items = ledger.get("work_items")
    if not isinstance(work_items, list):
        return None
    # Check all work items for acceptance status; if any is accepted, return accepted
    verdicts = []
    for wi in work_items:
        if isinstance(wi, dict):
            v = str(wi.get("acceptance") or "").strip()
            if v:
                verdicts.append(v)
    if not verdicts:
        return None
    # If all accepted → accepted; if any rejected → rejected; else latest
    if all(v == "accepted" for v in verdicts):
        return "accepted"
    if any(v == "rejected" for v in verdicts):
        return "rejected"
    return verdicts[-1]


def mainline_item_terminal(room_id: str, item: dict[str, Any]) -> bool:
    active_item = matching_mainline_item(room_id, item)
    return str(active_item.get("status") or "").strip() in MAINLINE_TERMINAL_STATUSES


def failure_recovery_policy(config: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for raw in (config.get("failure_recovery"), item.get("failure_recovery")):
        if isinstance(raw, dict):
            policy.update(raw)
    return policy


def int_policy_value(policy: dict[str, Any], keys: tuple[str, ...], default: int, minimum: int = 0) -> int:
    for key in keys:
        if key not in policy:
            continue
        try:
            return max(minimum, int(policy.get(key)))
        except Exception:
            continue
    return default


def failure_recovery_state(config: dict[str, Any], item: dict[str, Any], now: datetime) -> dict[str, Any]:
    state: dict[str, Any] = {
        "enabled": False,
        "due": False,
        "reason": "disabled_or_unconfigured",
        "latest_status": "",
        "latest_status_at": None,
        "retryable": 0,
        "unresolved": 0,
        "retry_after_seconds": None,
        "remaining_seconds": None,
        "latest_terminal_at": None,
    }
    policy = failure_recovery_policy(config, item)
    if not policy.get("enabled", False):
        return state
    state["enabled"] = True
    item_id = str(item.get("id") or "")
    if not item_id:
        state["reason"] = "item_id_missing"
        return state
    stats = standing_item_task_stats(item_id)
    try:
        state["retryable"] = int(stats.get("retryable") or 0)
    except Exception:
        state["retryable"] = 0
    try:
        state["unresolved"] = int(stats.get("unresolved") or 0)
    except Exception:
        state["unresolved"] = 0
    try:
        state["degraded_rounds"] = int(stats.get("completed") or 0) - int(stats.get("material_completed") or 0)
    except Exception:
        state["degraded_rounds"] = 0
    latest_status = str(stats.get("latest_status") or "").strip().lower()
    state["latest_status"] = latest_status
    if int(stats.get("unresolved") or 0) > 0:
        state["reason"] = "unresolved_retryable_work"
        return state
    if latest_status not in RETRYABLE_STANDING_STATUSES:
        # If the most recent manifest is a completed round but older blocked /
        # failed work still exists, recovery is still due.  Otherwise a quick
        # successful follow-up can mask an unresolved failed mainline and the
        # lane drops back to idle/suppressed instead of producing an RCA or
        # retry.  Use the latest retryable terminal timestamp for the delay.
        # Additionally, a completed round with degraded_no_progress (no material
        # evidence) is effectively a failed round for recovery purposes: the
        # autonomy loop should force material closure, not accept empty rounds.
        material_completed = int(stats.get("material_completed") or 0)
        total_completed = int(stats.get("completed") or 0)
        degraded_rounds = total_completed - material_completed
        if int(stats.get("retryable") or 0) <= 0 and degraded_rounds <= 0:
            state["reason"] = "latest_status_not_retryable_and_no_retryable_history"
            return state
        latest_material_at = parse_dt(
            stats.get("latest_material_completed_terminal_at")
            or stats.get("latest_material_completed_attempt_at")
            or stats.get("latest_material_completed_updated_at")
            or stats.get("latest_material_completed_created_at")
        )
        latest_retryable_at = parse_dt(
            stats.get("latest_retryable_terminal_at")
            or stats.get("latest_retryable_attempt_at")
            or stats.get("latest_retryable_updated_at")
            or stats.get("latest_retryable_created_at")
        )
        if (
            int(stats.get("retryable") or 0) > 0
            and bool(stats.get("latest_is_material_completed"))
            and latest_material_at is not None
            and latest_retryable_at is not None
            and latest_material_at >= latest_retryable_at
        ):
            state["latest_status_at"] = latest_material_at.isoformat(timespec="seconds")
            state["latest_terminal_at"] = latest_material_at.isoformat(timespec="seconds")
            state["reason"] = "latest_material_completed_after_retryable_history"
            return state
        if degraded_rounds > 0 and int(stats.get("retryable") or 0) <= 0:
            # Treat degraded_no_progress as retryable: override latest_status
            # so the recovery timer starts from the latest terminal timestamp.
            state["reason"] = "degraded_no_progress_recovery"
    retry_after = int_policy_value(
        policy,
        ("retry_after_seconds", "retry_after_failure_seconds"),
        default=900,
        minimum=60,
    )
    state["retry_after_seconds"] = retry_after
    latest_at = parse_dt(
        stats.get("latest_retryable_terminal_at")
        or stats.get("latest_retryable_attempt_at")
        or stats.get("latest_retryable_updated_at")
        or stats.get("latest_retryable_created_at")
        or stats.get("latest_terminal_at")
        or stats.get("latest_attempt_at")
        or stats.get("latest_updated_at")
        or stats.get("latest_created_at")
    )
    if not latest_at:
        state["reason"] = "missing_retryable_terminal_timestamp"
        return state
    state["latest_status_at"] = latest_at.isoformat(timespec="seconds")
    state["latest_terminal_at"] = latest_at.isoformat(timespec="seconds")
    remaining = int(retry_after - (now - latest_at).total_seconds())
    if remaining <= 0:
        state["due"] = True
        state["remaining_seconds"] = 0
        state["reason"] = "failure_recovery_due"
        return state
    state["remaining_seconds"] = remaining
    state["reason"] = "failure_recovery_wait"
    return state


def failure_recovery_due(config: dict[str, Any], item: dict[str, Any], now: datetime) -> bool:
    return bool(failure_recovery_state(config, item, now).get("due"))


def standing_item_selection_record(
    room_id: str,
    config: dict[str, Any],
    state: dict[str, Any],
    item: dict[str, Any],
    now: datetime,
    *,
    bypass_cooldown: bool = False,
) -> dict[str, Any]:
    item_id = str(item.get("id") or "")
    status = str(item.get("status") or "open").strip()
    priority = int(item.get("priority") or 0)
    global_interval = int(config.get("proactive_tick_interval_seconds") or 3600)
    state_items = state.get("items") if isinstance(state.get("items"), dict) else {}
    item_state = state_items.get(item_id) if isinstance(state_items, dict) else {}
    last_discussed = parse_dt(item_state.get("last_discussed_at") if isinstance(item_state, dict) else None) or parse_dt(item.get("last_discussed_at"))
    max_silence_raw = item.get("max_silence_seconds")
    max_silence = int(global_interval if max_silence_raw is None else max_silence_raw)
    seconds_since_last = int((now - last_discussed).total_seconds()) if last_discussed else None
    recovery_state = failure_recovery_state(config, item, now)
    recovery_due = bool(recovery_state.get("due"))
    global_cooldown_active = (not bypass_cooldown) and cooldown_remaining_seconds(config, state) > 0
    mainline_item = matching_mainline_item(room_id, item)
    mainline_status = str(mainline_item.get("status") or "").strip()
    mainline_attention, mainline_attention_reason = mainline_attention_rank(mainline_status)
    accepted = item_last_acceptance(item_id, state) == "accepted"
    capped = max_rounds_reached(config, item, state)

    # Failure recovery is a health-loop override.  A previous implementation let
    # the max-round safety fuse run before the recovery check, which meant a
    # standing item with failed/partial terminal evidence could be permanently
    # suppressed as ``standing_agenda_suppressed_max_rounds`` instead of creating
    # an explicit RCA/retry work item.  That is exactly the unhealthy idle gap
    # Alex reported: mainline work is unfinished, agents look idle, and the
    # system does not surface a blocker or retry.
    capped_for_selection = capped and not recovery_due

    due = False
    reason = "due"
    if status not in {"open", "in_progress"}:
        reason = "item_inactive"
    elif mainline_status in MAINLINE_TERMINAL_STATUSES:
        reason = "mainline_terminal"
    elif capped_for_selection:
        reason = "max_rounds_or_unresolved_work"
    elif global_cooldown_active and not recovery_due and mainline_attention == 0:
        reason = "global_cooldown_active"
    elif accepted and mainline_attention == 0:
        reason = "last_work_item_accepted"
    elif not recovery_due and last_discussed and seconds_since_last is not None and seconds_since_last < max_silence:
        reason = "silence_window_not_elapsed"
    else:
        due = True
        if recovery_due:
            reason = "failure_recovery_due"
        elif last_discussed:
            reason = "max_silence_elapsed"
        else:
            reason = "never_discussed"

    return {
        "item_id": item_id,
        "mainline_item_id": str(item.get("mainline_item_id") or item.get("mainline_agenda_item_id") or item_id),
        "title": str(item.get("title") or item_id),
        "priority": priority,
        "status": status,
        "mainline_status": mainline_status,
        "mainline_attention": mainline_attention,
        "mainline_attention_reason": mainline_attention_reason,
        "due": due,
        "reason": reason,
        "recovery_due": recovery_due,
        "failure_recovery_state": recovery_state,
        "global_cooldown_active": global_cooldown_active,
        "accepted": accepted,
        "max_rounds_reached": capped,
        "max_rounds_bypassed_for_recovery": bool(capped and recovery_due),
        "max_silence_seconds": max_silence,
        "seconds_since_last_discussed": seconds_since_last,
        "last_discussed_at": last_discussed.isoformat(timespec="seconds") if last_discussed else None,
    }


def standing_item_selection_records(
    room_id: str,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    bypass_cooldown: bool = False,
) -> list[dict[str, Any]]:
    now = now_dt()
    records: list[dict[str, Any]] = []
    for item in config.get("items") or []:
        if isinstance(item, dict):
            records.append(standing_item_selection_record(room_id, config, state, item, now, bypass_cooldown=bypass_cooldown))
    return records


def standing_item_selection_snapshot(
    room_id: str,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    bypass_cooldown: bool = False,
) -> dict[str, Any]:
    records = standing_item_selection_records(room_id, config, state, bypass_cooldown=bypass_cooldown)
    for record in records:
        recovery_due = bool(record.get("recovery_due") or record.get("reason") == "failure_recovery_due")
        try:
            mainline_attention = int(record.get("mainline_attention") or 0)
        except Exception:
            mainline_attention = 0
        try:
            priority = int(record.get("priority") or 0)
        except Exception:
            priority = 0
        try:
            raw_silence = record.get("seconds_since_last_discussed")
            # Never-discussed items have the longest effective silence; treat as
            # a very large value so they rank above recently-discussed items in
            # the reverse sort rather than being penalised with silence_age == 0.
            silence_age = int(raw_silence) if raw_silence is not None else 999_999_999
        except Exception:
            silence_age = 0
        if recovery_due:
            selection_class = "failure_recovery"
        elif mainline_attention > 0:
            selection_class = "mainline_blocker"
        else:
            selection_class = "proactive_due"
        record["selection_class"] = selection_class
        record["selection_rank"] = {
            "failure_recovery_first": 1 if recovery_due else 0,
            "mainline_attention": mainline_attention,
            "priority": priority,
            "seconds_since_last_discussed": silence_age,
        }
    due_records = sorted(
        [record for record in records if record.get("due")],
        key=lambda record: (
            int(((record.get("selection_rank") or {}).get("failure_recovery_first")) or 0),
            int(((record.get("selection_rank") or {}).get("mainline_attention")) or 0),
            int(((record.get("selection_rank") or {}).get("priority")) or 0),
            int(((record.get("selection_rank") or {}).get("seconds_since_last_discussed")) or 0),
            str(record.get("item_id") or ""),
        ),
        reverse=True,
    )
    selected = due_records[0] if due_records else None
    considered_items = list(records[:12])
    if selected is not None and str(selected.get("item_id") or "") not in {
        str(record.get("item_id") or "") for record in considered_items
    }:
        considered_items.append(selected)
    return {
        "schema": "openclaw.agent_room.standing_item_selection.v0",
        "policy": "failure_recovery_then_mainline_blocker_then_highest_priority_due_item_after_fresh_user_active_runner_pending_task_gates",
        "selected_item_id": selected.get("item_id") if selected else None,
        "selected_reason": selected.get("reason") if selected else None,
        "selected_priority": selected.get("priority") if selected else None,
        "selected_selection_class": selected.get("selection_class") if selected else None,
        "due_item_ids": [str(record.get("item_id") or "") for record in due_records],
        "suppressed_count": len([record for record in records if not record.get("due")]),
        "considered_items": considered_items,
        "tokens_printed": False,
    }


def due_items(
    room_id: str,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    bypass_cooldown: bool = False,
) -> list[dict[str, Any]]:
    selection = standing_item_selection_snapshot(room_id, config, state, bypass_cooldown=bypass_cooldown)
    due_order = [str(item_id) for item_id in selection.get("due_item_ids") or []]
    due_ids = set(due_order)
    due_index = {item_id: index for index, item_id in enumerate(due_order)}
    due: list[dict[str, Any]] = []
    for item in config.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id in due_ids:
            due.append(item)
    return sorted(due, key=lambda item: due_index.get(str(item.get("id") or ""), len(due_index)))


def mainline_item_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()

    def add(raw: Any) -> None:
        text = str(raw or "").strip()
        if text:
            keys.add(text)

    add(item.get("id"))
    add(item.get("mainline_id"))
    add(item.get("mainline_item_id"))
    add(item.get("mainline_agenda_item_id"))
    for field in ("aliases", "standing_aliases", "standing_item_ids", "legacy_ids"):
        raw = item.get(field)
        if isinstance(raw, list):
            for entry in raw:
                add(entry)
        else:
            add(raw)
    return keys


def find_mainline_item(active_items: Any, item_id: str) -> dict[str, Any] | None:
    wanted = str(item_id or "").strip()
    if not wanted or not isinstance(active_items, list):
        return None
    for candidate in active_items:
        if isinstance(candidate, dict) and wanted in mainline_item_keys(candidate):
            return candidate
    return None


def matching_mainline_item(room_id: str, item: dict[str, Any] | str) -> dict[str, Any]:
    agenda = read_json(ROOM / "rooms" / room_id / "mainline_agenda.json", {})
    if not isinstance(agenda, dict):
        return {}
    if isinstance(item, dict):
        lookup_ids = [
            str(item.get("mainline_item_id") or ""),
            str(item.get("mainline_agenda_item_id") or ""),
            str(item.get("id") or ""),
        ]
    else:
        lookup_ids = [str(item or "")]
    wanted = {item_id for item_id in lookup_ids if item_id}
    active_items = agenda.get("active_items") or []
    for item_id in wanted:
        found = find_mainline_item(active_items, item_id)
        if found is not None:
            return found
    return {}


def build_brief(room_id: str, item: dict[str, Any], active_item: dict[str, Any]) -> str:
    item_id = str(item.get("id") or "")
    title = str(item.get("title") or item_id or "Standing agenda item")
    description = str(item.get("description") or "")
    work_item = str(active_item.get("work_item") or item.get("work_item") or description)
    acceptance = active_item.get("acceptance_evidence") or item.get("acceptance_evidence") or []
    must_not = active_item.get("must_not_displace") or item.get("must_not_displace") or []
    lines = [
        "# Standing mainline discussion task",
        "",
        "你正在参与 OpenClaw 进化群的常态主线协作。本任务由 standing agenda 在安静期自动创建，不是 Alex 新发的一次性问题。",
        "",
        f"Room: {room_id}",
        f"Agenda item: {title}",
        "",
        "## Why this exists",
        description,
        "",
        "## Concrete work item",
        work_item,
        "",
        "## Acceptance evidence",
    ]
    lines.extend(f"- {entry}" for entry in acceptance)
    lines.extend([
        "",
        "## Required closure artifact",
        "- 本 standing task 不能只停留在“讨论过”。必须至少产出一个可验收物：patch、smoke、RCA、具体 blocker、或下一步任务切分。",
        "- 如果结论来自 peer/Windows Codex/openclaw-main，必须批判性引用或挑战一个具体 claim；任何 agent 的判断都不是权威。",
        "- 对安全、可逆、本地的 scoped patch/config/artifact/smoke，不要以“我可以修改/要不要执行”结尾；先与其它 agent 互审，能决定就执行并留下证据。",
        "- 如果无法完成，写明 root cause、缺的证据、谁接手、何时/什么条件下重试。",
        "",
        "## Boundaries",
        "- Use Chinese for any visible room-facing conclusion.",
        "- Do not send Telegram directly, publish Notion, push GitHub, read secrets, or change production workflow entrypoints.",
        "- Keep Translation Agent, People Daily, market/report workflows, provider gates, and publication gates intact.",
        "- If another agent is unavailable, continue safe local work and record degraded-quorum evidence.",
        "- Output NO_COMMENT only if you have no material evidence, patch, smoke, blocker, or next action.",
        "",
        "## Must not displace",
    ])
    lines.extend(f"- {entry}" for entry in must_not)
    return "\n".join(lines).rstrip() + "\n"


def create_task(
    room_id: str,
    item: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    selection_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_id = str(item.get("id") or "standing-item")
    created = now_iso()

    # --- Dedupe check (governance contract §4): skip if identical open task exists ---
    raw_dedupe_key = str(item.get("dedupe_key") or item.get("mainline_item_id") or item_id)
    existing_tasks = read_jsonl(TASKS_JSONL)
    for existing in reversed(existing_tasks[-100:]):
        existing_task_id = str(existing.get("task_id") or existing.get("run_id") or "")
        manifest_existing = read_json(ROOM / "tasks" / existing_task_id / "manifest.json", {}) if existing_task_id else {}
        if isinstance(manifest_existing, dict) and manifest_existing:
            existing = manifest_existing
        existing_status = str(existing.get("status") or "").strip().lower()
        existing_governance_state = str(existing.get("governance_state") or (existing.get("governance") or {}).get("state") or "").strip().lower()
        if existing_status in TASK_TERMINAL_STATUSES or existing_governance_state in GOVERNANCE_TERMINAL_STATES:
            continue
        existing_dedupe = None
        eg = existing.get("governance") if isinstance(existing.get("governance"), dict) else {}
        existing_dedupe = eg.get("dedupe_key") or existing.get("dedupe_key")
        if existing_dedupe and existing_dedupe == raw_dedupe_key:
            return {
                "created": False,
                "dedupe_merged": True,
                "status": "merged",
                "task_id": str(existing.get("task_id") or ""),
                "dedupe_key": raw_dedupe_key,
                "reason": f"duplicate of existing unresolved task {existing.get('task_id')} with same dedupe_key={raw_dedupe_key}",
            }

    digest = hashlib.sha256(
        json.dumps({"room_id": room_id, "item_id": item_id, "created_at": created}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    task_id = f"standing-{compact_slug(room_id)}-{compact_slug(item_id)}-{digest}"
    run_id = task_id
    targets = [
        str(agent_id)
        for agent_id in (item.get("target_agents") or config.get("target_agents") or ["codex", "claude-code"])
        if str(agent_id) in LOCAL_RUNTIME_AGENTS
    ] or ["codex", "claude-code"]
    agenda_path = ROOM / "rooms" / room_id / "mainline_agenda.json"
    active_item = matching_mainline_item(room_id, item)
    if selection_record is None:
        selection_record = standing_item_selection_record(room_id, config, state, item, now_dt())
    max_rounds = item_max_rounds(config, item)
    round_index = standing_item_round_count(item_id, state) + 1
    tick_policy: dict[str, Any] = {}
    for raw_tick in (config.get("standing_collaboration_tick"), item.get("collaboration_tick")):
        if isinstance(raw_tick, dict):
            tick_policy.update(raw_tick)
    tick_enabled = bool(tick_policy.get("enabled"))
    try:
        tick_max_rounds = int(tick_policy.get("max_rounds") if tick_policy.get("max_rounds") is not None else max_rounds)
    except Exception:
        tick_max_rounds = max_rounds
    tick_max_rounds = max(1, tick_max_rounds)
    task_dir = ROOM / "tasks" / task_id
    brief_path = task_dir / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(build_brief(room_id, item, active_item), encoding="utf-8")

    governance = build_task_governance(room_id, item, active_item, targets, raw_dedupe_key)
    standing_artifacts, standing_artifact_errors = create_standing_artifact_hooks(item, created)
    standing_artifact_paths = [
        str(artifact.get("path") or "")
        for artifact in standing_artifacts
        if isinstance(artifact, dict) and str(artifact.get("path") or "")
    ]

    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": run_id,
        "room_id": room_id,
        "requested_by": "agent-room-standing-mainline",
        "target_agents": targets,
        "delivery_policy": "standing_mainline_material_summary",
        "reply_policy": "agents_continue_open_mainline_until_done_blocked_or_decision_needed",
        "lane": "standing_mainline_discussion",
        "brief_path": str(brief_path),
        "context_paths": [str(CONFIG), str(agenda_path)],
        "permissions": {
            "source_edit": True,
            "telegram_send": False,
            "notion_publish": False,
            "github_push": False,
            "secrets_access": False,
            "global_state_change": True,
            "quality_surface_change": False,
        },
        "agent_room_profile": "standing-mainline-discussion",
        "expected_outputs": ["patch_or_artifact_or_smoke_or_blocker_or_material_room_comment"],
        "status": "queued",
        "review_status": "requested",
        "blocked_reason": None,
        "result_paths": standing_artifact_paths,
        "artifacts": standing_artifacts,
        "canonical_imported": True,
        "canonical_state_advanced": True,
        "created_at": created,
        "updated_at": created,
        "canonical_imported_at": created,
        "lease": {"owner": None, "heartbeat_at": None, "expires_at": None},
        "heartbeat": {"last_seen_at": None},
        "retry_budget": {"max_attempts": 1, "attempt": 0},
        "manual_boundary": True,
        "quality_gate_status": "not_applicable",
        "side_effect_gate_status": "closed",
        "telegram_projection_status": "room_bridge_gate_only",
        "standing_visible_allowed": bool(item.get("standing_visible_allowed", True)),
        "governance": governance,
        "mainline_id": governance["mainline_id"],
        "problem_statement": governance["problem_statement"],
        "expected_user_value": governance["expected_user_value"],
        "owner": governance["owner"],
        "participants": governance["participants"],
        "definition_of_done": governance["definition_of_done"],
        "approval_gate": governance["approval_gate"],
        "dedupe_key": governance["dedupe_key"],
        "next_action": governance["next_action"],
        "governance_state": governance["state"],
        "governance_contract_path": GOVERNANCE_CONTRACT_PATH,
        "drift_check_passed": bool(governance.get("drift_check_passed")),
        "standing_mainline": {
            "schema": "openclaw.agent_room.standing_mainline.v0",
            "item_id": item_id,
            "linked_mainline_item_id": active_item.get("id"),
            "agenda_path": str(agenda_path),
            "visibility_policy": "material_progress_only",
            "selection": selection_record,
        },
        "standing_agenda": {
            "schema": "openclaw.agent_room.standing_agenda_task.v0",
            "item_id": item_id,
            "priority": item.get("priority"),
            "max_rounds": max_rounds,
            "round": round_index,
            "selection_reason": selection_record.get("reason") if isinstance(selection_record, dict) else None,
        },
        "collaboration_tick": {
            "enabled": tick_enabled,
            "max_rounds": tick_max_rounds,
            "scope": "standing_mainline_task_local",
            "reason": "standing_tasks_must_close_with_peer_followup_or_clear_blocker",
        },
        "collab_tick_enabled": tick_enabled,
        "collab_tick_max_rounds": tick_max_rounds if tick_enabled else None,
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "mode": "standing_mainline_discussion",
            "status": "open",
            "participants": targets,
            "work_items": [
                {
                    "id": f"{item_id}_{compact_slug(agent_id)}",
                    "status": "open",
                    "assigned_to": agent_id,
                    "description": str(item.get("description") or item.get("title") or item_id),
                }
                for agent_id in targets
            ],
            "claims": [],
            "handoffs": [],
            "artifacts": standing_artifacts,
            "blockers": [],
            "max_rounds": max(max_rounds, tick_max_rounds),
            "created_at": created,
        },
        "source": {
            "transport": "agent-room-standing-mainline",
            "chat_id": room_chat_id(room_id),
            "update_id": f"standing-mainline:{room_id}:{item_id}:{digest}",
            "message_text_sha256": hashlib.sha256(str(item).encode("utf-8")).hexdigest(),
        },
    }
    if standing_artifact_errors:
        task["standing_artifact_hook_errors"] = standing_artifact_errors
    write_json(task_dir / "manifest.json", task)
    append_jsonl(TASKS_JSONL, [task])
    append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])

    state_items = state.setdefault("items", {})
    if isinstance(state_items, dict):
        state_items[item_id] = {
            "last_discussed_at": created,
            "last_discussed_task_id": task_id,
            "rounds_created": round_index,
        }
    state["last_injected_at"] = created
    state["pending_task"] = {
        "task_id": task_id,
        "run_id": run_id,
        "item_id": item_id,
        "target_agents": targets,
        "created_at": created,
        "round": round_index,
    }
    state["updated_at"] = created
    write_json(STATE, state)
    if active_item.get("id"):
        current_status = str(active_item.get("status") or "open")
        next_status = "in_progress" if current_status == "open" else current_status
        advance_mainline_item(
            room_id,
            str(active_item.get("id")),
            status=next_status,
            evidence_paths=[str(task_dir / "manifest.json"), str(brief_path), *standing_artifact_paths],
            note=f"standing agenda task created for {item_id}",
            source={"tool": "standing_agenda_tick.py", "task_id": task_id, "run_id": run_id},
        )
    return {
        "created": True,
        "task_id": task_id,
        "run_id": run_id,
        "room_id": room_id,
        "item_id": item_id,
        "target_agents": targets,
        "manifest_path": str(task_dir / "manifest.json"),
        "brief_path": str(brief_path),
        "standing_artifacts": standing_artifacts,
        "standing_artifact_hook_errors": standing_artifact_errors,
        "selection_record": selection_record,
    }


def post_completion_idle_rescan_delay_seconds(config: dict[str, Any]) -> int | None:
    if "post_completion_idle_rescan_seconds" not in config:
        return None
    try:
        delay = int(config.get("post_completion_idle_rescan_seconds"))
    except Exception:
        return None
    return delay if delay >= 0 else None


def post_completion_idle_rescan_state(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Return whether a resolved standing task should shorten quiet-period cooldown.

    A standing mainline task has two independent brakes: only one pending task at
    a time, and a quiet-period cooldown between new tasks.  The cooldown is too
    coarse after a pending task has just reached terminal/reply-artifact state:
    it leaves active mainline items idle even though the one-at-a-time gate has
    already cleared.  This explicit policy allows a bounded post-completion
    rescan while keeping fresh-user-task, active-runner, and pending-task gates.
    """
    delay = post_completion_idle_rescan_delay_seconds(config)
    if delay is None:
        return {"enabled": False, "due": False, "reason": "post_completion_idle_rescan_unset"}
    resolved = state.get("last_resolved_pending_task")
    if not isinstance(resolved, dict):
        return {"enabled": True, "due": False, "remaining_seconds": delay, "reason": "no_resolved_pending_task"}
    resolved_at = parse_dt(resolved.get("resolved_at"))
    if not resolved_at:
        return {"enabled": True, "due": False, "remaining_seconds": delay, "reason": "missing_resolved_at"}
    last_injected = parse_dt(state.get("last_injected_at"))
    if last_injected and last_injected > resolved_at:
        return {
            "enabled": True,
            "due": False,
            "remaining_seconds": delay,
            "reason": "resolved_task_already_followed_by_new_injection",
            "task_id": resolved.get("task_id"),
        }
    elapsed = max(0, int((now_dt() - resolved_at).total_seconds()))
    remaining = max(0, delay - elapsed)
    return {
        "enabled": True,
        "due": remaining == 0,
        "remaining_seconds": remaining,
        "delay_seconds": delay,
        "task_id": resolved.get("task_id"),
        "resolved_at": resolved.get("resolved_at"),
        "reason": "post_completion_idle_rescan_due" if remaining == 0 else "post_completion_idle_rescan_waiting",
    }


def immediate_rescan_after_resolved_pending(config: dict[str, Any]) -> bool:
    delay = post_completion_idle_rescan_delay_seconds(config)
    return delay == 0


def tick(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(CONFIG, {})
    if not isinstance(config, dict):
        return {"ok": False, "status": "config_read_error", "config_path": str(CONFIG), "tokens_printed": False}
    state = read_json(STATE, {"schema": "openclaw.agent_room.standing_agenda_state.v0"})
    if not isinstance(state, dict):
        state = {"schema": "openclaw.agent_room.standing_agenda_state.v0"}
    try:
        reconcile_limit = int(config.get("standing_reconcile_limit") or 50)
    except Exception:
        reconcile_limit = 50
    try:
        reconcile_max_age = int(config.get("standing_reconcile_max_age_seconds") or 7200)
    except Exception:
        reconcile_max_age = 7200
    try:
        dead_runner_grace = int(config.get("standing_dead_runner_grace_seconds") or os.environ.get("AGENT_ROOM_STANDING_DEAD_RUNNER_GRACE_SECONDS", "60"))
    except Exception:
        dead_runner_grace = 60
    reconciled = reconcile_standing_task_statuses(
        max_age_seconds=reconcile_max_age,
        limit=reconcile_limit,
        dry_run=bool(args.dry_run),
        dead_runner_grace_seconds=dead_runner_grace,
    )
    try:
        expired_claim_limit = int(
            config.get("expired_collaboration_claim_reconcile_limit")
            or os.environ.get("AGENT_ROOM_EXPIRED_COLLAB_CLAIM_RECONCILE_LIMIT", "50")
        )
    except Exception:
        expired_claim_limit = 50
    expired_collaboration_claims = reconcile_expired_collaboration_claims(
        limit=expired_claim_limit,
        dry_run=bool(args.dry_run),
    )

    def with_reconciliations(result: dict[str, Any]) -> dict[str, Any]:
        result.setdefault("reconciled_standing_tasks", reconciled)
        result["expired_collaboration_claims"] = expired_collaboration_claims
        return result

    if getattr(args, "reconcile_only", False):
        return with_reconciliations({"ok": True, "status": "reconciled_only", "created": False, "tokens_printed": False})
    if not config.get("enabled", False) or env_disabled():
        return with_reconciliations({"ok": True, "status": "disabled", "created": False, "tokens_printed": False})
    fresh_count = args.fresh_task_count
    if fresh_count is None:
        max_age = int(os.environ.get("AGENT_ROOM_STANDING_MAINLINE_USER_TASK_FRESH_SECONDS", "300"))
        fresh_count = fresh_user_task_count(max_age)
    if int(fresh_count or 0) > 0:
        return with_reconciliations({"ok": True, "status": "suppressed_fresh_user_task", "created": False, "fresh_task_count": fresh_count, "tokens_printed": False})
    runner_count = args.active_runner_count
    if runner_count is None:
        runner_count = active_runner_count()
    material_state = material_stall_info_for_output(material_stall_active_runner_info())
    active_runner_stall_threshold = int(
        os.environ.get("AGENT_ROOM_STANDING_ACTIVE_RUNNER_STALL_SECONDS", "120")
    )
    runner_stalled_without_progress = active_runners_stalled_past_threshold(
        material_state,
        active_runner_stall_threshold,
    )
    if int(runner_count or 0) > 0 and not runner_stalled_without_progress:
        return with_reconciliations({
            "ok": True,
            "status": "suppressed_active_runner",
            "created": False,
            "active_runner_count": runner_count,
            "active_runner_material_state": material_state,
            "tokens_printed": False,
        })
    pending, pending_task_id = pending_standing_task(state)
    if pending:
        return {
            "ok": True,
            "status": "suppressed_pending_standing_task",
            "created": False,
            "pending_task_id": pending_task_id,
            "reconciled_standing_tasks": reconciled,
            "expired_collaboration_claims": expired_collaboration_claims,
            "tokens_printed": False,
        }
    resolved_pending_task_id = None
    bypass_cooldown = bool(getattr(args, "bypass_cooldown", False))
    if pending_task_id and isinstance(state.get("pending_task"), dict):
        resolved_pending_task_id = pending_task_id
        bypass_cooldown = bypass_cooldown or immediate_rescan_after_resolved_pending(config)
        if not args.dry_run:
            resolved_at = now_iso()
            state["last_resolved_pending_task"] = {
                "task_id": pending_task_id,
                "resolved_at": resolved_at,
                "source": "standing_agenda_tick.py",
                "immediate_rescan": bypass_cooldown,
            }
            state.pop("pending_task", None)
            state["updated_at"] = resolved_at
            write_json(STATE, state)
    post_completion_rescan = post_completion_idle_rescan_state(config, state)
    if not bypass_cooldown and post_completion_rescan.get("due") is True:
        bypass_cooldown = True

    def with_resolved(result: dict[str, Any]) -> dict[str, Any]:
        if resolved_pending_task_id:
            result["resolved_pending_task_id"] = resolved_pending_task_id
        if post_completion_rescan.get("enabled"):
            result["post_completion_idle_rescan"] = post_completion_rescan
        if autonomy_evolution_snapshot:
            result["autonomy_evolution_snapshot"] = autonomy_evolution_snapshot
        return with_reconciliations(result)

    autonomy_evolution_snapshot: dict[str, Any] | None = None
    selection = standing_item_selection_snapshot(args.room_id, config, state, bypass_cooldown=bypass_cooldown)
    autonomy_evolution_snapshot = record_autonomy_evolution_snapshot(args.room_id, config, state, selection)
    items = due_items(args.room_id, config, state, bypass_cooldown=bypass_cooldown)
    cooldown_remaining = 0 if bypass_cooldown else cooldown_remaining_seconds(config, state)
    if not items and cooldown_remaining > 0:
        return with_resolved({
            "ok": True,
            "status": "suppressed_cooldown",
            "created": False,
            "cooldown_remaining_seconds": cooldown_remaining,
            "selection": selection,
            "reconciled_standing_tasks": reconciled,
            "tokens_printed": False,
        })
    if not items:
        capped = max_round_capped_item_ids(config, state)
        if capped:
            return with_resolved({
                "ok": True,
                "status": "suppressed_max_rounds",
                "created": False,
                "capped_item_ids": capped,
                "selection": selection,
                "reconciled_standing_tasks": reconciled,
                "tokens_printed": False,
            })
        return with_resolved({"ok": True, "status": "no_due_agenda_item", "created": False, "selection": selection, "reconciled_standing_tasks": reconciled, "tokens_printed": False})
    if args.dry_run:
        item = items[0]
        return with_resolved({
            "ok": True,
            "status": "would_create",
            "created": False,
            "item_id": item.get("id"),
            "target_agents": item.get("target_agents"),
            "selection": selection,
            "reconciled_standing_tasks": reconciled,
            "tokens_printed": False,
        })
    selected_record = None
    selected_item_id = str((selection.get("selected_item_id") if isinstance(selection, dict) else "") or "")
    for record in selection.get("considered_items") or []:
        if isinstance(record, dict) and str(record.get("item_id") or "") == selected_item_id:
            selected_record = record
            break
    created = create_task(args.room_id, items[0], config, state, selection_record=selected_record)
    created["ok"] = True
    if created.get("dedupe_merged"):
        created["status"] = "merged"
        created["governance_state"] = "merged"
    else:
        created["status"] = "created"
    created["selection"] = selection
    created["reconciled_standing_tasks"] = reconciled
    created["tokens_printed"] = False
    return with_resolved(created)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create at most one bounded standing Agent Room mainline task.")
    parser.add_argument("--room-id", default="openclaw-evolution")
    parser.add_argument("--fresh-task-count", type=int, default=None)
    parser.add_argument("--active-runner-count", type=int, default=None)
    parser.add_argument("--reconcile-only", action="store_true", help="Only reconcile existing standing task state; never create a new task.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--advance-mainline-item", default=None)
    parser.add_argument("--status", choices=sorted(MAINLINE_ALLOWED_STATUSES), default=None)
    parser.add_argument("--evidence-path", action="append", default=[])
    parser.add_argument("--note", default=None)
    parser.add_argument("--source-agent", default=None)
    parser.add_argument("--source-run-id", default=None)
    parser.add_argument("--bypass-cooldown", action="store_true", help="Skip cooldown check; used after harvest detects completed runners and system is idle.")
    args = parser.parse_args()
    if args.advance_mainline_item:
        source = {"tool": "standing_agenda_tick.py"}
        if args.source_agent:
            source["agent_id"] = args.source_agent
        if args.source_run_id:
            source["run_id"] = args.source_run_id
        result = advance_mainline_item(
            args.room_id,
            args.advance_mainline_item,
            status=args.status,
            evidence_paths=args.evidence_path,
            note=args.note,
            source=source,
        )
    else:
        result = tick(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
