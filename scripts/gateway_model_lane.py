#!/usr/bin/env python3
"""One-lane guard for local background OpenClaw model calls.

Background workflows are allowed to run outside Telegram, but they should not
fan out multiple `openclaw agent` calls and starve the gateway entrypoint. This
helper keeps local model calls boring: one call at a time, short gateway
preflight, whole-process-group timeout, and a clear retryable failure when the
lane is busy.
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import TextIO

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
STATE_DIR = WORKSPACE / "state"
LOCK_PATH = STATE_DIR / "gateway-model-lane.lock"
DEFAULT_WAIT_SECONDS = int(os.environ.get("OPENCLAW_BG_MODEL_LANE_WAIT_SECONDS") or "600")
DEFAULT_PROBE_SECONDS = float(os.environ.get("OPENCLAW_BG_MODEL_GATEWAY_PROBE_SECONDS") or "3")
BUSY_RETURN_CODE = 75


def gateway_reachable(timeout: float = DEFAULT_PROBE_SECONDS) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18789/", timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _try_lock(lock_file: TextIO, wait_seconds: int) -> bool:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(2)


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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    wait = DEFAULT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not gateway_reachable():
        return subprocess.CompletedProcess(
            cmd,
            BUSY_RETURN_CODE,
            "",
            "gateway_model_lane: gateway_unreachable_before_model_call",
        )
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        if not _try_lock(lock_file, wait):
            return subprocess.CompletedProcess(
                cmd,
                BUSY_RETURN_CODE,
                "",
                f"gateway_model_lane_busy: another background model call is running after {wait}s",
            )
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
