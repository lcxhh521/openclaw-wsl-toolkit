#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("OPENCLAW_AGENT_MAILBOX_DIR", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
STATE_FILE = ROOT / ".openclaw_main_watcher_state.json"
LOCK_FILE = ROOT / ".openclaw_main_watcher.lock"
LOG_FILE = ROOT / "openclaw-main-mailbox-watch.log"

OPENCLAW = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local" / "bin" / "openclaw"))
MAIN_SESSION_ID = os.environ.get("OPENCLAW_MAIN_SESSION_ID", "")
RETRY_AFTER_SECONDS = 10 * 60
MAX_TRIGGER_ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {message}\n")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log(f"json_read_failed path={path} error={exc}")
        return {}


def write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def iso_to_epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def pid_is_alive(pid: object) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> int | None:
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        return fd
    except FileExistsError:
        try:
            if time.time() - LOCK_FILE.stat().st_mtime > 1800:
                LOCK_FILE.unlink()
                return acquire_lock()
        except OSError:
            pass
        return None


def release_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    finally:
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    fd = acquire_lock()
    if fd is None:
        return 0

    try:
        turn = read_json(TURN_FILE)
        if turn.get("needs_reply") != "main":
            return 0

        seq = str(turn.get("seq", ""))
        if not seq:
            log("skip missing_seq")
            return 0

        state = read_json(STATE_FILE)
        attempts_by_seq = state.get("attempts_by_seq")
        if not isinstance(attempts_by_seq, dict):
            attempts_by_seq = {}

        seq_state = attempts_by_seq.get(seq)
        if not isinstance(seq_state, dict):
            seq_state = {}

        attempts = int(seq_state.get("attempts", 0) or 0)
        last_epoch = float(seq_state.get("last_epoch", 0) or 0)
        last_pid = seq_state.get("pid")

        # Backfill state from older watcher versions that only remembered the
        # most recent seq. This keeps an already-triggered turn retryable.
        if attempts == 0 and str(state.get("last_triggered_seq", "")) == seq:
            attempts = 1
            last_epoch = iso_to_epoch(state.get("last_triggered_at"))
            last_pid = state.get("last_triggered_pid")

        if attempts > 0 and pid_is_alive(last_pid):
            log(f"skip seq={seq} reason=previous_trigger_still_running pid={last_pid}")
            return 0

        if attempts >= MAX_TRIGGER_ATTEMPTS:
            log(f"skip seq={seq} reason=max_trigger_attempts attempts={attempts}")
            return 0

        seconds_since_last = time.time() - last_epoch if last_epoch else RETRY_AFTER_SECONDS
        if attempts > 0 and seconds_since_last < RETRY_AFTER_SECONDS:
            return 0

        if not MAIN_SESSION_ID:
            log("skip reason=missing_OPENCLAW_MAIN_SESSION_ID")
            return 0

        if not CODEX_FILE.exists() or CODEX_FILE.stat().st_size == 0:
            log(f"skip seq={seq} reason=missing_codex_file")
            return 0

        message = "\n".join(
            [
                f"Codex mailbox turn seq {seq} is waiting for OpenClaw main.",
                f"Read: {CODEX_FILE}",
                f"Reply by writing: {MAIN_FILE}",
                f"Then update: {TURN_FILE}",
                "Set turn.json to last_writer=main, needs_reply=codex, increment seq, and updated_at=now.",
                "If you cannot complete the request, write a short blocker reply and still update turn.json so Codex can recover.",
                "Do not edit source code for this bridge turn unless Alex explicitly asks.",
                "Keep the reply focused on Telegram user-experience reliability.",
            ]
        )

        with LOG_FILE.open("ab") as output:
            proc = subprocess.Popen(
                [
                    OPENCLAW,
                    "agent",
                    "--session-id",
                    MAIN_SESSION_ID,
                    "--message",
                    message,
                    "--thinking",
                    "minimal",
                    "--timeout",
                    "180",
                ],
                stdout=output,
                stderr=output,
                start_new_session=True,
            )

        attempts_by_seq[seq] = {
            "attempts": attempts + 1,
            "last_epoch": time.time(),
            "pid": proc.pid,
            "last_triggered_at": now_iso(),
        }
        if len(attempts_by_seq) > 20:
            attempts_by_seq = dict(list(attempts_by_seq.items())[-20:])

        state.update(
            {
                "last_triggered_seq": seq,
                "last_triggered_at": now_iso(),
                "last_triggered_pid": proc.pid,
                "last_status": "triggered",
                "turn_file": str(TURN_FILE),
                "attempts_by_seq": attempts_by_seq,
            }
        )
        write_json_atomic(STATE_FILE, state)
        action = "retry_triggered" if attempts else "triggered"
        log(f"{action} seq={seq} attempt={attempts + 1} pid={proc.pid}")
        return 0
    except Exception as exc:
        log(f"watcher_failed error={exc}")
        return 1
    finally:
        release_lock(fd)


if __name__ == "__main__":
    raise SystemExit(main())
