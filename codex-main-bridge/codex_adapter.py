#!/usr/bin/env python3
"""Codex mailbox adapter runner for the OpenClaw agent room.

This is a first-class wrapper around the existing Codex/Main mailbox. It does
not wake the Codex desktop app and does not send Telegram messages. It gives
OpenClaw main stable probe/status/send/read/archive commands for the Codex
participant.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ADAPTER_ROOT = ROOT / "codex_adapter"
STATUS_FILE = ADAPTER_ROOT / "status.json"
HEARTBEAT_FILE = ADAPTER_ROOT / "heartbeat.json"
ARTIFACT_ROOT = ADAPTER_ROOT / "artifacts"
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
WRITE_MAILBOX = ROOT / "write_mailbox_turn.py"
ARCHIVE = ROOT / "archive_mailbox_turn.py"
PROBE = ROOT / "adapter_probe_codex.py"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    try:
        encoding = "utf-8-sig" if path.name == "turn.json" else "utf-8"
        return path.read_text(encoding=encoding, errors="replace")
    except FileNotFoundError:
        return ""


def read_json(path: Path) -> dict[str, Any]:
    text = read_text(path)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception as exc:
        return {"_read_error": str(exc), "_raw": text}
    return data if isinstance(data, dict) else {"_read_error": "top-level JSON is not an object"}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def run_command(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def current_status() -> dict[str, Any]:
    turn = read_json(TURN_FILE)
    heartbeat = read_json(HEARTBEAT_FILE)
    return {
        "schema": "codex-adapter-status.v0",
        "participant_id": "codex",
        "checked_at": now_iso(),
        "adapter_status": "mailbox_active",
        "routing_enabled": True,
        "wake_supported": False,
        "transport": "mailbox",
        "turn": {
            "seq": turn.get("seq"),
            "last_writer": turn.get("last_writer"),
            "needs_reply": turn.get("needs_reply"),
            "updated_at": turn.get("updated_at"),
        },
        "paths": {
            "root": str(ROOT),
            "status": str(STATUS_FILE),
            "heartbeat": str(HEARTBEAT_FILE),
            "artifact_root": str(ARTIFACT_ROOT),
            "turn": str(TURN_FILE),
            "inbox_for_codex": str(MAIN_FILE),
            "outbox_from_codex": str(CODEX_FILE),
        },
        "capabilities_observed": {
            "probe": PROBE.exists(),
            "status": True,
            "heartbeat": True,
            "send_task": WRITE_MAILBOX.exists(),
            "read_result": CODEX_FILE.exists(),
            "archive": ARCHIVE.exists(),
            "cancel": False,
            "resume": False,
            "artifacts": True,
            "structured_output": True,
            "requires_gui": False,
            "requires_manual_auth": False,
        },
        "last_heartbeat": heartbeat or None,
        "blockers": [
            "wake/start is not supported from this adapter; Codex still relies on Codex-side heartbeat or foreground thread",
        ],
    }


def cmd_status(args: argparse.Namespace) -> int:
    payload = current_status()
    if args.write:
        write_json_atomic(STATUS_FILE, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    payload = {
        "schema": "codex-adapter-heartbeat.v0",
        "participant_id": "codex",
        "updated_at": now_iso(),
        "source": args.source,
        "note": args.note,
        "turn": read_json(TURN_FILE),
    }
    write_json_atomic(HEARTBEAT_FILE, payload)
    status = current_status()
    write_json_atomic(STATUS_FILE, status)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(_args: argparse.Namespace) -> int:
    if not PROBE.exists():
        print(json.dumps({"ok": False, "error": f"missing probe: {PROBE}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    rc, stdout, stderr = run_command([sys.executable, str(PROBE)])
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return rc


def cmd_send(args: argparse.Namespace) -> int:
    content = args.message
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8", errors="replace")
    if not content:
        print("send requires --message or --content-file", file=sys.stderr)
        return 2
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    content_path = ARTIFACT_ROOT / f"send-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.md"
    write_text_atomic(content_path, content)
    rc, stdout, stderr = run_command(
        [
            sys.executable,
            str(WRITE_MAILBOX),
            "--writer",
            "main",
            "--needs-reply",
            "codex",
            "--content-file",
            str(content_path),
            "--note",
            args.note or "codex adapter send",
            "--event",
            "codex_adapter_send",
        ]
    )
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    cmd_heartbeat(argparse.Namespace(source="codex_adapter_send", note=args.note or "sent task to Codex"))
    return rc


def cmd_read(args: argparse.Namespace) -> int:
    turn = read_json(TURN_FILE)
    text = read_text(CODEX_FILE)
    payload = {
        "schema": "codex-adapter-read.v0",
        "read_at": now_iso(),
        "turn": turn,
        "ready": turn.get("last_writer") == "codex",
        "needs_reply": turn.get("needs_reply"),
        "source_file": str(CODEX_FILE),
        "text": text,
    }
    if args.text:
        print(text)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    if not ARCHIVE.exists():
        print(json.dumps({"ok": False, "error": f"missing archive script: {ARCHIVE}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    rc, stdout, stderr = run_command(
        [
            sys.executable,
            str(ARCHIVE),
            "--event",
            args.event,
            "--actor",
            "codex-adapter",
            "--note",
            args.note,
        ]
    )
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--write", action="store_true")
    status.set_defaults(func=cmd_status)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--source", default="manual")
    heartbeat.add_argument("--note", default="")
    heartbeat.set_defaults(func=cmd_heartbeat)

    probe = sub.add_parser("probe")
    probe.set_defaults(func=cmd_probe)

    send = sub.add_parser("send")
    send.add_argument("--message", default="")
    send.add_argument("--content-file", default="")
    send.add_argument("--note", default="")
    send.set_defaults(func=cmd_send)

    read = sub.add_parser("read")
    read.add_argument("--text", action="store_true")
    read.set_defaults(func=cmd_read)

    archive = sub.add_parser("archive")
    archive.add_argument("--event", default="codex_adapter_snapshot")
    archive.add_argument("--note", default="")
    archive.set_defaults(func=cmd_archive)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
