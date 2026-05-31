#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from mailbox_paths import MAILBOX_ROOT as ROOT
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
ARCHIVE_DIR = ROOT / "archive"
SNAPSHOT_DIR = ARCHIVE_DIR / "snapshots"
JSONL_FILE = ARCHIVE_DIR / "mailbox-turns.jsonl"

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|ACCESS[_-]?KEY)[A-Z0-9_]*\s*[:=]\s*)(.+)$"
)
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


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
    except Exception as exc:  # Preserve broken state for diagnosis.
        return {"_read_error": str(exc), "_raw": text}
    return data if isinstance(data, dict) else {"_read_error": "top-level JSON is not an object", "_raw": text}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redacted(text: str) -> tuple[str, int]:
    count = 0

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}[REDACTED]"

    text = SECRET_ASSIGNMENT_RE.sub(replace_assignment, text)
    text, token_count = TELEGRAM_BOT_TOKEN_RE.subn("[REDACTED_TELEGRAM_TOKEN]", text)
    count += token_count
    return text, count


def safe_part(value: object) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:80]


def make_idempotency_key(
    *,
    seq: object,
    last_writer: object,
    needs_reply: object,
    event: str,
    codex_hash: str,
    main_hash: str,
) -> str:
    basis = "\0".join(
        [
            str(seq),
            str(last_writer),
            str(needs_reply),
            event,
            codex_hash,
            main_hash,
        ]
    )
    return sha256(basis)


def write_text_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-only archive snapshot for Codex/Main mailbox turns.")
    parser.add_argument("--event", default="snapshot", help="Event label, e.g. incoming_codex, outgoing_main, watcher_seen.")
    parser.add_argument("--actor", default="", help="Actor writing/observing this snapshot.")
    parser.add_argument("--note", default="", help="Short note.")
    args = parser.parse_args()

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    turn = read_json(TURN_FILE)
    codex_text_raw = read_text(CODEX_FILE)
    main_text_raw = read_text(MAIN_FILE)
    codex_text, codex_redactions = redacted(codex_text_raw)
    main_text, main_redactions = redacted(main_text_raw)

    captured = now()
    captured_at = captured.isoformat(timespec="seconds")
    seq = turn.get("seq", "unknown")
    needs_reply = turn.get("needs_reply", "")
    last_writer = turn.get("last_writer", "")
    codex_hash = sha256(codex_text_raw)
    main_hash = sha256(main_text_raw)
    idempotency_key = make_idempotency_key(
        seq=seq,
        last_writer=last_writer,
        needs_reply=needs_reply,
        event=args.event,
        codex_hash=codex_hash,
        main_hash=main_hash,
    )

    record = {
        "captured_at": captured_at,
        "event": args.event,
        "actor": args.actor,
        "note": args.note,
        "idempotency_key": idempotency_key,
        "idempotency_basis": {
            "seq": seq,
            "last_writer": last_writer,
            "needs_reply": needs_reply,
            "event": args.event,
            "codex_to_main_sha256": codex_hash,
            "main_to_codex_sha256": main_hash,
        },
        "seq": seq,
        "last_writer": last_writer,
        "needs_reply": needs_reply,
        "turn": turn,
        "files": {
            "turn_json": str(TURN_FILE),
            "codex_to_main": str(CODEX_FILE),
            "main_to_codex": str(MAIN_FILE),
        },
        "hashes": {
            "codex_to_main_sha256": codex_hash,
            "main_to_codex_sha256": main_hash,
        },
        "redactions": {
            "codex_to_main": codex_redactions,
            "main_to_codex": main_redactions,
        },
        "codex_to_main": codex_text,
        "main_to_codex": main_text,
    }

    append_jsonl(JSONL_FILE, record)

    stamp = captured.strftime("%Y%m%d-%H%M%S-%f")
    stem = (
        f"{safe_part(seq)}-{safe_part(args.event)}-{safe_part(last_writer)}-"
        f"needs_{safe_part(needs_reply)}-{stamp}-{idempotency_key[:12]}"
    )
    md_path = SNAPSHOT_DIR / f"{stem}.md"
    markdown = "\n".join(
        [
            f"# Mailbox turn snapshot seq {seq}",
            "",
            f"- captured_at: `{captured_at}`",
            f"- event: `{args.event}`",
            f"- actor: `{args.actor}`",
            f"- last_writer: `{last_writer}`",
            f"- needs_reply: `{needs_reply}`",
            f"- idempotency_key: `{idempotency_key}`",
            f"- note: {args.note}",
            f"- redactions: codex_to_main={codex_redactions}, main_to_codex={main_redactions}",
            "",
            "## turn.json",
            "```json",
            json.dumps(turn, ensure_ascii=False, indent=2),
            "```",
            "",
            "## codex_to_main.md",
            "```markdown",
            codex_text.replace("```", "`\u200b``"),
            "```",
            "",
            "## main_to_codex.md",
            "```markdown",
            main_text.replace("```", "`\u200b``"),
            "```",
            "",
        ]
    )
    write_text_atomic(md_path, markdown)

    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
