#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
LOCK_FILE = ROOT / ".mailbox-write.lock"
ARCHIVE_SCRIPT = ROOT / "archive_mailbox_turn.py"


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
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def next_seq(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("seq", 0)) + 1
    except Exception:
        return 1


def archive(event: str, actor: str, note: str) -> int:
    if not ARCHIVE_SCRIPT.exists():
        return 0
    proc = subprocess.run(
        [sys.executable, str(ARCHIVE_SCRIPT), "--event", event, "--actor", actor, "--note", note],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic writer wrapper for the Codex/Main mailbox.")
    parser.add_argument("--writer", choices=["codex", "main"], required=True)
    parser.add_argument("--needs-reply", choices=["codex", "main", "none"], required=True)
    parser.add_argument("--content-file", required=True, help="UTF-8 file containing the writer's message body.")
    parser.add_argument("--note", default="", help="Short turn note for turn.json and archive.")
    parser.add_argument("--event", default="", help="Archive event label. Defaults to '<writer>_writer_commit'.")
    args = parser.parse_args()

    ROOT.mkdir(parents=True, exist_ok=True)
    content = Path(args.content_file).read_text(encoding="utf-8", errors="replace")
    event = args.event or f"{args.writer}_writer_commit"
    target = CODEX_FILE if args.writer == "codex" else MAIN_FILE

    with LOCK_FILE.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        turn = read_json(TURN_FILE)
        seq = next_seq(turn)
        new_turn = {
            "bridge": "codex-main-mailbox",
            "seq": seq,
            "last_writer": args.writer,
            "needs_reply": args.needs_reply,
            "updated_at": now_iso(),
            "codex_file": str(CODEX_FILE),
            "main_file": str(MAIN_FILE),
            "note": args.note,
        }

        atomic_write_text(target, content)
        atomic_write_text(TURN_FILE, json.dumps(new_turn, ensure_ascii=False, indent=2) + "\n")
        archive_rc = archive(event, args.writer, args.note)

        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    print(
        json.dumps(
            {
                "ok": archive_rc == 0,
                "seq": seq,
                "writer": args.writer,
                "needs_reply": args.needs_reply,
                "target": str(target),
                "archive_returncode": archive_rc,
            },
            ensure_ascii=False,
        )
    )
    return archive_rc


if __name__ == "__main__":
    raise SystemExit(main())
