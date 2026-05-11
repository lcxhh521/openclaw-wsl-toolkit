#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("AGENT_MAILBOX_DIR", ".")).expanduser().resolve()
TURN_FILE = Path(os.environ.get("AGENT_TURN_FILE", str(ROOT / "turn.json"))).expanduser()
INBOX_FILE = Path(os.environ.get("CODEX_INBOX_FILE", str(ROOT / "main_to_codex.md"))).expanduser()
OUTBOX_FILE = Path(os.environ.get("CODEX_OUTBOX_FILE", str(ROOT / "codex_to_main.md"))).expanduser()
STATE_FILE = Path(os.environ.get("CODEX_WATCHER_STATE_FILE", str(ROOT / ".codex_watcher_state.json"))).expanduser()
LOCK_FILE = Path(os.environ.get("CODEX_WATCHER_LOCK_FILE", str(ROOT / ".codex_watcher.lock"))).expanduser()
LOG_FILE = Path(os.environ.get("CODEX_WATCHER_LOG_FILE", str(ROOT / "codex-mailbox-watch.log"))).expanduser()

# Command used to wake the external/Codex side. It is intentionally supplied by
# environment instead of hard-coded because Codex Desktop/CLI/automation differs
# by machine. Supported placeholders: {seq}, {inbox}, {outbox}, {turn}.
CODEX_WAKE_COMMAND = os.environ.get("CODEX_WAKE_COMMAND", "")
RETRY_AFTER_SECONDS = int(os.environ.get("CODEX_RETRY_AFTER_SECONDS", "600"))
MAX_TRIGGER_ATTEMPTS = int(os.environ.get("CODEX_MAX_TRIGGER_ATTEMPTS", "3"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {message}\n")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        log(f"json_read_failed path={path} error={exc}")
        return {}


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
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


def format_command(template: str, seq: str) -> str:
    return template.format(
        seq=seq,
        inbox=str(INBOX_FILE),
        outbox=str(OUTBOX_FILE),
        turn=str(TURN_FILE),
    )


def main() -> int:
    fd = acquire_lock()
    if fd is None:
        return 0

    try:
        turn = read_json(TURN_FILE)
        if turn.get("needs_reply") != "codex":
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

        if not INBOX_FILE.exists() or INBOX_FILE.stat().st_size == 0:
            log(f"skip seq={seq} reason=missing_inbox_file")
            return 0

        if not CODEX_WAKE_COMMAND:
            log("skip reason=missing_CODEX_WAKE_COMMAND")
            return 0

        cmd = format_command(CODEX_WAKE_COMMAND, seq)
        with LOG_FILE.open("ab") as output:
            proc = subprocess.Popen(
                cmd,
                shell=True,
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
    except Exception as exc:  # noqa: BLE001
        log(f"watcher_failed error={exc}")
        return 1
    finally:
        release_lock(fd)


if __name__ == "__main__":
    raise SystemExit(main())
