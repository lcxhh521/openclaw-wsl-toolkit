#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT as ROOT
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
FOREGROUND_GUARD_FILE = ROOT / "foreground_guard.json"
RUN_LOG_DIR = ROOT / "watch-runs"
ACTIVE_RUN_FILE = RUN_LOG_DIR / "active-run.json"
STATE_FILE = ROOT / "lightweight_responder_state.json"
ARCHIVE_SCRIPT = CODE_ROOT / "archive_mailbox_turn.py"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc)}


def write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def archive_snapshot(event: str, note: str = "") -> None:
    if not ARCHIVE_SCRIPT.exists():
        return
    try:
        import subprocess

        subprocess.run(
            [
                "python3",
                str(ARCHIVE_SCRIPT),
                "--event",
                event,
                "--actor",
                "lightweight_main_responder",
                "--note",
                note,
            ],
            cwd=str(CODE_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        return


def latest_run_log() -> str | None:
    try:
        files = [path for path in RUN_LOG_DIR.glob("seq-*.log") if path.is_file()]
    except FileNotFoundError:
        return None
    if not files:
        return None
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(files[0])


def is_coordination(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "bridge_status_ping",
        "lightweight_bridge_status_only",
        "[bridge-health-check]",
        "bridge-local health check",
    ]
    return any(marker in lowered for marker in markers)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic bridge-local responder for coordination turns.")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    turn = read_json(TURN_FILE)
    seq = str(turn.get("seq", ""))
    archive_snapshot("lightweight_seen", f"seq={seq}")
    if turn.get("needs_reply") != "main" or not seq:
        write_json_atomic(
            STATE_FILE,
            {
                "status": "idle",
                "checked_at": now_iso(),
                "seq": seq,
                "needs_reply": turn.get("needs_reply"),
            },
        )
        return 0

    codex_text = CODEX_FILE.read_text(encoding="utf-8") if CODEX_FILE.exists() else ""
    if not is_coordination(codex_text):
        write_json_atomic(
            STATE_FILE,
            {
                "status": "skipped_non_coordination",
                "checked_at": now_iso(),
                "seq": seq,
                "reason": "full main review is required unless an explicit lightweight health-check marker is present",
            },
        )
        return 0

    guard = read_json(FOREGROUND_GUARD_FILE)
    active_run = read_json(ACTIVE_RUN_FILE)
    latest_log = latest_run_log()
    next_seq = int(seq) + 1

    reply = f"""# OpenClaw Main Bridge Supervisor

## {now_iso()} Reply {next_seq} - lightweight coordination ack

This is a bridge-local lightweight responder, not a full Telegram/main agent turn. It is handling only an explicit bridge health-check marker because full main-session autoinject is currently guarded.

Runtime verification:

- Current seq handled: `{seq}`.
- Foreground guard reason: `{guard.get("reason", "")}`.
- Foreground guard until: `{guard.get("until", "")}`.
- Latest active-run status: `{active_run.get("status", "")}`.
- Latest active-run seq: `{active_run.get("seq", "")}`.
- Latest active-run returncode: `{active_run.get("returncode", "")}`.
- Latest active-run timed_out: `{active_run.get("timed_out", "")}`.
- Latest run log: `{latest_log or ""}`.

Decision:

- Keep full Telegram/main-session autoinject disabled while Alex's foreground responsiveness is the priority.
- Use this lightweight responder only for explicit bridge health checks.
- Use full OpenClaw main review for architecture/product decisions, scheduled-task root-cause analysis, model strategy, quality gates, and any judgment that depends on main's context.
- Codex can continue local UX reliability work now: architect watchdog heartbeat, task status, delivery preflight, and main-agent-only model fallback design.

No OpenClaw source code was changed by this responder. No Telegram outbound message was sent.
"""

    MAIN_FILE.write_text(reply.rstrip() + "\n", encoding="utf-8")
    turn.update(
        {
            "seq": next_seq,
            "last_writer": "main",
            "needs_reply": "codex",
            "updated_at": now_iso(),
            "note": f"Lightweight bridge responder handled coordination seq {seq}; full autoinject remains guarded.",
        }
    )
    write_json_atomic(TURN_FILE, turn)
    archive_snapshot("lightweight_replied", f"handled seq={seq} wrote seq={next_seq}")
    write_json_atomic(
        STATE_FILE,
        {
            "status": "replied",
            "seq": seq,
            "wrote_seq": next_seq,
            "updated_at": now_iso(),
            "reason": args.reason,
            "latest_run_log": latest_log,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
