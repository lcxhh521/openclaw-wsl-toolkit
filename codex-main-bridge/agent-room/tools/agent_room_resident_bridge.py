#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import shutil
import shlex

from agent_room_detection_shared import idle_agent_contribution_problem_requested

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TOOLS = ROOM / "tools"
STATE = ROOM / "telegram_agent_bridge_poll_state.json"
RUNS = ROOM / "resident-runs"
ACTIVE_RUNNERS = ROOM / "active-runners"
FINISHED_RUNNERS = ROOM / "finished-runners"
COLLABORATION_STATUS = ROOM / "collaboration-status"
LOCAL_RUNTIME_AGENTS = {"codex", "claude-code"}
INTERNAL_AGENT_ROOM_TRANSPORTS = {
    "agent-room-collab-followup",
    "agent-room-bot-mention",
    "agent-room-runtime-takeover",
    "agent-room-context-rebase",
    "agent-room-proactive-mainline",
    "agent-room-standing-mainline",
    "agent_room_inject_message",
}
DEDICATED_COLLAB_TASK_TRANSPORTS = {
    "agent-room-collab-followup",
    "agent-room-bot-mention",
    "agent-room-runtime-takeover",
    "agent-room-context-rebase",
    "agent-room-proactive-mainline",
    "agent-room-standing-mainline",
    "agent_room_inject_message",
}
STALE_INTERNAL_FOLLOWUP_TRANSPORTS = {
    "agent-room-collab-followup",
    "agent-room-bot-mention",
}
TASK_ACCELERATION_MARKERS = (
    "加速",
    "提速",
    "提高效率",
    "尽可能快",
    "尽快",
    "太慢",
    "更快",
    "优先",
    "紧急",
    "马上",
    "立即",
    "asap",
    "urgent",
)
TASK_BUDGET_V0_ENABLED = os.environ.get("AGENT_ROOM_TASK_BUDGET_V0", "1").strip().lower() not in {"0", "false", "no", "off"}
TASK_BUDGET_CLASS_SECONDS = {
    "quick_answer": {"soft": 90, "hard": 720},
    "status_question": {"soft": 90, "hard": 720},
    "design_discussion": {"soft": 180, "hard": 1800},
    "implementation": {"soft": 300, "hard": 1800},
    "long_background": {"soft": 600, "hard": 3600},
}
DEFAULT_GLOBAL_ACTIVE_RUNNER_LIMIT = 10
DEFAULT_USER_MAIN_RESERVED_RUNNER_SLOTS = 2
DEFAULT_CHAT_ACTION_THROTTLE_SECONDS = 4
DEFAULT_NEW_TASK_LIMIT_PER_TICK = 2
DEFAULT_ACCELERATION_POLICY = "nonexclusive"
AGENT_INNER_TIMEOUT_FLOORS = {
    # agent_task_runner.py currently allows Codex CLI up to 600s. Keep the
    # outer active-runner hard cap above that until inner timeouts are fully
    # derived from TaskBudget.
    "codex": 720,
    # claude_code_ark_runner.py defaults to 300s and the wrapper adds margin.
    "claude-code": 540,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cmd(args: list[str], timeout: int = 600, env: dict[str, str] | None = None) -> dict[str, Any]:
    proc = subprocess.Popen(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {"exit_code": proc.returncode, "ok": proc.returncode == 0, "stdout": stdout, "stderr": stderr, "timeout": False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
        return {
            "exit_code": 124,
            "ok": False,
            "stdout": stdout or "",
            "stderr": (stderr or "") + f"\n[resident_bridge] timeout after {timeout}s",
            "timeout": True,
        }


def pinned_card_agent_id() -> str:
    """Let pinned_status_card.py resolve the current room status-card owner."""
    return "auto"


def pinned_status_card_command(*, room_id: str, chat_id: str, allow_send: bool, agent_id: str | None = None) -> dict[str, Any]:
    if agent_id is None:
        agent_id = pinned_card_agent_id()
    command = [
        "python3", str(TOOLS / "pinned_status_card.py"),
        "--room-id", room_id,
        "--chat-id", chat_id,
        "--agent-id", agent_id,
    ]
    if allow_send:
        command.append("--live")
        return {
            "command": command,
            "execution_mode": "live",
            "telegram_outbound": True,
        }
    return {
        "command": command,
        "execution_mode": "dry_run_projection",
        "telegram_outbound": False,
    }


def task_files(out_dir: Path) -> list[Path]:
    task_dir = out_dir / "task-manifests"
    if not task_dir.exists():
        return []
    return sorted(task_dir.glob("*.json"))


def task_chat_id(task: dict[str, Any]) -> str | None:
    src = task.get("source") or {}
    return str(src.get("chat_id") or "") or None


def task_allows_telegram_chat_action(task: dict[str, Any]) -> bool:
    src = task.get("source") if isinstance(task.get("source"), dict) else {}
    return str(src.get("transport") or "") == "telegram" and bool(src.get("chat_id"))


def comment_effective_permissions(task: dict[str, Any]) -> dict[str, bool]:
    permissions = {
        "source_edit": False,
        "telegram_send": False,
        "notion_publish": False,
        "github_push": False,
        "secrets_access": False,
        "global_state_change": False,
        "quality_surface_change": False,
    }
    raw = task.get("permissions")
    if isinstance(raw, dict):
        for key, value in raw.items():
            permissions[str(key)] = bool(value)
    permissions["telegram_send"] = False
    permissions["github_push"] = False
    permissions["secrets_access"] = False
    return permissions


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except Exception:
            continue
    return records


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_import_state() -> dict[str, Any]:
    return read_json(ROOM / "canonical_import_state.json", {
        "schema": "openclaw.agent_room.canonical_import_state.v0",
        "event_ids": [],
        "task_ids": [],
        "message_ids": [],
    })


def save_import_state(state: dict[str, Any]) -> None:
    # Keep bounded memory while still preventing recent duplicate imports.
    state["event_ids"] = list(dict.fromkeys(state.get("event_ids") or []))[-5000:]
    state["task_ids"] = list(dict.fromkeys(state.get("task_ids") or []))[-5000:]
    state["message_ids"] = list(dict.fromkeys(state.get("message_ids") or []))[-5000:]
    state["updated_at"] = now_iso()
    write_json(ROOM / "canonical_import_state.json", state)


def canonicalize_path(value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = ROOT / p
    return p


def merge_room_runtime_metadata(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Keep canonical room-local runtime policy when importing fresh poll metadata."""
    if not existing:
        return incoming
    merged = dict(incoming)
    for key in ("policies",):
        existing_value = existing.get(key)
        incoming_value = incoming.get(key)
        if key not in incoming and existing_value is not None:
            merged[key] = existing_value
        elif isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            merged[key] = {**existing_value, **incoming_value}
    return merged


def room_tool_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OPENCLAW_ROOM_ROOT"] = str(ROOM.parent)
    return env


def refresh_compact_status() -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "compact_status": "", "tokens_printed": False}
    script = TOOLS / "collaboration_status.py"
    if not script.exists():
        result["status"] = "missing_collaboration_status_script"
        return result
    run = run_cmd(["python3", str(script), "--include-background"], timeout=30, env=room_tool_env())
    result.update({
        "ok": run.get("ok"),
        "exit_code": run.get("exit_code"),
        "stderr_tail": str(run.get("stderr") or "")[-1000:],
    })
    try:
        payload = json.loads(str(run.get("stdout") or "{}"))
        if isinstance(payload, dict):
            result["compact_status"] = str(payload.get("compact_status") or "").strip()
            result["status_path"] = payload.get("path")
    except Exception as exc:
        result["parse_error"] = type(exc).__name__ + ": " + str(exc)
    if not result.get("compact_status"):
        compact_path = ROOM / "collaboration-status" / "compact.txt"
        if compact_path.exists():
            result["compact_status"] = compact_path.read_text(encoding="utf-8").strip()
            result["status_path"] = str(compact_path)
    return result


def project_status_fast_path_replies(poll_dir: Path, *, allow_send: bool) -> dict[str, Any]:
    intents_dir = poll_dir / "status-fast-path"
    result: dict[str, Any] = {
        "schema": "openclaw.agent_room.status_fast_path_projection.v0",
        "intents": 0,
        "reply_files_written": [],
        "results": [],
        "telegram_outbound": bool(allow_send),
        "tokens_printed": False,
    }
    if not intents_dir.exists():
        return result
    refresh: dict[str, Any] | None = None
    for intent_path in sorted(intents_dir.glob("*.json")):
        intent = read_json(intent_path, {})
        if not isinstance(intent, dict):
            continue
        result["intents"] += 1
        agent_id = str(intent.get("agent_id") or "openclaw-main")
        chat_id = str(intent.get("chat_id") or "")
        run_id = str(intent.get("run_id") or intent_path.stem)
        out_path = ROOM / "telegram-agent-reply" / f"{agent_id}-{run_id}.json"
        if not chat_id or not run_id:
            result["results"].append({"intent": str(intent_path), "ok": False, "status": "missing_chat_id_or_run_id"})
            continue
        if refresh is None:
            refresh = refresh_compact_status()
            result["status_refresh"] = refresh
        text = str((refresh or {}).get("compact_status") or "").strip() or "Agent Room status snapshot is temporarily unavailable."
        text_path = intents_dir / f"{run_id}.txt"
        text_path.write_text(text + "\n", encoding="utf-8")
        cmd = [
            "python3", str(TOOLS / "telegram_agent_reply.py"),
            "--agent-id", agent_id,
            "--chat-id", chat_id,
            "--run-id", run_id,
            "--direct-text-file", str(text_path),
        ]
        if allow_send:
            cmd.append("--allow-send")
        reply_run = run_cmd(cmd, timeout=120, env=room_tool_env())
        row = {
            "intent": str(intent_path),
            "agent_id": agent_id,
            "run_id": run_id,
            "reply_path": str(out_path),
            "ok": reply_run.get("ok"),
            "exit_code": reply_run.get("exit_code"),
            "stderr_tail": str(reply_run.get("stderr") or "")[-1000:],
        }
        try:
            stdout_payload = json.loads(str(reply_run.get("stdout") or "{}"))
            if isinstance(stdout_payload, dict):
                row["sent"] = stdout_payload.get("sent")
                row["would_send"] = stdout_payload.get("would_send")
        except Exception:
            row["stdout_tail"] = str(reply_run.get("stdout") or "")[-1000:]
        if out_path.exists():
            result["reply_files_written"].append(str(out_path))
        result["results"].append(row)
    return result


def import_canonical_artifacts(poll_dir: Path, *, allow_send: bool = False) -> dict[str, Any]:
    state = load_import_state()
    seen_events = set(state.get("event_ids") or [])
    seen_tasks = set(state.get("task_ids") or [])
    imported_events: list[dict[str, Any]] = []
    imported_messages: list[dict[str, Any]] = []
    imported_tasks: list[dict[str, Any]] = []
    seen_messages = set(state.get("message_ids") or [])
    rooms_written: list[str] = []
    task_files_written: list[str] = []
    status_fast_path_projection = project_status_fast_path_replies(poll_dir, allow_send=allow_send)

    rooms_root = poll_dir / "rooms"
    if rooms_root.exists():
        for room_dir in sorted(p for p in rooms_root.iterdir() if p.is_dir()):
            room_json = read_json(room_dir / "room.json", {})
            participants_json = read_json(room_dir / "participants.json", {})
            room_id = str(room_json.get("room_id") or participants_json.get("room_id") or room_dir.name)
            canonical_dir = ROOM / "rooms" / room_id
            if room_json:
                existing_room_json = read_json(canonical_dir / "room.json", {})
                room_json = merge_room_runtime_metadata(room_json, existing_room_json)
                room_json["canonical_state_advanced"] = True
                room_json["canonical_updated_at"] = now_iso()
                write_json(canonical_dir / "room.json", room_json)
            if participants_json:
                participants_json["canonical_state_advanced"] = True
                participants_json["canonical_updated_at"] = now_iso()
                write_json(canonical_dir / "participants.json", participants_json)
            rooms_written.append(room_id)

    for event in read_jsonl(poll_dir / "events.jsonl"):
        event_id = str(event.get("event_id") or "")
        if event_id and event_id in seen_events:
            continue
        event["canonical_state_advanced"] = True
        event["canonical_imported_at"] = now_iso()
        imported_events.append(event)
        if event_id:
            seen_events.add(event_id)
        room_id = str(event.get("room_id") or "")
        if room_id:
            append_jsonl(ROOM / "rooms" / room_id / "events.jsonl", [event])

    if imported_events:
        append_jsonl(ROOM / "events.jsonl", imported_events)

    for message in read_jsonl(poll_dir / "messages.jsonl"):
        message_id = str(message.get("message_event_id") or message.get("update_id") or "")
        if message_id and message_id in seen_messages:
            continue
        message["canonical_state_advanced"] = True
        message["canonical_imported_at"] = now_iso()
        imported_messages.append(message)
        if message_id:
            seen_messages.add(message_id)
        room_id = str(message.get("room_id") or "")
        if room_id:
            append_jsonl(ROOM / "rooms" / room_id / "messages.jsonl", [message])

    if imported_messages:
        append_jsonl(ROOM / "messages.jsonl", imported_messages)

    for task in read_jsonl(poll_dir / "tasks.jsonl"):
        task_id = str(task.get("task_id") or "")
        if task_id and task_id in seen_tasks:
            continue
        task["canonical_state_advanced"] = True
        task["canonical_imported_at"] = now_iso()
        task_dir = ROOM / "tasks" / task_id
        brief_src = canonicalize_path(task.get("brief_path"))
        if brief_src and brief_src.exists():
            task_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(brief_src, task_dir / "brief.md")
            task["brief_path"] = str((task_dir / "brief.md"))
        write_json(task_dir / "manifest.json", task)
        task_files_written.append(str(task_dir / "manifest.json"))
        imported_tasks.append(task)
        if task_id:
            seen_tasks.add(task_id)
        room_id = str(task.get("room_id") or "")
        if room_id:
            append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])

    if imported_tasks:
        append_jsonl(ROOM / "tasks.jsonl", imported_tasks)

    state["event_ids"] = sorted(seen_events)
    state["task_ids"] = sorted(seen_tasks)
    state["message_ids"] = sorted(seen_messages)
    save_import_state(state)
    return {
        "schema": "openclaw.agent_room.canonical_import_result.v0",
        "poll_dir": str(poll_dir),
        "rooms_written": sorted(set(rooms_written)),
        "events_imported": len(imported_events),
        "messages_imported": len(imported_messages),
        "tasks_imported": len(imported_tasks),
        "task_files_written": task_files_written,
        "status_fast_path_projection": status_fast_path_projection,
        "status_reply_files_written": status_fast_path_projection.get("reply_files_written") or [],
        "canonical_state_advanced": bool(
            rooms_written
            or imported_events
            or imported_messages
            or imported_tasks
            or status_fast_path_projection.get("reply_files_written")
        ),
    }


def latest_update_offsets(raw_updates: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for update in raw_updates:
        agent_id = str(update.get("receiver_agent_id") or "")
        update_id = update.get("update_id")
        if not agent_id or update_id is None:
            continue
        out[agent_id] = max(out.get(agent_id, 0), int(update_id) + 1)
    return out


def runner_comments(runner_result: dict[str, Any]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for item in runner_result.get("results") or []:
        comment = item.get("comment")
        if isinstance(comment, dict):
            comments.append(comment)
    return comments


def runner_result_is_terminal(runner_result: dict[str, Any]) -> bool:
    """Return True only for a complete runner result, not a half-written marker."""
    if not isinstance(runner_result, dict) or not runner_result:
        return False
    results = runner_result.get("results")
    if isinstance(results, list):
        return True
    status = str(runner_result.get("status") or "").lower()
    if status in {"completed", "failed", "blocked", "partial", "partial_failed", "cancelled", "stale"}:
        return True
    if "ok" in runner_result and ("exit_code" in runner_result or "runner_status" in runner_result):
        return True
    return False


def comment_path(agent_id: str) -> Path:
    if agent_id == "claude-code":
        return ROOT / "agent-comments" / "claude.jsonl"
    return ROOT / "agent-comments" / f"{agent_id}.jsonl"


def process_observation(pid: int | None) -> dict[str, Any]:
    observed_at = now_iso()
    try:
        pid_value = int(pid or 0)
    except Exception:
        pid_value = 0
    observation: dict[str, Any] = {
        "pid": pid_value,
        "observed_at": observed_at,
        "proc_exists": False,
    }
    if pid_value <= 0:
        observation["reason"] = "missing_pid"
        return observation

    proc_dir = Path(f"/proc/{pid_value}")
    stat_path = proc_dir / "stat"
    status_path = proc_dir / "status"
    if not stat_path.exists():
        observation["reason"] = "proc_stat_missing"
        return observation

    observation["proc_exists"] = True
    try:
        stat_text = stat_path.read_text(encoding="utf-8", errors="replace")
        right_paren = stat_text.rfind(")")
        after_name = stat_text[right_paren + 2:].split() if right_paren != -1 else stat_text.split()[2:]
        if after_name:
            observation["state"] = after_name[0]
        if len(after_name) > 1:
            try:
                observation["ppid"] = int(after_name[1])
            except ValueError:
                observation["ppid"] = after_name[1]
    except Exception as exc:
        observation["stat_error"] = str(exc)[:240]

    try:
        status = status_path.read_text(encoding="utf-8", errors="replace")
        for line in status.splitlines():
            key, sep, value = line.partition(":")
            if sep and key in {"Name", "State", "PPid", "Threads"}:
                observation[f"status_{key.lower()}"] = value.strip()[:120]
    except Exception as exc:
        observation["status_error"] = str(exc)[:240]
    return observation


def process_observation_alive(observation: dict[str, Any]) -> bool:
    if not observation.get("proc_exists"):
        return False
    if str(observation.get("state") or "") == "Z":
        return False
    status_state = str(observation.get("status_state") or "").lower()
    if "zombie" in status_state:
        return False
    try:
        os.kill(int(observation.get("pid") or 0), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def process_alive(pid: int | None) -> bool:
    # In Linux/WSL, /proc is the reliable source of process liveness.
    # If the process directory is gone, release the active-runner lease
    # instead of letting stale metadata block the room queue.
    return process_observation_alive(process_observation(pid))


def process_cmdline(pid: int) -> list[str]:
    # Read a bounded process command-line fingerprint to avoid classifying PID reuse as runner liveness.
    # Any failure returns an empty list and caller should fall back to conservative checks.
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
    return True

def agent_room_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def runner_unit_name(agent_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{agent_id}:{run_id}".encode("utf-8")).hexdigest()[:16]
    return f"openclaw-agent-runner-{compact_slug(agent_id)[:24]}-{digest}"


def systemd_show_unit(unit: str) -> dict[str, str]:
    if not unit:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "--no-pager"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
    except Exception as exc:
        return {"show_error": str(exc)[:240]}
    out: dict[str, str] = {"show_exit_code": str(proc.returncode)}
    if proc.stderr:
        out["stderr"] = proc.stderr[-500:]
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def systemd_unit_main_pid(unit: str) -> int:
    state = systemd_show_unit(unit)
    try:
        return int(state.get("MainPID") or 0)
    except ValueError:
        return 0


def active_runner_dispatch_lock_path(agent_id: str, run_id: str) -> Path:
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(run_id))
    safe_agent = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(agent_id))
    return ROOM / "active-runner-locks" / f"{safe_agent}-{safe_run_id}.lock"


def try_acquire_active_runner_dispatch_lock(agent_id: str, run_id: str) -> tuple[Any | None, dict[str, Any]]:
    lock_path = active_runner_dispatch_lock_path(agent_id, run_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None, {
            "acquired": False,
            "lock_path": str(lock_path),
            "reason": "same_run_dispatch_lock_busy",
        }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({
        "schema": "openclaw.agent_room.active_runner_dispatch_lock.v0",
        "agent_id": agent_id,
        "run_id": run_id,
        "pid": os.getpid(),
        "locked_at": now_iso(),
    }, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    return handle, {
        "acquired": True,
        "lock_path": str(lock_path),
        "pid": os.getpid(),
    }


def release_active_runner_dispatch_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def active_runner_alive(record: dict[str, Any]) -> bool:
    # Exit marker: if the runner wrote .runner-exit-marker before exiting,
    # it is definitively not alive. This eliminates the harvest-lag window
    # where /proc/systemd show stale data (especially on WSL) and cause
    # repeated "same run already running" noise between harvest ticks.
    runner_dir = Path(str(record.get("runner_dir") or ""))
    if runner_dir and (runner_dir / ".runner-exit-marker").exists():
        return False
    unit = str(record.get("systemd_unit") or "")
    if unit:
        state = systemd_show_unit(unit)
        pid = int(state.get("MainPID") or 0) if str(state.get("MainPID") or "").isdigit() else 0
        # Runner liveness must be process-backed. A lingering transient unit
        # with no live MainPID is not evidence that an agent is still working.
        if pid and process_alive(pid):
            if not process_looks_like_runner_process(pid, record):
                return False
            return True
        if state.get("show_exit_code") == "0" and pid <= 0:
            return False
        # If the systemd user bus itself is unavailable, do not turn that
        # infrastructure visibility failure into a destructive harvest signal.
        fallback_pid = int(record.get("pid") or 0)
        if fallback_pid and not process_looks_like_runner_process(fallback_pid, record):
            return False
        return process_alive(fallback_pid)
    return process_alive(int(record.get("pid") or 0))


def start_runner_process_isolated(
    runner_cmd: list[str],
    runner_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    agent_id: str,
    run_id: str,
) -> dict[str, Any]:
    if not agent_room_flag("AGENT_ROOM_RUNNER_SYSTEMD_ISOLATION", "1"):
        with stdout_path.open("w", encoding="utf-8") as stdout_f, stderr_path.open("w", encoding="utf-8") as stderr_f:
            proc = subprocess.Popen(runner_cmd, text=True, stdout=stdout_f, stderr=stderr_f, start_new_session=True)
        return {"launch_mode": "popen", "pid": proc.pid}

    unit = runner_unit_name(agent_id, run_id)
    script_path = runner_dir / "runner-systemd-entrypoint.sh"
    cmd_line = " ".join(shlex.quote(str(part)) for part in runner_cmd)
    marker_path = runner_dir / ".runner-exit-marker"
    script_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(ROOT))}\n"
        f"marker_path={shlex.quote(str(marker_path))}\n"
        "write_exit_marker() {\n"
        "  status=$?\n"
        "  finished_at=$(date --iso-8601=seconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%S%z)\n"
        "  printf '{\"finished_at\":\"%s\",\"exit_code\":%s,\"source\":\"runner-systemd-entrypoint\"}\\n' \"$finished_at\" \"$status\" > \"$marker_path\" 2>/dev/null || true\n"
        "  return \"$status\"\n"
        "}\n"
        "trap write_exit_marker EXIT\n"
        f"{cmd_line} > {shlex.quote(str(stdout_path))} 2> {shlex.quote(str(stderr_path))}\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    memory_max = os.environ.get("AGENT_ROOM_RUNNER_MEMORY_MAX", "3072M")
    tasks_max = os.environ.get("AGENT_ROOM_RUNNER_TASKS_MAX", "256")
    run_cmdline = [
        "systemd-run", "--user", "--unit", unit, "--collect", "--quiet",
        "--property=MemoryAccounting=yes",
        f"--property=MemoryMax={memory_max}",
        f"--property=TasksMax={tasks_max}",
        "--property=KillMode=mixed",
        str(script_path),
    ]
    proc = subprocess.run(run_cmdline, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    retry_cleanup: dict[str, Any] | None = None
    if proc.returncode != 0 and "already loaded or has a fragment file" in (proc.stderr or ""):
        state = systemd_show_unit(unit)
        try:
            existing_pid = int(state.get("MainPID") or 0)
        except Exception:
            existing_pid = 0
        if existing_pid and process_alive(existing_pid) and process_looks_like_runner_process(existing_pid, {"systemd_unit": unit}):
            return {
                "launch_mode": "systemd_service",
                "pid": 0,
                "systemd_unit": unit,
                "systemd_state": state,
                "systemd_run": {
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-1000:],
                    "stderr": proc.stderr[-1000:],
                    "duplicate_live_unit": True,
                    "existing_pid": existing_pid,
                },
                "duplicate_live_unit": True,
                "existing_pid": existing_pid,
            }
        stop = subprocess.run(
            ["systemctl", "--user", "stop", unit],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        reset = subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        retry_cleanup = {
            "reason": "systemd_unit_already_loaded",
            "stop_exit_code": stop.returncode,
            "stop_stderr": stop.stderr[-500:],
            "reset_exit_code": reset.returncode,
            "reset_stderr": reset.stderr[-500:],
        }
        proc = subprocess.run(run_cmdline, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    pid = 0
    state: dict[str, str] = {}
    if proc.returncode == 0:
        for _ in range(10):
            state = systemd_show_unit(unit)
            pid = systemd_unit_main_pid(unit)
            if pid or state.get("ActiveState") in {"active", "activating"}:
                break
            time.sleep(0.2)
    if proc.returncode != 0 and agent_room_flag("AGENT_ROOM_RUNNER_ALLOW_POPEN_FALLBACK", "0"):
        with stdout_path.open("w", encoding="utf-8") as stdout_f, stderr_path.open("w", encoding="utf-8") as stderr_f:
            fallback = subprocess.Popen(runner_cmd, text=True, stdout=stdout_f, stderr=stderr_f, start_new_session=True)
        return {
            "launch_mode": "popen_fallback",
            "pid": fallback.pid,
            "systemd_unit": unit,
            "systemd_run": {"exit_code": proc.returncode, "stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]},
        }
    systemd_run = {"exit_code": proc.returncode, "stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]}
    if retry_cleanup is not None:
        systemd_run["retry_cleanup"] = retry_cleanup
    return {
        "launch_mode": "systemd_service",
        "pid": pid,
        "systemd_unit": unit,
        "systemd_state": state,
        "systemd_run": systemd_run,
        "runner_memory_max": memory_max,
        "runner_tasks_max": tasks_max,
    }


def terminate_runner_record(record: dict[str, Any]) -> dict[str, Any]:
    unit = str(record.get("systemd_unit") or "")
    if unit:
        proc = subprocess.run(["systemctl", "--user", "stop", unit], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return {
            "attempted": True,
            "method": "systemctl_stop",
            "unit": unit,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
            "alive_after": active_runner_alive(record),
        }
    return terminate_runner_process(int(record.get("pid") or 0))


def _harvest_dead_runner_orphan(ar_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Remove a dead runner's active-runner file without waiting for result.json.

    Called only for the narrow window where the process has exited but
    result.json has not been written yet.  The finished-runners archive
    gets a minimal record so the orphan is observable in harvested_runners
    output without relying on runner output that does not yet exist.

    Returns a harvested_runners summary so harvest-only mode remains observable.
    """
    finished: dict[str, Any] = dict(record)
    finished.update({
        "status": "finished",
        "finished_at": now_iso(),
        "orphan_harvest": True,
        "missing_process": True,
        "missing_result_json": True,
        "comments": 0,
        "collab_followups": [],
        "runtime_takeovers": [],
    })
    FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)
    write_json(FINISHED_RUNNERS / ar_path.name, finished)
    ar_path.unlink(missing_ok=True)
    summary = {
        "active_runner": str(ar_path),
        "agent_id": record.get("agent_id"),
        "run_id": record.get("run_id"),
        "pid": record.get("pid"),
        "status": "finished",
        "orphan_harvest": True,
        "missing_process": True,
        "missing_result_json": True,
        "comments": 0,
        "collab_followups": [],
        "runtime_takeovers": [],
        "reply_attempted": False,
        "telegram_projection_suppressed_reason": "missing_result_json",
        "telegram_projection_mode": "suppressed",
    }
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    write_collaboration_status_snapshot(task, "harvest_dead_runner_orphan", agent_runs=[summary])
    return summary


def cleanup_stale_active_runner_before_dispatch(ar_path: Path, record: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Archive a stale same-run active-runner lock before retrying dispatch.

    Normal harvest runs before live dispatch, but the dispatch guard is the last
    line of defense for same-run idempotency. If a record is already beyond its
    hard runner budget, it should not keep returning "already_running".
    """
    agent_id = str(record.get("agent_id") or "")
    run_id = str(record.get("run_id") or "")
    alive_before = active_runner_alive(record)
    termination_result = terminate_runner_record(record) if alive_before else None
    finished: dict[str, Any] = dict(record)
    finished.update({
        "status": "finished",
        "finished_at": now_iso(),
        "pre_dispatch_cleanup": True,
        "cleanup_reason": reason,
        "stale_runner": active_runner_stale(record),
        "missing_process": not alive_before,
        "missing_result_json": True,
        "comments": 0,
        "collab_followups": [],
        "runtime_takeovers": [],
        "reply_attempted": False,
        "telegram_projection_suppressed_reason": "pre_dispatch_stale_active_runner_cleanup",
        "telegram_projection_mode": "suppressed",
        "deadline_state": classify_runner_deadline_state(record),
        "termination_result": termination_result,
    })
    FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)
    write_json(FINISHED_RUNNERS / ar_path.name, finished)
    ar_path.unlink(missing_ok=True)
    summary = {
        "active_runner": str(ar_path),
        "agent_id": agent_id,
        "run_id": run_id,
        "pid": record.get("pid"),
        "status": "cleaned_stale_before_dispatch",
        "pre_dispatch_cleanup": True,
        "cleanup_reason": reason,
        "stale_runner": active_runner_stale(record),
        "missing_process": not alive_before,
        "missing_result_json": True,
        "deadline_state": classify_runner_deadline_state(record),
        "termination_result": termination_result,
        "reply_attempted": False,
        "telegram_projection_suppressed_reason": "pre_dispatch_stale_active_runner_cleanup",
        "telegram_projection_mode": "suppressed",
    }
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    write_collaboration_status_snapshot(task, "dispatch_cleaned_stale_active_runner", agent_runs=[summary])
    return summary


def file_tail_evidence(path: Path | None, *, max_chars: int = 4000) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(path) if path else "",
        "exists": False,
        "bytes": 0,
        "tail": "",
    }
    if path is None:
        evidence["reason"] = "missing_path"
        return evidence
    try:
        if not path.exists():
            return evidence
        evidence["exists"] = True
        size = path.stat().st_size
        evidence["bytes"] = size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_chars * 4))
            tail_bytes = handle.read()
        evidence["tail"] = tail_bytes.decode("utf-8", errors="replace")[-max_chars:]
    except Exception as exc:
        evidence["error"] = str(exc)[:240]
    return evidence


def runner_artifact_evidence(record: dict[str, Any], result_json_path: Path | None) -> dict[str, Any]:
    stdout_path = Path(str(record.get("stdout_path") or "")) if record.get("stdout_path") else None
    stderr_path = Path(str(record.get("stderr_path") or "")) if record.get("stderr_path") else None
    return {
        "result_json": file_tail_evidence(result_json_path, max_chars=1200),
        "stdout": file_tail_evidence(stdout_path),
        "stderr": file_tail_evidence(stderr_path),
    }



def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def room_message_ref(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "")
    return {
        "stable_message_id": item.get("stable_message_id"),
        "message_event_id": item.get("message_event_id"),
        "update_id": item.get("update_id"),
        "telegram_message_id": item.get("telegram_message_id"),
        "created_at": item.get("created_at"),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
    }


def room_message_identity(item: dict[str, Any]) -> str:
    for key in ("stable_message_id", "message_event_id", "update_id", "telegram_message_id"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def room_messages(room_id: str) -> list[dict[str, Any]]:
    if not room_id:
        return []
    return read_jsonl(ROOM / "rooms" / room_id / "messages.jsonl")


def is_human_room_message(item: dict[str, Any]) -> bool:
    return bool(item.get("actor_user_id")) and not item.get("actor_agent_id")


def human_room_message_text(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("caption") or "").strip()


def classify_newer_human_message_for_runner(item: dict[str, Any]) -> dict[str, Any]:
    """Decide whether a newer human message invalidates an in-flight runner.

    A later room message is not automatically an interrupt. Status probes and
    mainline supplements should be merged at the next safe checkpoint while the
    current runner continues. Explicit cancellations, direction changes, or
    urgent reprioritization still block stale projection and force rebase.
    """
    text = human_room_message_text(item)
    lowered = text.lower()
    interrupt_markers = (
        "停止", "停下", "暂停", "别做", "不要继续", "先别", "先停",
        "取消", "撤回", "别发", "不要发", "别发布", "不要发布",
        "改成", "换成", "重新来", "重做", "优先", "紧急", "马上", "立即",
        "不对", "错了", "不是这个", "方向错", "打断",
        "stop", "pause", "cancel", "abort", "instead", "change", "urgent", "asap",
    )
    non_interrupting_supplement_markers = (
        "补充", "另外", "还有", "顺便", "同时", "继续干活", "继续推进",
        "不一定就是", "不需要停", "不要停下", "不用停下", "不是让你们停下",
        "不影响当前任务", "不影响手中的活", "不影响主线", "不影响", "不用打断",
        "对主线的补充", "mainline supplement", "supplement", "keep working",
    )
    non_interrupting_visible_reply_markers = (
        "立刻回复", "马上回复", "直接回复", "及时回复",
        "不影响，", "不影响就", "不影响当前任务就",
    )
    status_probe_markers = (
        "状态", "进度", "在推进吗", "什么时候", "卡住", "一眼", "status",
        "progress", "eta", "when", "怎么回事", "什么情况", "发生了什么",
        "什么错", "什么问题", "why", "what happened",
    )
    if any(marker in lowered for marker in non_interrupting_supplement_markers):
        explicit_reply_request = any(marker in lowered for marker in non_interrupting_visible_reply_markers)
        return {
            "schema": "openclaw.agent_room.newer_human_message_policy.v0",
            "mode": "non_interrupting_supplement",
            "reason": "supplement_marker",
            "runtime_action": (
                "continue_runner_and_answer_visible_message_immediately"
                if explicit_reply_request
                else "continue_runner_answer_ack_and_merge_update_at_next_safe_checkpoint"
            ),
            # Alex clarified 2026-05-27: a non-interrupting supplement must not
            # disappear behind active runners.  The room should get a prompt
            # visible acknowledgement/answer while current work continues.
            "visible_reply_expected": True,
            "message": room_message_ref(item),
        }
    if any(marker in lowered for marker in status_probe_markers):
        return {
            "schema": "openclaw.agent_room.newer_human_message_policy.v0",
            "mode": "non_interrupting_status_probe",
            "reason": "status_probe_marker",
            "runtime_action": "continue_runner_and_answer_status_immediately",
            "visible_reply_expected": True,
            "message": room_message_ref(item),
        }
    if any(marker in lowered for marker in interrupt_markers):
        return {
            "schema": "openclaw.agent_room.newer_human_message_policy.v0",
            "mode": "interrupting_context_change",
            "reason": "interrupt_marker",
            "runtime_action": "block_old_projection_and_rebase_from_latest_room_state",
            "message": room_message_ref(item),
        }
    return {
        "schema": "openclaw.agent_room.newer_human_message_policy.v0",
        "mode": "non_interrupting_side_message",
        "reason": "no_marker_match_default_as_non_impacting",
        "runtime_action": "continue_runner_and_answer_visible_message_immediately",
        "visible_reply_expected": True,
        "message": room_message_ref(item),
    }


def latest_human_room_message(room_id: str) -> dict[str, Any] | None:
    for item in reversed(room_messages(room_id)):
        if is_human_room_message(item):
            return item
    return None


def latest_human_room_message_after(room_id: str, after: datetime | None) -> dict[str, Any] | None:
    if after is None:
        return None
    newest: dict[str, Any] | None = None
    newest_dt: datetime | None = None
    for item in room_messages(room_id):
        if not is_human_room_message(item):
            continue
        created = parse_iso_datetime(str(item.get("created_at") or ""))
        if not created or created <= after:
            continue
        if newest_dt is None or created > newest_dt:
            newest = item
            newest_dt = created
    return newest


def latest_human_room_message_after_snapshot(room_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    latest_seen = snapshot.get("latest_human_message") if isinstance(snapshot.get("latest_human_message"), dict) else {}
    seen_identity = room_message_identity(latest_seen)
    if not seen_identity:
        return None
    found_seen = False
    newest_after: dict[str, Any] | None = None
    for item in room_messages(room_id):
        if not is_human_room_message(item):
            continue
        if found_seen:
            newest_after = item
            continue
        if room_message_identity(item) == seen_identity:
            found_seen = True
    return newest_after


def room_context_snapshot(room_id: str) -> dict[str, Any]:
    latest_human = latest_human_room_message(room_id)
    return {
        "schema": "openclaw.agent_room.context_snapshot.v0",
        "room_id": room_id,
        "captured_at": now_iso(),
        "latest_human_message": room_message_ref(latest_human) if latest_human else None,
    }


def runner_context_freshness(record: dict[str, Any]) -> dict[str, Any]:
    room_id = str(record.get("room_id") or "")
    started_at = parse_iso_datetime(str(record.get("started_at") or ""))
    snapshot = record.get("context_snapshot") if isinstance(record.get("context_snapshot"), dict) else {}
    newest_after_snapshot = latest_human_room_message_after_snapshot(room_id, snapshot)
    newest_after_start = latest_human_room_message_after(room_id, started_at)
    newest_after = newest_after_snapshot or newest_after_start
    if newest_after:
        newer_policy = classify_newer_human_message_for_runner(newest_after)
        if str(newer_policy.get("mode") or "").startswith("non_interrupting_"):
            return {
                "schema": "openclaw.agent_room.context_freshness.v0",
                "status": "context_update_available",
                "reason": "newer_human_room_message_classified_non_interrupting",
                "trigger": "newer_human_room_message",
                "user_fault": False,
                "runtime_action_required": newer_policy.get("runtime_action"),
                "projection_should_continue": True,
                "runner_started_at": record.get("started_at"),
                "snapshot": snapshot,
                "newer_human_message": room_message_ref(newest_after),
                "newer_message_policy": newer_policy,
            }
        return {
            "schema": "openclaw.agent_room.context_freshness.v0",
            "status": "stale_context",
            "reason": "newer_human_room_message_classified_interrupting",
            "trigger": "newer_human_room_message",
            "user_fault": False,
            "runtime_action_required": "merge_or_requeue_from_latest_room_state",
            "projection_should_continue": False,
            "runner_started_at": record.get("started_at"),
            "snapshot": snapshot,
            "newer_human_message": room_message_ref(newest_after),
            "newer_message_policy": newer_policy,
        }
    return {
        "schema": "openclaw.agent_room.context_freshness.v0",
        "status": "current",
        "reason": "no_newer_human_room_message_after_runner_start",
        "runner_started_at": record.get("started_at"),
        "snapshot": snapshot,
    }


def recurring_systemic_problem_requested(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        "这种问题不是第一次出现",
        "不是第一次出现",
        "之前也修补",
        "修补过",
        "从根本上解决",
        "根本上解决",
        "根本解决",
        "反复出现",
        "总是复发",
        "系统性",
        "系统性的解决方案",
    )
    return any(marker in lowered for marker in markers)

def task_interaction_class(task: dict[str, Any]) -> str:
    text_parts = [task_user_message(task)]
    for key in ("user_message", "message", "text", "body"):
        value = task.get(key)
        if isinstance(value, str):
            text_parts.append(value)
    text = "\n".join(part for part in text_parts if part).lower()
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    transport = str(source.get("transport") or "")
    if transport in {"agent-room-collab-followup", "agent-room-runtime-takeover"}:
        return "design_discussion"
    if recurring_systemic_problem_requested(text) or idle_agent_contribution_problem_requested(text):
        return "design_discussion"
    if any(marker in text for marker in ("实现", "修改", "修复", "接入", "代码", "脚本", "patch", "code", "implement", "fix")):
        return "implementation"
    if any(
        marker in text
        for marker in (
            "架构",
            "设计",
            "讨论",
            "方案",
            "根因",
            "系统性",
            "协作",
            "机制",
            "主线",
            "每个人都应该",
            "主动探索",
            "runner",
            "runners",
            "保留槽",
            "上下文",
            "迟钝",
            "反应慢",
            "滞后",
            "调度",
            "architecture",
            "design",
            "runtime",
            "scheduler",
            "context",
        )
    ):
        return "design_discussion"
    if any(marker in text for marker in ("状态", "进度", "为什么", "怎么回事", "卡住", "不回", "status", "progress", "why")):
        return "status_question"
    if transport in INTERNAL_AGENT_ROOM_TRANSPORTS:
        return "design_discussion"
    return "quick_answer"


def build_task_budget(task: dict[str, Any]) -> dict[str, Any]:
    created_at = str(task.get("created_at") or task.get("canonical_imported_at") or now_iso())
    created_dt = parse_iso_datetime(created_at) or datetime.now(timezone.utc).astimezone()
    interaction_class = task_interaction_class(task)
    seconds = TASK_BUDGET_CLASS_SECONDS.get(interaction_class, TASK_BUDGET_CLASS_SECONDS["quick_answer"])
    targets = [agent_id for agent_id in (task.get("target_agents") or []) if agent_id in LOCAL_RUNTIME_AGENTS]
    explicit_first_owner = str(task.get("first_response_owner") or "").strip()
    first_owner = explicit_first_owner or (targets[0] if len(targets) == 1 else "")
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    transport = str(source.get("transport") or "")

    # --- Governance contract fields (mainline-governance-contract-20260528) ---
    governance: dict[str, Any] = {}
    raw_governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    for key in ("mainline_id", "problem_statement", "expected_user_value",
                 "owner", "definition_of_done", "approval_gate", "dedupe_key",
                 "next_action"):
        value = raw_governance.get(key) or task.get(key)
        if value:
            governance[key] = value
    # Fallback: infer mainline_id from lane or standing_mainline metadata
    if "mainline_id" not in governance:
        standing = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
        mainline_item_id = standing.get("linked_mainline_item_id") or standing.get("item_id")
        if mainline_item_id:
            governance["mainline_id"] = str(mainline_item_id)
        elif task.get("lane") in ("standing_mainline_discussion", "agent_to_agent_mention"):
            governance["mainline_id"] = "agent_room_infrastructure"
    # Infer first_response_owner (already computed above) as default owner
    if "owner" not in governance and first_owner:
        governance["owner"] = first_owner

    return {
        "schema": "openclaw.agent_room.task_budget.v0",
        "enabled": TASK_BUDGET_V0_ENABLED,
        "task_id": task.get("task_id"),
        "interaction_class": interaction_class,
        "created_at": created_dt.isoformat(timespec="seconds"),
        "soft_deadline_at": (created_dt + timedelta(seconds=int(seconds["soft"]))).isoformat(timespec="seconds"),
        "hard_deadline_at": (created_dt + timedelta(seconds=int(seconds["hard"]))).isoformat(timespec="seconds"),
        "soft_seconds": int(seconds["soft"]),
        "hard_seconds": int(seconds["hard"]),
        "expected_agents": targets,
        "first_response_owner": first_owner,
        "allow_partial_visible": True,
        "requires_visible_if_all_fail": transport == "telegram" or str(task.get("delivery_policy") or "") == "targeted_reply",
        "salvage_required": True,
        # Governance contract fields
        "governance": governance,
    }


def runner_budget_for_agent(task_budget: dict[str, Any], agent_id: str) -> dict[str, Any]:
    hard_seconds = int(task_budget.get("hard_seconds") or TASK_BUDGET_CLASS_SECONDS["quick_answer"]["hard"])
    hard_seconds = max(hard_seconds, int(AGENT_INNER_TIMEOUT_FLOORS.get(agent_id, 600)))
    soft_seconds = int(task_budget.get("soft_seconds") or TASK_BUDGET_CLASS_SECONDS["quick_answer"]["soft"])
    # The task soft deadline belongs to first-response UX/handoff. A runner
    # attempt gets its own soft deadline from its actual start time; otherwise a
    # backlog or peer-followup runner can be born already "over soft deadline",
    # which creates false red status cards even while the process is healthy.
    started_dt = datetime.now(timezone.utc).astimezone()
    return {
        "schema": "openclaw.agent_room.runner_budget.v0",
        "agent_id": agent_id,
        "task_id": task_budget.get("task_id"),
        "interaction_class": task_budget.get("interaction_class"),
        "task_soft_deadline_at": task_budget.get("soft_deadline_at"),
        "soft_deadline_at": (started_dt + timedelta(seconds=soft_seconds)).isoformat(timespec="seconds"),
        "hard_deadline_at": (started_dt + timedelta(seconds=hard_seconds)).isoformat(timespec="seconds"),
        "soft_seconds": soft_seconds,
        "hard_seconds": hard_seconds,
        "derived_from_task_budget": bool(task_budget.get("enabled")),
    }


def active_runner_max_seconds(agent_id: str, record: dict[str, Any] | None = None) -> int:
    if TASK_BUDGET_V0_ENABLED and isinstance(record, dict):
        runner_budget = record.get("runner_budget")
        if isinstance(runner_budget, dict):
            hard_seconds = runner_budget.get("hard_seconds")
            try:
                return max(60, int(hard_seconds))
            except (TypeError, ValueError):
                pass
    env_key = f"AGENT_ROOM_{agent_id.upper().replace('-', '_')}_ACTIVE_RUNNER_MAX_SECONDS"
    # Live Agent Room turns must fail visibly before the room looks silent.
    # Codex CLI runs in particular can hang without stdout while the user is
    # waiting in Telegram, so its default watchdog is intentionally shorter
    # than slower Claude Code read/write turns. Env vars can still widen this
    # for deliberate long-running experiments.
    default_by_agent = {
        "codex": "240",
        "claude-code": "600",
    }
    raw = os.environ.get(env_key) or os.environ.get("AGENT_ROOM_ACTIVE_RUNNER_MAX_SECONDS") or default_by_agent.get(agent_id, "600")
    try:
        return max(60, int(raw))
    except ValueError:
        return max(60, int(default_by_agent.get(agent_id, "600")))


def runner_age_seconds(record: dict[str, Any]) -> float | None:
    started = parse_iso_datetime(str(record.get("started_at") or ""))
    if not started:
        return None
    return (datetime.now(timezone.utc).astimezone() - started).total_seconds()


def active_runner_stale(record: dict[str, Any]) -> bool:
    # Use the same process-backed liveness projection as dispatch/harvest.
    # For systemd-isolated runners the record pid can lag behind the transient
    # unit's MainPID, so checking the stored pid alone can misclassify a live
    # runner as stale and either kill it or release the same-run lock.
    if not active_runner_alive(record):
        return True
    age = runner_age_seconds(record)
    if age is None:
        return False
    agent_id = str(record.get("agent_id") or "")
    return age > active_runner_max_seconds(agent_id, record)


def active_runner_completed(record: dict[str, Any]) -> bool:
    """Return True when the runner has a terminal result.json (completed/failed).

    Unlike active_runner_exists(), this only checks the result artifact and
    does NOT conflate "process alive" with "result ready". Used to distinguish
    completed-but-unharvested runners from genuinely active ones.
    """
    if not isinstance(record, dict):
        return False
    runner_dir = Path(str(record.get("runner_dir") or ""))
    if not runner_dir or not runner_dir.is_dir():
        return False
    result_path = runner_dir / "result.json"
    if not result_path.exists():
        return False
    return runner_result_is_terminal(read_json(result_path, {}))


def effective_runner_soft_deadline(record: dict[str, Any]) -> Any:
    soft = parse_iso_datetime(str(record.get("soft_deadline_at") or ""))
    started = parse_iso_datetime(str(record.get("started_at") or ""))
    runner_budget = record.get("runner_budget") if isinstance(record.get("runner_budget"), dict) else {}
    try:
        soft_seconds = int(runner_budget.get("soft_seconds") or 0)
    except Exception:
        soft_seconds = 0
    if started and soft and soft < started and soft_seconds > 0:
        return (started + timedelta(seconds=soft_seconds)).isoformat(timespec="seconds")
    return record.get("soft_deadline_at")


def classify_runner_deadline_state(record: dict[str, Any]) -> str:
    if not TASK_BUDGET_V0_ENABLED:
        return "legacy_timeout_model"
    now = datetime.now(timezone.utc).astimezone()
    hard = parse_iso_datetime(str(record.get("hard_deadline_at") or ""))
    soft = parse_iso_datetime(str(effective_runner_soft_deadline(record) or ""))
    if hard and now > hard:
        return "hard_deadline_exceeded"
    if soft and now > soft:
        return "soft_deadline_exceeded"
    return "running_within_budget"


def terminate_runner_process(pid: int) -> dict[str, Any]:
    if not pid or pid <= 0:
        return {"attempted": False, "reason": "missing_pid"}
    result: dict[str, Any] = {"attempted": True, "pid": pid, "sigterm": False, "sigkill": False}
    try:
        os.killpg(pid, signal.SIGTERM)
        result["sigterm"] = True
    except ProcessLookupError:
        result["already_exited"] = True
        return result
    except Exception as exc:
        result["sigterm_error"] = str(exc)[:240]
        try:
            os.kill(pid, signal.SIGTERM)
            result["sigterm_pid"] = True
        except Exception as pid_exc:
            result["sigterm_pid_error"] = str(pid_exc)[:240]
    time.sleep(1)
    if process_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
            result["sigkill"] = True
        except ProcessLookupError:
            result["already_exited_after_sigterm"] = True
        except Exception as exc:
            result["sigkill_error"] = str(exc)[:240]
            try:
                os.kill(pid, signal.SIGKILL)
                result["sigkill_pid"] = True
            except Exception as pid_exc:
                result["sigkill_pid_error"] = str(pid_exc)[:240]
    result["alive_after"] = process_alive(pid)
    return result


def active_runner_path(agent_id: str, run_id: str) -> Path:
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(run_id))
    safe_agent = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(agent_id))
    return ACTIVE_RUNNERS / f"{safe_agent}-{safe_run_id}.json"


def active_runner_exists(agent_id: str, run_id: str) -> bool:
    path = active_runner_path(agent_id, run_id)
    record = read_json(path, {}) if path.exists() else {}
    if not isinstance(record, dict):
        return False
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    if result_path and result_path.exists() and runner_result_is_terminal(read_json(result_path, {})):
        return True
    if not active_runner_alive(record):
        return False
    return not active_runner_stale(record)


def task_retryable_agents(task: dict[str, Any]) -> set[str]:
    summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
    return {str(agent_id) for agent_id in (summary.get("retryable_agents") or []) if str(agent_id)}


def task_retryable_for_agent(task: dict[str, Any] | None, agent_id: str) -> bool:
    if not isinstance(task, dict):
        return False
    if str(task.get("status") or "").strip().lower() != "retryable":
        return False
    retryable_agents = task_retryable_agents(task)
    return not retryable_agents or agent_id in retryable_agents


def task_retry_after(task: dict[str, Any]) -> datetime | None:
    summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
    for raw in (
        task.get("retry_after"),
        task.get("cooldown_until"),
        summary.get("retry_after"),
        summary.get("cooldown_until"),
    ):
        parsed = parse_iso_datetime(str(raw or ""))
        if parsed:
            return parsed
    return None


def task_retry_due(task: dict[str, Any], now: datetime | None = None) -> bool:
    retry_after = task_retry_after(task)
    if retry_after is None:
        return True
    return (now or datetime.now(timezone.utc).astimezone()) >= retry_after


def task_has_dispatchable_agent(task: dict[str, Any], targets: list[str], run_id: str) -> bool:
    """Return True only when a task needs a new runner started.

    A task with an already-live runner is active, not pending backlog. Treating
    that state as pending caused repeated "already_running" room messages while
    the actual owner was still working.
    """
    effective_targets = private_dm_visible_targets(task, targets)
    if not effective_targets:
        return False
    for agent_id in effective_targets:
        if reply_artifact_exists(agent_id, run_id, task):
            continue
        if not active_runner_exists(agent_id, run_id):
            return True
    return bool(soft_deadline_handoff_targets(task, effective_targets))


def active_runner_count(agent_id: str | None = None) -> int:
    count = 0
    ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
    for path in ACTIVE_RUNNERS.glob("*.json"):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        if agent_id and str(record.get("agent_id") or "") != agent_id:
            continue
        if active_runner_alive(record) and not active_runner_stale(record):
            count += 1
    return count


def active_runner_blocking_count_for_standing_agenda() -> int:
    """Count live runners that should block proactive mainline injection.

    A runner that has already exceeded its soft deadline is still allowed to
    finish until the hard deadline, but it should not be a total room-level
    veto on the standing mainline lane. Otherwise one slow/stalled peer makes
    the room look silent and prevents degraded-quorum continuation.
    """
    count = 0
    ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
    for path in ACTIVE_RUNNERS.glob("*.json"):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        if not active_runner_alive(record):
            continue
        if active_runner_stale(record):
            continue
        if classify_runner_deadline_state(record) == "soft_deadline_exceeded":
            continue
        count += 1
    return count


def compact_slug(value: str) -> str:
    out = []
    for ch in str(value).lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")[:80] or "item"


def collaboration_ledger_slug(value: str) -> str:
    """Match agent_task_runner's per-task collaboration ledger slug."""
    out: list[str] = []
    last_dash = False
    for ch in str(value or "").strip():
        if ch.isalnum() or ch in {".", "_", "-"}:
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-._")[:120] or "agent-room-task"


def collaboration_ledger_state_path(task_id: str) -> Path:
    return ROOM / "collaboration-ledgers" / f"{collaboration_ledger_slug(task_id)}.json"


def collaboration_ledger_archive_path(task_id: str) -> Path:
    return ROOM / "collaboration-ledgers" / f"{collaboration_ledger_slug(task_id)}.jsonl"


def task_manifest_path_for_id(task_id: str) -> Path:
    return ROOM / "tasks" / task_id / "manifest.json"


LEDGER_POINT_KINDS = {"claim", "proposal", "risk", "evidence", "question", "decision", "summary"}
ACTIVE_COLLAB_CLAIM_STATUSES = {"active", "claimed", "running", "handoff"}


def parse_command_json(result: dict[str, Any]) -> dict[str, Any]:
    text = str(result.get("stdout") or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def collaboration_claim_expired(claim: dict[str, Any], now: datetime | None = None) -> bool:
    if str(claim.get("status") or "").strip() not in ACTIVE_COLLAB_CLAIM_STATUSES:
        return False
    expiry = parse_iso_datetime(str(claim.get("lease_expiry") or ""))
    if expiry is None:
        return False
    return (now or datetime.now(timezone.utc).astimezone()) > expiry


def collaboration_claim_has_live_or_result_runner(ledger: dict[str, Any], claim: dict[str, Any]) -> bool:
    agent_id = str(claim.get("agent_id") or "").strip()
    run_id = str(ledger.get("run_id") or ledger.get("task_id") or "").strip()
    if agent_id not in LOCAL_RUNTIME_AGENTS or not run_id:
        return False
    path = active_runner_path(agent_id, run_id)
    record = read_json(path, {}) if path.exists() else {}
    if not isinstance(record, dict) or not record:
        return False
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    if result_path and result_path.exists() and runner_result_is_terminal(read_json(result_path, {})):
        return True
    return active_runner_alive(record) and not active_runner_stale(record)


def reconcile_expired_collaboration_claims(limit: int = 50) -> list[dict[str, Any]]:
    """Turn orphaned expired claims into explicit blocked ledger items."""
    if limit <= 0:
        return []
    ledger_dir = ROOM / "collaboration-ledgers"
    if not ledger_dir.exists():
        return []
    reconciled: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).astimezone()
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
            result = run_cmd(
                [
                    sys.executable,
                    str(TOOLS / "collaboration_ledger.py"),
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
                timeout=30,
            )
            payload = parse_command_json(result)
            if result.get("ok") and payload.get("ok") and int(payload.get("released_count") or 0) > 0:
                reconciled.append({
                    "task_id": task_id,
                    "work_item_id": work_item_id,
                    "agent_id": agent_id,
                    "state_file": str(state_file),
                    "status": "blocked_expired_claim",
                    "released_count": payload.get("released_count"),
                })
            elif not result.get("ok") or payload.get("ok") is False:
                reconciled.append({
                    "task_id": task_id,
                    "work_item_id": work_item_id,
                    "agent_id": agent_id,
                    "state_file": str(state_file),
                    "status": "reconcile_failed",
                    "error": (payload.get("error") if isinstance(payload, dict) else None) or str(result.get("stderr") or "")[-400:],
                })
    return reconciled


def collaboration_work_item_for_agent(task: dict[str, Any], agent_id: str) -> str | None:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        for key in ("assigned_to", "agent_id", "owner", "claimed_by", "source_agent_id"):
            value = item.get(key)
            if value == agent_id:
                return item_id
            if isinstance(value, list) and agent_id in value:
                return item_id
    return None


def point_kind_for_peer_comment(comment: dict[str, Any]) -> str:
    raw_kind = str(comment.get("kind") or "").strip()
    if raw_kind in LEDGER_POINT_KINDS:
        return raw_kind
    body = f"{comment.get('title') or ''}\n{comment.get('body') or ''}".lower()
    if comment.get("blockers"):
        return "risk"
    if "?" in body or "？" in body:
        return "question"
    if any(token in body for token in ("blocker", "risk", "风险", "阻断", "失败", "regression")):
        return "risk"
    if any(token in body for token in ("决定", "结论", "accepted", "rejected", "decision")):
        return "decision"
    if any(token in body for token in ("patch", "修复", "方案", "proposal", "建议", "下一步")):
        return "proposal"
    if any(token in body for token in ("smoke", "artifact", "验证", "证据", "核实", "evidence")):
        return "evidence"
    return "summary"


def point_text_for_peer_comment(parent_task_id: str, speaker: str, title: str, body: str) -> str:
    text = "\n\n".join(part for part in (title.strip(), body.strip()) if part).strip()
    if text:
        return text[:2000]
    return f"{speaker} produced a material Agent Room comment on parent task {parent_task_id}."[:2000]


def source_artifact_for_peer_comment(speaker: str, comment: dict[str, Any]) -> str:
    for key in ("source_artifact", "artifact_path"):
        value = str(comment.get(key) or "").strip()
        if value:
            return value
    result_paths = comment.get("result_paths")
    if isinstance(result_paths, list):
        for value in result_paths:
            text = str(value or "").strip()
            if text:
                return text
    path = comment_path(speaker)
    return str(path) if path.exists() else ""


def existing_point_for_source_message(state_file: Path, source_message_id: str) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    try:
        state = read_json(state_file, {})
    except Exception:
        return None
    points = state.get("points") if isinstance(state, dict) and isinstance(state.get("points"), list) else []
    for point in reversed(points):
        if isinstance(point, dict) and str(point.get("source_message_id") or "") == source_message_id:
            return point
    return None


def record_material_peer_comment_point(
    parent_task: dict[str, Any],
    comment: dict[str, Any],
    *,
    body: str,
    title: str,
) -> dict[str, Any]:
    parent_task_id = str(parent_task.get("task_id") or parent_task.get("run_id") or "").strip()
    speaker = str(comment.get("agent_id") or "").strip()
    if not parent_task_id or speaker not in LOCAL_RUNTIME_AGENTS:
        return {"ok": False, "status": "skipped_missing_parent_or_speaker"}

    state_file = collaboration_ledger_state_path(parent_task_id)
    archive_file = collaboration_ledger_archive_path(parent_task_id)
    message_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    source_message_id = f"agent-comment:{speaker}:{comment.get('run_id') or parent_task_id}:{message_hash}"

    existing = existing_point_for_source_message(state_file, source_message_id)
    if existing and existing.get("id"):
        return {"ok": True, "status": "already_recorded", "point_id": existing.get("id"), "state_file": str(state_file)}

    if not state_file.exists():
        manifest_path = task_manifest_path_for_id(parent_task_id)
        if not manifest_path.exists():
            return {
                "ok": False,
                "status": "parent_ledger_missing",
                "state_file": str(state_file),
                "manifest_path": str(manifest_path),
            }
        init_result = run_cmd(
            [
                sys.executable,
                str(TOOLS / "collaboration_ledger.py"),
                "--state-file", str(state_file),
                "--archive-file", str(archive_file),
                "init",
                "--task-file", str(manifest_path),
                "--if-needed",
            ],
            timeout=30,
        )
        init_payload = parse_command_json(init_result)
        if not init_result.get("ok") or not init_payload.get("ok", init_result.get("ok")):
            return {"ok": False, "status": "parent_ledger_init_failed", "detail": init_payload or init_result}

    existing = existing_point_for_source_message(state_file, source_message_id)
    if existing and existing.get("id"):
        return {"ok": True, "status": "already_recorded", "point_id": existing.get("id"), "state_file": str(state_file)}

    args = [
        sys.executable,
        str(TOOLS / "collaboration_ledger.py"),
        "--state-file", str(state_file),
        "--archive-file", str(archive_file),
        "point",
        "--agent-id", speaker,
        "--kind", point_kind_for_peer_comment(comment),
        "--text", point_text_for_peer_comment(parent_task_id, speaker, title, body),
        "--source-message-id", source_message_id,
    ]
    work_item_id = collaboration_work_item_for_agent(parent_task, speaker)
    if work_item_id:
        args.extend(["--work-item-id", work_item_id])
    source_artifact = source_artifact_for_peer_comment(speaker, comment)
    if source_artifact:
        args.extend(["--source-artifact", source_artifact])
    point_result = run_cmd(args, timeout=30)
    point_payload = parse_command_json(point_result)
    if not point_result.get("ok") or not point_payload.get("ok", point_result.get("ok")):
        return {"ok": False, "status": "point_record_failed", "detail": point_payload or point_result}
    return {
        "ok": True,
        "status": "recorded",
        "point_id": point_payload.get("point_id"),
        "state_file": str(state_file),
        "source_message_id": source_message_id,
    }


def collaboration_status_path(run_id: str) -> Path:
    return COLLABORATION_STATUS / f"{collaboration_ledger_slug(run_id)}.json"


def lease_state(value: Any) -> str:
    if not value:
        return "missing"
    parsed = parse_iso_datetime(str(value))
    if parsed is None:
        return "invalid"
    return "expired" if datetime.now(timezone.utc).astimezone() > parsed else "active"


def seconds_since_iso(value: Any) -> int | None:
    parsed = parse_iso_datetime(str(value or ""))
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc).astimezone() - parsed).total_seconds()))


def file_size_or_none(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


def output_state(stdout_size: int | None, stderr_size: int | None) -> str:
    if int(stdout_size or 0) > 0 or int(stderr_size or 0) > 0:
        return "has_local_output"
    return "no_local_output_yet"


def runner_liveness_state(status: str, output: str) -> str:
    if status == "running":
        return "alive_with_local_output" if output == "has_local_output" else "alive_black_box_no_output_yet"
    if status == "stale_running":
        return "stale_runner_with_output" if output == "has_local_output" else "stale_runner_no_output"
    if status == "dead_missing_result":
        return "dead_missing_result"
    if status == "result_pending_harvest":
        return "result_pending_harvest"
    return status or "unknown"


def active_runner_status_records(run_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
    for path in sorted(ACTIVE_RUNNERS.glob("*.json")):
        try:
            record = read_json(path, {})
        except Exception:
            continue
        if not isinstance(record, dict) or str(record.get("run_id") or "") != run_id:
            continue
        agent_id = str(record.get("agent_id") or "")
        runner_dir = Path(str(record.get("runner_dir") or ""))
        result_path = runner_dir / "result.json" if runner_dir else None
        try:
            runner_result = read_json(result_path, {}) if result_path and result_path.exists() else {}
        except Exception:
            runner_result = {}
        has_result = isinstance(runner_result, dict) and runner_result_is_terminal(runner_result)
        alive = active_runner_alive(record)
        stale = alive and active_runner_stale(record)
        stdout_path = Path(str(record.get("stdout_path") or runner_dir / "stdout.log"))
        stderr_path = Path(str(record.get("stderr_path") or runner_dir / "stderr.log"))
        stdout_size = file_size_or_none(stdout_path)
        stderr_size = file_size_or_none(stderr_path)
        local_output_state = output_state(stdout_size, stderr_size)
        if has_result:
            status = "result_pending_harvest"
        elif alive and stale:
            status = "stale_running"
        elif alive:
            status = "running"
        else:
            status = "dead_missing_result"
        records.append(
            {
                "agent_id": agent_id,
                "status": status,
                "pid": int(record.get("pid") or 0),
                "active_runner": str(path),
                "age_seconds": runner_age_seconds(record),
                "deadline_state": classify_runner_deadline_state(record),
                "stdout_size": stdout_size,
                "stderr_size": stderr_size,
                "output_state": local_output_state,
                "liveness_state": runner_liveness_state(status, local_output_state),
                "soft_deadline_at": record.get("soft_deadline_at"),
                "hard_deadline_at": record.get("hard_deadline_at"),
                "last_chat_action_at": record.get("last_chat_action_at"),
                "last_chat_action_age_seconds": seconds_since_iso(record.get("last_chat_action_at")),
                "last_chat_action_reason": record.get("last_chat_action_reason"),
            }
        )
    return records


def build_agent_liveness_snapshot(
    participants: list[Any],
    active_runners: list[dict[str, Any]],
    ledger_summary: dict[str, Any],
    agent_runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    agent_ids = {
        str(agent_id)
        for agent_id in participants
        if str(agent_id or "").strip()
    } | {
        str(row.get("agent_id") or "")
        for row in active_runners
        if str(row.get("agent_id") or "").strip()
    } | {
        str(run.get("agent_id") or "")
        for run in agent_runs
        if str(run.get("agent_id") or "").strip()
    }
    out: dict[str, dict[str, Any]] = {}
    work_items = ledger_summary.get("work_items") if isinstance(ledger_summary.get("work_items"), list) else []
    claims = ledger_summary.get("claims") if isinstance(ledger_summary.get("claims"), list) else []
    priority = {
        "dead_missing_result": 100,
        "stale_runner_no_output": 90,
        "stale_runner_with_output": 80,
        "result_pending_harvest": 70,
        "alive_black_box_no_output_yet": 60,
        "alive_with_local_output": 50,
        "claimed_no_live_runner": 40,
        "completed": 20,
        "blocked": 20,
        "not_observed": 0,
    }
    for agent_id in sorted(agent_ids):
        agent_runner_rows = [row for row in active_runners if row.get("agent_id") == agent_id]
        agent_work_items = [
            item for item in work_items
            if isinstance(item, dict) and (item.get("assigned_to") == agent_id or item.get("claimed_by") == agent_id)
        ]
        agent_claims = [claim for claim in claims if isinstance(claim, dict) and claim.get("agent_id") == agent_id]
        states = [str(row.get("liveness_state") or "unknown") for row in agent_runner_rows]
        if not states:
            if any(str(item.get("status") or "") == "claimed" for item in agent_work_items):
                states = ["claimed_no_live_runner"]
            elif any(str(item.get("status") or "") == "completed" for item in agent_work_items):
                states = ["completed"]
            elif any(str(item.get("status") or "") == "blocked" for item in agent_work_items):
                states = ["blocked"]
            else:
                states = ["not_observed"]
        state = max(states, key=lambda value: priority.get(value, 0))
        out[agent_id] = {
            "agent_id": agent_id,
            "state": state,
            "runner_count": len(agent_runner_rows),
            "live_runner_count": sum(1 for row in agent_runner_rows if row.get("status") in {"running", "stale_running"}),
            "needs_attention": state in {"dead_missing_result", "stale_runner_no_output", "claimed_no_live_runner"},
            "black_box_runner_count": sum(1 for row in agent_runner_rows if row.get("output_state") == "no_local_output_yet" and row.get("status") in {"running", "stale_running"}),
            "work_items": agent_work_items,
            "claims": agent_claims,
            "latest_agent_run": next((run for run in reversed(agent_runs) if run.get("agent_id") == agent_id), None),
        }
    return out


def existing_degraded_quorum_record(task: dict[str, Any]) -> dict[str, Any] | None:
    degraded_quorum = task.get("degraded_quorum")
    if isinstance(degraded_quorum, dict):
        return degraded_quorum
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    degraded_quorum = collaboration.get("degraded_quorum")
    return degraded_quorum if isinstance(degraded_quorum, dict) else None


def local_collaboration_participants(task: dict[str, Any]) -> list[str]:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    raw = collaboration.get("participants") or task.get("target_agents") or []
    participants: list[str] = []
    for agent_id in raw:
        text = str(agent_id or "").strip()
        if text in LOCAL_RUNTIME_AGENTS and text not in participants:
            participants.append(text)
    return participants


def status_snapshot_degraded_quorum_record(
    task: dict[str, Any],
    *,
    reason: str,
    unavailable_agents: list[str],
    continued_by: list[str] | None = None,
    evidence: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("run_id") or "task")
    return {
        "schema": "openclaw.agent_room.degraded_quorum.v0",
        "mode": "status_snapshot",
        "status": "degraded_quorum_observed",
        "created_at": now_iso(),
        "parent_task_id": task_id,
        "reason": reason,
        "unavailable_agents": [
            {
                "agent_id": agent_id,
                "reason": reason,
                "evidence": evidence,
            }
            for agent_id in unavailable_agents
        ],
        "continued_by": continued_by or [],
        "follow_up_review_needed_by": unavailable_agents,
        "main_review_needed": True,
        "main_review_reason": "status surface observed collaboration without full local agent quorum",
        "detail": detail or {},
    }


def infer_status_snapshot_degraded_quorum(
    task: dict[str, Any],
    active_runners: list[dict[str, Any]],
    agent_liveness: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    existing = existing_degraded_quorum_record(task)
    if existing:
        return existing

    participants = local_collaboration_participants(task)
    if len(participants) <= 1:
        return None

    runner_summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
    quality_gate = runner_summary.get("collaboration_quality_gate") if isinstance(runner_summary.get("collaboration_quality_gate"), dict) else {}
    quality_gate_status = str(task.get("quality_gate_status") or quality_gate.get("status") or "")
    if quality_gate_status == "degraded_quorum":
        unavailable = [
            str(agent_id)
            for agent_id in (quality_gate.get("missing_agents") or runner_summary.get("missing_agents") or [])
            if str(agent_id) in participants
        ]
        if not unavailable:
            completed = {
                str(agent_id)
                for agent_id in (runner_summary.get("completed_agents") or [])
                if str(agent_id) in participants
            }
            blocked = {
                str(agent_id)
                for agent_id in (runner_summary.get("blocked_agents") or [])
                if str(agent_id) in participants
            }
            failed = {
                str(agent_id)
                for agent_id in (runner_summary.get("failed_agents") or [])
                if str(agent_id) in participants
            }
            unavailable = sorted(set(participants).difference(completed | blocked | failed))
        if not unavailable:
            unavailable = [agent_id for agent_id in participants if agent_liveness.get(agent_id, {}).get("needs_attention")]
        return status_snapshot_degraded_quorum_record(
            task,
            reason=str(quality_gate.get("reason") or "quality_gate_degraded_quorum"),
            unavailable_agents=sorted(set(unavailable)),
            continued_by=[agent_id for agent_id in participants if agent_id not in set(unavailable)],
            evidence="task_quality_gate_status",
            detail={"quality_gate": quality_gate, "runner_summary_degraded_quorum": bool(runner_summary.get("degraded_quorum"))},
        )

    attention_agents = sorted({
        str(agent_id)
        for agent_id, row in agent_liveness.items()
        if str(agent_id) in participants and row.get("needs_attention")
    })
    if attention_agents and set(participants).issubset(attention_agents):
        runner_states = {
            str(row.get("agent_id") or ""): str(row.get("liveness_state") or row.get("status") or "unknown")
            for row in active_runners
            if str(row.get("agent_id") or "") in participants
        }
        return status_snapshot_degraded_quorum_record(
            task,
            reason="all_local_agents_need_attention",
            unavailable_agents=attention_agents,
            continued_by=[],
            evidence="active_runner_liveness",
            detail={"runner_states": runner_states},
        )
    return None


def write_collaboration_status_snapshot(
    task: dict[str, Any],
    phase: str,
    *,
    agent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Persist a queryable status surface for silent collaboration windows."""
    if not isinstance(task.get("collaboration"), dict):
        return None
    task_id = str(task.get("task_id") or "")
    run_id = str(task.get("run_id") or task_id)
    if not task_id or not run_id:
        return None
    ledger_path = collaboration_ledger_state_path(task_id)
    try:
        ledger = read_json(ledger_path, {}) if ledger_path.exists() else {}
    except Exception:
        ledger = {}
    active_runners = active_runner_status_records(run_id)
    ledger_ok = (
        isinstance(ledger, dict)
        and ledger.get("schema") == "openclaw.agent_room.collaboration_ledger.v0"
        and str(ledger.get("task_id") or "") == task_id
    )
    if ledger_ok:
        work_items = ledger.get("work_items") if isinstance(ledger.get("work_items"), list) else []
        claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
        artifacts = ledger.get("artifacts") if isinstance(ledger.get("artifacts"), list) else []
        blockers = ledger.get("blockers") if isinstance(ledger.get("blockers"), list) else []
        handoffs = ledger.get("handoffs") if isinstance(ledger.get("handoffs"), list) else []
        ledger_summary = {
            "available": True,
            "path": str(ledger_path),
            "status": ledger.get("status"),
            "updated_at": ledger.get("updated_at"),
            "work_items": [
                {
                    "id": item.get("id"),
                    "assigned_to": item.get("assigned_to"),
                    "status": item.get("status"),
                    "claimed_by": item.get("claimed_by"),
                    "lease_expiry": item.get("lease_expiry"),
                    "lease_state": lease_state(item.get("lease_expiry")),
                }
                for item in work_items
                if isinstance(item, dict)
            ],
            "claims": [
                {
                    "work_item_id": claim.get("work_item_id"),
                    "agent_id": claim.get("agent_id"),
                    "status": claim.get("status"),
                    "claimed_at": claim.get("claimed_at"),
                    "lease_expiry": claim.get("lease_expiry"),
                    "lease_state": lease_state(claim.get("lease_expiry")),
                }
                for claim in claims
                if isinstance(claim, dict)
            ],
            "artifact_count": len(artifacts),
            "blocker_count": len(blockers),
            "handoff_count": len(handoffs),
        }
    else:
        ledger_summary = {
            "available": False,
            "path": str(ledger_path),
            "status": None,
            "work_items": [],
            "claims": [],
            "artifact_count": 0,
            "blocker_count": 0,
            "handoff_count": 0,
        }
    if any(item.get("status") == "dead_missing_result" for item in active_runners):
        snapshot_status = "runner_attention_needed"
    elif any(run.get("missing_result_json") or run.get("orphan_harvest") for run in (agent_runs or [])):
        snapshot_status = "runner_attention_needed"
    elif any(item.get("status") in {"running", "stale_running"} for item in active_runners):
        snapshot_status = "running"
    elif any(item.get("status") == "result_pending_harvest" for item in active_runners):
        snapshot_status = "result_pending_harvest"
    else:
        snapshot_status = str(ledger_summary.get("status") or "open")
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    agent_liveness = build_agent_liveness_snapshot(
        collaboration.get("participants") or [],
        active_runners,
        ledger_summary,
        agent_runs or [],
    )
    degraded_quorum = infer_status_snapshot_degraded_quorum(task, active_runners, agent_liveness)
    snapshot = {
        "schema": "openclaw.agent_room.collaboration_status.v0",
        "room_id": task.get("room_id"),
        "task_id": task_id,
        "run_id": run_id,
        "phase": phase,
        "status": snapshot_status,
        "updated_at": now_iso(),
        "participants": collaboration.get("participants") or [],
        "degraded_quorum": degraded_quorum,
        "active_runners": active_runners,
        "ledger": ledger_summary,
        "agent_liveness": agent_liveness,
        "agent_runs": agent_runs or [],
        "room_visibility": {
            "telegram_projection": "final_comments_or_explicit_status_only",
            "status_surface": str(collaboration_status_path(run_id)),
        },
    }
    write_json(collaboration_status_path(run_id), snapshot)
    return snapshot


def task_exists(task_id: str) -> bool:
    if (ROOM / "tasks" / task_id / "manifest.json").exists():
        return True
    return any(str(row.get("task_id") or "") == task_id for row in read_jsonl(ROOM / "tasks.jsonl"))


def is_private_dm_room(room_id: str) -> bool:
    return str(room_id or "").startswith("dm-")


def private_dm_owner_agent(room_id: str) -> str | None:
    if not is_private_dm_room(room_id):
        return None
    for agent_id in sorted(LOCAL_RUNTIME_AGENTS):
        if str(room_id or "").startswith(f"dm-{compact_slug(agent_id)}-"):
            return agent_id
    return None


def private_dm_visible_targets(task: dict[str, Any], targets: list[str]) -> list[str]:
    owner = private_dm_owner_agent(str(task.get("room_id") or ""))
    if not owner:
        return targets
    return [agent_id for agent_id in targets if agent_id == owner]


def private_dm_agent_mismatch(task: dict[str, Any], comments: list[dict[str, Any]]) -> bool:
    owner = private_dm_owner_agent(str(task.get("room_id") or ""))
    if not owner:
        return False
    targets = [str(agent_id) for agent_id in (task.get("target_agents") or []) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
    if targets and any(agent_id != owner for agent_id in targets):
        return True
    comment_agents = {
        str(comment.get("agent_id") or "")
        for comment in comments
        if str(comment.get("agent_id") or "")
    }
    return any(agent_id != owner for agent_id in comment_agents)


def peer_followup_may_project_to_telegram(parent_task: dict[str, Any]) -> bool:
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    if str(source.get("transport") or "") != "telegram":
        return False
    if str(parent_task.get("requested_by") or "") != "telegram-user":
        return False
    # A private bot chat is a single-agent visible surface. Other agents may be
    # consulted through local artifacts, but their bot must not answer in its own
    # private chat as a side effect of a Claude/Codex DM. If Alex wants visible
    # multi-agent discussion, the Telegram group room is the explicit front.
    if is_private_dm_room(str(parent_task.get("room_id") or "")):
        return False
    delivery_policy = str(parent_task.get("delivery_policy") or "")
    if delivery_policy == "targeted_reply":
        return True
    # Broadcast-derived peer follow-ups can be useful for interaction, but they
    # are also visible room traffic. Keep the default local and require an
    # explicit task/env opt-in for experiments instead of changing global UX.
    visible_broadcast_followup = (
        parent_task.get("broadcast_peer_followup_visible") is True
        or os.environ.get("AGENT_ROOM_BROADCAST_PEER_FOLLOWUP_VISIBLE", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if delivery_policy == "broadcast_all_agents_decide" or parent_task.get("broadcast_targets"):
        return visible_broadcast_followup
    return False


def collaboration_tick_config(parent_task: dict[str, Any]) -> dict[str, Any]:
    """Return opt-in collaboration tick policy for this room/task.

    The default is intentionally closed.  A room/global config or task-local
    field must opt in before an internal peer follow-up can create another
    round after harvest.
    """
    config: dict[str, Any] = {}
    global_config = read_json(ROOM / "config" / "standing-agenda.json", {})
    global_tick = global_config.get("collaboration_tick") if isinstance(global_config, dict) else None
    if isinstance(global_tick, dict):
        config.update(global_tick)
    room_id = str(parent_task.get("room_id") or "")
    if room_id:
        room_json = read_json(ROOM / "rooms" / room_id / "room.json", {})
        if isinstance(room_json, dict):
            room_tick = room_json.get("collaboration_tick")
            policies = room_json.get("policies") if isinstance(room_json.get("policies"), dict) else {}
            policy_tick = policies.get("collaboration_tick") if isinstance(policies, dict) else None
            if isinstance(policy_tick, dict):
                config.update(policy_tick)
            if isinstance(room_tick, dict):
                config.update(room_tick)
    task_tick = parent_task.get("collaboration_tick")
    if isinstance(task_tick, dict):
        config.update(task_tick)
    return config


def collaboration_tick_enabled(parent_task: dict[str, Any]) -> bool:
    override = env_flag("AGENT_ROOM_COLLAB_TICK_ENABLED")
    if override is not None:
        return override
    if isinstance(parent_task.get("collab_tick_enabled"), bool):
        return bool(parent_task.get("collab_tick_enabled"))
    config = collaboration_tick_config(parent_task)
    return bool(config.get("enabled"))


def collaboration_tick_max_rounds(parent_task: dict[str, Any]) -> int:
    raw_env = os.environ.get("AGENT_ROOM_COLLAB_TICK_MAX_ROUNDS")
    if raw_env:
        try:
            return max(1, int(raw_env))
        except ValueError:
            pass
    for value in (
        parent_task.get("collab_tick_max_rounds"),
        collaboration_tick_config(parent_task).get("max_rounds"),
    ):
        if value is None:
            continue
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return 2


def recent_room_context_excerpt(room_id: str, limit: int = 12) -> str:
    path = ROOM / "rooms" / room_id / "messages.jsonl"
    rows = read_jsonl(path)
    lines: list[str] = []
    for item in rows[-limit:]:
        actor = item.get("actor_agent_id") or "user"
        created = item.get("created_at") or ""
        text = str(item.get("text") or "").replace("\n", " ").strip()
        if len(text) > 600:
            text = text[:600] + "...[truncated]"
        lines.append(f"- {created} {actor}: {text}")
    return "\n".join(lines)


def create_collab_followup_task(parent_task: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any] | None:
    """Create one bounded peer follow-up task from an agent comment.

    This is the missing piece between "parallel one-shot replies" and actual
    room collaboration: a material agent comment should become something another
    peer can answer, but only for a bounded number of rounds so the room cannot
    self-amplify indefinitely.
    """
    room_id = str(parent_task.get("room_id") or comment.get("room_id") or "")
    chat_id = task_chat_id(parent_task)
    speaker = str(comment.get("agent_id") or "")
    if not room_id or not chat_id or speaker not in LOCAL_RUNTIME_AGENTS:
        return None
    # Only user-originated room tasks may normally spawn a peer follow-up.
    # Exception: auto-review/repair follow-ups may trigger one bounded
    # verification/repair round when the follow-up itself finds another factual
    # or workflow error. This makes agents fix each other without turning the
    # room into an unbounded process discussion.
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    source_transport = str(source.get("transport") or "")
    internal_transport = source_transport in INTERNAL_AGENT_ROOM_TRANSPORTS
    allow_internal_collab_tick = (
        source_transport == "agent-room-collab-followup"
        and collaboration_tick_enabled(parent_task)
    )
    if internal_transport and not (
        parent_task.get("auto_review_repair")
        and comment_indicates_auto_review_repair(comment)
        or allow_internal_collab_tick
    ):
        return None
    # Direct user @mentions choose the first responder, not a permanent silence
    # order for every other agent. We still gate peer follow-up below through
    # material triggers so a single @ does not create routine duplicated replies.
    parent_round = int(parent_task.get("collab_round") or 0)
    max_rounds = review_repair_followup_max_rounds(parent_task, comment)
    if parent_round >= max_rounds:
        return None
    if not is_material_peer_comment(comment):
        return None
    parent_targets = {
        str(agent_id)
        for agent_id in (parent_task.get("target_agents") or [])
        if str(agent_id) in LOCAL_RUNTIME_AGENTS
    }
    if not should_create_collab_followup(parent_task, comment, parent_targets):
        return None
    auto_review_repair = should_expand_peer_targets_for_review_repair(parent_task, comment)
    targets = peer_followup_targets(parent_task, comment, parent_targets)
    if not targets:
        return None
    collaboration_participants = list(dict.fromkeys([speaker, *targets]))
    expected_outputs = collaboration_followup_expected_outputs(parent_task, comment, auto_review_repair)
    collab_intent = collaboration_followup_intent(parent_task, comment, expected_outputs, auto_review_repair)
    tick_enabled = collaboration_tick_enabled(parent_task)
    parent_task_id = str(parent_task.get("task_id") or parent_task.get("run_id") or "task")
    digest = hashlib.sha256(
        json.dumps({
            "parent_task_id": parent_task_id,
            "speaker": speaker,
            "run_id": comment.get("run_id"),
            "round": parent_round + 1,
            "auto_review_repair": auto_review_repair,
            "body": str(comment.get("body") or "")[:1000],
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    task_id = f"collab-{compact_slug(room_id)}-{digest}"
    if task_exists(task_id):
        return None

    followup_dir = ROOM / "collab-followups" / task_id
    brief_path = followup_dir / "brief.md"
    followup_dir.mkdir(parents=True, exist_ok=True)
    body = str(comment.get("body") or "").strip()
    title = str(comment.get("title") or "").strip()
    source_point = record_material_peer_comment_point(parent_task, comment, body=body, title=title)
    source_point_id = str(source_point.get("point_id") or "").strip() if source_point.get("ok") else ""
    followup_title = "# Agent Room automatic review/repair follow-up" if auto_review_repair else "# Agent Room peer follow-up"
    followup_instruction = (
        "这轮是自动审查/修复 follow-up：先核查 peer 说法是否有证据；若发现事实、边界、流程或实现问题，在当前权限内直接修正、补 patch、补 smoke 或留下可复现 blocker。不要等 Alex 再纠错。"
        if auto_review_repair
        else "请先引用下面 peer comment 的一个具体主张、产物或下一步，再说明你同意什么、反对什么、还缺什么证据、下一步谁该做什么；不要另起一篇独立回答。"
    )
    brief = "\n".join([
        followup_title,
        "",
        "你正在参与 OpenClaw 进化群的多 agent 协作，不是单独回答用户的一次性 runner。",
        followup_instruction,
        "如果能补 patch 或验证证据，就直接做；如果不能，明确 blocker。使用中文。",
        "",
        f"Parent task: {parent_task_id}",
        f"Collab round: {parent_round + 1}/{max_rounds}",
        f"Peer speaker: {speaker}",
        f"Peer title: {title}",
        "",
        "## Collaboration contract",
        f"- collab_intent: `{collab_intent}`",
        f"- expected_output: `{', '.join(expected_outputs)}`",
        "- stop_condition: `max_rounds_or_material_blocker`",
        "",
        "## Peer comment",
        body[:6000],
        "",
        "## Original recent room context",
        recent_room_context_excerpt(room_id, limit=12),
    ])
    brief_path.write_text(brief + "\n", encoding="utf-8")
    created = now_iso()
    permissions = dict(parent_task.get("permissions") or {})
    # Capability is bounded by task permissions and safety gates, not by a fixed
    # reviewer/executor role. Keep external effects closed; allow source/state
    # edits for material implementation follow-ups only when the parent task
    # already allowed them.
    if not peer_followup_can_edit(parent_task, comment):
        permissions["source_edit"] = False
        permissions["global_state_change"] = False
    permissions["telegram_send"] = False
    permissions["secrets_access"] = False
    permissions["github_push"] = False
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": room_id,
        "requested_by": "agent-room-collab-followup",
        "target_agents": targets,
        "lane": "peer_collaboration_followup",
        "brief_path": str(brief_path),
        "context_paths": parent_task.get("context_paths") or [],
        "permissions": permissions,
        "agent_room_profile": "material-peer-followup",
        "expected_outputs": [
            {
                "type": expected_output,
                "source": "collaboration_action",
                "required": True,
            }
            for expected_output in expected_outputs
        ],
        "status": "queued",
        "review_status": "requested",
        "blocked_reason": None,
        "result_paths": [],
        "canonical_imported": True,
        "created_at": created,
        "updated_at": created,
        "lease": {"owner": None, "heartbeat_at": None, "expires_at": None},
        "heartbeat": {"last_seen_at": None},
        "retry_budget": {"max_attempts": 1, "attempt": 0},
        "manual_boundary": True,
        "quality_gate_status": "not_applicable",
        "side_effect_gate_status": "closed",
        "telegram_projection_status": "room_bridge_gate_only",
        "peer_followup_visible_allowed": peer_followup_may_project_to_telegram(parent_task),
        "collab_round": parent_round + 1,
        "collab_tick_enabled": tick_enabled,
        "collab_tick_max_rounds": max_rounds if tick_enabled else None,
        "collab_parent_task_id": parent_task_id,
        "collab_parent_agent_id": speaker,
        "collab_source_point_id": source_point_id or None,
        "collab_source_point_status": source_point.get("status"),
        "auto_review_repair": auto_review_repair,
        "collab_intent": collab_intent,
        "collaboration_action": {
            "schema": "openclaw.agent_room.collaboration_action.v0",
            "action": collab_intent,
            "source_agent_id": speaker,
            "source_point_id": source_point_id or None,
            "source_point_status": source_point.get("status"),
            "target_agent_ids": targets,
            "expected_output": expected_outputs[0],
            "expected_outputs": expected_outputs,
            "evidence_paths": [],
            "stop_condition": "max_rounds_or_material_blocker",
        },
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "mode": "auto_review_repair" if auto_review_repair else "peer_followup",
            "status": "open",
            "participants": collaboration_participants,
            "work_items": peer_followup_work_items(
                targets=targets,
                speaker=speaker,
                collab_intent=collab_intent,
                expected_outputs=expected_outputs,
                auto_review_repair=auto_review_repair,
            ),
            "claims": [],
            "handoffs": [],
            "artifacts": [],
            "blockers": [],
            "max_rounds": max_rounds,
            "created_at": created,
        },
        "source": {
            "transport": "agent-room-collab-followup",
            "chat_id": chat_id,
            "update_id": f"agent-comment:{speaker}:{comment.get('run_id')}",
            "message_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        },
    }
    manifest = ROOM / "tasks" / task_id / "manifest.json"
    write_json(manifest, task)
    append_jsonl(ROOM / "tasks.jsonl", [task])
    append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])
    return task


def peer_followup_work_items(
    *,
    targets: list[str],
    speaker: str,
    collab_intent: str,
    expected_outputs: list[str],
    auto_review_repair: bool,
) -> list[dict[str, Any]]:
    outputs = expected_outputs or ["evidence"]
    base_id = "auto_review_repair" if auto_review_repair else "peer_followup_response"
    description = (
        "Review the peer comment for factual/process/implementation errors and directly correct, patch, smoke, or record a reproducible blocker."
        if auto_review_repair
        else f"Respond to the peer comment with {collab_intent} output: {', '.join(outputs)}."
    )
    multiple_targets = len(targets) > 1
    return [
        {
            "id": f"{base_id}_{compact_slug(agent_id)}" if multiple_targets else base_id,
            "status": "open",
            "assigned_to": agent_id,
            "source_agent_id": speaker,
            "collab_intent": collab_intent,
            "expected_output": outputs[0],
            "expected_outputs": outputs,
            "description": description,
        }
        for agent_id in targets
    ]


RUNTIME_TAKEOVER_BLOCKERS = {
    "claude_ark_model_chain_failed",
    "codex_cli_failed",
    "usage_limit",
    "rate_limit",
    "model_overloaded",
    "model_unavailable",
    "agent_cli_not_installed",
    "agent_not_ready",
    "unsupported_agent",
    "runner_process_missing",
    "runner_timeout",
    "duplicate_run_dir",
    "raw_internal_json_body",
    "raw_internal_status_body",
}


def body_indicates_runtime_takeover(body: str) -> bool:
    """Natural-language body text must not decide runtime takeover.

    Takeover is a state-machine action. It should be driven by structured
    blocker/projection fields produced by the runner, not by scanning the user's
    or an agent's prose for words like “timeout / 额度 / 卡住”. Keyword matching
    was the exact class of failure Alex called out: descriptive text and actual
    runtime state were being conflated.
    """
    return False


def is_runtime_takeover_trigger_comment(comment: dict[str, Any]) -> bool:
    blockers = {str(item).strip().lower() for item in (comment.get("blockers") or []) if str(item).strip()}
    if blockers.intersection(RUNTIME_TAKEOVER_BLOCKERS):
        return True
    if any(item.startswith("runner_exit_") for item in blockers):
        return True
    projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
    if projection_status in {"local_only_runner_failure", "suppressed_runner_failure", "user_visible_runner_failure"} and blockers:
        return True
    return bool(comment.get("runtime_takeover_required") is True and blockers)


def runtime_takeover_reply_visible_allowed(parent_task: dict[str, Any]) -> bool:
    """Allow the peer's semantic takeover answer back into the room.

    Runner failures themselves stay local, but a recovery task for a Telegram
    group mention may produce a visible semantic answer. A private bot DM remains
    owned by that bot; another agent can recover locally but must not reply
    through its own private chat as a side effect.
    """
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    if is_private_dm_room(str(parent_task.get("room_id") or "")):
        return False
    return (
        str(source.get("transport") or "") == "telegram"
        and str(parent_task.get("requested_by") or "") == "telegram-user"
    )


def degraded_quorum_record_for_runtime_takeover(
    parent_task: dict[str, Any],
    *,
    failed_agent: str,
    blockers: list[str],
    takeover_targets: list[str],
    comment: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Record why a collaboration continues without one unavailable peer.

    This is deliberately small and structured. It gives OpenClaw/main and peer
    agents a status surface to review later without exposing raw runner logs or
    asking Alex to arbitrate routine runtime recovery.
    """
    parent_task_id = str(parent_task.get("task_id") or parent_task.get("run_id") or "task")
    primary_reason = blockers[0] if blockers else "runner_failure"
    return {
        "schema": "openclaw.agent_room.degraded_quorum.v0",
        "mode": "runtime_takeover",
        "status": "continued_without_unavailable_peer",
        "created_at": created_at,
        "parent_task_id": parent_task_id,
        "unavailable_agents": [
            {
                "agent_id": failed_agent,
                "reason": primary_reason,
                "blockers": blockers[:10],
                "failed_run_id": comment.get("run_id"),
                "evidence": "structured_runner_failure_comment",
            }
        ],
        "continued_by": takeover_targets,
        "follow_up_review_needed_by": [failed_agent],
        "main_review_needed": True,
        "main_review_reason": "runtime/session context and UX boundary review after degraded-quorum continuation",
    }


def create_runtime_takeover_task(parent_task: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any] | None:
    """Create a bounded peer takeover when a single targeted local agent fails.

    This is different from ordinary peer discussion: Alex asked that another
    available agent should keep working when one runtime is blocked. Only
    single-target failures create a takeover; broadcast tasks already launch the
    other peer independently.
    """
    room_id = str(parent_task.get("room_id") or comment.get("room_id") or "")
    chat_id = task_chat_id(parent_task)
    speaker = str(comment.get("agent_id") or "")
    if not room_id or not chat_id or speaker not in LOCAL_RUNTIME_AGENTS:
        return None
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    # Do not let runtime recovery tasks recursively spawn more recovery tasks.
    # They are internal plumbing, not an agent conversation tree.
    if str(source.get("transport") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS:
        return None
    parent_targets = {
        str(agent_id)
        for agent_id in (parent_task.get("target_agents") or [])
        if str(agent_id) in LOCAL_RUNTIME_AGENTS
    }
    if parent_targets and parent_targets != {speaker}:
        return None
    if not is_runtime_takeover_trigger_comment(comment):
        return None
    targets = sorted(LOCAL_RUNTIME_AGENTS.difference({speaker}))
    if not targets:
        return None

    parent_task_id = str(parent_task.get("task_id") or parent_task.get("run_id") or "task")
    body = str(comment.get("body") or "").strip()
    blockers = [str(item) for item in (comment.get("blockers") or []) if str(item)]
    digest = hashlib.sha256(
        json.dumps({
            "parent_task_id": parent_task_id,
            "speaker": speaker,
            "run_id": comment.get("run_id"),
            "blockers": blockers,
            "kind": "runtime_takeover",
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    task_id = f"takeover-{compact_slug(room_id)}-{digest}"
    if task_exists(task_id):
        return None

    created = now_iso()
    degraded_quorum = degraded_quorum_record_for_runtime_takeover(
        parent_task,
        failed_agent=speaker,
        blockers=blockers,
        takeover_targets=targets,
        comment=comment,
        created_at=created,
    )
    followup_dir = ROOM / "runtime-takeovers" / task_id
    brief_path = followup_dir / "brief.md"
    followup_dir.mkdir(parents=True, exist_ok=True)
    original_brief = task_brief_text(parent_task)
    brief = "\n".join([
        "# Agent Room runtime takeover",
        "",
        f"{speaker} 没有完成这个用户任务，原因已经转成 blocker。你现在要作为可用 peer 接手。",
        "先基于本地证据判断它为什么不可用；如果原任务还能继续，就继续完成；如果不能，给出明确 blocker 和下一步。",
        "不要只确认“可以接管”，必须给证据、产物、smoke 或具体阻塞。",
        "",
        f"Parent task: {parent_task_id}",
        f"Failed agent: {speaker}",
        f"Blockers: {', '.join(blockers) if blockers else 'unknown'}",
        "",
        "## Failed agent comment",
        body[:4000],
        "",
        "## Original task brief",
        original_brief[:8000],
        "",
        "## Recent room context",
        recent_room_context_excerpt(room_id, limit=12),
    ])
    brief_path.write_text(brief + "\n", encoding="utf-8")
    permissions = dict(parent_task.get("permissions") or {})
    permissions["telegram_send"] = False
    permissions["secrets_access"] = False
    permissions["github_push"] = False
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": room_id,
        "requested_by": "agent-room-runtime-takeover",
        "target_agents": targets,
        "lane": "runtime_takeover",
        "brief_path": str(brief_path),
        "context_paths": parent_task.get("context_paths") or [],
        "permissions": permissions,
        "expected_outputs": [],
        "status": "queued",
        "review_status": "requested",
        "blocked_reason": None,
        "result_paths": [],
        "canonical_imported": True,
        "created_at": created,
        "updated_at": created,
        "lease": {"owner": None, "heartbeat_at": None, "expires_at": None},
        "heartbeat": {"last_seen_at": None},
        "retry_budget": {"max_attempts": 1, "attempt": 0},
        "manual_boundary": True,
        "quality_gate_status": "not_applicable",
        "side_effect_gate_status": "closed",
        "telegram_projection_status": "room_bridge_gate_only",
        "runtime_takeover_visible_allowed": runtime_takeover_reply_visible_allowed(parent_task),
        "runtime_takeover_visibility_reason": (
            "recover_user_visible_telegram_task"
            if runtime_takeover_reply_visible_allowed(parent_task)
            else "internal_runtime_recovery_local_only"
        ),
        "runtime_takeover_of_task_id": parent_task_id,
        "runtime_takeover_from_agent_id": speaker,
        "degraded_quorum": degraded_quorum,
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "mode": "runtime_takeover",
            "status": "open",
            "participants": targets,
            "degraded_quorum": degraded_quorum,
            "work_items": [
                {
                    "id": f"runtime_takeover_{compact_slug(agent_id)}",
                    "status": "open",
                    "assigned_to": agent_id,
                    "description": "Diagnose the failed peer runtime and continue the original user task if possible.",
                }
                for agent_id in targets
            ],
            "claims": [],
            "handoffs": [],
            "artifacts": [],
            "blockers": [],
            "max_rounds": 1,
            "created_at": created,
        },
        "source": {
            "transport": "agent-room-runtime-takeover",
            "chat_id": chat_id,
            "update_id": f"runtime-takeover:{speaker}:{comment.get('run_id')}",
            "message_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        },
    }
    manifest = ROOM / "tasks" / task_id / "manifest.json"
    write_json(manifest, task)
    append_jsonl(ROOM / "tasks.jsonl", [task])
    append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])
    return task


def peer_followup_text(parent_task: dict[str, Any], comment: dict[str, Any]) -> str:
    return (task_user_message(parent_task) + "\n" + str(comment.get("title") or "") + "\n" + str(comment.get("body") or "")).lower()


AUTO_REVIEW_REPAIR_TASK_MARKERS = (
    "互相审查",
    "互相审核",
    "交叉审查",
    "交叉审核",
    "加大审查",
    "加大审核",
    "自动解决",
    "自动修复",
    "自动纠错",
    "不能等着我",
    "不要等我",
    "我去纠错",
    "人工纠错",
    "review harder",
    "cross-review",
    "cross review",
)

AUTO_REVIEW_REPAIR_COMMENT_MARKERS = (
    "胡说八道",
    "事实错误",
    "错误表述",
    "信息不一致",
    "没核实",
    "未核实",
    "不应当说成",
    "不能说成",
    "我刚才说错",
    "刚才说错",
    "需要纠正",
    "修正错误",
    "更正",
    "纠错",
    "unsupported claim",
    "unverified",
    "hallucination",
    "incorrect claim",
)

FACTUAL_CLAIM_RISK_MARKERS = (
    "规则具体为",
    "默认使用",
    "默认模型",
    "自动切换",
    "自动降级",
    "自动升级",
    "自动重译",
    "预设阈值",
    "错误率超过",
    "上下文窗口对齐",
    "模型选择规则",
    "模型路由",
    "工作流规则",
    "现有流程",
    "translation agent",
    "翻译agent",
    "翻译 agent",
    "claude-3-opus",
    "doubao-seed",
    "kimi-k2",
    "glm-5",
    "minimax-m2",
    "deepseek-v3",
)

FACTUAL_CLAIM_EVIDENCE_MARKERS = (
    "已查",
    "核查",
    "复核",
    "证据",
    "依据",
    "文件",
    "artifact",
    "manifest",
    "contract",
    "grep",
    "read",
    "source:",
    ".md",
    ".py",
    ".json",
    "`",
)


def task_requests_auto_review_repair(parent_task: dict[str, Any]) -> bool:
    text = task_user_message(parent_task).lower()
    return any(marker in text for marker in AUTO_REVIEW_REPAIR_TASK_MARKERS)


def comment_indicates_auto_review_repair(comment: dict[str, Any]) -> bool:
    title = str(comment.get("title") or "").lower()
    body = str(comment.get("body") or "").lower()
    text = title + "\n" + body
    blockers = [str(item).strip().lower() for item in (comment.get("blockers") or []) if str(item).strip()]
    if any(blocker in {"unsupported_claim", "unverified_claim", "factual_error", "wrong_claim"} for blocker in blockers):
        return True
    if comment_has_unsupported_factual_claim_risk(comment):
        return True
    return any(marker in text for marker in AUTO_REVIEW_REPAIR_COMMENT_MARKERS)


def comment_has_unsupported_factual_claim_risk(comment: dict[str, Any]) -> bool:
    """Heuristic: risky workflow/model/config claims need peer review unless grounded.

    This is intentionally conservative and bounded: it does not decide that the
    claim is wrong; it only creates a peer follow-up when a visible agent comment
    asserts existing rules/defaults/automatic behavior without local evidence.
    """
    title = str(comment.get("title") or "").lower()
    body = str(comment.get("body") or "").lower()
    text = title + "\n" + body
    if not any(marker in text for marker in FACTUAL_CLAIM_RISK_MARKERS):
        return False
    if any(marker in text for marker in FACTUAL_CLAIM_EVIDENCE_MARKERS):
        return False
    return True


def review_repair_followup_max_rounds(parent_task: dict[str, Any], comment: dict[str, Any]) -> int:
    normal = max(0, int(os.environ.get("AGENT_ROOM_COLLAB_FOLLOWUP_MAX_ROUNDS", "1")))
    if collaboration_tick_enabled(parent_task):
        normal = max(normal, collaboration_tick_max_rounds(parent_task))
    if (
        parent_task.get("auto_review_repair")
        or task_requests_auto_review_repair(parent_task)
        or comment_indicates_auto_review_repair(comment)
    ):
        return max(normal, max(1, int(os.environ.get("AGENT_ROOM_REVIEW_REPAIR_FOLLOWUP_MAX_ROUNDS", "2"))))
    return normal


def should_expand_peer_targets_for_review_repair(parent_task: dict[str, Any], comment: dict[str, Any]) -> bool:
    """Escalate peer review even when all peers were already initial targets.

    A normal multi-target room task should not recursively create more turns.
    The exception is a review/repair signal: Alex explicitly asked for agents to
    catch and fix peer errors without waiting for him, so this creates one
    bounded follow-up for the other local runtime.
    """
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    if str(source.get("transport") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS:
        if not parent_task.get("auto_review_repair"):
            return False
        parent_round = int(parent_task.get("collab_round") or 0)
        return parent_round < review_repair_followup_max_rounds(parent_task, comment) and comment_indicates_auto_review_repair(comment)
    return task_requests_auto_review_repair(parent_task) or comment_indicates_auto_review_repair(comment)


def peer_followup_targets(parent_task: dict[str, Any], comment: dict[str, Any], parent_targets: set[str]) -> list[str]:
    speaker = str(comment.get("agent_id") or "")
    if speaker not in LOCAL_RUNTIME_AGENTS:
        return []
    candidates = LOCAL_RUNTIME_AGENTS.difference({speaker})
    delivery_policy = str(parent_task.get("delivery_policy") or "")
    is_broadcast_turn = delivery_policy == "broadcast_all_agents_decide" or bool(parent_task.get("broadcast_targets"))
    if not should_expand_peer_targets_for_review_repair(parent_task, comment) and not is_broadcast_turn:
        candidates = candidates.difference(parent_targets)
    return sorted(candidates)


def has_material_followup_trigger(parent_task: dict[str, Any], comment: dict[str, Any]) -> bool:
    text = peer_followup_text(parent_task, comment)
    blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []
    if blockers:
        return True
    material_markers = (
        "blocker", "blocked", "runtime", "runner", "timeout", "timed out",
        "usage_limit", "rate_limit", "cooldown", "quota", "gateway", "watcher",
        "liveness", "not responding", "no reply", "stalled",
        "卡死", "卡住", "无响应", "不回", "不说话", "链路", "机器人",
        "额度", "限额", "冷却", "网关", "进程", "超时", "失活",
        "安全", "边界", "风险", "事实错误", "纠正", "反例",
        "架构", "协作", "共享上下文", "路由", "权限", "source_edit", "global_state_change",
        "用户体验", "体验", "接管", "执行", "patch", "smoke", "验证", "回归",
        "互相审查", "交叉审查", "自动解决", "自动修复", "纠错", "事实错误", "胡说八道",
        "方案", "提案", "提出来", "观点", "主张", "建议", "接住", "记下来",
        "默默记", "影响自己的行为", "影响行为", "采纳", "吸收", "后续行为",
    )
    return any(marker in text for marker in material_markers)


def peer_followup_can_edit(parent_task: dict[str, Any], comment: dict[str, Any]) -> bool:
    permissions = parent_task.get("permissions") if isinstance(parent_task.get("permissions"), dict) else {}
    if not bool(permissions.get("source_edit") or permissions.get("global_state_change")):
        return False
    if should_expand_peer_targets_for_review_repair(parent_task, comment):
        return True
    text = peer_followup_text(parent_task, comment)
    edit_markers = (
        "patch", "source_edit", "global_state_change", "代码", "脚本", "修改", "修复", "落地",
        "执行", "smoke", "验证", "回归", "状态巡检", "诊断", "实现", "配置", "runtime",
    )
    return any(marker in text for marker in edit_markers)


def collaboration_followup_expected_outputs(parent_task: dict[str, Any], comment: dict[str, Any], auto_review_repair: bool = False) -> list[str]:
    text = peer_followup_text(parent_task, comment).lower()
    blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []
    expected: list[str] = []
    if blockers:
        expected.append("blocker")
    if auto_review_repair:
        expected.append("evidence")
    if peer_followup_can_edit(parent_task, comment) and any(
        marker in text
        for marker in (
            "patch", "修改", "修复", "落地", "代码", "实现", "改 ", "fix", "implement",
        )
    ):
        expected.append("patch")
    if any(marker in text for marker in ("smoke", "验证", "回归", "test", "tests")):
        expected.append("smoke")
    if any(marker in text for marker in ("设计", "方案", "架构", "protocol", "设计稿")):
        expected.append("design")
    if any(marker in text for marker in ("artifact", "产物", "文档", "沉淀", "报告")):
        expected.append("artifact")
    if any(marker in text for marker in (
        "提出来", "提案", "观点", "主张", "建议", "接住", "记下来",
        "默默记", "影响自己的行为", "影响行为", "采纳", "吸收", "后续行为",
    )):
        expected.append("uptake_decision")
    if any(marker in text for marker in ("系统性", "暴露的问题", "原则", "记不住", "讨论决定", "方法是错误", "有待改进")):
        expected.extend(["design", "artifact", "evidence"])
    if any(marker in text for marker in ("证据", "核查", "检查", "review", "风险", "边界", "反例")):
        expected.append("evidence")
    if any(marker in text for marker in ("blocker", "blocked", "卡住", "阻塞")):
        expected.append("blocker")
    if not expected:
        expected.append("evidence")
    return list(dict.fromkeys(expected))


def collaboration_followup_intent(
    parent_task: dict[str, Any],
    comment: dict[str, Any],
    expected_outputs: list[str],
    auto_review_repair: bool = False,
) -> str:
    if auto_review_repair:
        return "delegate_work"
    text = peer_followup_text(parent_task, comment).lower()
    delegation_markers = (
        "@codex", "@claude", "交给", "接管", "认领", "补 patch", "补smoke", "补 smoke",
        "建议你", "请你", "handoff", "delegate", "patch", "smoke", "验证", "修复", "实现",
    )
    question_markers = ("?", "？", "为什么", "怎么", "是否", "能不能", "是不是", "请问")
    if any(marker in text for marker in delegation_markers):
        return "delegate_work"
    if any(output in {"patch", "smoke", "artifact", "blocker", "design", "uptake_decision"} for output in expected_outputs):
        return "delegate_work"
    if any(marker in text for marker in question_markers):
        return "ask_question"
    return "delegate_work"


def parent_task_blocks_peer_followup(parent_task: dict[str, Any], comment: dict[str, Any] | None = None) -> bool:
    """Return True only when the current task explicitly disables follow-up.

    This is deliberately not a keyword classifier.  Alex corrected the previous
    version as too dogmatic: words like “重复” can be a valid topic for design
    work, not a universal ban on collaboration.  Follow-up blocking must be an
    explicit control signal on the task, set by the orchestrator/main after
    deciding that auto-collaboration would amplify noise for this turn.
    """
    control = parent_task.get("collaboration_control")
    if isinstance(control, dict) and control.get("disable_peer_followup") is True:
        return True
    if parent_task.get("disable_peer_followup") is True:
        return True
    if parent_task.get("peer_followup_disabled") is True:
        return True
    return False


def should_create_collab_followup(parent_task: dict[str, Any], comment: dict[str, Any], parent_targets: set[str]) -> bool:
    """Return whether a peer follow-up should become a room collaboration turn.

    Mentions choose the first response owner. They do not hide context from peer
    agents, and they do not forbid later material contribution. The anti-noise
    gate is material value, not a fixed reviewer/executor role.
    """
    if parent_task_blocks_peer_followup(parent_task, comment):
        return False
    if should_expand_peer_targets_for_review_repair(parent_task, comment):
        return True
    text = peer_followup_text(parent_task, comment)
    explicit_markers = (
        "一起讨论", "一起协作", "协作", "讨论", "跟他们", "跟 codex", "跟codex",
        "和 codex", "和codex", "让 codex", "让codex", "问 codex", "问codex",
        "你们两个", "你们俩", "大家", "各位", "peer", "review",
    )
    if any(marker in text for marker in explicit_markers):
        return True
    if str(parent_task.get("delivery_policy") or "") == "broadcast_all_agents_decide":
        return has_material_followup_trigger(parent_task, comment)
    if parent_task.get("broadcast_targets"):
        return has_material_followup_trigger(parent_task, comment)
    return has_material_followup_trigger(parent_task, comment)


def is_material_peer_comment(comment: dict[str, Any]) -> bool:
    body = str(comment.get("body") or "").strip()
    title = str(comment.get("title") or "").strip().lower()
    if not body:
        return False
    normalized = body.strip().upper()
    if normalized in {"NO_COMMENT", "NO COMMENT", "???", "????"}:
        return False
    blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []
    projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
    if projection_status in {"user_visible_runner_failure", "user_visible_quota_exhausted", "user_visible_quota_notification"}:
        return True
    if projection_status in {
        "local_only_runner_failure",
        "suppressed_runner_failure",
        "local_only_quota_silenced",
        "suppressed_quota_silenced",
        "local_only_deferred_liveness_signal",
        "deferred_liveness_signal",
        "local_only_stale_context",
        "local_only_context_rebase_evidence",
    }:
        return False
    if "codex_cli_failed" in {str(item).strip().lower() for item in blockers}:
        return False
    if blockers and any(marker in title for marker in (
        "runner did not produce",
        "did not produce parsed room json",
        "execution failed",
        "returned internal status",
    )):
        return False
    # Do not suppress a normal diagnostic merely because it quotes a previous
    # runner failure phrase such as “没有形成可发布正文”. Fallback runner failures
    # are already caught above by title/blockers/projection_status. A body-only
    # phrase check hid valid Claude Code analysis after it finished.
    return True


def comment_turn_position(comment: dict[str, Any]) -> str:
    assignment = comment.get("collaboration_assignment")
    if not isinstance(assignment, dict):
        return ""
    return str(assignment.get("turn_position") or "").strip().lower()


CONCRETE_VISIBLE_DELTA_FIELDS = (
    "patch",
    "patches",
    "patch_path",
    "patch_paths",
    "changed_files",
    "files_changed",
    "modified_files",
    "artifact",
    "artifacts",
    "artifact_path",
    "artifact_paths",
    "source_artifact",
    "verification",
    "verifications",
    "smoke",
    "smoke_result",
    "smoke_results",
    "test_result",
    "test_results",
    "checks",
    "evidence",
    "evidence_paths",
    "finding",
    "findings",
    "risk",
    "risks",
    "decision",
    "review_decision",
    "handoff",
    "run_result",
    "command_result",
)

CONCRETE_VISIBLE_DELTA_KINDS = {
    "artifact",
    "blocker",
    "closure_review",
    "decision",
    "evidence",
    "finding",
    "findings",
    "handoff",
    "patch",
    "patch_and_handoff",
    "review",
    "risk",
    "smoke",
    "verification",
}

NON_CONCRETE_DELTA_STRINGS = {
    "no_comment",
    "no comment",
    "none",
    "null",
    "n/a",
    "na",
    "not applicable",
    "pending",
    "tbd",
    "todo",
    "later",
    "无",
    "没有",
    "暂无",
    "待补",
    "后补",
    "稍后",
    "不适用",
}

CONCRETE_EVIDENCE_PATH_RE = re.compile(
    r"(?:/home/|codex-main-bridge/|agent-room/|tools/|tests/|artifacts/)[^\s`'\"<>]+"
    r"|(?:^|[\s`])[\w.-]+\.(?:py|md|json|jsonl|sh|ps1|cs):\d+"
)
CONCRETE_EVIDENCE_COMMAND_RE = re.compile(
    r"\b(?:python3?\s+-m\s+py_compile|pytest(?:\s|$)|py_compile(?:\s|$)|smoke_[a-z0-9_./-]+(?:\.py)?|exit\s+[01])\b"
)


def has_concrete_delta_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().strip("`'\"")
        folded = normalized.casefold()
        return bool(normalized) and folded not in NON_CONCRETE_DELTA_STRINGS
    if isinstance(value, dict):
        if not value:
            return False
        result_keys = {"ok", "passed", "success", "accepted"}
        if any(isinstance(value.get(key), bool) for key in result_keys):
            return True
        return any(has_concrete_delta_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(has_concrete_delta_value(item) for item in value)
    return bool(value)


def comment_has_concrete_visible_delta(comment: dict[str, Any]) -> bool:
    """Decide if a comment carries concrete, non-redundant value.

    Priority order:
    1. Structured contribution fields from the agent comment.
    2. Comment kind, when it is already a typed contribution.
    3. Narrow evidence-anchor fallback in title/body.
    """
    for field in CONCRETE_VISIBLE_DELTA_FIELDS:
        if field in comment and has_concrete_delta_value(comment.get(field)):
            return True
    kind = str(comment.get("kind") or "").strip().lower()
    if kind in CONCRETE_VISIBLE_DELTA_KINDS:
        return True
    # Fallback only catches concrete anchors that the comment schema did not
    # preserve. Generic labels such as "patch" or "smoke" are not sufficient.
    text = "\n".join(
        str(comment.get(key) or "")
        for key in ("title", "body")
    ).lower()
    return bool(CONCRETE_EVIDENCE_PATH_RE.search(text) or CONCRETE_EVIDENCE_COMMAND_RE.search(text))


def collaboration_acceptance_requires_nonduplicative_output(task: dict[str, Any]) -> bool:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    acceptance = str(collaboration.get("acceptance") or "").lower()
    problem = str(task.get("problem_statement") or "").lower()
    return (
        "non-duplicative" in acceptance
        or "重复" in problem
        or "duplicate" in problem
    )


def suppress_repetitive_coproducer_projection(task: dict[str, Any], comments: list[dict[str, Any]]) -> bool:
    if not comments:
        return False
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    if not (
        str(source.get("transport") or "") == "telegram"
        and str(task.get("requested_by") or "") == "telegram-user"
        and str(task.get("delivery_policy") or "") == "broadcast_all_agents_decide"
    ):
        return False
    if not collaboration_acceptance_requires_nonduplicative_output(task):
        return False
    material_comments = [comment for comment in comments if is_material_peer_comment(comment)]
    if not material_comments:
        return False
    return all(
        comment_turn_position(comment) == "co_producer"
        and not comment_has_concrete_visible_delta(comment)
        for comment in material_comments
    )


def send_agent_reply(agent_id: str, chat_id: str | None, run_id: str, allow_send: bool, *, prefix: str, projection_mode: str = "normal") -> dict[str, Any] | None:
    if not chat_id or not allow_send:
        return None
    reply_cmd = [
        "python3", str(TOOLS / "telegram_agent_reply.py"),
        "--agent-id", agent_id,
        "--chat-id", chat_id,
        "--run-id", run_id,
        "--allow-send",
        "--prefix", prefix,
        "--projection-mode", projection_mode,
    ]
    sent = run_cmd(reply_cmd, timeout=120)
    return {"agent_id": agent_id, "ok": sent["ok"], "result": sent}


def configured_chat_action_throttle_seconds() -> int:
    try:
        return max(0, int(os.environ.get("AGENT_ROOM_CHAT_ACTION_THROTTLE_SECONDS", str(DEFAULT_CHAT_ACTION_THROTTLE_SECONDS))))
    except ValueError:
        return DEFAULT_CHAT_ACTION_THROTTLE_SECONDS


def send_agent_chat_action(agent_id: str, chat_id: str | None, run_id: str, allow_send: bool, *, action: str = "typing") -> dict[str, Any]:
    if not chat_id:
        return {"ok": True, "sent": False, "suppressed_reason": "missing_chat_id"}
    if not allow_send:
        return {"ok": True, "sent": False, "suppressed_reason": "send_not_allowed"}
    cmd = [
        "python3", str(TOOLS / "telegram_agent_reply.py"),
        "--agent-id", agent_id,
        "--chat-id", chat_id,
        "--run-id", run_id,
        "--only-chat-action",
        "--chat-action", action,
        "--allow-send",
    ]
    result = run_cmd(cmd, timeout=15)
    try:
        parsed = json.loads(str(result.get("stdout") or "{}"))
    except Exception:
        parsed = {}
    if isinstance(parsed, dict) and parsed:
        parsed["ok"] = bool(result.get("ok"))
        return parsed
    return {
        "ok": bool(result.get("ok")),
        "sent": False,
        "suppressed_reason": "chat_action_result_parse_failed",
        "exit_code": result.get("exit_code"),
    }


def maybe_send_runner_chat_action(path: Path, record: dict[str, Any], allow_send: bool, *, reason: str) -> dict[str, Any]:
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    if not task_allows_telegram_chat_action(task):
        return {"ok": True, "sent": False, "suppressed_reason": "non_telegram_user_task"}
    if not allow_send:
        return {"ok": True, "sent": False, "suppressed_reason": "send_not_allowed"}
    now_dt = datetime.now(timezone.utc).astimezone()
    last = parse_iso_datetime(str(record.get("last_chat_action_at") or ""))
    throttle = configured_chat_action_throttle_seconds()
    if last is not None and throttle > 0 and (now_dt - last).total_seconds() < throttle:
        return {
            "ok": True,
            "sent": False,
            "suppressed_reason": "chat_action_throttled",
            "last_chat_action_at": record.get("last_chat_action_at"),
            "throttle_seconds": throttle,
        }
    result = send_agent_chat_action(
        str(record.get("agent_id") or ""),
        str(record.get("chat_id") or "") or None,
        str(record.get("run_id") or ""),
        allow_send,
        action="typing",
    )
    updated = dict(record)
    updated["last_chat_action_at"] = now_dt.isoformat(timespec="seconds")
    updated["last_chat_action_reason"] = reason
    updated["last_chat_action_result"] = {
        "schema": result.get("schema"),
        "sent": bool(result.get("sent")),
        "suppressed_reason": result.get("suppressed_reason"),
        "telegram_error": result.get("telegram_error"),
    }
    write_json(path, updated)
    return result


def telegram_projection_decision(task: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Decide whether harvested comments may be sent to Telegram.

    Agent-to-agent collaboration is useful, but raw turns must not become
    visible group chat. Alex clarified that internal turns may be projected when
    converted into concise, human-readable summaries by the corresponding agent
    bot. The second tuple value is either a projection mode (for allowed sends)
    or a suppression reason (for blocked sends).
    """
    if not comments:
        return False, "no_comments"
    if private_dm_agent_mismatch(task, comments):
        return False, "private_dm_agent_mismatch"
    if task_requests_single_visible_speaker(task):
        # Alex clarified 2026-05-27: "单一发言口径不是说一直让你们中只有main出来说".
        # Coherent channel means one lead speaker per turn, NOT blanket silencing.
        # Material peer comments (corrections, evidence, blockers, distinct angles)
        # must still be projectable. Only truly redundant/echo responses are
        # filtered — which the is_material_peer_comment gate below already handles.
        if not any(is_material_peer_comment(c) for c in comments):
            return False, "single_visible_speaker_no_material_comment"
        # At least one material peer comment exists; allow projection.
        # The lead speaker's answer goes first; peer contributions are supplementary.
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() in {"local_only_quota_silenced", "suppressed_quota_silenced"} for comment in comments):
        return False, "quota_silenced_already_notified"
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() == "local_only_retryable_runner_failure" for comment in comments):
        return False, "retryable_runner_failure_waiting_for_cooldown"
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() in {"local_only_deferred_liveness_signal", "deferred_liveness_signal"} for comment in comments):
        return False, "local_only_deferred_liveness_signal"
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() == "local_only_stale_context" for comment in comments):
        return False, "stale_context_superseded_by_room_state_update"
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() in {"local_only_stale_context", "local_only_context_rebase_evidence"} for comment in comments):
        return False, "context_rebase_evidence_local_only"
    if comments and all(is_internal_runner_failure_comment(comment) for comment in comments):
        return False, "runner_lifecycle_failure_local_only"
    if comments and all(str(comment.get("telegram_projection_status") or "").strip() in {"local_only_runner_failure", "suppressed_runner_failure"} for comment in comments):
        return False, "local_only_runner_failure"
    if not any(is_material_peer_comment(comment) for comment in comments):
        return False, "no_material_publishable_comment"
    if suppress_repetitive_coproducer_projection(task, comments):
        return False, "coproducer_no_concrete_delta"
    if any(str(comment.get("telegram_projection_status") or "").strip() in {"user_visible_quota_exhausted", "user_visible_quota_notification"} for comment in comments):
        return True, "normal"
    if comments and all(
        "codex_cli_failed" in {
            str(item).strip().lower()
            for item in (comment.get("blockers") if isinstance(comment.get("blockers"), list) else [])
        }
        for comment in comments
    ):
        return False, "codex_cli_failure_local_only"
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    is_internal = (
        str(source.get("transport") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS
        or str(task.get("requested_by") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS
    )
    if is_internal:
        transport = str(source.get("transport") or "")
        # Main-initiated coordination turns are internal work management. Keep
        # each peer's raw answer local; main should send one concise human
        # outcome instead of letting the room see runner/sequence chatter.
        if transport == "agent_room_inject_message":
            return False, "main_coordination_local_only"
        if transport == "agent-room-collab-followup" and task.get("peer_followup_visible_allowed") is not True:
            return False, "peer_followup_projection_not_explicit"
        if transport == "agent-room-runtime-takeover" and task.get("runtime_takeover_visible_allowed") is not True:
            return False, "runtime_takeover_projection_not_explicit"
        if transport in {"agent-room-proactive-mainline", "agent-room-standing-mainline"} and not (
            task.get("proactive_visible_allowed") is True or task.get("standing_visible_allowed") is True
        ):
            return False, "standing_mainline_projection_not_explicit"
        return True, "internal-summary"
    # User-originated broadcast turns are visible room discussion. Let each
    # targeted peer project a material answer; NO_COMMENT and non-material turns
    # are still suppressed above.
    if (
        str(source.get("transport") or "") == "telegram"
        and str(task.get("requested_by") or "") == "telegram-user"
        and str(task.get("delivery_policy") or "") == "broadcast_all_agents_decide"
    ):
        return True, "normal"
    # User-originated targeted replies must be visible from the addressed
    # agent. Older bridge output marked all Telegram-created tasks suppressed,
    # which made a valid Claude Code reply invisible while a later Codex
    # follow-up could still appear. Treat targeted_reply as the stronger
    # contract.
    if (
        str(source.get("transport") or "") == "telegram"
        and str(task.get("requested_by") or "") == "telegram-user"
        and str(task.get("delivery_policy") or "") == "targeted_reply"
    ):
        return True, "normal"
    if str(task.get("telegram_projection_status") or "") == "suppressed":
        return False, "task_projection_suppressed"
    return True, "normal"


def visible_agent_prefix(agent_id: str, chat_id: str | None) -> str:
    # Telegram already displays the sending bot/account name above each message.
    # Repeating "Codex:" or "Claude Code:" inside the body makes the room noisy
    # and was explicitly called out by Alex. Keep this hook for callers that may
    # pass a prefix deliberately, but default Agent Room Telegram projection to
    # plain message body only.
    return ""


def task_file_slot(task: dict[str, Any], task_path: Path) -> str:
    """Stable per-task runner slot name.

    Do not use `task_path.stem` here: every canonical task lives at
    `.../<task_id>/manifest.json`, so the stem is always `manifest`. That caused
    later tasks in the same bridge tick to overwrite `local-runtime-task.json`
    for runners that had just been started asynchronously.
    """
    value = str(task.get("task_id") or task.get("run_id") or task_path.parent.name or "task")
    return compact_slug(value)


def deferred_agent_comment(task: dict[str, Any], agent_id: str, runner_status: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """Emit a lightweight room-visible comment when an agent is deferred.

    This is the "response completeness invariant" patch: every dispatched agent
    must eventually produce a visible response.  If the agent is deferred by a
    concurrency gate and never re-dispatched before the task expires, the room
    would otherwise see silence.  A deferred comment is NOT a real reply; it is
    a liveness signal so the room can distinguish "agent queued" from "agent
    broken / silent".

    Dedup: the caller (dispatch loop) is responsible for emitting this only
    once per (agent_id, run_id, runner_status).  append_agent_comments_to_room
    also deduplicates by event_id.
    """
    reasons = {
        "already_running": "同一个 run 的 runner 已在运行，等待其完成",
        "deferred_global_active_runner_limit": f"全局活跃 runner 达到上限（{detail.get('global_active_runner_limit') if detail else '?'}），排队等待",
        "deferred_reserved_runner_slots": f"普通/内部任务等待；保留 {detail.get('user_main_reserved_runner_slots') or detail.get('fresh_user_reserved_runner_slots') if detail else '?'} 个 runner 槽给新用户消息或 openclaw-main 控制任务",
        "deferred_per_agent_active_runner_limit": f"该 agent 活跃 runner 达到上限（{detail.get('active_runner_limit') if detail else '?'}），排队等待",
    }
    body = reasons.get(runner_status, f"已被调度延迟（{runner_status}），将在资源释放后响应")
    comment: dict[str, Any] = {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "status",
        "confidence": "high",
        "title": f"{agent_id} 调度排队中",
        "body": body,
        "blockers": [],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": False,
        "effective_permissions": comment_effective_permissions(task),
        "telegram_projection_status": "local_only_deferred_liveness_signal",
    }
    return comment


DEFERRED_COMMENT_TRACKER: dict[str, str] = {}


def deferred_comment_already_emitted(agent_id: str, run_id: str, runner_status: str) -> bool:
    """Check whether a deferred comment was already emitted for this (agent, run, status).

    Uses an in-process set plus the durable comment ledger so separate bridge
    ticks do not keep appending the same "already running" liveness signal.
    """
    key = f"{agent_id}:{run_id}:{runner_status}"
    if key in DEFERRED_COMMENT_TRACKER:
        return True
    for record in reversed(read_jsonl(comment_path(agent_id))[-200:]):
        if str(record.get("run_id") or "") != run_id:
            continue
        if str(record.get("agent_id") or "") != agent_id:
            continue
        status = str(record.get("telegram_projection_status") or "").strip().lower()
        if status not in {"local_only_deferred_liveness_signal", "deferred_liveness_signal"}:
            continue
        body = str(record.get("body") or "")
        title = str(record.get("title") or "")
        if runner_status == "already_running":
            if "同一个 run 的 runner 已在运行" not in body:
                continue
        elif runner_status not in body and runner_status not in title:
            continue
        mark_deferred_comment_emitted(agent_id, run_id, runner_status)
        return True
    return False


def mark_deferred_comment_emitted(agent_id: str, run_id: str, runner_status: str) -> None:
    key = f"{agent_id}:{run_id}:{runner_status}"
    DEFERRED_COMMENT_TRACKER[key] = now_iso()
    # Evict old entries to prevent unbounded growth
    if len(DEFERRED_COMMENT_TRACKER) > 500:
        cutoff = datetime.now(timezone.utc).astimezone() - timedelta(hours=2)
        stale_keys = [k for k, v in DEFERRED_COMMENT_TRACKER.items()
                      if parse_iso_datetime(v) and (parse_iso_datetime(v) or datetime.now(timezone.utc).astimezone()) < cutoff]
        for k in stale_keys:
            del DEFERRED_COMMENT_TRACKER[k]


def maybe_emit_deferred_comment(task: dict[str, Any], agent_id: str, runner_status: str, detail: dict[str, Any] | None = None) -> None:
    """Emit a deferred liveness comment if one has not already been emitted."""
    if runner_status == "already_running":
        return
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    if deferred_comment_already_emitted(agent_id, run_id, runner_status):
        return
    comment = deferred_agent_comment(task, agent_id, runner_status, detail)
    append_jsonl(comment_path(agent_id), [comment])
    # Keep the scheduler accounting locally. A deferred runner is not a peer
    # answer; projecting it made the room look responsive while the actual
    # agent result was still blocked behind harvest/retry.
    mark_deferred_comment_emitted(agent_id, run_id, runner_status)


def stale_context_comment(task: dict[str, Any], agent_id: str, freshness: dict[str, Any]) -> dict[str, Any]:
    newer = freshness.get("newer_human_message") if isinstance(freshness.get("newer_human_message"), dict) else {}
    body = (
        f"{agent_id} 的旧 runner 快照已过期：runner 启动后 room state 出现了更新，"
        "已阻止旧上下文回答投影。这是运行时缺少消息合并/最新意图重算的问题，"
        "不是用户连续发送消息的责任。后续必须基于最新 room state 重新评估，"
        "并尽量复用旧 runner 已形成的本地证据，而不是直接丢弃工作。"
    )
    if newer.get("created_at"):
        body += f" 最新 room state 更新时间：{newer.get('created_at')}。"
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "risk",
        "confidence": "high",
        "title": f"{agent_id} stale context projection blocked",
        "body": body,
        "blockers": ["stale_context_snapshot"],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": False,
        "effective_permissions": comment_effective_permissions(task),
        "telegram_projection_status": "local_only_stale_context",
        "context_freshness": freshness,
    }


def context_rebase_evidence_comments(comments: list[dict[str, Any]], freshness: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve useful output from a stale runner as local rebase evidence.

    Older behavior replaced the runner's actual comments with only a stale
    blocker.  That avoided a wrong visible reply, but it also threw away work.
    P0 rebase behavior keeps the evidence local so a latest-context task/main
    synthesis can incorporate or reject it explicitly.
    """
    preserved: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "").strip()
        title = str(comment.get("title") or "").strip()
        if not body and not title:
            continue
        cloned = dict(comment)
        cloned["original_telegram_projection_status"] = comment.get("telegram_projection_status")
        cloned["telegram_projection_status"] = "local_only_context_rebase_evidence"
        cloned["context_freshness"] = freshness
        cloned["context_rebase_evidence"] = True
        cloned["created_at"] = now_iso()
        cloned["canonical_state_advanced"] = False
        cloned["side_effects_used"] = bool(comment.get("side_effects_used"))
        blockers = list(comment.get("blockers") or []) if isinstance(comment.get("blockers"), list) else []
        if "context_rebase_needed" not in blockers:
            blockers.append("context_rebase_needed")
        cloned["blockers"] = blockers
        preserved.append(cloned)
    return preserved


def create_context_rebase_task(parent_task: dict[str, Any], agent_id: str, freshness: dict[str, Any], evidence_comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Queue one local-only latest-context rebase task for stale runner evidence."""
    if not evidence_comments:
        return None
    room_id = str(parent_task.get("room_id") or "")
    chat_id = task_chat_id(parent_task)
    if not room_id or not chat_id or agent_id not in LOCAL_RUNTIME_AGENTS:
        return None
    source = parent_task.get("source") if isinstance(parent_task.get("source"), dict) else {}
    if str(source.get("transport") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS:
        return None
    parent_task_id = str(parent_task.get("task_id") or parent_task.get("run_id") or "task")
    newer = freshness.get("newer_human_message") if isinstance(freshness.get("newer_human_message"), dict) else {}
    newer_id = room_message_identity(newer) or str(newer.get("created_at") or "latest")
    digest = hashlib.sha256(
        json.dumps(
            {
                "parent_task_id": parent_task_id,
                "agent_id": agent_id,
                "newer_id": newer_id,
                "evidence_count": len(evidence_comments),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    task_id = f"context-rebase-{parent_task_id}-{agent_id}-{digest}"
    manifest = ROOM / "tasks" / task_id / "manifest.json"
    if manifest.exists():
        existing = read_json(manifest, {})
        return existing if isinstance(existing, dict) else None
    created = now_iso()
    evidence_text = "\n\n".join(
        f"### Evidence from {comment.get('agent_id') or agent_id}\nTitle: {comment.get('title') or ''}\nBody:\n{str(comment.get('body') or '').strip()[:4000]}"
        for comment in evidence_comments[:4]
    )
    latest_ref = json.dumps(newer, ensure_ascii=False, indent=2)
    original_user = task_user_message(parent_task).strip()
    brief = f"""# Agent Room context rebase

## User message
Re-evaluate the stale runner output against the latest room state. Preserve useful evidence, reject obsolete claims, and return strict JSON only. Do not send Telegram messages; this is local evidence for main synthesis.

## Original user/task context
{original_user[:4000]}

## Newer human room message ref
```json
{latest_ref}
```

## Stale runner evidence to rebase
{evidence_text}
""".strip() + "\n"
    brief_path = ROOM / "tasks" / task_id / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief, encoding="utf-8")
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": room_id,
        "requested_by": "agent-room-context-rebase",
        "target_agents": [agent_id],
        "lane": "context_rebase",
        "brief_path": str(brief_path),
        "status": "queued",
        "review_status": "requested",
        "created_at": created,
        "updated_at": created,
        "delivery_policy": "local_only_context_rebase",
        "telegram_projection_status": "local_only_context_rebase",
        "parent_task_id": parent_task_id,
        "context_rebase": {
            "schema": "openclaw.agent_room.context_rebase.v0",
            "status": "queued",
            "agent_id": agent_id,
            "freshness": freshness,
            "evidence_comment_count": len(evidence_comments),
            "intent": "merge_or_reject_stale_runner_evidence_against_latest_room_state",
        },
        "source": {
            "transport": "agent-room-context-rebase",
            "chat_id": chat_id,
            "update_id": f"context-rebase:{parent_task_id}:{agent_id}:{digest}",
            "message_text_sha256": hashlib.sha256(brief.encode("utf-8")).hexdigest(),
        },
    }
    write_json(manifest, task)
    append_jsonl(ROOM / "tasks.jsonl", [task])
    append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])
    return task


def fallback_runner_comment(task: dict[str, Any], agent_id: str, runner: dict[str, Any]) -> dict[str, Any]:
    impact = "该 agent 本轮没有形成可发布回复；系统应释放/归档 runner，并允许其它 agent 或后续重试继续推进。"
    if runner.get("missing_process"):
        reason = "runner_process_missing"
        recovery_action = "检查 runner 退出原因和日志；必要时用更小任务重试或让 peer agent 接手。"
        body = f"{agent_id} 本轮 runner 进程已经不存在，且没有留下可发布正文。已转为 blocker，避免把内部状态或乱码发到群里。"
    elif runner.get("timeout"):
        reason = "runner_timeout"
        age = runner.get("age_seconds")
        max_seconds = runner.get("max_seconds")
        detail = ""
        if isinstance(age, (int, float)) and isinstance(max_seconds, int):
            detail = f"已运行约 {int(age)} 秒，超过当前上限 {max_seconds} 秒。"
        recovery_action = "缩小任务、检查 provider/CLI 卡点，或转交 peer agent；不能继续把 runner 存在视为进展。"
        body = f"{agent_id} 本轮 runner 超时，没有形成可发布正文。{detail}已转为 blocker，后续需要缩小任务或检查 runner 日志。"
    else:
        reason = f"runner_exit_{runner.get('exit_code')}"
        stderr = str(runner.get("stderr") or "").strip().splitlines()[-1:] or [""]
        recovery_action = "根据最后错误行定位 provider/CLI/权限问题；修复后重试或转交 peer agent。"
        body = f"{agent_id} 本轮 runner 异常退出，没有形成可发布正文。已转为 blocker，避免把原始 JSON 或运行日志发到群里。原因：{reason}。" + (f"最后一行错误：{stderr[0][:240]}" if stderr[0] else "")
    telegram_safe_summary = f"{agent_id} runner 未产出可发布回复；影响：{impact}责任方：{agent_id}；恢复动作：{recovery_action}"
    comment = {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "risk",
        "confidence": "high",
        "title": f"{agent_id} runner did not produce a publishable reply",
        "body": body,
        "blockers": [reason],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": False,
        "effective_permissions": comment_effective_permissions(task),
        "runner_recovery": {
            "owner": agent_id,
            "impact": impact,
            "recovery_action": recovery_action,
            "reason": reason,
            "deadline_state": runner.get("deadline_state"),
            "telegram_safe_summary": telegram_safe_summary,
        },
        "telegram_safe_summary": telegram_safe_summary,
    }
    # Runner lifecycle failures are runtime plumbing, not peer-agent speech.
    # Keep them local so Codex/Claude bots do not post duplicate internal
    # blockers into Alex's Telegram room. A coordinator/main summary can still
    # surface the system issue after dedupe and recovery routing.
    comment["telegram_projection_status"] = "local_only_runner_failure"
    comment["visibility_reason"] = "runner_lifecycle_failure_local_only"
    return comment


def runner_failure_should_be_user_visible(task: dict[str, Any]) -> bool:
    """Return whether runner plumbing failures may speak in Telegram.

    A Codex/Claude bot message should represent that agent's semantic answer.
    Runner lifecycle failures are local runtime diagnostics by default; otherwise
    the room sees "runner timed out" as if the agent had contributed. A task may
    explicitly opt in when the caller is asking for runtime status, but ordinary
    user/group turns must recover locally or be summarized by the coordinator.
    """
    if task.get("visible_runner_failure_allowed") is True:
        return True
    if task.get("visible_runtime_failure_allowed") is True:
        return True
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    if (
        task_interaction_class(task) == "status_question"
        and (
            str(source.get("transport") or "") == "telegram"
            or str(task.get("delivery_policy") or "") == "targeted_reply"
        )
    ):
        return True
    # If Alex directly addressed a Telegram bot/agent, runner failure must not
    # become silence.  Internal agent-to-agent work stays local, but a
    # user-originated targeted reply has a visible liveness contract: either the
    # selected agent answers, or the room sees a concise blocker with owner and
    # recovery action.
    if (
        str(source.get("transport") or "") == "telegram"
        and str(task.get("requested_by") or "") == "telegram-user"
        and str(task.get("delivery_policy") or "") == "targeted_reply"
    ):
        return True
    return False


def promote_runner_failures_for_visible_silence(
    task: dict[str, Any],
    comments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Make runner failures publishable when the task has a visible liveness contract.

    `agent_task_runner.py` may return a guarded `suppressed_runner_failure`
    comment. User-originated Telegram tasks should not silently lose all visible
    agent feedback when a selected runner times out or disappears. Promote only
    concise runner blocker comments, and keep internal Agent Room transports
    local unless their task contract explicitly allows visibility.
    """
    if not comments or not runner_failure_should_be_user_visible(task):
        return comments, []

    promoted_comments: list[dict[str, Any]] = []
    rewritten_comments: list[dict[str, Any]] = []
    for comment in comments:
        projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
        if projection_status in {"suppressed_runner_failure", "local_only_runner_failure"} and is_internal_runner_failure_comment(comment):
            promoted = dict(comment)
            promoted["telegram_projection_status"] = "user_visible_runner_failure"
            promoted["visibility_reason"] = "telegram_user_task_liveness_contract"
            promoted["source_projection_status"] = projection_status
            promoted_comments.append(promoted)
            rewritten_comments.append(promoted)
        else:
            rewritten_comments.append(comment)
    return rewritten_comments, promoted_comments


def group_targeted_task_allows_silent_failure_projection(task: dict[str, Any]) -> bool:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    if str(source.get("transport") or "") != "telegram":
        return False
    if str(task.get("requested_by") or "") != "telegram-user":
        return False
    if str(task.get("delivery_policy") or "") != "targeted_reply":
        return False
    targets = [str(agent_id) for agent_id in (task.get("target_agents") or []) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
    if len(targets) != 1:
        return False
    room_id = str(task.get("room_id") or "")
    chat_id = task_chat_id(task)
    return bool(str(chat_id or "").startswith("-") or (room_id and not room_id.startswith("dm-")))


def silent_failure_projection_run_id(parent_run_id: str, failed_agent: str, observer_id: str) -> str:
    return (
        f"{compact_slug(parent_run_id)}"
        f"-silent-failure-{compact_slug(failed_agent)}"
        f"-by-{compact_slug(observer_id)}"
    )


def silent_failure_projection_already_emitted(observer_id: str, status_run_id: str) -> bool:
    if reply_artifact_exists(observer_id, status_run_id):
        return True
    for record in reversed(read_jsonl(comment_path(observer_id))[-200:]):
        if str(record.get("run_id") or "") == status_run_id:
            return True
    return False


def runner_silent_failure_diagnostic(task: dict[str, Any], failed_agent: str) -> dict[str, Any] | None:
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    if not run_id:
        return None
    ar_path = active_runner_path(failed_agent, run_id)
    if not ar_path.exists():
        return {
            "reason": "runner_missing",
            "detail": "active_runner_record_missing",
            "deadline_state": "soft_deadline_exceeded",
            "active_runner": str(ar_path),
        }
    record = read_json(ar_path, {})
    if not isinstance(record, dict):
        return {
            "reason": "runner_missing",
            "detail": "active_runner_record_invalid",
            "deadline_state": "soft_deadline_exceeded",
            "active_runner": str(ar_path),
        }
    runner_dir = Path(str(record.get("runner_dir") or ""))
    result_path = runner_dir / "result.json" if runner_dir else None
    runner_result = read_json(result_path, {}) if result_path and result_path.exists() else {}
    if runner_result_is_terminal(runner_result):
        return None

    pid = int(record.get("pid") or 0)
    alive = active_runner_alive(record)
    deadline_state = classify_runner_deadline_state(record)
    stdout = file_tail_evidence(Path(str(record.get("stdout_path") or "")) if record.get("stdout_path") else None, max_chars=0)
    stderr = file_tail_evidence(Path(str(record.get("stderr_path") or "")) if record.get("stderr_path") else None, max_chars=0)
    base = {
        "deadline_state": deadline_state,
        "active_runner": str(ar_path),
        "pid": pid,
        "pid_alive": alive,
        "soft_deadline_at": record.get("soft_deadline_at"),
        "hard_deadline_at": record.get("hard_deadline_at"),
        "stdout_bytes": stdout.get("bytes"),
        "stderr_bytes": stderr.get("bytes"),
    }
    if not alive:
        return {**base, "reason": "runner_missing", "detail": "runner_process_not_alive"}
    if deadline_state in {"soft_deadline_exceeded", "hard_deadline_exceeded"}:
        return {**base, "reason": "runner_timeout", "detail": deadline_state}
    return None


def silent_failure_handoff_comment(
    task: dict[str, Any],
    failed_agent: str,
    observer_id: str,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    parent_run_id = str(task.get("run_id") or task.get("task_id") or "")
    status_run_id = silent_failure_projection_run_id(parent_run_id, failed_agent, observer_id)
    reason = str(diagnostic.get("reason") or "runner_missing")
    deadline_state = str(diagnostic.get("deadline_state") or "unknown")
    soft_deadline_at = str(diagnostic.get("soft_deadline_at") or "")
    byte_detail = ""
    if diagnostic.get("stdout_bytes") is not None or diagnostic.get("stderr_bytes") is not None:
        byte_detail = f"stdout={diagnostic.get('stdout_bytes') or 0} bytes，stderr={diagnostic.get('stderr_bytes') or 0} bytes。"
    body = (
        f"{failed_agent} 这轮已超过 soft deadline 且还没有可发布正文；诊断：{reason}"
        f"（deadline_state={deadline_state}）。"
        + (f"soft_deadline_at={soft_deadline_at}。" if soft_deadline_at else "")
        + byte_detail
        + f"{observer_id} 已进入 degraded-quorum 接手路径；原 runner 如果稍后产出，仍会由 harvest 正常归档。"
    )
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": observer_id,
        "run_id": status_run_id,
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "status",
        "confidence": "high",
        "title": f"{failed_agent} silent-failure handoff",
        "body": body,
        "blockers": [reason],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": False,
        "effective_permissions": comment_effective_permissions(task),
        "telegram_projection_status": "user_visible_silent_failure_handoff",
        "silent_failure_handoff": {
            "failed_agent": failed_agent,
            "observer_agent": observer_id,
            "parent_run_id": parent_run_id,
            "diagnostic": diagnostic,
        },
    }


def maybe_emit_silent_failure_handoff_projection(
    task: dict[str, Any],
    failed_agent: str,
    observer_id: str,
    *,
    allow_send: bool,
) -> dict[str, Any] | None:
    if not group_targeted_task_allows_silent_failure_projection(task):
        return None
    if failed_agent not in LOCAL_RUNTIME_AGENTS or observer_id not in LOCAL_RUNTIME_AGENTS or observer_id == failed_agent:
        return None
    parent_run_id = str(task.get("run_id") or task.get("task_id") or "")
    if not parent_run_id:
        return None
    status_run_id = silent_failure_projection_run_id(parent_run_id, failed_agent, observer_id)
    if silent_failure_projection_already_emitted(observer_id, status_run_id):
        return None
    diagnostic = runner_silent_failure_diagnostic(task, failed_agent)
    if not diagnostic:
        return None
    comment = silent_failure_handoff_comment(task, failed_agent, observer_id, diagnostic)
    append_jsonl(comment_path(observer_id), [comment])
    append_agent_comments_to_room(str(task.get("room_id") or ""), [comment], source="silent_failure_handoff")
    may_project, projection_mode_or_reason = telegram_projection_decision(task, [comment])
    projection_mode = projection_mode_or_reason if may_project else "suppressed"
    chat_id = task_chat_id(task)
    if may_project and allow_send:
        reply_result = send_agent_reply(
            observer_id,
            chat_id,
            status_run_id,
            allow_send,
            prefix=visible_agent_prefix(observer_id, chat_id),
            projection_mode=projection_mode or "normal",
        )
    else:
        reply_result = write_suppressed_reply_artifact(
            observer_id,
            chat_id,
            status_run_id,
            None if may_project else projection_mode_or_reason,
            projection_mode,
        )
        reply_result["ok"] = True
    return {
        "agent_id": observer_id,
        "failed_agent": failed_agent,
        "status_run_id": status_run_id,
        "diagnostic": diagnostic,
        "reply_attempted": bool(may_project and allow_send),
        "reply_ok": (reply_result or {}).get("ok"),
        "projection_mode": projection_mode,
        "suppressed_reason": None if may_project else projection_mode_or_reason,
    }


def is_internal_runner_failure_comment(comment: dict[str, Any]) -> bool:
    """Return True for local runtime plumbing failures, not peer discussion."""
    title = " ".join(str(comment.get("title") or "").strip().lower().split())
    body = " ".join(str(comment.get("body") or "").strip().lower().split())
    blockers = {str(item).strip().lower() for item in (comment.get("blockers") or []) if str(item).strip()}
    projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
    if projection_status in {"visible_failure_pending", "visible_failure_delivered"}:
        return False
    if projection_status == "user_visible_runner_failure":
        return str(comment.get("visibility_reason") or "").strip() != "telegram_user_task_liveness_contract"
    if projection_status in {"local_only_runner_failure", "suppressed_runner_failure", "local_only_retryable_runner_failure"}:
        return True
    failure_blockers = {
        "codex_cli_failed",
        "duplicate_run_dir",
        "raw_internal_json_body",
        "raw_internal_status_body",
        "runner_failed",
        "runner_process_missing",
        "runner_timeout",
        "worker_timeout",
    }
    runner_title = any(marker in title for marker in (
        "runner did not produce a publishable reply",
        "room runner did not produce parsed room json",
        "room runner returned internal status",
        "ark execution failed",
        "execution failed",
    ))
    if runner_title and blockers.intersection(failure_blockers):
        return True
    if runner_title and any(marker in body for marker in (
        "没有形成可直接发布",
        "没有形成可发布正文",
        "no parsed room json",
        "no publishable reply",
        "进程已经不存在",
    )):
        return True
    return title == "codex cli execution blocked" and "codex_cli_failed" in blockers


def start_agent_runner_async(task: dict[str, Any], agent_id: str, local_task_path: Path, runner_dir: Path, runner_cmd: list[str], chat_id: str | None) -> dict[str, Any]:
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    started_at = now_iso()
    task_budget = build_task_budget(task)
    runner_budget = runner_budget_for_agent(task_budget, agent_id)
    max_seconds = active_runner_max_seconds(agent_id, {"runner_budget": runner_budget})
    started_dt = parse_iso_datetime(started_at) or datetime.now(timezone.utc).astimezone()
    runner_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = runner_dir / "stdout.log"
    stderr_path = runner_dir / "stderr.log"
    lock_handle, lock_state = try_acquire_active_runner_dispatch_lock(agent_id, run_id)
    if lock_handle is None:
        return {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "dispatch_deferred",
            "runner_start_deferred": True,
            "defer_reason": "same_run_dispatch_lock_busy",
            "agent_id": agent_id,
            "run_id": run_id,
            "pid": 0,
            "dispatch_lock": lock_state,
            "started_at": started_at,
            "telegram_outbound": False,
            "tokens_printed": False,
        }
    try:
        existing_path = active_runner_path(agent_id, run_id)
        existing_record = read_json(existing_path, {}) if existing_path.exists() else {}
        if isinstance(existing_record, dict) and existing_record:
            existing_runner_dir = Path(str(existing_record.get("runner_dir") or ""))
            existing_result_path = existing_runner_dir / "result.json" if existing_runner_dir else None
            existing_result = read_json(existing_result_path, {}) if existing_result_path and existing_result_path.exists() else {}
            if runner_result_is_terminal(existing_result):
                return {
                    "schema": "openclaw.agent_room.active_runner.v0",
                    "status": "dispatch_deferred",
                    "runner_start_deferred": True,
                    "defer_reason": "result_pending_harvest",
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "pid": int(existing_record.get("pid") or 0),
                    "active_runner": str(existing_path),
                    "dispatch_lock": lock_state,
                    "telegram_outbound": False,
                    "tokens_printed": False,
                }
            existing_alive = active_runner_alive(existing_record)
            existing_stale = active_runner_stale(existing_record)
            if existing_alive and not existing_stale:
                return {
                    "schema": "openclaw.agent_room.active_runner.v0",
                    "status": "dispatch_deferred",
                    "runner_start_deferred": True,
                    "defer_reason": "already_running",
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "pid": int(existing_record.get("pid") or 0),
                    "active_runner": str(existing_path),
                    "dispatch_lock": lock_state,
                    "deadline_state": classify_runner_deadline_state(existing_record),
                    "telegram_outbound": False,
                    "tokens_printed": False,
                }
            if existing_stale:
                cleanup_stale_active_runner_before_dispatch(
                    existing_path,
                    existing_record,
                    reason="stale_active_runner_under_dispatch_lock",
                )
            else:
                _harvest_dead_runner_orphan(existing_path, existing_record)

        launch = start_runner_process_isolated(runner_cmd, runner_dir, stdout_path, stderr_path, agent_id, run_id)
        if launch.get("duplicate_live_unit"):
            return {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "dispatch_deferred",
                "runner_start_deferred": True,
                "defer_reason": "systemd_unit_already_running",
                "agent_id": agent_id,
                "run_id": run_id,
                "pid": int(launch.get("existing_pid") or 0),
                "launch_mode": launch.get("launch_mode"),
                "systemd_unit": launch.get("systemd_unit"),
                "systemd_state": launch.get("systemd_state"),
                "systemd_run": launch.get("systemd_run"),
                "dispatch_lock": lock_state,
                "telegram_outbound": False,
                "tokens_printed": False,
            }
        record = {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": agent_id,
            "run_id": run_id,
            "task_id": task.get("task_id"),
            "room_id": task.get("room_id"),
            "chat_id": chat_id,
            "pid": int(launch.get("pid") or 0),
            "launch_mode": launch.get("launch_mode"),
            "systemd_unit": launch.get("systemd_unit"),
            "systemd_state": launch.get("systemd_state"),
            "systemd_run": launch.get("systemd_run"),
            "runner_memory_max": launch.get("runner_memory_max"),
            "runner_tasks_max": launch.get("runner_tasks_max"),
            "started_at": started_at,
            "soft_deadline_at": runner_budget.get("soft_deadline_at"),
            "hard_deadline_at": runner_budget.get("hard_deadline_at") or (started_dt + timedelta(seconds=max_seconds)).isoformat(timespec="seconds"),
            "expires_at": runner_budget.get("hard_deadline_at") or (started_dt + timedelta(seconds=max_seconds)).isoformat(timespec="seconds"),
            "max_seconds": max_seconds,
            "task_budget": task_budget,
            "runner_budget": runner_budget,
            "context_snapshot": room_context_snapshot(str(task.get("room_id") or "")),
            "task": task,
            "local_task_path": str(local_task_path),
            "runner_dir": str(runner_dir),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "cmd": runner_cmd,
            "telegram_outbound": False,
            "tokens_printed": False,
        }
        if int(record.get("pid") or 0) <= 0:
            systemd_run = record.get("systemd_run") if isinstance(record.get("systemd_run"), dict) else {}
            launch_exit_code = systemd_run.get("exit_code")
            record.update({
                "status": "launch_failed",
                "runner_start_failed": True,
                "failure_reason": "runner_start_missing_pid" if launch_exit_code == 0 else "runner_start_failed",
                "finished_at": now_iso(),
                "comments": 0,
                "collab_followups": [],
                "runtime_takeovers": [],
            })
            FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)
            write_json(FINISHED_RUNNERS / active_runner_path(agent_id, run_id).name, record)
            write_collaboration_status_snapshot(
                task,
                "runner_launch_failed",
                agent_runs=[{
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": record.get("failure_reason"),
                    "pid": record.get("pid"),
                }],
            )
            return record
        write_json(active_runner_path(agent_id, run_id), record)
        write_collaboration_status_snapshot(
            task,
            "runner_started",
            agent_runs=[{
                "agent_id": agent_id,
                "runner_started": True,
                "runner_status": "started_async",
                "pid": record.get("pid"),
                "active_runner": str(active_runner_path(agent_id, run_id)),
            }],
        )
        return record
    finally:
        release_active_runner_dispatch_lock(lock_handle)


def harvest_active_runners(*, allow_send: bool) -> list[dict[str, Any]]:
    harvested: list[dict[str, Any]] = []
    ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
    FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)
    for path in sorted(ACTIVE_RUNNERS.glob("*.json")):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        pid = int(record.get("pid") or 0)
        agent_id = str(record.get("agent_id") or "")
        run_id = str(record.get("run_id") or "")
        alive = active_runner_alive(record)
        stale = alive and active_runner_stale(record)
        # Deadline force-harvest: if hard_deadline_at has passed, force harvest
        # even when systemd shows a stale process (WSL transient unit wrappers
        # can remain ActiveState=active after the agent has exited, creating
        # "fake running" state that blocks re-dispatch with repeated
        # "same run already running" noise).  The hard deadline is the
        # infrastructure's definitive bound — no runner should be treated as
        # still running past its allowed budget.
        deadline_state = classify_runner_deadline_state(record)
        deadline_exceeded = (deadline_state == "hard_deadline_exceeded")
        if deadline_exceeded and not stale:
            alive = False
            stale = False
        task = record.get("task") if isinstance(record.get("task"), dict) else {}
        runner_dir = Path(str(record.get("runner_dir") or ""))
        runner_result = read_json(runner_dir / "result.json", {}) if runner_dir else {}
        has_result = runner_result_is_terminal(runner_result)
        if alive and not stale and not has_result:
            chat_action = maybe_send_runner_chat_action(path, record, allow_send, reason="runner_still_running")
            running_record = {
                "active_runner": str(path),
                "agent_id": agent_id,
                "run_id": run_id,
                "pid": pid,
                "status": "still_running",
                "age_seconds": runner_age_seconds(record),
                "max_seconds": active_runner_max_seconds(agent_id, record),
                "deadline_state": classify_runner_deadline_state(record),
                "soft_deadline_at": record.get("soft_deadline_at"),
                "hard_deadline_at": record.get("hard_deadline_at"),
                "chat_action": chat_action,
            }
            write_collaboration_status_snapshot(task, "harvest_still_running", agent_runs=[running_record])
            harvested.append(running_record)
            continue
        # Always harvest dead runners.  A runner that exits cleanly before writing
        # result.json (e.g. write-then-exit not yet flushed, or crash before write)
        # must not stay in active-runners: it would be re-reported as "still_running"
        # on every subsequent tick and would trigger the dead-runner dispatch guard
        # on every new dispatch attempt, producing repeated "same run already running"
        # noise.  The dispatch guard path (try_dispatch_agent_runner ~line 3752) can
        # clean a single dead file opportunistically, but it is not a substitute for
        # harvest because it only runs when a *new* dispatch is attempted for the
        # same run_id.
        termination_result: dict[str, Any] | None = None
        if alive or has_result:
            termination_result = terminate_runner_record(record)
        else:
            # Orphan harvested and written to finished-runners; continue to next file.
            harvested.append(_harvest_dead_runner_orphan(path, record))
            continue
        comments = runner_comments(runner_result) if isinstance(runner_result, dict) else []
        if not comments:
            runner = {
                "exit_code": runner_result.get("exit_code") if isinstance(runner_result, dict) else None,
                "ok": bool(isinstance(runner_result, dict) and runner_result.get("ok")),
                "stdout": Path(str(record.get("stdout_path") or "")).read_text(encoding="utf-8", errors="replace")[-4000:] if record.get("stdout_path") and Path(str(record.get("stdout_path"))).exists() else "",
                "stderr": Path(str(record.get("stderr_path") or "")).read_text(encoding="utf-8", errors="replace")[-4000:] if record.get("stderr_path") and Path(str(record.get("stderr_path"))).exists() else "",
                "timeout": bool(stale),
                "missing_process": (not alive and not has_result),
                "age_seconds": runner_age_seconds(record),
                "max_seconds": active_runner_max_seconds(agent_id, record),
                "deadline_state": classify_runner_deadline_state(record),
            }
            if not runner.get("ok"):
                comment = fallback_runner_comment(task, agent_id, runner)
                append_jsonl(comment_path(agent_id), [comment])
                comments = [comment]
        context_freshness = runner_context_freshness(record)
        context_rebase_task: dict[str, Any] | None = None
        if context_freshness.get("status") == "stale_context":
            rebase_evidence_comments = context_rebase_evidence_comments(comments, context_freshness)
            if rebase_evidence_comments:
                append_jsonl(comment_path(agent_id), rebase_evidence_comments)
                context_rebase_task = create_context_rebase_task(task, agent_id, context_freshness, rebase_evidence_comments)
            stale_comment = stale_context_comment(task, agent_id, context_freshness)
            append_jsonl(comment_path(agent_id), [stale_comment])
            comments = rebase_evidence_comments + [stale_comment]
            promoted_comments: list[dict[str, Any]] = []
        else:
            comments, promoted_comments = promote_runner_failures_for_visible_silence(task, comments)
            if promoted_comments:
                append_jsonl(comment_path(agent_id), promoted_comments)
        append_agent_comments_to_room(str(task.get("room_id") or record.get("room_id") or ""), comments, source="primary_agent_reply")

        collab_followups: list[dict[str, Any]] = []
        runtime_takeovers: list[dict[str, Any]] = []
        context_rebases: list[dict[str, Any]] = []
        if context_rebase_task:
            context_rebases.append({
                "task_id": context_rebase_task.get("task_id"),
                "target_agents": context_rebase_task.get("target_agents"),
                "from_agent": agent_id,
                "reason": "stale_runner_evidence_rebase",
            })
        for comment in comments:
            takeover_task = create_runtime_takeover_task(task, comment)
            if takeover_task:
                runtime_takeovers.append({
                    "task_id": takeover_task.get("task_id"),
                    "target_agents": takeover_task.get("target_agents"),
                    "from_agent": comment.get("agent_id"),
                })
            created_task = create_collab_followup_task(task, comment)
            if created_task:
                collab_followups.append({
                    "task_id": created_task.get("task_id"),
                    "target_agents": created_task.get("target_agents"),
                    "collab_round": created_task.get("collab_round"),
                })

        reply_result = None
        may_project, projection_mode_or_reason = telegram_projection_decision(task, comments)
        suppress_reason = None if may_project else projection_mode_or_reason
        projection_mode = projection_mode_or_reason if may_project else "suppressed"
        if comments and may_project:
            chat_id = str(record.get("chat_id") or "") or None
            if allow_send:
                reply_result = send_agent_reply(
                    agent_id,
                    chat_id,
                    run_id,
                    allow_send,
                    prefix=visible_agent_prefix(agent_id, chat_id),
                    projection_mode=projection_mode or "normal",
                )
            else:
                reply_result = write_suppressed_reply_artifact(
                    agent_id,
                    chat_id,
                    run_id,
                    "send_not_allowed_for_this_maintenance_harvest",
                    projection_mode,
                )
                reply_result["ok"] = True
        elif comments:
            reply_result = write_suppressed_reply_artifact(
                agent_id,
                str(record.get("chat_id") or "") or None,
                run_id,
                suppress_reason,
                projection_mode,
            )
            reply_result["ok"] = True
        finished = dict(record)
        visible_failure_state = None
        if isinstance(reply_result, dict):
            reply_payload = reply_result_payload(reply_result)
            projection_error = reply_payload.get("projection_error") if isinstance(reply_payload, dict) else None
            telegram_error = reply_payload.get("telegram_error") if isinstance(reply_payload, dict) else None
            sent = bool(reply_payload.get("sent")) if isinstance(reply_payload, dict) else False
            has_user_visible_failure = any(
                str(comment.get("telegram_projection_status") or "").strip().lower() == "user_visible_runner_failure"
                for comment in comments
            )
            if has_user_visible_failure and sent:
                visible_failure_state = "visible_failure_delivered"
            elif has_user_visible_failure and projection_error:
                visible_failure_state = "reply_projection_failed"
            elif has_user_visible_failure and telegram_error:
                visible_failure_state = "telegram_send_failed"
            elif has_user_visible_failure and reply_result.get("ok") is False:
                visible_failure_state = "visible_failure_delivery_failed"
            elif has_user_visible_failure and reply_result.get("ok"):
                visible_failure_state = "visible_failure_candidate_recorded"
            elif has_user_visible_failure:
                visible_failure_state = "visible_failure_delivery_failed"
        reply_delivery_state = classify_reply_delivery_state(
            reply_result,
            visible_failure_state,
            suppress_reason,
        )
        finished.update({
            "status": "finished",
            "finished_at": now_iso(),
            "comments": len(comments),
            "collab_followups": collab_followups,
            "runtime_takeovers": runtime_takeovers,
            "context_rebases": context_rebases,
            "reply_result": reply_result,
            "visible_failure_state": visible_failure_state,
            "reply_delivery_state": reply_delivery_state,
            "reply_failed_visible_layer": reply_delivery_failed(reply_result, reply_delivery_state),
            "telegram_projection_suppressed_reason": suppress_reason,
            "telegram_projection_mode": projection_mode,
            "runner_result": runner_result,
            "stale_runner": bool(stale),
            "context_freshness": context_freshness,
            "missing_process": (not alive and not has_result),
            "deadline_state": classify_runner_deadline_state(record),
            "soft_deadline_at": record.get("soft_deadline_at"),
            "hard_deadline_at": record.get("hard_deadline_at"),
            "termination_result": termination_result,
        })
        write_json(FINISHED_RUNNERS / path.name, finished)
        path.unlink(missing_ok=True)
        finished_record = {
            "active_runner": str(path),
            "agent_id": agent_id,
            "run_id": run_id,
            "pid": pid,
            "status": "finished",
            "stale_runner": bool(stale),
            "context_freshness": context_freshness,
            "missing_process": (not alive and not has_result),
            "deadline_state": classify_runner_deadline_state(record),
            "soft_deadline_at": record.get("soft_deadline_at"),
            "hard_deadline_at": record.get("hard_deadline_at"),
            "comments": len(comments),
            "collab_followups": collab_followups,
            "runtime_takeovers": runtime_takeovers,
            "context_rebases": context_rebases,
            "reply_attempted": bool(reply_result and not reply_result.get("suppressed_reason")),
            "reply_ok": reply_result.get("ok") if isinstance(reply_result, dict) else None,
            "reply_delivery_state": reply_delivery_state,
            "reply_failed_visible_layer": reply_delivery_failed(reply_result, reply_delivery_state),
            "telegram_projection_suppressed_reason": suppress_reason,
            "telegram_projection_mode": projection_mode,
        }
        write_collaboration_status_snapshot(task, "harvest_finished", agent_runs=[finished_record])
        harvested.append(finished_record)
    return harvested



def existing_message_event_ids(paths: list[Path]) -> set[str]:
    seen: set[str] = set()
    for path in paths:
        for record in read_jsonl(path):
            event_id = str(record.get("message_event_id") or "")
            if event_id:
                seen.add(event_id)
    return seen


def append_agent_comments_to_room(room_id: str, comments: list[dict[str, Any]], *, source: str) -> None:
    if not room_id or not comments:
        return
    room_messages = ROOM / "rooms" / room_id / "messages.jsonl"
    seen_ids = existing_message_event_ids([ROOM / "messages.jsonl", room_messages])
    records: list[dict[str, Any]] = []
    for comment in comments:
        projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
        if projection_status in {
            "local_only_quota_silenced",
            "suppressed_quota_silenced",
            "local_only_deferred_liveness_signal",
            "deferred_liveness_signal",
            "local_only_stale_context",
            "local_only_context_rebase_evidence",
        }:
            continue
        if is_internal_runner_failure_comment(comment):
            continue
        body = str(comment.get("body") or "").strip()
        if not body:
            continue
        event_id = f"agent-comment:{comment.get('agent_id')}:{comment.get('run_id')}:{source}"
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        records.append({
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": event_id,
            "room_id": room_id,
            "chat_id": None,
            "chat_type": "agent-room",
            "telegram_message_id": None,
            "update_id": None,
            "actor_agent_id": comment.get("agent_id"),
            "receiver_agent_id": None,
            "target_agents": [],
            "mentioned_targets": [],
            "command_targets": [],
            "text": body[:4000],
            "title": comment.get("title"),
            "kind": comment.get("kind"),
            "created_at": now_iso(),
            "attention_scope": "all_active_room_agents",
            "source": source,
            "canonical_state_advanced": True,
        })
    if records:
        append_jsonl(room_messages, records)
        append_jsonl(ROOM / "messages.jsonl", records)


def observer_followup_task(original_task: dict[str, Any], observer_id: str, primary_comments: list[dict[str, Any]], run_dir: Path) -> Path:
    task_id = f"{original_task.get('task_id', 'task')}-observer-{observer_id}"
    observer_dir = run_dir / "observer" / task_id
    observer_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Agent Room Observer Follow-up",
        "",
        "你是本轮未被直接点名的 peer agent。请只在能补充实质证据、风险或反例时发言。",
        "不要重复 primary agent 已经覆盖的内容；没有新增价值就保持安静。",
        "",
        "如果没有材料补充，请输出 NO_COMMENT。",
        "如果需要发言，请返回 JSON object，body 写给 Alex 和 peer agents 看。",
        "可见文字使用普通中文；不要使用 emoji、勾叉、图标类字符或装饰性项目符号。",
        "",
        "Required JSON keys: agent_id, run_id, kind, confidence, title, body, blockers.",
        "body 必须直接回应当前房间问题或 primary agent 的结论。",
        "",
        "## Original task",
        json.dumps(original_task, ensure_ascii=False, indent=2)[:5000],
        "",
        "## Primary agent comments",
        json.dumps(primary_comments, ensure_ascii=False, indent=2)[:8000],
    ]
    brief_path = observer_dir / "brief.md"
    brief_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    task = dict(original_task)
    task.update({
        "task_id": task_id,
        "run_id": f"{original_task.get('run_id', original_task.get('task_id', 'task'))}-observer-{observer_id}",
        "target_agents": [observer_id],
        "lane": "observer_advisory",
        "brief_path": str(brief_path),
        "requested_by": "agent-room-observer-lane",
        "observer_of_task_id": original_task.get("task_id"),
        "observer_primary_agents": [c.get("agent_id") for c in primary_comments],
        "permissions": {
            "source_edit": False,
            "telegram_send": False,
            "notion_publish": False,
            "github_push": False,
            "secrets_access": False,
            "global_state_change": False,
            "quality_surface_change": False,
        },
    })
    task_path = observer_dir / "task.json"
    write_json(task_path, task)
    return task_path


def material_observer_comments(runner_result: dict[str, Any]) -> list[dict[str, Any]]:
    material: list[dict[str, Any]] = []
    for comment in runner_comments(runner_result):
        body = str(comment.get("body") or "").strip()
        title = str(comment.get("title") or "").strip()
        normalized = (body or title).strip().upper()
        if normalized in {"NO_COMMENT", "NO COMMENT", "???", "????"}:
            continue
        if "NO_COMMENT" in normalized and len(normalized) < 80:
            continue
        material.append(comment)
    return material


def task_brief_text(task: dict[str, Any]) -> str:
    brief_path = task.get("brief_path")
    if not brief_path:
        return ""
    path = Path(str(brief_path))
    if not path.is_absolute():
        path = ROOT / path
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def task_user_message(task: dict[str, Any]) -> str:
    brief = task_brief_text(task)
    marker = "## User message"
    if marker not in brief:
        return brief
    after = brief.split(marker, 1)[1]
    if "\n## " in after:
        after = after.split("\n## ", 1)[0]
    return after.strip()


def task_requests_single_visible_speaker(task: dict[str, Any]) -> bool:
    """Return true when Alex asked for a coherent lead speaker on a specific turn.

    Alex clarified (2026-05-27): "单一发言口径不是说一直让你们中只有main出来说".
    This means: one agent leads the answer on a specific question, but other agents
    should still speak when they have material, distinct contributions (corrections,
    evidence, blockers, different perspectives). It is NOT a blanket silencing of
    non-main agents.

    The projection gate uses this as a per-task coherence signal: peer comments
    can still become local evidence, follow-up work, or the selected summary, but
    the setting must not leak into later room messages.
    """
    if task.get("single_visible_speaker_requested") is True:
        return True
    policy = str(task.get("delivery_policy") or "").strip().lower()
    if policy in {"main_summary_only", "local_only_main_summary", "single_visible_speaker"}:
        return True
    text = task_user_message(task).lower()
    markers = (
        "派一个人出来说",
        "一个人出来说",
        "一个人说就行",
        "派一个人说",
        "其余人继续干活",
        "其他人继续干活",
        "其余继续干活",
        "one person speak",
        "single speaker",
        "main summary only",
    )
    return any(marker in text for marker in markers)


def collaboration_assignments(task: dict[str, Any], targets: list[str]) -> dict[str, dict[str, Any]]:
    """Soft role split for multi-agent turns.

    This is intentionally not a rigid protocol. It gives each peer a distinct
    first angle so the room does not become either one-person work or duplicate
    answers. Agents may still escalate, swap, or say NO_COMMENT when evidence
    suggests the assignment is wrong.
    """
    if len(targets) <= 1:
        return {}
    text = task_user_message(task).lower()
    mechanism_markers = (
        "协作", "机制", "分工", "重复", "主线", "agent room", "@",
        "讨论", "系统性", "系统性的解决方案", "暴露的问题", "原则", "记不住",
        "讨论决定", "方法是错误", "有待改进", "上下文理解", "没跟上", "跟不上",
        "理解错误", "误读", "误解", "错误执行", "错误的执行", "执行错误", "落实不了",
    )
    review_repair_markers = (
        "互相审查", "互相审核", "交叉审查", "交叉审核", "加大审查", "加大审核",
        "发现问题", "自动解决", "自动修复", "自动纠错", "纠错", "胡说八道",
        "不能等着我", "不要等我", "review harder", "cross-review", "cross review",
    )
    parallel_production_requested = bool(
        task.get("parallel_production")
        or task.get("agent_room_parallel_production")
        or str(task.get("production_mode") or "").strip().lower() == "parallel"
    )
    implementation_markers = ("代码", "修", "改", "实现", "patch", "脚本", "runner", "bridge", "daemon")
    reliability_markers = ("失败", "超时", "慢", "刷屏", "limited", "blocker", "gate", "验证", "smoke")
    is_implementation = any(m in text for m in implementation_markers)
    if parallel_production_requested:
        topic = "parallel_production"
    elif any(m in text for m in review_repair_markers):
        topic = "review_repair"
    elif any(m in text for m in mechanism_markers) or idle_agent_contribution_problem_requested(text):
        topic = "collaboration_mechanism"
    elif is_implementation:
        topic = "runtime_implementation"
    elif any(m in text for m in reliability_markers):
        topic = "reliability_diagnosis"
    else:
        topic = "general_room_turn"

    common = {
        "version": "soft-v0",
        "topic": topic,
        "anti_duplication": "只贡献自己分到的生产角度；不要复述另一个 agent 已经明显会覆盖的内容；没有可交付新增价值就输出 NO_COMMENT。",
        "rotation_hint": "这是本轮临时生产分工，不是永久角色；如果证据显示分配不合适，可以说明并提出交换/升级。",
        "production_principle": "每个被调度的 agent 都应尽量成为生产贡献者，而不是只评论。产出可以是 patch、artifact、smoke、验证结论、blocker、可执行设计或可合并的任务切分；纯态度评论不算生产。如果当前 work item 没有直接可做的部分，先在权限内主动寻找不重复的主线推进点（本地证据、patch/artifact、smoke、blocker 或具体 handoff），只有确认没有材料贡献时才 NO_COMMENT。并行生产只适用于明确 opt-in 的新 Agent Room 协作任务，不能自动套用或改写已有生产流程/任务流程。代码只是其中一种产物。",
        "collaboration_system_principle": "协作机制和协作能力是系统责任；agent 需要自动讨论、挑战、收敛、验证和沉淀，不把 Alex 变成手动操作员或默认仲裁者。",
        "first_principles_resolution": "机制或运行时问题先从目标、不变量和边界推导：最新用户意图不能被旧上下文覆盖、agent 在已授权边界内要自主生产、外部/破坏性/secret 动作仍需明确边界、既有 workflow 质量门不能被绕过。不能把问题降级成一次性止血，也不能把安全本地动作的下一步执行交回 Alex。",
        "systemic_problem_protocol": [
            "任何被 Alex 或 peer 暴露的问题，先判定它是一次性执行错误、边界误读、runner/ledger 缺口、提示协议缺口，还是既有 workflow 的质量门问题；不能只回答态度或局部现象。",
            "先从系统目标、不变量和权限边界推导该改哪一层：runtime/runner/ledger/schema/prompt 边界/validator/smoke/artifact/runbook 之一；再落地可逆、可验证的实现切片。不能只做局部止血，不能把下一步执行交回 Alex。",
            "Alex 已给出的方向性原则、类比或纠正应当作为设计约束沉淀；先映射到 OpenClaw 现有入口、状态机和质量门。证据不足时记录具体证据缺口和下一次验证点，而不是要求 Alex 重复说明。",
            "系统方案由 agent 间自动收敛：lead 给候选改动和验收目标，co-producer 用本地证据、反例、patch、smoke 或 blocker 接受/挑战；不可把 Alex 变成内部仲裁器。",
            "输出必须携带可验收物：patch、artifact、smoke/QC 结果、验证结论、可合并任务切分或 blocker；纯解释不算完成。",
            "不得借系统化之名绕过 Translation、People Daily/日报、market、Notion、gateway/provider 等既有流程入口和质量门。",
        ],
        "peer_interaction_protocol": [
            "先处理已可见的 peer 观点、产物或证据：点名一个具体 claim/action/path/run result，并说明同意、反对、补充或交接下一步。",
            "如果同轮并行导致还看不到 peer 当前输出，明确这是 first-pass contribution，并留下一个可由 peer follow-up 验收的 handoff、smoke、patch 范围或 blocker。",
            "不要把 co-producer 写成另一篇独立回答；没有针对 peer 的新增证据、修正、风险或交接就输出 NO_COMMENT。",
        ],
    }
    if topic == "collaboration_mechanism":
        common["mechanism_change_protocol"] = [
            "先对齐已可见 peer 的具体观点或产物，再提出自己的根因假设、系统不变量和可逆实现切片；如果暂无可见 peer 输出，说明 first-pass 并留下 handoff。",
            "先由 agent 之间提出系统目标/不变量、候选方案、风险和可验收输出。",
            "另一个 agent 必须给出不同角度的证据、反例、补充、smoke 建议、blocker 或 NO_COMMENT。",
            "没有收敛前保持现有默认行为；需要实验时优先用显式开关、局部路径和验证，不直接改全局默认。",
            "agent 自己推进下一步协作和验证，不要求 Alex 手动选择内部 lane、触发开关或充当默认裁决者。",
            "Alex 对协作边界或确认负担的纠正，不需要再向 Alex 二次确认；agent 应在权限内自行固化到可逆 patch、artifact、smoke 或记录 blocker。",
        ]
    if topic == "review_repair":
        common["review_repair_protocol"] = [
            "回答现有 workflow、配置、模型规则或运行时事实前，必须先查本地文件、artifact、runner 结果或明确标注未知；不能把记忆/推断说成事实。",
            "发现 peer 的事实、边界、流程或实现错误时，直接给出更正证据；当前权限允许时补可逆 patch、artifact、smoke 或可复现 blocker，不等 Alex 纠错。",
            "互审不是刷更多意见：没有新证据、修正、风险或下一步产物就输出 NO_COMMENT。",
            "自动修复只限可逆的本地 runtime/task-state/提示边界修正；外部发布、 secrets、push、全局不可逆默认值仍然暂停并记录 blocker。",
        ]
    if topic == "parallel_production":
        common["parallel_production_protocol"] = [
            "先把大任务切成互不重叠的 work_items，写清每个分片的 owned artifacts/paths、接口或内容契约、验收命令/质检标准和禁止触碰范围。",
            "多个 agent 可以同时生产不同 work_items；不得回滚或覆盖其他 agent 的产物，遇到接口、口径、格式或事实冲突必须记录 blocker 或 handoff。",
            "第一轮生产后进入交叉审查：每个 agent 审另一个分片的行为/内容、边界、测试/质检和集成风险，并给出 patch、修订、smoke/QC 结果或 blocker。",
            "最终由 runner/lead/main 收敛集成，必须跑共享验收命令或质检清单；未通过时继续分配修复 work_item，直到通过或留下可复现 blocker。",
            "代码只是这种模式的一种产物。对 Translation、日报、People Daily、market、Notion 等既有业务流，必须使用它们已有 workflow 的分片/质检/发布规则；默认排除，不能绕过、替代或改默认入口。"
        ]
    ordered_targets = sorted(targets)
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    roles = collaboration.get("roles") if isinstance(collaboration.get("roles"), list) else []
    manifest_lead = ""
    for role in roles:
        if not isinstance(role, dict):
            continue
        if str(role.get("role") or "").strip() != "lead":
            continue
        candidate = str(role.get("agent_id") or "").strip()
        if candidate in targets:
            manifest_lead = candidate
            break
    digest = hashlib.sha256(str(task.get("task_id") or task.get("run_id") or task_user_message(task)).encode("utf-8")).hexdigest()
    lead_agent = manifest_lead or (ordered_targets[int(digest[:2], 16) % len(ordered_targets)] if ordered_targets else "")
    assignments: dict[str, dict[str, Any]] = {}
    if "codex" in targets:
        assignments["codex"] = {
            **common,
            "turn_position": "lead" if lead_agent == "codex" else "co_producer",
            "role": "runtime_patch_evidence_or_boundary_producer",
            "focus": "从代码、配置、runner、ledger、smoke 或边界审查角度推进；优先给可验收 patch/artifact/smoke/blocker 或可交付任务切分。",
            "avoid": "不要重复 Claude Code 已经覆盖的实现或话术；如果不能形成代码、证据、边界结论、可执行设计或 blocker，就短答或 NO_COMMENT。"
        }
    if "claude-code" in targets:
        assignments["claude-code"] = {
            **common,
            "turn_position": "lead" if lead_agent == "claude-code" else "co_producer",
            "role": "implementation_or_verification_producer",
            "focus": "从实现、任务切分、验收标准、风险和反例角度推进；能直接写代码就认领清晰 work_item，不能就给可验收验证、补丁建议、smoke 或 blocker。",
            "avoid": "不要重复 Codex 已经覆盖的代码或证据；不要泛泛赞同，必须给不同判断、补丁、审核点、smoke 建议、可执行切分或 NO_COMMENT。"
        }
    for agent_id, assignment in assignments.items():
        if assignment.get("turn_position") == "lead":
            assignment["turn_expectation"] = "本轮先给出根因分类、系统不变量和一个可验收实现/验证切片；不要包揽全部。"
        else:
            assignment["turn_expectation"] = "本轮作为 co-producer 审核/补充/挑战 lead 的根因假设或实现切片，同时产出自己的可验收贡献；不要只评论，必要时认领下一个不重复的 work_item。"
    return assignments


def commit_offsets(offsets: dict[str, int], note: str) -> None:
    state = read_json(STATE, {"schema": "openclaw.agent_room.telegram_agent_bridge_poll_state.v0", "offsets": {}})
    state.setdefault("offsets", {}).update(offsets)
    state["updated_at"] = now_iso()
    state["note"] = note
    write_json(STATE, state)


def reply_result_payload(reply_result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(reply_result, dict):
        return {}
    payload = reply_result.get("result")
    if not isinstance(payload, dict):
        payload = reply_result
    stdout = payload.get("stdout")
    if isinstance(stdout, str) and stdout.strip().startswith("{"):
        try:
            decoded = json.loads(stdout)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            pass
    return payload


def classify_reply_delivery_state(
    reply_result: dict[str, Any] | None,
    visible_failure_state: str | None = None,
    suppress_reason: str | None = None,
) -> str:
    if visible_failure_state:
        return visible_failure_state
    if not isinstance(reply_result, dict):
        return "not_attempted"

    payload = reply_result_payload(reply_result)
    if payload.get("sent"):
        return "sent"
    if payload.get("suppressed_reason") or suppress_reason:
        return "suppressed"
    if payload.get("projection_error"):
        return "reply_projection_failed"
    if payload.get("telegram_error"):
        return "telegram_send_failed"
    if reply_result.get("ok") is False or payload.get("ok") is False:
        return "reply_delivery_failed"
    if payload.get("would_send") and not payload.get("sent"):
        return "would_send_not_sent"
    return "unknown"


def reply_delivery_failed(reply_result: dict[str, Any] | None, delivery_state: str | None = None) -> bool:
    if delivery_state in {
        "reply_delivery_failed",
        "reply_projection_failed",
        "telegram_send_failed",
        "visible_failure_delivery_failed",
    }:
        return True
    return isinstance(reply_result, dict) and reply_result.get("ok") is False


def runner_result_has_retryable_failure(runner_result: dict[str, Any]) -> bool:
    if not isinstance(runner_result, dict):
        return False
    for item in runner_result.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("retryable"):
            return True
        comment = item.get("comment") if isinstance(item.get("comment"), dict) else {}
        if isinstance(comment.get("retryable_failure"), dict):
            return True
    return False


def reply_artifact_exists(agent_id: str, run_id: str, task: dict[str, Any] | None = None) -> bool:
    if task_retryable_for_agent(task, agent_id):
        return False
    path = ROOM / "telegram-agent-reply" / f"{agent_id}-{run_id}.json"
    data = read_json(path, {}) if path.exists() else {}
    if data.get("sent") or data.get("suppressed_reason"):
        return True
    finished_path = FINISHED_RUNNERS / f"{agent_id}-{run_id}.json"
    finished = read_json(finished_path, {}) if finished_path.exists() else {}
    reply_result = finished.get("reply_result") if isinstance(finished.get("reply_result"), dict) else {}
    reply_delivery_state = str(finished.get("reply_delivery_state") or "")
    runner_result = finished.get("runner_result") if isinstance(finished.get("runner_result"), dict) else {}
    if runner_result_has_retryable_failure(runner_result):
        return False
    return bool(
        finished.get("status") == "finished"
        and (
            reply_result.get("sent")
            or reply_result.get("suppressed_reason")
            or finished.get("telegram_projection_suppressed_reason")
            or reply_delivery_failed(reply_result, reply_delivery_state)
        )
    )


def write_suppressed_reply_artifact(agent_id: str, chat_id: str | None, run_id: str, reason: str | None, projection_mode: str) -> dict[str, Any]:
    """Record a non-visible projection decision so backlog repair does not rerun it.

    The backlog scanner keys off telegram-agent-reply artifacts. Previously a
    locally suppressed runner comment (for example a runtime takeover or Codex
    CLI cooldown failure) left no reply artifact, so the resident bridge kept
    treating the task as pending and started it again on later ticks. A
    suppressed artifact is the durable "handled, do not re-project/re-run"
    marker.
    """
    out_path = ROOM / "telegram-agent-reply" / f"{agent_id}-{run_id}.json"
    result = {
        "schema": "openclaw.agent_room.telegram_agent_reply.v0",
        "agent_id": agent_id,
        "chat_id": chat_id,
        "run_id": run_id,
        "would_send": False,
        "sent": False,
        "suppressed_reason": reason or "projection_suppressed",
        "body_transformed_reason": None,
        "projection_mode": projection_mode,
        "tokens_printed": False,
        "text_preview": "",
        "created_at": now_iso(),
    }
    write_json(out_path, result)
    return result


def task_source_transport(task: dict[str, Any]) -> str:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return str(source.get("transport") or task.get("requested_by") or "")


def task_manifest_paths_newest_first() -> list[Path]:
    task_root = ROOM / "tasks"
    if not task_root.exists():
        return []
    return sorted(
        task_root.glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )


def recent_pending_task_paths(
    limit: int = 20,
    source_transports: set[str] | None = None,
    *,
    scan_rows: int | None = None,
    include_manifest_scan: bool = False,
) -> list[Path]:
    """Return recent canonical tasks that still lack reply artifacts.

    This repairs the gap where a task entered canonical state before the live
    runner sent a visible reply. It avoids re-running old work once every target
    has either sent or explicitly suppressed a reply.
    """
    rows = read_jsonl(ROOM / "tasks.jsonl")
    pending: list[Path] = []
    seen: set[str] = set()

    def consider(task: dict[str, Any], manifest: Path) -> None:
        if len(pending) >= limit:
            return
        task_id = str(task.get("task_id") or "")
        run_id = str(task.get("run_id") or task_id)
        if not task_id or not run_id:
            return
        manifest_task = read_json(manifest, {}) if manifest.exists() else {}
        effective_task = manifest_task if isinstance(manifest_task, dict) and manifest_task else task
        status = str(effective_task.get("status") or task.get("status") or "").strip().lower()
        if status in {"completed", "failed", "blocked", "partial", "partial_failed", "cancelled", "stale"}:
            return
        if status == "retryable" and not task_retry_due(effective_task):
            return
        transport = task_source_transport(effective_task)
        if source_transports is not None and transport not in source_transports:
            return
        if (
            source_transports is None
            and transport == "agent-room-collab-followup"
            and effective_task.get("peer_followup_visible_allowed") is not True
        ):
            return
        chat_id = task_chat_id(effective_task)
        if not chat_id:
            return
        targets = [agent_id for agent_id in (effective_task.get("target_agents") or []) if agent_id in LOCAL_RUNTIME_AGENTS]
        if not targets:
            return
        if all(reply_artifact_exists(agent_id, run_id, effective_task) for agent_id in targets):
            return
        if not task_has_dispatchable_agent(effective_task, targets, run_id):
            return
        if manifest.exists():
            key = str(manifest)
            if key not in seen:
                seen.add(key)
                pending.append(manifest)

    if scan_rows is None:
        row_slice = rows[-50:]
    elif scan_rows <= 0:
        row_slice = rows
    else:
        row_slice = rows[-scan_rows:]
    for task in reversed(row_slice):
        task_id = str(task.get("task_id") or "")
        manifest = ROOM / "tasks" / task_id / "manifest.json" if task_id else Path()
        consider(task, manifest)
        if len(pending) >= limit:
            break
    if include_manifest_scan and len(pending) < limit:
        for manifest in task_manifest_paths_newest_first():
            if str(manifest) in seen:
                continue
            task = read_json(manifest, {})
            if isinstance(task, dict):
                consider(task, manifest)
            if len(pending) >= limit:
                break
    # Prefer the newest unresolved room task. In live chat, an old slow/failed
    # agent turn must not starve the message Alex just sent.
    return pending


def mark_stale_internal_followup_tasks(max_age_seconds: int, limit: int = 25) -> list[dict[str, Any]]:
    """Mark old queued peer follow-up/bot-mention tasks stale.

    These internal tasks are only useful while their source conversation is
    fresh. Once they sit unclaimed beyond the queue TTL, keeping them `queued`
    makes the room look like it has live collaboration work when it does not.
    """
    if max_age_seconds <= 0 or limit <= 0:
        return []
    marked: list[dict[str, Any]] = []
    for manifest in reversed(task_manifest_paths_newest_first()):
        if len(marked) >= limit:
            break
        task = read_json(manifest, {})
        if not isinstance(task, dict):
            continue
        transport = task_source_transport(task)
        if transport not in STALE_INTERNAL_FOLLOWUP_TRANSPORTS:
            continue
        status = str(task.get("status") or "").strip().lower()
        if status not in {"queued", "deferred"}:
            continue
        lease = task.get("lease") if isinstance(task.get("lease"), dict) else {}
        if lease.get("owner"):
            continue
        age = task_age_seconds(task)
        if age is None or age <= max_age_seconds:
            continue
        run_id = str(task.get("run_id") or task.get("task_id") or "")
        targets = [agent_id for agent_id in (task.get("target_agents") or []) if agent_id in LOCAL_RUNTIME_AGENTS]
        if run_id and any(active_runner_exists(agent_id, run_id) for agent_id in targets):
            continue
        if run_id and targets and all(reply_artifact_exists(agent_id, run_id) for agent_id in targets):
            continue
        now = now_iso()
        summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
        summary["stale_marked_at"] = now
        summary["stale_reason"] = "internal_followup_queue_timeout"
        summary["stale_age_seconds"] = int(age)
        summary["stale_max_age_seconds"] = int(max_age_seconds)
        task["runner_summary"] = summary
        task["status"] = "stale"
        task["blocked_reason"] = "internal_followup_queue_stale"
        task["updated_at"] = now
        write_json(manifest, task)
        marked.append({
            "task_id": task.get("task_id"),
            "transport": transport,
            "age_seconds": int(age),
            "manifest": str(manifest),
        })
    return marked


def unique_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def mark_manifest_dispatch_state(task_path: Path, agent_id: str, state: str, detail: dict[str, Any] | None = None) -> None:
    """Record dispatch state on the canonical manifest before async completion.

    Async runners used to leave canonical manifests as `queued` until the child
    process finished. If a runner was deferred by a concurrency gate, the offset
    could already be committed while the manifest still looked like untouched
    queue state. This is the durable state edge for running/deferred turns.
    """
    if not task_path.exists():
        return
    task = read_json(task_path, {})
    if not isinstance(task, dict):
        return
    summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
    key = "active_agents" if state == "running" else "deferred_agents"
    values = [str(x) for x in (summary.get(key) or []) if str(x)]
    if agent_id not in values:
        values.append(agent_id)
    summary[key] = values
    if detail:
        dispatch_details = summary.get("dispatch_details") if isinstance(summary.get("dispatch_details"), dict) else {}
        dispatch_details[agent_id] = detail
        summary["dispatch_details"] = dispatch_details
    task["runner_summary"] = summary
    if state == "running":
        task["status"] = "running"
    elif str(task.get("status") or "") == "queued":
        task["status"] = "deferred"
    task["updated_at"] = now_iso()
    task.setdefault("heartbeat", {})["last_seen_at"] = now_iso()
    write_json(task_path, task)


def task_age_seconds(task: dict[str, Any]) -> float | None:
    created = parse_iso_datetime(str(task.get("created_at") or task.get("updated_at") or ""))
    if not created:
        return None
    return (datetime.now(timezone.utc).astimezone() - created).total_seconds()


def int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def configured_global_active_runner_limit() -> int:
    return int_env("AGENT_ROOM_GLOBAL_ACTIVE_RUNNER_LIMIT", DEFAULT_GLOBAL_ACTIVE_RUNNER_LIMIT, minimum=1)


def configured_user_main_reserved_runner_slots(global_active_limit: int) -> int:
    raw = os.environ.get("AGENT_ROOM_USER_MAIN_RESERVED_RUNNER_SLOTS")
    if raw is None:
        # Legacy name kept for existing deployments. The lane now covers both
        # fresh user traffic and openclaw-main control/review tasks.
        raw = os.environ.get("AGENT_ROOM_FRESH_USER_RESERVED_RUNNER_SLOTS")
    try:
        value = int(raw) if raw is not None else DEFAULT_USER_MAIN_RESERVED_RUNNER_SLOTS
    except Exception:
        value = DEFAULT_USER_MAIN_RESERVED_RUNNER_SLOTS
    value = max(0, value)
    return min(value, max(0, global_active_limit - 1))


def configured_new_task_limit_per_tick() -> int:
    return int_env("AGENT_ROOM_NEW_TASK_LIMIT_PER_TICK", DEFAULT_NEW_TASK_LIMIT_PER_TICK, minimum=1)


def configured_acceleration_policy() -> str:
    return os.environ.get("AGENT_ROOM_ACCELERATION_POLICY", DEFAULT_ACCELERATION_POLICY).strip().lower()


def acceleration_priority_enabled(policy: str | None = None) -> bool:
    selected = configured_acceleration_policy() if policy is None else str(policy).strip().lower()
    return selected in {"1", "true", "yes", "on", "nonexclusive"}


def reserved_runner_lane(task: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether this task may use the user/main reserved runner lane.

    The global runner cap protects the machine, but the final few slots must not
    be consumed by old standing-agenda or peer-followup work. Fresh Telegram
    turns and main-origin coordination/review tasks are the control plane for the
    room: if they cannot start, agents keep reasoning from stale briefs and Alex
    has to manually correct them. The reserve is intentionally age-bounded so
    old user tasks do not permanently outrank the mainline backlog.
    """
    age = task_age_seconds(task)
    max_age = int_env("AGENT_ROOM_RESERVED_LANE_MAX_AGE_SECONDS", 900, minimum=0)
    if age is not None and max_age > 0 and age > max_age:
        return False, None
    transport = task_source_transport(task)
    requested_by = str(task.get("requested_by") or "")
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    if transport == "telegram":
        return True, "fresh_telegram_user_task"
    if requested_by == "openclaw-main" or str(source.get("stable_message_id") or "").startswith("main-"):
        return True, "openclaw_main_control_task"
    return False, None


def runner_admission_limit(task: dict[str, Any]) -> dict[str, Any]:
    global_active_limit = configured_global_active_runner_limit()
    reserved_slots = configured_user_main_reserved_runner_slots(global_active_limit)
    uses_reserved_lane, reserved_lane_reason = reserved_runner_lane(task)
    effective_limit = global_active_limit if uses_reserved_lane else max(1, global_active_limit - reserved_slots)
    return {
        "global_active_runner_limit": global_active_limit,
        "effective_active_runner_limit": effective_limit,
        "user_main_reserved_runner_slots": reserved_slots,
        "fresh_user_reserved_runner_slots": reserved_slots,
        "reserved_runner_lane": uses_reserved_lane,
        "reserved_lane_reason": reserved_lane_reason,
    }


def configured_enforce_per_agent_active_limit() -> bool:
    return os.environ.get("AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT", "0").strip().lower() in {"1", "true", "yes", "on"}


def configured_per_agent_active_runner_limit() -> int:
    return int_env("AGENT_ROOM_ACTIVE_RUNNERS_PER_AGENT", 2, minimum=1)


def runner_per_agent_limit_decision(agent_id: str, *, reserved_runner_lane: bool) -> dict[str, Any]:
    active_count = active_runner_count(agent_id)
    active_limit = configured_per_agent_active_runner_limit()
    # The per-agent cap throttles ordinary/backlog work. Fresh user and
    # openclaw-main control tasks must still be able to use the reserved lane;
    # the global cap remains the hard machine-level bound.
    blocked = active_count >= active_limit and not reserved_runner_lane
    return {
        "blocked": blocked,
        "active_runner_count": active_count,
        "active_runner_limit": active_limit,
        "reserved_runner_lane": reserved_runner_lane,
    }


def task_recent_enough_for_backlog(task: dict[str, Any], max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return True
    age = task_age_seconds(task)
    return age is None or age <= max_age_seconds


def soft_deadline_handoff_targets(task: dict[str, Any], targets: list[str]) -> list[str]:
    """Let another local peer take over after the first owner stays silent.

    The first-response owner is a UX hint, not a lock. Before the soft deadline
    we give the explicitly addressed agent the first shot; after it passes with
    no reply artifact, other local peers may start the same task while the
    original runner continues until its hard deadline.
    """
    local_targets = [agent_id for agent_id in targets if agent_id in LOCAL_RUNTIME_AGENTS]
    if not local_targets:
        return []
    if is_private_dm_room(str(task.get("room_id") or "")):
        return []
    first_owner = str(task.get("first_response_owner") or "").strip()
    if not first_owner and len(local_targets) == 1:
        first_owner = local_targets[0]
    if not first_owner or first_owner not in LOCAL_RUNTIME_AGENTS:
        return []
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    if not run_id or reply_artifact_exists(first_owner, run_id):
        return []
    budget = build_task_budget({**task, "target_agents": local_targets, "first_response_owner": first_owner})
    soft_deadline = parse_iso_datetime(str(budget.get("soft_deadline_at") or ""))
    if not soft_deadline or datetime.now(timezone.utc).astimezone() < soft_deadline:
        return []
    return sorted(
        agent_id
        for agent_id in LOCAL_RUNTIME_AGENTS
        if agent_id not in local_targets and not reply_artifact_exists(agent_id, run_id)
    )


def task_requests_acceleration(task: dict[str, Any]) -> bool:
    text = task_user_message(task).lower()
    return any(marker in text for marker in TASK_ACCELERATION_MARKERS)


def select_new_task_paths(paths: list[Path], limit: int, prioritize_acceleration: bool = False) -> list[Path]:
    """Select new tasks; acceleration priority is opt-in until the room agrees on policy."""
    unique = unique_paths(paths)
    if len(unique) <= limit:
        return unique
    if not prioritize_acceleration:
        return unique[-limit:]

    records: list[dict[str, Any]] = []
    for idx, path in enumerate(unique):
        task = read_json(path, {}) if path.exists() else {}
        if not isinstance(task, dict):
            task = {}
        records.append({
            "idx": idx,
            "path": path,
            "accelerated": task_requests_acceleration(task),
        })

    accelerated = [record for record in records if record["accelerated"]]
    ordinary = [record for record in records if not record["accelerated"]]
    selected: list[dict[str, Any]] = []
    if accelerated:
        accel_budget = limit if not ordinary else max(1, limit - 1)
        selected.extend(accelerated[-accel_budget:])

    remaining = limit - len(selected)
    if remaining > 0:
        selected.extend(ordinary[-remaining:])

    remaining = limit - len(selected)
    if remaining > 0:
        seen = {record["idx"] for record in selected}
        selected.extend([record for record in accelerated if record["idx"] not in seen][-remaining:])

    return [record["path"] for record in sorted(selected, key=lambda item: item["idx"])]


def env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


# === DEPRECATED: 2026-05-26 ===
# The functions room_mainline_agenda_paths through maybe_create_standing_mainline_task
# are legacy scheduler code superseded by standing_agenda_tick.py (called via subprocess
# at line ~3237). These functions share NO state with the active scheduler — if
# inadvertently re-enabled they would create DUPLICATE standing tasks bypassing the
# active scheduler's cooldown, round counting, and max_rounds gate.
#
# Only smoke_runtime_recovery.py (which is already failing) still references
# maybe_create_standing_mainline_task via inspect.getsource; it is NOT called in
# any production path.
#
# Do NOT re-enable. Remove after smoke_runtime_recovery.py is updated.
def room_mainline_agenda_paths() -> list[Path]:
    rooms_root = ROOM / "rooms"
    if not rooms_root.exists():
        return []
    return sorted(rooms_root.glob("*/mainline_agenda.json"))


def mainline_scheduler_enabled(agenda: dict[str, Any]) -> bool:
    override = env_flag("AGENT_ROOM_STANDING_MAINLINE_DISCUSSION")
    if override is not None:
        return override
    scheduler = agenda.get("scheduler") if isinstance(agenda.get("scheduler"), dict) else {}
    return bool(scheduler.get("enabled"))


def first_open_mainline_item(agenda: dict[str, Any]) -> dict[str, Any] | None:
    for item in agenda.get("active_items") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "open").strip().lower()
        if status in {"open", "in_progress", "active", "blocked_pending_agent"}:
            return item
    return None


def mainline_scheduler_on_cooldown(scheduler: dict[str, Any]) -> bool:
    cooldown_seconds = int(scheduler.get("cooldown_seconds") or os.environ.get("AGENT_ROOM_STANDING_MAINLINE_COOLDOWN_SECONDS", "900"))
    if cooldown_seconds <= 0:
        return False
    last = parse_iso_datetime(str(scheduler.get("last_injected_at") or ""))
    if not last:
        return False
    return (datetime.now(timezone.utc).astimezone() - last).total_seconds() < cooldown_seconds


def mainline_pending_task_exists(room_id: str, item_id: str) -> bool:
    for row in reversed(read_jsonl(ROOM / "tasks.jsonl")[-200:]):
        if str(row.get("room_id") or "") != room_id:
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        if str(source.get("transport") or "") not in {"agent-room-proactive-mainline", "agent-room-standing-mainline"}:
            continue
        standing = row.get("standing_mainline") if isinstance(row.get("standing_mainline"), dict) else {}
        if str(standing.get("item_id") or "") != item_id:
            continue
        run_id = str(row.get("run_id") or row.get("task_id") or "")
        targets = [str(agent_id) for agent_id in (row.get("target_agents") or []) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
        if run_id and targets and not all(reply_artifact_exists(agent_id, run_id) for agent_id in targets):
            return True
    return False


def standing_mainline_brief(room_id: str, agenda: dict[str, Any], item: dict[str, Any], round_index: int) -> str:
    visible = agenda.get("visible_policy") if isinstance(agenda.get("visible_policy"), dict) else {}
    acceptance = "\n".join(f"- {value}" for value in (item.get("acceptance_evidence") or []))
    must_not = "\n".join(f"- {value}" for value in (item.get("must_not_displace") or []))
    recent = recent_room_context_excerpt(room_id, limit=12)
    return "\n".join([
        "# Agent Room standing mainline discussion",
        "",
        "这不是用户新发起的一次性问答，而是已经打开的 OpenClaw 进化主线的自动续议任务。",
        "你们的职责是继续讨论、挑战、实现、验证和沉淀，不要等 Alex 再发消息拉起。",
        "能做最小 patch / smoke / artifact 就直接做；不能做就给出可复现 blocker 和下一步。使用中文。",
        "",
        f"Room: `{room_id}`",
        f"Standing round for this item: `{round_index}`",
        f"Mainline item: `{item.get('id')}` — {item.get('title') or ''}",
        f"Layer: {item.get('layer') or ''}",
        "",
        "## User value",
        str(item.get("user_value") or "").strip(),
        "",
        "## Exact work item",
        str(item.get("work_item") or "").strip(),
        "",
        "## Acceptance evidence",
        acceptance or "- 未声明；请先补清楚可验收证据。",
        "",
        "## Must not displace / must not break",
        must_not or "- 保持既有生产流程和外部发布 gate。",
        "",
        "## Visible projection rule",
        "群里只发材料性进展、分歧/纠错、blocker、验证结果或需要 Alex 决策的点；普通中间推演留在 artifact/ledger。",
        "Visible send_when: " + ", ".join(str(x) for x in (visible.get("send_when") or [])),
        "Do not send for: " + ", ".join(str(x) for x in (visible.get("do_not_send_for") or [])),
        "",
        "## Recent room context",
        recent,
    ])


def maybe_create_standing_mainline_task(existing_task_paths: list[Path] | None = None) -> Path | None:
    """DEPRECATED (2026-05-26): superseded by standing_agenda_tick.py subprocess.

    This function is NOT called in any production path. It remains only as
    reference for smoke_runtime_recovery.py's inspect.getsource check. The
    active scheduler is standing_agenda_tick.py, invoked by the daemon at
    line ~3237 with shared cooldown/round-counting state.

    DO NOT re-enable — no state is shared with the active scheduler; doing so
    would create duplicate standing tasks bypassing cooldown/round gates.
    Do NOT re-enable. Remove after smoke_runtime_recovery.py is updated.
    """
    if existing_task_paths:
        return None
    if active_runner_count() > 0:
        return None
    for agenda_path in room_mainline_agenda_paths():
        agenda = read_json(agenda_path, {})
        if not isinstance(agenda, dict) or not mainline_scheduler_enabled(agenda):
            continue
        room_id = str(agenda.get("room_id") or agenda_path.parent.name)
        item = first_open_mainline_item(agenda)
        if not item:
            continue
        item_id = str(item.get("id") or compact_slug(str(item.get("title") or "mainline")))
        scheduler = agenda.get("scheduler") if isinstance(agenda.get("scheduler"), dict) else {}
        if mainline_scheduler_on_cooldown(scheduler):
            continue
        injected_counts = scheduler.get("injected_counts") if isinstance(scheduler.get("injected_counts"), dict) else {}
        count = int(injected_counts.get(item_id) or 0)
        max_rounds = int(scheduler.get("max_rounds_per_item") or os.environ.get("AGENT_ROOM_STANDING_MAINLINE_MAX_ROUNDS_PER_ITEM", "3"))
        if max_rounds >= 0 and count >= max_rounds:
            continue
        if mainline_pending_task_exists(room_id, item_id):
            continue
        room_json = read_json(ROOM / "rooms" / room_id / "room.json", {})
        chat_id = str(room_json.get("telegram_chat_id") or agenda.get("telegram_chat_id") or "")
        if not chat_id:
            continue
        round_index = count + 1
        digest = hashlib.sha256(
            json.dumps({"room_id": room_id, "item_id": item_id, "round": round_index}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        task_id = f"standing-{compact_slug(room_id)}-{compact_slug(item_id)}-{digest}"
        if task_exists(task_id):
            continue
        task_dir = ROOM / "tasks" / task_id
        brief_path = task_dir / "brief.md"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(standing_mainline_brief(room_id, agenda, item, round_index) + "\n", encoding="utf-8")
        created = now_iso()
        targets = [str(agent_id) for agent_id in (scheduler.get("target_agents") or ["codex", "claude-code"]) if str(agent_id) in LOCAL_RUNTIME_AGENTS]
        if not targets:
            targets = ["codex", "claude-code"]
        task = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": room_id,
            "requested_by": "agent-room-standing-mainline",
            "target_agents": targets,
            "lane": "standing_mainline_discussion",
            "brief_path": str(brief_path),
            "context_paths": [f"agent-room/rooms/{room_id}/mainline_agenda.json", "agent-room/telegram_room_runtime_plan.md"],
            "permissions": {
                "source_edit": True,
                "telegram_send": False,
                "notion_publish": False,
                "github_push": False,
                "secrets_access": False,
                "global_state_change": True,
                "quality_surface_change": False,
            },
            "expected_outputs": [],
            "status": "queued",
            "review_status": "requested",
            "blocked_reason": None,
            "result_paths": [],
            "canonical_imported": True,
            "created_at": created,
            "updated_at": created,
            "lease": {"owner": None, "heartbeat_at": None, "expires_at": None},
            "heartbeat": {"last_seen_at": None},
            "retry_budget": {"max_attempts": 1, "attempt": 0},
            "manual_boundary": True,
            "quality_gate_status": "not_applicable",
            "side_effect_gate_status": "closed",
            "telegram_projection_status": "room_bridge_gate_only",
            "standing_mainline": {
                "schema": "openclaw.agent_room.standing_mainline.v0",
                "item_id": item_id,
                "round": round_index,
                "agenda_path": str(agenda_path),
            },
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "mode": "standing_mainline_discussion",
                "status": "open",
                "participants": targets,
                "work_items": [
                    {
                        "id": f"standing_mainline_{compact_slug(item_id)}_{compact_slug(agent_id)}",
                        "status": "open",
                        "assigned_to": agent_id,
                        "description": "Continue the already-open OpenClaw evolution mainline; produce patch, smoke, verification, blocker, or material disagreement without waiting for Alex.",
                    }
                    for agent_id in targets
                ],
                "claims": [],
                "handoffs": [],
                "artifacts": [],
                "blockers": [],
                "max_rounds": 1,
                "created_at": created,
            },
            "source": {
                "transport": "agent-room-proactive-mainline",
                "chat_id": chat_id,
                "update_id": f"standing-mainline:{room_id}:{item_id}:{round_index}",
                "message_text_sha256": hashlib.sha256(str(item).encode("utf-8", errors="replace")).hexdigest(),
            },
            "delivery_policy": "standing_mainline_material_summary",
            "reply_policy": "agents_continue_open_mainline_until_done_blocked_or_decision_needed",
            "canonical_state_advanced": True,
            "canonical_imported_at": created,
        }
        manifest = task_dir / "manifest.json"
        write_json(manifest, task)
        append_jsonl(ROOM / "tasks.jsonl", [task])
        append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])
        injected_counts[item_id] = round_index
        scheduler["enabled"] = True
        scheduler["last_injected_at"] = created
        scheduler["last_injected_task_id"] = task_id
        scheduler["last_injected_item_id"] = item_id
        scheduler["injected_counts"] = injected_counts
        agenda["scheduler"] = scheduler
        agenda["updated_at"] = created
        write_json(agenda_path, agenda)
        return manifest
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Resident Agent Room bridge tick for Codex/Claude Telegram bots.")
    parser.add_argument("--mode", choices=["inspect-only", "harvest-only", "consume-only", "live"], default=None,
                        help="inspect-only: no send/no offset commit; harvest-only: local runner harvest only/no poll/no send; consume-only: durable local accounting plus offset commit/no send; live: execute/reply/commit.")
    parser.add_argument("--allow-send", action="store_true", help="Legacy/live gate: allow Telegram replies for processed tasks.")
    parser.add_argument("--commit-offset", action="store_true", help="Legacy/live gate: commit offsets after each update is accounted for.")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--limit-per-bot", type=int, default=20)
    parser.add_argument("--room-id", default="openclaw-evolution")
    args = parser.parse_args()

    if args.mode is None:
        mode = "live" if args.allow_send else ("consume-only" if args.commit_offset else "inspect-only")
    else:
        mode = args.mode

    allow_send = bool(args.allow_send and mode == "live")
    commit_offset = bool(args.commit_offset and mode in {"consume-only", "live"})
    run_agents = bool(mode == "live")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Egress/recovery must not depend on fresh Telegram ingress. If polling is
    # slow or unavailable, already-started agent runners still need to be
    # harvested so their real comments, suppressed decisions, or blockers can
    # advance the room state.
    should_harvest = mode in {"harvest-only", "consume-only", "live"}
    harvested_runners: list[dict[str, Any]] = harvest_active_runners(allow_send=allow_send) if should_harvest else []
    expired_collaboration_claims: list[dict[str, Any]] = (
        reconcile_expired_collaboration_claims(
            int_env("AGENT_ROOM_EXPIRED_COLLAB_CLAIM_RECONCILE_LIMIT", 50, minimum=0)
        )
        if should_harvest
        else []
    )

    # POST-HARVEST STANDING AGENDA RESCAN (2026-05-26):
    # After harvesting completed agent output, immediately check for due
    # standing agenda items to chain autonomous continuation WITHOUT
    # waiting for the next periodic tick (which is often suppressed by
    # fresh user tasks). Using --fresh-task-count 0 bypasses the
    # "suppressed_fresh_user_task" gate since the agent output produced
    # here is exactly the trigger that should advance the agenda.
    # This is the fix for "Alex still needs to say '继续'" — the system
    # should autonomously inspect completed work and launch the next
    # bounded task instead of stopping after one round.
    post_harvest_standing: dict[str, Any] = {"status": "not_run", "created": False}
    if should_harvest and harvested_runners and mode == "live":
        post_harvest_run = run_cmd([
            "python3", str(TOOLS / "standing_agenda_tick.py"),
            "--room-id", args.room_id,
            "--fresh-task-count", "0",
            "--active-runner-count", str(active_runner_blocking_count_for_standing_agenda()),
        ], timeout=60)
        try:
            parsed = json.loads(str(post_harvest_run.get("stdout") or "{}"))
            if isinstance(parsed, dict):
                post_harvest_standing.update(parsed)
        except Exception:
            post_harvest_standing["parse_error"] = str(post_harvest_run.get("stdout") or "")[-400:]

    if mode == "harvest-only":
        harvested_runner_count = sum(1 for item in harvested_runners if item.get("status") != "still_running")
        result = {
            "schema": "openclaw.agent_room.resident_bridge_tick.v0",
            "ok": True,
            "stage": "harvest",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "mode": mode,
            "harvested_runners": harvested_runners,
            "harvested_runner_count": harvested_runner_count,
            "still_running_count": sum(1 for item in harvested_runners if item.get("status") == "still_running"),
            "active_runner_count_after_harvest": active_runner_count(),
            "blocking_active_runner_count_after_harvest": active_runner_blocking_count_for_standing_agenda(),
            "expired_collaboration_claims": expired_collaboration_claims,
            "post_harvest_standing_agenda": post_harvest_standing,
            "telegram_outbound": False,
            "tokens_printed": False,
        }
        write_json(run_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    poller = TOOLS / "telegram_agent_bridge_poll.py"
    poll_cmd = [
        "python3", str(poller),
        "--dry-run",
        "--timeout", str(args.timeout),
        "--limit-per-bot", str(args.limit_per_bot),
        "--out-dir", str(run_dir / "poll"),
    ]
    poll = run_cmd(poll_cmd, timeout=max(120, args.timeout + 120))
    poll_soft_ok = bool(poll.get("ok"))
    if not poll_soft_ok:
        # The poller may return non-zero when an optional/non-ingress bot lacks
        # a token (for example openclaw-main used only for status-card egress),
        # while the normalized room ingest still succeeded for Codex/Claude and
        # wrote canonical dry-run task artifacts.  Treat that as degraded ingest
        # instead of aborting the whole resident tick; otherwise no tasks are
        # dispatched and the status board misleadingly shows all agents idle.
        try:
            poll_stdout = json.loads(str(poll.get("stdout") or "{}"))
        except Exception:
            poll_stdout = {}
        normalized = poll_stdout.get("normalized") if isinstance(poll_stdout, dict) else {}
        if isinstance(normalized, dict) and normalized.get("ok"):
            poll_soft_ok = True
            poll["degraded_ok"] = True
            poll["degraded_reason"] = "poll_normalized_ok_optional_bot_failure"
    if not poll_soft_ok:
        result = {
            "schema": "openclaw.agent_room.resident_bridge_tick.v0",
            "ok": False,
            "stage": "poll",
            "poll": poll,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "mode": mode,
            "harvested_runners": harvested_runners,
            "expired_collaboration_claims": expired_collaboration_claims,
            "telegram_outbound": False,
            "tokens_printed": False,
        }
        write_json(run_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    poll_result = read_json(run_dir / "poll" / "poll-result.json", {})
    raw_updates = read_json(run_dir / "poll" / "raw-updates.sanitized.json", [])
    canonical_import = {"canonical_state_advanced": False}
    if mode in {"consume-only", "live"}:
        canonical_import = import_canonical_artifacts(run_dir / "poll", allow_send=allow_send)
    # Pinned status card: live ticks may send/edit the fixed Telegram message.
    # Consume-only ticks only refresh the dry-run projection; no Telegram egress.
    pinned_card_result: dict[str, Any] = {"status": "skipped", "ok": False, "steps": []}
    if mode in {"consume-only", "live"}:
        room_config = read_json(ROOM / "rooms" / args.room_id / "room.json", {})
        card_chat_id = str(room_config.get("telegram_chat_id") or "")
        if card_chat_id:
            pinned_card_plan = pinned_status_card_command(
                room_id=args.room_id,
                chat_id=card_chat_id,
                allow_send=allow_send,
            )
            pinned_card_result = run_cmd(
                pinned_card_plan["command"],
                timeout=30,
                env=room_tool_env(),
            )
            try:
                pinned_card_result = json.loads(str(pinned_card_result.get("stdout") or "{}"))
                pinned_card_result["execution_mode"] = pinned_card_plan["execution_mode"]
                pinned_card_result["telegram_outbound"] = pinned_card_plan["telegram_outbound"]
            except Exception:
                pinned_card_result = {
                    "ok": False,
                    "status": "parse_error",
                    "stdout": str(pinned_card_result.get("stdout") or "")[-500:],
                    "execution_mode": pinned_card_plan["execution_mode"],
                    "telegram_outbound": pinned_card_plan["telegram_outbound"],
                }
        else:
            pinned_card_result = {"status": "no_chat_id", "ok": False}

    auto_key_provision_result: dict[str, Any] = {
        "status": "not_run",
        "command": ["python3", str(TOOLS / "auto_key_provision.py")],
    }
    if mode in {"consume-only", "live"}:
        key_provision_run = run_cmd(["python3", str(TOOLS / "auto_key_provision.py")], timeout=20)
        key_provision_payload = {}
        try:
            if key_provision_run.get("stdout"):
                key_provision_payload = json.loads(key_provision_run["stdout"])
        except Exception:
            key_provision_payload = {"status": "parse_error", "stdout": str(key_provision_run.get("stdout") or "")[:4000]}
        auto_key_provision_result = {
            "ok": key_provision_run.get("ok"),
            "status": "ok" if key_provision_run.get("ok") else "failed",
            "command_exit_code": key_provision_run.get("exit_code"),
            "command_stderr": str(key_provision_run.get("stderr") or "")[:4000],
            "payload": key_provision_payload,
        }
    processed: list[dict[str, Any]] = []
    standing_agenda_result: dict[str, Any] = {"status": "not_run", "created": False}
    stale_internal_followups: list[dict[str, Any]] = []
    committed: dict[str, int] = {}
    if mode in {"consume-only", "live"}:
        # New Telegram messages are real-time room traffic. Give them a wider
        # non-exclusive lane: acceleration pulls a task forward, but ordinary
        # collaboration still keeps budget in the same tick. Liveness is
        # protected by per-agent runner timeouts and fallback comments, not by
        # blocking the whole room after one task.
        new_task_paths = unique_paths([Path(p) for p in (canonical_import.get("task_files_written") or [])])
        new_task_limit = configured_new_task_limit_per_tick()
        acceleration_policy = configured_acceleration_policy()
        prioritize_acceleration = acceleration_priority_enabled(acceleration_policy)
        new_task_paths = select_new_task_paths(new_task_paths, new_task_limit, prioritize_acceleration)
        standing_task_paths: list[Path] = []
        if run_agents:
            standing_run = run_cmd([
                "python3", str(TOOLS / "standing_agenda_tick.py"),
                "--room-id", args.room_id,
                "--active-runner-count", str(active_runner_blocking_count_for_standing_agenda()),
            ], timeout=60)
            standing_agenda_result = {
                "ok": standing_run.get("ok"),
                "status": "subprocess_failed" if not standing_run.get("ok") else "completed",
                "exit_code": standing_run.get("exit_code"),
                "created": False,
                "tokens_printed": False,
            }
            try:
                parsed_standing = json.loads(str(standing_run.get("stdout") or "{}"))
                if isinstance(parsed_standing, dict):
                    standing_agenda_result.update(parsed_standing)
            except Exception as exc:
                standing_agenda_result["parse_error"] = type(exc).__name__ + ": " + str(exc)
                standing_agenda_result["stdout_tail"] = str(standing_run.get("stdout") or "")[-1200:]
            manifest_path = standing_agenda_result.get("manifest_path")
            if standing_agenda_result.get("created") and manifest_path:
                manifest = Path(str(manifest_path))
                if manifest.exists():
                    standing_task_paths.append(manifest)
        # Peer collaboration is real work, not ordinary stale backlog. Give
        # internal follow-up / bot-mention tasks a small dedicated lane so the
        # room can keep collaborating while quiet, without letting old tasks
        # flood the chat.
        stale_seconds = max(0, int(os.environ.get("AGENT_ROOM_INTERNAL_FOLLOWUP_STALE_SECONDS", "21600")))
        stale_limit = max(0, int(os.environ.get("AGENT_ROOM_INTERNAL_FOLLOWUP_STALE_LIMIT_PER_TICK", "25")))
        stale_internal_followups = mark_stale_internal_followup_tasks(stale_seconds, stale_limit) if run_agents else []
        collab_limit = max(0, int(os.environ.get("AGENT_ROOM_COLLAB_TASK_LIMIT_PER_TICK", "2")))
        collab_scan_rows = max(0, int(os.environ.get("AGENT_ROOM_COLLAB_TASK_SCAN_ROWS", "500")))
        collab_paths = [
            p
            for p in recent_pending_task_paths(
                source_transports=DEDICATED_COLLAB_TASK_TRANSPORTS,
                scan_rows=collab_scan_rows,
                include_manifest_scan=True,
            )
            if p not in new_task_paths and p not in standing_task_paths
        ][:collab_limit]
        backlog_max_age = max(0, int(os.environ.get("AGENT_ROOM_BACKLOG_REPAIR_MAX_AGE_SECONDS", "1800")))
        backlog_paths = []
        for p in recent_pending_task_paths():
            if p in new_task_paths or p in standing_task_paths or p in collab_paths:
                continue
            task_for_backlog = read_json(p, {}) if p.exists() else {}
            if isinstance(task_for_backlog, dict) and not task_recent_enough_for_backlog(task_for_backlog, backlog_max_age):
                continue
            backlog_paths.append(p)
        backlog_limit = max(0, int(os.environ.get("AGENT_ROOM_BACKLOG_REPAIR_LIMIT", "1")))
        task_paths_to_process = new_task_paths + unique_paths(standing_task_paths) + unique_paths(collab_paths) + unique_paths(backlog_paths)[:backlog_limit]
    else:
        task_paths_to_process = task_files(run_dir / "poll")

    for task_path in task_paths_to_process:
        if not run_agents:
            processed.append({
                "task": str(task_path),
                "mode": mode,
                "agent_execution_skipped": True,
                "reply_attempted": False,
                "reply_ok": None,
            })
            continue
        task = read_json(task_path)
        raw_targets = list(task.get("target_agents") or [])
        local_targets = [agent_id for agent_id in raw_targets if agent_id in LOCAL_RUNTIME_AGENTS]
        targets = private_dm_visible_targets(task, local_targets)
        skipped_targets = [agent_id for agent_id in raw_targets if agent_id not in LOCAL_RUNTIME_AGENTS]
        private_dm_skipped_targets = [agent_id for agent_id in local_targets if agent_id not in targets]
        skipped_targets.extend(private_dm_skipped_targets)
        if not targets:
            skip_reason = "private_dm_agent_mismatch" if private_dm_skipped_targets else "no_local_runtime_targets"
            processed.append({
                "task": str(task_path),
                "raw_targets": raw_targets,
                "skipped_targets": skipped_targets,
                "private_dm_skipped_targets": private_dm_skipped_targets,
                "skip_reason": skip_reason,
                "reply_attempted": False,
                "reply_ok": None,
            })
            continue
        chat_id = task_chat_id(task)
        handoff_targets = soft_deadline_handoff_targets(task, targets)
        silent_failure_projections: list[dict[str, Any]] = []
        if handoff_targets:
            failed_first_owner = str(task.get("first_response_owner") or (targets[0] if len(targets) == 1 else "")).strip()
            for observer_id in handoff_targets:
                projection = maybe_emit_silent_failure_handoff_projection(
                    task,
                    failed_first_owner,
                    observer_id,
                    allow_send=allow_send,
                )
                if projection:
                    silent_failure_projections.append(projection)
            targets = list(dict.fromkeys([*targets, *handoff_targets]))
            summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
            summary["soft_deadline_handoff_agents"] = sorted(set([*(summary.get("soft_deadline_handoff_agents") or []), *handoff_targets]))
            summary["soft_deadline_handoff_at"] = now_iso()
            if silent_failure_projections:
                summary["silent_failure_handoff_projections"] = silent_failure_projections
            task["runner_summary"] = summary
            task["target_agents_effective"] = targets
            task["updated_at"] = now_iso()
            write_json(task_path, task)
        primary_comments: list[dict[str, Any]] = []
        reply_results: list[dict[str, Any]] = []
        agent_runs: list[dict[str, Any]] = []

        # Run and reply per agent instead of batching all targets into one
        # runner. Otherwise a slow/blocked Claude Code run prevents Codex from
        # speaking, which makes the room look silent even when one peer already
        # has a valid answer.
        assignments = collaboration_assignments(task, targets)
        for agent_id in targets:
            single_task = dict(task)
            single_task["target_agents"] = [agent_id]
            single_task["_canonical_manifest_path"] = str(task_path)
            if assignments:
                single_task["collaboration_assignment"] = assignments.get(agent_id) or {}
                single_task["collaboration_peer_agents"] = [peer for peer in targets if peer != agent_id]
            task_slot = task_file_slot(task, task_path)
            local_task_path = run_dir / "runner" / task_slot / agent_id / "local-runtime-task.json"
            write_json(local_task_path, single_task)
            runner_dir = run_dir / "runner" / task_slot / agent_id
            runner_cmd = [
                "python3", str(TOOLS / "agent_task_runner.py"),
                "--task-file", str(local_task_path),
                "--out-dir", str(runner_dir),
                "--allow-exec",
            ]
            run_id_for_agent = str(task.get("run_id") or task.get("task_id") or "")
            if reply_artifact_exists(agent_id, run_id_for_agent, task):
                agent_runs.append({
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": "reply_already_recorded",
                    "reply_attempted": False,
                    "reply_ok": True,
                })
                continue
            # ---- First-principles fix: active-runner records must not fake liveness ----
            # Inspect the active-runner file directly so we control the exact decision
            # in this guard: terminal results wait for harvest, hard-expired records
            # are released for retry, and non-stale dead runners become visible
            # failures instead of repeated "already_running" noise.
            ar_path = active_runner_path(agent_id, run_id_for_agent)
            ar_record = read_json(ar_path, {}) if ar_path.exists() else {}
            ar_pid = int(ar_record.get("pid") or 0) if isinstance(ar_record, dict) else 0
            runner_result_path = Path(str(ar_record.get("runner_dir", ""))) / "result.json" if isinstance(ar_record, dict) and ar_record.get("runner_dir") else None
            runner_result_for_guard = read_json(runner_result_path, {}) if runner_result_path and runner_result_path.exists() else {}
            has_result = runner_result_is_terminal(runner_result_for_guard)
            runner_alive_flag = active_runner_alive(ar_record) if isinstance(ar_record, dict) and ar_record else False
            stale_runner_flag = active_runner_stale(ar_record) if isinstance(ar_record, dict) else False
            if isinstance(ar_record, dict) and ar_record:
                if has_result:
                    # Terminal result.json exists; runner is done regardless of
                    # systemd/process liveness. Codex Windows runners can leave a
                    # WSL systemd wrapper showing ActiveState=active with MainPID
                    # after the agent has completed; checking result.json first
                    # prevents fake "already_running" noise from that stale PID.
                    mark_manifest_dispatch_state(task_path, agent_id, "deferred", {"runner_status": "result_pending_harvest"})
                    agent_run = {
                        "agent_id": agent_id,
                        "runner_started": False,
                        "runner_status": "result_pending_harvest",
                        "reply_attempted": False,
                        "reply_ok": None,
                    }
                    write_collaboration_status_snapshot(task, "dispatch_seen_result_pending_harvest", agent_runs=[agent_run])
                    agent_runs.append(agent_run)
                    continue
                elif runner_alive_flag and not stale_runner_flag:
                    # Live runner: normal deferral.
                    mark_manifest_dispatch_state(task_path, agent_id, "deferred", {"runner_status": "already_running"})
                    maybe_emit_deferred_comment(task, agent_id, "already_running")
                    agent_run = {
                        "agent_id": agent_id,
                        "runner_started": False,
                        "runner_status": "already_running",
                        "reply_attempted": False,
                        "reply_ok": None,
                    }
                    write_collaboration_status_snapshot(task, "dispatch_seen_already_running", agent_runs=[agent_run])
                    agent_runs.append(agent_run)
                    continue
                elif stale_runner_flag:
                    # The active-runner file is beyond its hard runner budget.
                    # Archive and release it before admission checks so a quota
                    # recovery tick can retry instead of looping on
                    # "already_running".
                    cleanup_stale_active_runner_before_dispatch(
                        ar_path,
                        ar_record,
                        reason="stale_active_runner_prior_to_dispatch",
                    )
                else:
                    # Dead PID + no result.json: runner failure, NOT a valid "already running".
                    # Write a failure comment/reply artifact and stop this same run
                    # from being re-dispatched indefinitely.
                    dead_runner_info = {
                        "exit_code": None,
                        "ok": False,
                        "stdout": Path(str(ar_record.get("stdout_path") or "")).read_text(encoding="utf-8", errors="replace")[-4000:]
                            if ar_record.get("stdout_path") and Path(str(ar_record["stdout_path"])).exists() else "",
                        "stderr": Path(str(ar_record.get("stderr_path") or "")).read_text(encoding="utf-8", errors="replace")[-4000:]
                            if ar_record.get("stderr_path") and Path(str(ar_record["stderr_path"])).exists() else "",
                        "timeout": False,
                        "missing_process": True,
                        "age_seconds": runner_age_seconds(ar_record),
                        "max_seconds": active_runner_max_seconds(agent_id, ar_record),
                        "deadline_state": classify_runner_deadline_state(ar_record),
                    }
                    failure_comment = fallback_runner_comment(ar_record.get("task", {}), agent_id, dead_runner_info)
                    if runner_failure_should_be_user_visible(task):
                        failure_comment["source_projection_status"] = failure_comment.get("telegram_projection_status")
                        failure_comment["telegram_projection_status"] = "user_visible_runner_failure"
                        failure_comment["visibility_reason"] = "telegram_user_task_liveness_contract"
                    append_jsonl(comment_path(agent_id), [failure_comment])
                    mark_manifest_dispatch_state(task_path, agent_id, "failed", {"runner_status": "dead_runner_prior_to_dispatch"})
                    may_project, projection_mode_or_reason = telegram_projection_decision(task, [failure_comment])
                    projection_mode = projection_mode_or_reason if may_project else "suppressed"
                    if may_project and allow_send:
                        failure_reply = send_agent_reply(
                            agent_id,
                            chat_id,
                            run_id_for_agent,
                            allow_send,
                            prefix=visible_agent_prefix(agent_id, chat_id),
                            projection_mode=projection_mode or "normal",
                        )
                    else:
                        failure_reply = write_suppressed_reply_artifact(
                            agent_id,
                            chat_id,
                            run_id_for_agent,
                            None if may_project else projection_mode_or_reason,
                            projection_mode,
                        )
                        failure_reply["ok"] = True
                    reply_results.append(failure_reply or {})
                    ar_path.unlink(missing_ok=True)
                    agent_runs.append({
                        "agent_id": agent_id,
                        "runner_started": False,
                        "runner_status": "dead_runner_recorded",
                        "reply_attempted": bool(may_project and allow_send),
                        "reply_ok": (failure_reply or {}).get("ok"),
                    })
                    continue
            elif active_runner_exists(agent_id, run_id_for_agent):
                # active_runner_exists returns True for two distinct states:
                # 1) Live runner (alive + not stale) -> correct "already_running"
                # 2) Completed runner (terminal result.json, pid may be 0/dead)
                #    -> must use "result_pending_harvest" to avoid fake-running noise.
                # has_result is already computed above from ar_record (line 3706).
                runner_status = "result_pending_harvest" if has_result else "already_running"
                mark_manifest_dispatch_state(task_path, agent_id, "deferred", {"runner_status": runner_status})
                if runner_status != "result_pending_harvest":
                    maybe_emit_deferred_comment(task, agent_id, runner_status)
                agent_runs.append({
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": runner_status,
                    "reply_attempted": False,
                    "reply_ok": None,
                })
                continue
            # First-principles dispatch guard: prevent duplicate same-run work and
            # total resource blow-up, but do not use a per-agent single-slot lock
            # as the room's notion of "turns". That old default made a slow
            # Codex/Claude runner starve later Telegram messages and looked like
            # everyone went silent. Operators can re-enable per-agent caps via an
            # explicit env flag for experiments, but normal room liveness relies
            # on run-id idempotency + global cap + hard-deadline harvest.
            admission_limit = runner_admission_limit(task)
            global_active_limit = int(admission_limit["global_active_runner_limit"])
            reserved_slots = int(admission_limit["user_main_reserved_runner_slots"])
            uses_reserved_lane = bool(admission_limit["reserved_runner_lane"])
            reserved_lane_reason = admission_limit["reserved_lane_reason"]
            current_active = active_runner_count()
            effective_limit = int(admission_limit["effective_active_runner_limit"])
            if current_active >= effective_limit:
                limit_reason = "deferred_global_active_runner_limit" if uses_reserved_lane else "deferred_reserved_runner_slots"
                limit_detail = {
                    "runner_status": limit_reason,
                    "global_active_runner_limit": global_active_limit,
                    "effective_active_runner_limit": effective_limit,
                    "user_main_reserved_runner_slots": reserved_slots,
                    "fresh_user_reserved_runner_slots": reserved_slots,
                    "reserved_runner_lane": uses_reserved_lane,
                    "reserved_lane_reason": reserved_lane_reason,
                    "active_runner_count": current_active,
                }
                mark_manifest_dispatch_state(task_path, agent_id, "deferred", limit_detail)
                maybe_emit_deferred_comment(task, agent_id, limit_reason, limit_detail)
                agent_runs.append({
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": limit_reason,
                    "global_active_runner_limit": global_active_limit,
                    "effective_active_runner_limit": effective_limit,
                    "fresh_user_reserved_runner_slots": reserved_slots,
                    "reserved_runner_lane": uses_reserved_lane,
                    "reserved_lane_reason": reserved_lane_reason,
                    "reply_attempted": False,
                    "reply_ok": None,
                })
                continue
            if configured_enforce_per_agent_active_limit():
                per_agent_limit = runner_per_agent_limit_decision(agent_id, reserved_runner_lane=uses_reserved_lane)
                if per_agent_limit["blocked"]:
                    limit_detail = {
                        "runner_status": "deferred_per_agent_active_runner_limit",
                        "active_runner_limit": per_agent_limit["active_runner_limit"],
                        "active_runner_count": per_agent_limit["active_runner_count"],
                        "reserved_runner_lane": uses_reserved_lane,
                        "reserved_lane_reason": reserved_lane_reason,
                    }
                    mark_manifest_dispatch_state(task_path, agent_id, "deferred", limit_detail)
                    maybe_emit_deferred_comment(task, agent_id, "deferred_per_agent_active_runner_limit", limit_detail)
                    agent_runs.append({
                        "agent_id": agent_id,
                        "runner_started": False,
                        "runner_status": "deferred_per_agent_active_runner_limit",
                        "active_runner_limit": per_agent_limit["active_runner_limit"],
                        "active_runner_count": per_agent_limit["active_runner_count"],
                        "reserved_runner_lane": uses_reserved_lane,
                        "reserved_lane_reason": reserved_lane_reason,
                        "reply_attempted": False,
                        "reply_ok": None,
                    })
                    continue
            # Agent room turns must not have a short "group chat budget". Start
            # each local agent runner under a durable active-runner record, let it
            # take the time it needs, and harvest/send the final comment on a later
            # tick. This keeps the room responsive without killing slow peers.
            active_record = start_agent_runner_async(task, agent_id, local_task_path, runner_dir, runner_cmd, chat_id)
            active_runner_json = active_runner_path(agent_id, run_id_for_agent)
            if active_record.get("runner_start_deferred"):
                runner_status = str(active_record.get("defer_reason") or "runner_start_deferred")
                mark_manifest_dispatch_state(task_path, agent_id, "deferred", {"runner_status": runner_status, "pid": active_record.get("pid")})
                if runner_status not in {"result_pending_harvest", "same_run_dispatch_lock_busy", "systemd_unit_already_running"}:
                    maybe_emit_deferred_comment(task, agent_id, runner_status)
                agent_runs.append({
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": runner_status,
                    "pid": active_record.get("pid"),
                    "active_runner": active_record.get("active_runner") or str(active_runner_json),
                    "dispatch_lock": active_record.get("dispatch_lock"),
                    "reply_attempted": False,
                    "reply_ok": None,
                })
                write_collaboration_status_snapshot(task, "dispatch_deferred_under_lock", agent_runs=[agent_runs[-1]])
                continue
            if active_record.get("runner_start_failed"):
                chat_action = {"ok": True, "sent": False, "suppressed_reason": "runner_start_failed"}
                systemd_run = active_record.get("systemd_run") if isinstance(active_record.get("systemd_run"), dict) else {}
                failure_comment = fallback_runner_comment(task, agent_id, {
                    "exit_code": systemd_run.get("exit_code"),
                    "ok": False,
                    "stdout": systemd_run.get("stdout", ""),
                    "stderr": systemd_run.get("stderr", ""),
                    "missing_process": True,
                    "age_seconds": 0,
                    "max_seconds": active_runner_max_seconds(agent_id, active_record),
                    "deadline_state": "launch_failed",
                })
                failure_comment["runner_launch_failure"] = {
                    "reason": active_record.get("failure_reason"),
                    "launch_mode": active_record.get("launch_mode"),
                    "systemd_unit": active_record.get("systemd_unit"),
                }
                append_jsonl(comment_path(agent_id), [failure_comment])
                mark_manifest_dispatch_state(task_path, agent_id, "failed", {
                    "runner_status": active_record.get("failure_reason") or "runner_start_failed",
                    "pid": active_record.get("pid"),
                })
                may_project, projection_mode_or_reason = telegram_projection_decision(task, [failure_comment])
                projection_mode = projection_mode_or_reason if may_project else "suppressed"
                if may_project and allow_send:
                    failure_reply = send_agent_reply(
                        agent_id,
                        chat_id,
                        run_id_for_agent,
                        allow_send,
                        prefix=visible_agent_prefix(agent_id, chat_id),
                        projection_mode=projection_mode or "normal",
                    )
                else:
                    failure_reply = write_suppressed_reply_artifact(
                        agent_id,
                        chat_id,
                        run_id_for_agent,
                        None if may_project else projection_mode_or_reason,
                        projection_mode,
                    )
                    failure_reply["ok"] = True
                reply_results.append(failure_reply or {})
                agent_runs.append({
                    "agent_id": agent_id,
                    "runner_started": False,
                    "runner_status": active_record.get("failure_reason") or "runner_start_failed",
                    "pid": active_record.get("pid"),
                    "chat_action": chat_action,
                    "reply_attempted": bool(may_project and allow_send),
                    "reply_ok": (failure_reply or {}).get("ok"),
                })
                continue
            chat_action = maybe_send_runner_chat_action(active_runner_json, active_record, allow_send, reason="runner_started")
            mark_manifest_dispatch_state(task_path, agent_id, "running", {"runner_status": "started_async", "pid": active_record.get("pid")})
            agent_runs.append({
                "agent_id": agent_id,
                "runner_started": True,
                "runner_status": "started_async",
                "pid": active_record.get("pid"),
                "active_runner": str(active_runner_json),
                "chat_action": chat_action,
                "reply_attempted": False,
                "reply_ok": None,
            })

        observer_results: list[dict[str, Any]] = []
        # Observer lane: do not key follow-up eligibility to len(targets). In a
        # multi-target turn one peer may still be the only material speaker, and
        # the other peer should be allowed to inspect/correct if it adds value.
        is_group_context = bool(str(chat_id or "").startswith("-")) or not str(task.get("room_id") or "").startswith("dm-")
        runner_ok = all(item.get("runner_ok") for item in agent_runs) if agent_runs else False
        if runner_ok and is_group_context and primary_comments:
            primary_speakers = {str(comment.get("agent_id") or "") for comment in primary_comments if str(comment.get("agent_id") or "")}
            observers = sorted(LOCAL_RUNTIME_AGENTS.difference(primary_speakers))
            for observer_id in observers:
                observer_task_path = observer_followup_task(task, observer_id, primary_comments, run_dir)
                observer_runner_dir = run_dir / "observer-runner" / observer_id / task_file_slot(task, task_path)
                observer_cmd = [
                    "python3", str(TOOLS / "agent_task_runner.py"),
                    "--task-file", str(observer_task_path),
                    "--out-dir", str(observer_runner_dir),
                    "--allow-exec",
                ]
                observer_run = run_cmd(observer_cmd, timeout=int(os.environ.get("AGENT_ROOM_RUNNER_TIMEOUT_SECONDS", "240")))
                observer_result = read_json(observer_runner_dir / "result.json", {})
                material_comments = material_observer_comments(observer_result)
                append_agent_comments_to_room(str(task.get("room_id") or ""), material_comments, source="observer_agent_followup")
                observer_replies: list[dict[str, Any]] = []
                if material_comments and chat_id and allow_send:
                    for comment in material_comments:
                        agent_id = str(comment.get("agent_id") or observer_id)
                        reply_cmd = [
                            "python3", str(TOOLS / "telegram_agent_reply.py"),
                            "--agent-id", agent_id,
                            "--chat-id", chat_id,
                            "--run-id", str(comment.get("run_id") or observer_result.get("run_id") or ""),
                            "--allow-send",
                            "--prefix", visible_agent_prefix(agent_id, chat_id),
                        ]
                        sent = run_cmd(reply_cmd, timeout=120)
                        observer_replies.append({"agent_id": agent_id, "ok": sent["ok"], "result": sent})
                observer_results.append({
                    "observer_id": observer_id,
                    "runner_ok": observer_run["ok"],
                    "material_comments": len(material_comments),
                    "reply_attempted": bool(observer_replies),
                    "reply_ok": all(item.get("ok") for item in observer_replies) if observer_replies else None,
                    "reply_results": observer_replies,
                    "runner_result": observer_result,
                })
        processed.append({
            "task": str(task_path),
            "agent_ids": targets,
            "skipped_targets": skipped_targets,
            "chat_id": chat_id,
            "runner_ok": runner_ok,
            "agent_runs": agent_runs,
            "reply_attempted": bool(reply_results),
            "reply_ok": all(item.get("ok") for item in reply_results) if reply_results else None,
            "reply_results": reply_results,
            "silent_failure_projections": silent_failure_projections,
            "observer_results": observer_results,
        })

    if commit_offset:
        offsets = latest_update_offsets(raw_updates)
        commit_offsets(offsets, f"resident bridge tick {run_id}")
        committed = offsets

    result = {
        "schema": "openclaw.agent_room.resident_bridge_tick.v0",
        "ok": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "mode": mode,
        "poll": poll_result,
        "canonical_import": canonical_import,
        "pinned_card": pinned_card_result,
        "auto_key_provision": auto_key_provision_result,
        "harvested_runners": harvested_runners,
        "expired_collaboration_claims": expired_collaboration_claims,
        "standing_agenda": standing_agenda_result,
        "post_harvest_standing": post_harvest_standing,
        "stale_internal_followups": stale_internal_followups,
        "processed_tasks": processed,
        "offset_committed": bool(commit_offset),
        "committed_offsets": committed,
        "telegram_outbound": bool(allow_send and processed),
        "external_side_effects": bool(allow_send or commit_offset),
        "tokens_printed": False,
    }
    write_json(run_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
