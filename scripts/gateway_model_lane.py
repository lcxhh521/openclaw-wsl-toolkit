#!/usr/bin/env python3
"""Admission controller for local background OpenClaw model calls.

Background workflows may use native ``openclaw agent`` for formal GPT-required
writing, but those calls share the local OpenClaw/Gateway runtime with the
Telegram foreground.  This helper protects the foreground by admitting only one
background native model call at a time, while also making that admission
observable and fair enough for workflow parallelism.

This is still a P0 file-backed controller, not the final first-class scheduler:
it provides FIFO/priority admission, holder/waiter metadata, process-group
timeouts, and a retryable busy return code.  Higher-level workflows should treat
return code 75 as temporary contention, not content failure.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import random
import signal
import subprocess
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TextIO

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
STATE_DIR = WORKSPACE / "state"
LOCK_PATH = STATE_DIR / "gateway-model-lane.lock"
LANE_DIR = STATE_DIR / "gateway-model-lane"
CONTROL_LOCK_PATH = LANE_DIR / "control.lock"
QUEUE_DIR = LANE_DIR / "queue"
HOLDER_PATH = LANE_DIR / "holder.json"
EVENTS_PATH = LANE_DIR / "events.jsonl"
DEFAULT_WAIT_SECONDS = int(os.environ.get("OPENCLAW_BG_MODEL_LANE_WAIT_SECONDS") or "600")
DEFAULT_PROBE_SECONDS = float(os.environ.get("OPENCLAW_BG_MODEL_GATEWAY_PROBE_SECONDS") or "3")
DEFAULT_POLL_SECONDS = float(os.environ.get("OPENCLAW_BG_MODEL_LANE_POLL_SECONDS") or "1.0")
DEFAULT_PRIORITY = int(os.environ.get("OPENCLAW_BG_MODEL_LANE_PRIORITY") or "10")
DEFAULT_STALE_WAITER_SECONDS = int(os.environ.get("OPENCLAW_BG_MODEL_LANE_STALE_WAITER_SECONDS") or "1800")
BUSY_RETURN_CODE = 75


def admission_enabled() -> bool:
    value = str(os.environ.get("OPENCLAW_BG_MODEL_LANE_V0_ENABLED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def iso_after(seconds: float) -> str:
    return (dt.datetime.now().astimezone() + dt.timedelta(seconds=max(0, seconds))).isoformat(timespec="seconds")


def gateway_reachable(timeout: float = DEFAULT_PROBE_SECONDS) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18789/", timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _option(cmd: list[str], name: str, default: str = "") -> str:
    try:
        index = cmd.index(name)
    except ValueError:
        return default
    if index + 1 >= len(cmd):
        return default
    return str(cmd[index + 1])


def _cmd_summary(cmd: list[str]) -> dict[str, Any]:
    """Return safe command metadata without persisting prompt text."""
    prompt = _option(cmd, "--message")
    executable = Path(str(cmd[0])).name if cmd else ""
    summary: dict[str, Any] = {
        "executable": executable,
        "argv_head": [str(part) for part in cmd[:6]],
        "agent": _option(cmd, "--agent"),
        "session_id": _option(cmd, "--session-id"),
        "model": _option(cmd, "--model"),
        "thinking": _option(cmd, "--thinking"),
        "cli_timeout": _option(cmd, "--timeout"),
        "message_chars": len(prompt),
    }
    if prompt:
        summary["message_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return summary


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _setup_state_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LANE_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _control_lock() -> Iterator[None]:
    _setup_state_dirs()
    with CONTROL_LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def _append_event(event: dict[str, Any]) -> None:
    payload = {"at": now_iso(), **event}
    try:
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        # Observability must never prevent releasing/admitting the lane.
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _waiter_path(waiter_id: str, priority: int, created_ns: int, pid: int) -> Path:
    # Higher priority sorts earlier; same priority is FIFO by creation time.
    priority_key = 999_999 - max(0, min(999_999, priority))
    safe_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in waiter_id)[:120]
    return QUEUE_DIR / f"{priority_key:06d}-{created_ns}-{pid}-{safe_id}.json"


def _register_waiter(cmd: list[str], *, wait_seconds: int, timeout: int) -> tuple[str, Path, dict[str, Any]]:
    created_ns = time.time_ns()
    priority = int(os.environ.get("OPENCLAW_BG_MODEL_LANE_PRIORITY") or DEFAULT_PRIORITY)
    waiter_id = f"{created_ns}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    command = _cmd_summary(cmd)
    payload: dict[str, Any] = {
        "id": waiter_id,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "priority": priority,
        "created_at": now_iso(),
        "created_ns": created_ns,
        "deadline_at": iso_after(wait_seconds),
        "wait_seconds": wait_seconds,
        "timeout_seconds": timeout,
        "task_id": os.environ.get("OPENCLAW_BACKGROUND_TASK_ID") or os.environ.get("OPENCLAW_TASK_ID") or "",
        "task_type": os.environ.get("OPENCLAW_BACKGROUND_TASK_TYPE") or "",
        "requested_by": os.environ.get("OPENCLAW_REQUESTED_BY") or "background_workflow",
        "agent": command.get("agent") or "",
        "session_id": command.get("session_id") or "",
        "model": command.get("model") or "",
        "command": command,
        "state": "waiting",
        "last_seen_at": now_iso(),
    }
    path = _waiter_path(waiter_id, priority, created_ns, os.getpid())
    with _control_lock():
        _write_json_atomic(path, payload)
        _append_event({"kind": "waiter_registered", "waiter_id": waiter_id, "task_id": payload.get("task_id"), "session_id": payload.get("session_id"), "priority": priority})
    return waiter_id, path, payload


def _cleanup_stale_holder_locked() -> None:
    if not HOLDER_PATH.exists():
        return
    holder = _read_json(HOLDER_PATH)
    holder_pid = int(holder.get("holder_pid") or holder.get("pid") or 0)
    holder_id = str(holder.get("id") or "")
    if holder_pid and _pid_alive(holder_pid):
        return
    HOLDER_PATH.unlink(missing_ok=True)
    _append_event({"kind": "holder_removed_stale", "holder_id": holder_id, "pid": holder_pid})


def _cleanup_waiters_locked(*, active_waiter_id: str = "") -> None:
    now = time.time()
    for path in list(QUEUE_DIR.glob("*.json")):
        item = _read_json(path)
        waiter_id = str(item.get("id") or "")
        if not item:
            path.unlink(missing_ok=True)
            continue
        if waiter_id == active_waiter_id:
            continue
        pid = int(item.get("pid") or 0)
        created_ns = int(item.get("created_ns") or 0)
        age = max(0.0, now - (created_ns / 1_000_000_000 if created_ns else now))
        deadline = str(item.get("deadline_at") or "")
        deadline_expired = bool(deadline and deadline < now_iso())
        if not _pid_alive(pid) or (deadline_expired and age > DEFAULT_STALE_WAITER_SECONDS):
            path.unlink(missing_ok=True)
            _append_event({"kind": "waiter_removed_stale", "waiter_id": waiter_id, "pid": pid, "age_seconds": round(age, 3)})


def _sorted_waiters_locked() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in QUEUE_DIR.glob("*.json"):
        item = _read_json(path)
        if not item:
            continue
        item["_path"] = str(path)
        items.append(item)
    items.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_ns") or 0), str(item.get("id") or "")))
    return items


def _update_waiter_position_locked(waiter_path: Path, waiter_id: str, waiters: list[dict[str, Any]]) -> None:
    position = next((idx + 1 for idx, item in enumerate(waiters) if item.get("id") == waiter_id), None)
    item = _read_json(waiter_path)
    if not item:
        return
    item["last_seen_at"] = now_iso()
    item["position"] = position
    item["queue_depth"] = len(waiters)
    _write_json_atomic(waiter_path, item)


def _queue_snapshot_locked(limit: int = 8) -> dict[str, Any]:
    waiters = _sorted_waiters_locked()
    holder = _read_json(HOLDER_PATH) if HOLDER_PATH.exists() else {}
    return {
        "holder": holder,
        "queue_depth": len(waiters),
        "waiters": [
            {
                "id": item.get("id"),
                "pid": item.get("pid"),
                "task_id": item.get("task_id"),
                "session_id": item.get("session_id"),
                "priority": item.get("priority"),
                "created_at": item.get("created_at"),
            }
            for item in waiters[:limit]
        ],
    }


def _write_holder_locked(waiter: dict[str, Any], *, timeout: int) -> str:
    holder_id = str(waiter.get("id") or f"holder-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    payload = {
        **{k: v for k, v in waiter.items() if not str(k).startswith("_")},
        "id": holder_id,
        "state": "running",
        "holder_pid": os.getpid(),
        "acquired_at": now_iso(),
        "lease_expires_at": iso_after(timeout + 20),
    }
    _write_json_atomic(HOLDER_PATH, payload)
    _append_event({"kind": "holder_acquired", "holder_id": holder_id, "pid": os.getpid(), "task_id": payload.get("task_id"), "session_id": payload.get("session_id")})
    return holder_id


def _clear_holder(holder_id: str) -> None:
    with _control_lock():
        holder = _read_json(HOLDER_PATH) if HOLDER_PATH.exists() else {}
        if not holder or str(holder.get("id") or "") == holder_id:
            HOLDER_PATH.unlink(missing_ok=True)
        _append_event({"kind": "holder_released", "holder_id": holder_id, "pid": os.getpid()})


def _busy_completed(cmd: list[str], wait: int, reason: str, snapshot: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    detail = {
        "reason": reason,
        "wait_seconds": wait,
        "state_dir": str(LANE_DIR),
        "holder_path": str(HOLDER_PATH),
        "queue_dir": str(QUEUE_DIR),
        "snapshot": snapshot or {},
    }
    return subprocess.CompletedProcess(
        cmd,
        BUSY_RETURN_CODE,
        "",
        f"gateway_model_lane_busy: {reason} after {wait}s; detail={json.dumps(detail, ensure_ascii=False, sort_keys=True)}",
    )


def _acquire_fair_lock(
    lock_file: TextIO,
    *,
    cmd: list[str],
    waiter_id: str,
    waiter_path: Path,
    waiter: dict[str, Any],
    wait: int,
    timeout: int,
) -> tuple[bool, str, dict[str, Any]]:
    deadline = time.monotonic() + max(0, wait)
    poll = max(0.1, DEFAULT_POLL_SECONDS)
    snapshot: dict[str, Any] = {}
    while True:
        with _control_lock():
            _cleanup_stale_holder_locked()
            _cleanup_waiters_locked(active_waiter_id=waiter_id)
            waiters = _sorted_waiters_locked()
            _update_waiter_position_locked(waiter_path, waiter_id, waiters)
            first_id = str(waiters[0].get("id") or "") if waiters else ""
            if first_id == waiter_id:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    waiter_path.unlink(missing_ok=True)
                    holder_id = _write_holder_locked(waiter, timeout=timeout)
                    return True, holder_id, {}
                except BlockingIOError:
                    pass
            snapshot = _queue_snapshot_locked()
        if time.monotonic() >= deadline:
            with _control_lock():
                waiter_path.unlink(missing_ok=True)
                snapshot = _queue_snapshot_locked()
                _append_event({"kind": "waiter_timed_out", "waiter_id": waiter_id, "pid": os.getpid(), "wait_seconds": wait})
            return False, "", snapshot
        time.sleep(poll + random.uniform(0, min(0.25, poll / 2)))


def _try_legacy_lock(lock_file: TextIO, wait_seconds: int) -> bool:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(2)


def _run_subprocess_under_lane(
    cmd: list[str],
    *,
    timeout: int,
    text: bool,
    capture_output: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        cmd,
        text=text,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        completed = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, cmd, output=stdout, stderr=stderr)
        return completed
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass
            stdout, stderr = process.communicate(timeout=5)
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from exc


def _run_legacy_openclaw_model_call(
    cmd: list[str],
    *,
    timeout: int,
    wait: int,
    text: bool,
    capture_output: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    if not gateway_reachable():
        return subprocess.CompletedProcess(cmd, BUSY_RETURN_CODE, "", "gateway_model_lane: gateway_unreachable_before_model_call")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        if not _try_legacy_lock(lock_file, wait):
            return subprocess.CompletedProcess(cmd, BUSY_RETURN_CODE, "", f"gateway_model_lane_busy: another background model call is running after {wait}s")
        return _run_subprocess_under_lane(cmd, timeout=timeout, text=text, capture_output=capture_output, check=check)


def run_openclaw_model_call(
    cmd: list[str],
    *,
    timeout: int,
    wait_seconds: int | None = None,
    text: bool = True,
    capture_output: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one OpenClaw model call under the shared local background lane."""
    _setup_state_dirs()
    wait = DEFAULT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not admission_enabled():
        return _run_legacy_openclaw_model_call(cmd, timeout=timeout, wait=wait, text=text, capture_output=capture_output, check=check)
    if not gateway_reachable():
        return _busy_completed(cmd, wait, "gateway_unreachable_before_model_call")

    waiter_id, waiter_path, waiter = _register_waiter(cmd, wait_seconds=wait, timeout=timeout)
    holder_id = ""
    acquired = False
    try:
        with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
            acquired, holder_id, snapshot = _acquire_fair_lock(
                lock_file,
                cmd=cmd,
                waiter_id=waiter_id,
                waiter_path=waiter_path,
                waiter=waiter,
                wait=wait,
                timeout=timeout,
            )
            if not acquired:
                return _busy_completed(cmd, wait, "lane_not_acquired", snapshot)
            return _run_subprocess_under_lane(cmd, timeout=timeout, text=text, capture_output=capture_output, check=check)
    finally:
        waiter_path.unlink(missing_ok=True)
        if acquired and holder_id:
            _clear_holder(holder_id)
