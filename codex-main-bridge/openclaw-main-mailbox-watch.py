#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
STATE_FILE = ROOT / ".openclaw_main_watcher_state.json"
LOCK_FILE = ROOT / ".openclaw_main_watcher.lock"
LOG_FILE = ROOT / "openclaw-main-mailbox-watch.log"
FOREGROUND_GUARD_FILE = ROOT / "foreground_guard.json"
RUN_LOG_DIR = ROOT / "watch-runs"
ACTIVE_RUN_FILE = RUN_LOG_DIR / "active-run.json"
ARCHIVE_SCRIPT = ROOT / "archive_mailbox_turn.py"

OPENCLAW = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local" / "bin" / "openclaw"))
SESSIONS_FILE = Path(os.environ.get("OPENCLAW_MAIN_SESSIONS_JSON", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json")))
MAILBOX_MAIN_SESSION_KEY = "agent:main:main"
MAIN_SESSION_KEY = os.environ.get("OPENCLAW_MAIN_SESSION_KEY", "")
FALLBACK_MAIN_SESSION_ID = "c7d56b53-b915-45d6-9614-129f2633bc22"
RETRY_AFTER_SECONDS = 10 * 60
MAX_TRIGGER_ATTEMPTS = 3
AGENT_COMMAND_TIMEOUT_SECONDS = 300
AGENT_WAIT_TIMEOUT_SECONDS = AGENT_COMMAND_TIMEOUT_SECONDS + 30


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


def archive_snapshot(event: str, note: str = "") -> None:
    if not ARCHIVE_SCRIPT.exists():
        return
    try:
        subprocess.run(
            [
                "python3",
                str(ARCHIVE_SCRIPT),
                "--event",
                event,
                "--actor",
                "openclaw-main-mailbox-watch",
                "--note",
                note,
            ],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        return


def read_turn_seq() -> tuple[str, str]:
    turn = read_json(TURN_FILE)
    return str(turn.get("seq", "")), str(turn.get("needs_reply", ""))


def seq_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as exc:
        log(f"text_read_failed path={path} error={exc}")
        return ""


def requires_full_main_review(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "full_main_review_required",
        "[full-main-review]",
        "requires full main review",
    ]
    return any(marker in lowered for marker in markers)


def iso_to_epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def foreground_guard_active() -> tuple[bool, str]:
    guard = read_json(FOREGROUND_GUARD_FILE)
    until_epoch = iso_to_epoch(guard.get("until"))
    if until_epoch and time.time() < until_epoch:
        reason = str(guard.get("reason") or "foreground_priority")
        return True, reason
    return False, ""


def resolve_main_session_id() -> str:
    sessions = read_json(SESSIONS_FILE)
    mailbox = sessions.get(MAILBOX_MAIN_SESSION_KEY)
    if isinstance(mailbox, dict) and mailbox.get("sessionId"):
        return str(mailbox["sessionId"])
    direct = sessions.get(MAIN_SESSION_KEY)
    if isinstance(direct, dict) and direct.get("sessionId"):
        return str(direct["sessionId"])
    newest_id = ""
    newest_updated = -1
    for value in sessions.values():
        if not isinstance(value, dict):
            continue
        origin = value.get("origin")
        if not isinstance(origin, dict):
            continue
        if origin.get("provider") != "telegram" or origin.get("chatType") != "direct":
            continue
        session_id = value.get("sessionId")
        if not session_id:
            continue
        try:
            updated = int(value.get("updatedAt") or 0)
        except (TypeError, ValueError):
            updated = 0
        if updated > newest_updated:
            newest_updated = updated
            newest_id = str(session_id)
    return newest_id or FALLBACK_MAIN_SESSION_ID


def pid_is_alive(pid: object) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False

    proc_path = Path(f"/proc/{pid_int}")
    stat_path = proc_path / "stat"
    if proc_path.exists():
        try:
            fields = stat_path.read_text(encoding="utf-8", errors="replace").split()
            if len(fields) >= 3 and fields[2] == "Z":
                return False
        except OSError:
            pass
    elif os.name == "posix":
        return False

    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def active_run_blocks() -> tuple[bool, dict]:
    active = read_json(ACTIVE_RUN_FILE)
    if active.get("status") != "running":
        return False, active
    pid = active.get("pid")
    if pid_is_alive(pid):
        return True, active
    active.update(
        {
            "status": "process_gone",
            "observed_at": now_iso(),
            "note": "Active run pid was no longer alive; allowing next mailbox trigger.",
        }
    )
    write_json_atomic(ACTIVE_RUN_FILE, active)
    return False, active


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
        archive_snapshot("watcher_seen_needs_main", f"seq={seq}")
        if not seq:
            log("skip missing_seq")
            return 0

        guard_active, guard_reason = foreground_guard_active()
        if guard_active:
            log(
                f"foreground_guard_observed_not_blocking seq={seq} "
                f"guard_reason={guard_reason}"
            )

        active_blocks, active = active_run_blocks()
        if active_blocks:
            state = read_json(STATE_FILE)
            state.update(
                {
                    "last_status": "deferred_active_run",
                    "last_deferred_seq": seq,
                    "last_deferred_at": now_iso(),
                    "active_run": active,
                }
            )
            write_json_atomic(STATE_FILE, state)
            log(
                f"skip seq={seq} reason=active_run_still_running "
                f"active_seq={active.get('seq')} pid={active.get('pid')}"
            )
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

        main_session_id = resolve_main_session_id()
        route_changed = attempts > 0 and str(state.get("last_triggered_session_id", "")) != main_session_id

        if attempts >= MAX_TRIGGER_ATTEMPTS and not route_changed:
            log(f"skip seq={seq} reason=max_trigger_attempts attempts={attempts}")
            return 0

        seconds_since_last = time.time() - last_epoch if last_epoch else RETRY_AFTER_SECONDS
        if attempts > 0 and seconds_since_last < RETRY_AFTER_SECONDS and not route_changed:
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
                f"Before writing, re-read {TURN_FILE}; only write if seq is still {seq} and needs_reply is still main.",
                "If the turn already advanced, do not write mailbox files; put the stale-turn diagnostic in your normal response only.",
                "Do not edit source code for this bridge turn unless Alex explicitly asks.",
                "Keep the reply focused on Telegram user-experience reliability.",
            ]
        )

        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        run_log_path = RUN_LOG_DIR / f"seq-{seq}-{int(time.time())}.log"

        command = [
            OPENCLAW,
            "agent",
            "--session-id",
            main_session_id,
            "--message",
            message,
            "--thinking",
            "minimal",
            "--timeout",
            str(AGENT_COMMAND_TIMEOUT_SECONDS),
            "--json",
        ]

        run_log_path.write_text(
            "\n".join(
                [
                    f"started_at={now_iso()}",
                    f"seq={seq}",
                    f"session_id={main_session_id}",
                    "command=" + json.dumps(command, ensure_ascii=False),
                    "",
                ]
            ),
            encoding="utf-8",
        )

        started_epoch = time.time()
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        write_json_atomic(
            ACTIVE_RUN_FILE,
            {
                "status": "running",
                "seq": seq,
                "pid": proc.pid,
                "started_at": now_iso(),
                "deadline_at_epoch": round(started_epoch + AGENT_WAIT_TIMEOUT_SECONDS, 3),
                "command_kind": "openclaw_agent_session_json",
                "session_id": main_session_id,
                "run_log": str(run_log_path),
            },
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
                "last_triggered_session_id": main_session_id,
                "last_run_log": str(run_log_path),
                "last_status": "triggered",
                "turn_file": str(TURN_FILE),
                "attempts_by_seq": attempts_by_seq,
            }
        )
        write_json_atomic(STATE_FILE, state)
        action = "retry_triggered" if attempts else "triggered"
        log(
            f"{action} seq={seq} attempt={attempts + 1} "
            f"pid={proc.pid} session_id={main_session_id} run_log={run_log_path}"
        )

        timed_out = False
        stdout = ""
        stderr = ""
        try:
            stdout, stderr = proc.communicate(timeout=AGENT_WAIT_TIMEOUT_SECONDS)
            returncode = proc.returncode
            process_state = "exited"
        except subprocess.TimeoutExpired:
            timed_out = True
            process_state = "timeout"
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            returncode = proc.returncode

        observed_at = now_iso()
        duration_seconds = round(time.time() - started_epoch, 1)
        with run_log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write(f"finished_at={observed_at}\n")
            handle.write(f"duration_seconds={duration_seconds}\n")
            handle.write(f"process_state={process_state}\n")
            handle.write(f"returncode={returncode}\n")
            handle.write(f"timed_out={str(timed_out).lower()}\n")
            if stdout:
                handle.write("\n--- stdout ---\n")
                handle.write(stdout)
                if not stdout.endswith("\n"):
                    handle.write("\n")
            if stderr:
                handle.write("\n--- stderr ---\n")
                handle.write(stderr)
                if not stderr.endswith("\n"):
                    handle.write("\n")

        after_seq, after_needs_reply = read_turn_seq()
        state = read_json(STATE_FILE)
        state.update(
            {
                "last_observed_at": observed_at,
                "last_trigger_returncode": returncode,
                "last_trigger_timed_out": timed_out,
                "last_trigger_stdout_bytes": len(stdout.encode("utf-8")),
                "last_trigger_stderr_bytes": len(stderr.encode("utf-8")),
                "last_trigger_duration_seconds": duration_seconds,
            }
        )
        seq_before_int = seq_int(seq)
        seq_after_int = seq_int(after_seq)
        if after_seq == seq and after_needs_reply == "main":
            state["last_post_trigger_status"] = "no_advance"
            write_json_atomic(STATE_FILE, state)
            write_json_atomic(
                ACTIVE_RUN_FILE,
                {
                    "status": "completed_no_advance",
                    "seq": seq,
                    "pid": proc.pid,
                    "finished_at": observed_at,
                    "duration_seconds": duration_seconds,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "run_log": str(run_log_path),
                },
            )
            log(
                f"post_trigger_no_advance seq={seq} pid={proc.pid} "
                f"returncode={returncode} timed_out={timed_out} run_log={run_log_path}"
            )
        elif (
            seq_before_int is not None
            and seq_after_int is not None
            and seq_after_int <= seq_before_int
        ):
            state["last_post_trigger_status"] = "stale_or_regressed"
            state["last_post_trigger_after_seq"] = after_seq
            state["last_post_trigger_after_needs_reply"] = after_needs_reply
            write_json_atomic(STATE_FILE, state)
            write_json_atomic(
                ACTIVE_RUN_FILE,
                {
                    "status": "completed_stale_or_regressed",
                    "seq": seq,
                    "pid": proc.pid,
                    "finished_at": observed_at,
                    "duration_seconds": duration_seconds,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "after_seq": after_seq,
                    "after_needs_reply": after_needs_reply,
                    "run_log": str(run_log_path),
                },
            )
            log(
                f"post_trigger_stale_or_regressed seq={seq} after_seq={after_seq} "
                f"after_needs_reply={after_needs_reply} returncode={returncode}"
            )
        else:
            state["last_post_trigger_status"] = "advanced"
            state["last_post_trigger_after_seq"] = after_seq
            state["last_post_trigger_after_needs_reply"] = after_needs_reply
            write_json_atomic(STATE_FILE, state)
            write_json_atomic(
                ACTIVE_RUN_FILE,
                {
                    "status": "completed_advanced",
                    "seq": seq,
                    "pid": proc.pid,
                    "finished_at": observed_at,
                    "duration_seconds": duration_seconds,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "after_seq": after_seq,
                    "after_needs_reply": after_needs_reply,
                    "run_log": str(run_log_path),
                },
            )
            log(
                f"post_trigger_advanced seq={seq} after_seq={after_seq} "
                f"after_needs_reply={after_needs_reply} returncode={returncode}"
            )
            archive_snapshot("watcher_observed_advanced", f"seq={seq} after_seq={after_seq} after_needs_reply={after_needs_reply}")
        return 0
    except Exception as exc:
        log(f"watcher_failed error={exc}")
        return 1
    finally:
        release_lock(fd)


if __name__ == "__main__":
    raise SystemExit(main())
