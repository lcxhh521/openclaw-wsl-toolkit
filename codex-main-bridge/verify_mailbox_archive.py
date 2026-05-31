#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from mailbox_paths import MAILBOX_ROOT as ROOT
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
JSONL_FILE = ROOT / "archive" / "mailbox-turns.jsonl"
SNAPSHOT_DIR = ROOT / "archive" / "snapshots"
KNOWN_GAPS_FILE = ROOT / "archive" / "known-gaps.json"


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
    return data if isinstance(data, dict) else {"_read_error": "top-level JSON is not an object", "_raw": text}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_idempotency_key(record: dict[str, Any]) -> str:
    basis = record.get("idempotency_basis")
    if isinstance(basis, dict):
        parts = [
            str(basis.get("seq", "")),
            str(basis.get("last_writer", "")),
            str(basis.get("needs_reply", "")),
            str(basis.get("event", "")),
            str(basis.get("codex_to_main_sha256", "")),
            str(basis.get("main_to_codex_sha256", "")),
        ]
    else:
        hashes = record.get("hashes") if isinstance(record.get("hashes"), dict) else {}
        parts = [
            str(record.get("seq", "")),
            str(record.get("last_writer", "")),
            str(record.get("needs_reply", "")),
            str(record.get("event", "")),
            str(hashes.get("codex_to_main_sha256", "")),
            str(hashes.get("main_to_codex_sha256", "")),
        ]
    return sha256("\0".join(parts))


def load_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not JSONL_FILE.exists():
        return records, [{"line": 0, "error": "archive JSONL missing"}]
    with JSONL_FILE.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception as exc:
                errors.append({"line": line_number, "error": str(exc)})
                continue
            if isinstance(data, dict):
                records.append(data)
            else:
                errors.append({"line": line_number, "error": "record is not an object"})
    return records, errors


def load_known_gaps() -> dict[int, dict[str, Any]]:
    if not KNOWN_GAPS_FILE.exists():
        return {}
    try:
        data = json.loads(KNOWN_GAPS_FILE.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    items = data.get("known_gaps", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    gaps: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        seq = int_seq(item.get("seq"))
        if seq is not None:
            gaps[seq] = item
    return gaps


def int_seq(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def summarize() -> dict[str, Any]:
    turn = read_json(TURN_FILE)
    codex_hash = sha256(read_text(CODEX_FILE))
    main_hash = sha256(read_text(MAIN_FILE))
    records, parse_errors = load_records()
    known_gaps = load_known_gaps()
    key_counts = Counter(str(record.get("idempotency_key") or make_idempotency_key(record)) for record in records)
    duplicate_keys = {key: count for key, count in key_counts.items() if count > 1}

    seqs = sorted({seq for seq in (int_seq(record.get("seq")) for record in records) if seq is not None})
    missing_seqs: list[int] = []
    if seqs:
        present = set(seqs)
        missing_seqs = [seq for seq in range(seqs[0], seqs[-1] + 1) if seq not in present]

    current_matches = []
    for index, record in enumerate(records, 1):
        hashes = record.get("hashes") if isinstance(record.get("hashes"), dict) else {}
        if (
            record.get("seq") == turn.get("seq")
            and record.get("last_writer") == turn.get("last_writer")
            and record.get("needs_reply") == turn.get("needs_reply")
            and hashes.get("codex_to_main_sha256") == codex_hash
            and hashes.get("main_to_codex_sha256") == main_hash
        ):
            current_matches.append(
                {
                    "line": index,
                    "event": record.get("event"),
                    "captured_at": record.get("captured_at"),
                    "idempotency_key": record.get("idempotency_key") or make_idempotency_key(record),
                }
            )

    status = "ok"
    warnings: list[str] = []
    if parse_errors:
        status = "warning"
        warnings.append("archive JSONL has parse errors")
    if not current_matches:
        status = "warning"
        warnings.append("current mailbox state has not been archived yet")
    unacknowledged_missing = [seq for seq in missing_seqs if seq not in known_gaps]
    acknowledged_missing = [seq for seq in missing_seqs if seq in known_gaps]

    if unacknowledged_missing:
        status = "warning"
        warnings.append("archive has unacknowledged sequence gaps")

    return {
        "status": status,
        "warnings": warnings,
        "root": str(ROOT),
        "turn": turn,
        "archive_jsonl": str(JSONL_FILE),
        "snapshot_dir": str(SNAPSHOT_DIR),
        "record_count": len(records),
        "parse_errors": parse_errors,
        "seq": {
            "min": seqs[0] if seqs else None,
            "max": seqs[-1] if seqs else None,
            "unique_count": len(seqs),
            "missing": missing_seqs,
            "missing_acknowledged": acknowledged_missing,
            "missing_unacknowledged": unacknowledged_missing,
        },
        "known_gaps_file": str(KNOWN_GAPS_FILE),
        "known_gaps": known_gaps,
        "duplicates": {
            "idempotency_key_count": len(duplicate_keys),
            "keys": duplicate_keys,
        },
        "current_hashes": {
            "codex_to_main_sha256": codex_hash,
            "main_to_codex_sha256": main_hash,
        },
        "current_archived": bool(current_matches),
        "current_matches": current_matches,
        "latest_records": [
            {
                "seq": record.get("seq"),
                "event": record.get("event"),
                "actor": record.get("actor"),
                "last_writer": record.get("last_writer"),
                "needs_reply": record.get("needs_reply"),
                "captured_at": record.get("captured_at"),
                "idempotency_key": record.get("idempotency_key") or make_idempotency_key(record),
            }
            for record in records[-5:]
        ],
    }


def print_plain(summary: dict[str, Any]) -> None:
    print(f"status: {summary['status']}")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")
    turn = summary["turn"]
    print(
        "current_turn: "
        f"seq={turn.get('seq')} last_writer={turn.get('last_writer')} needs_reply={turn.get('needs_reply')}"
    )
    print(f"records: {summary['record_count']}")
    seq = summary["seq"]
    print(
        f"seq_range: {seq['min']}..{seq['max']} unique={seq['unique_count']} "
        f"missing={len(seq['missing'])} acknowledged={len(seq['missing_acknowledged'])} "
        f"unacknowledged={len(seq['missing_unacknowledged'])}"
    )
    print(f"duplicate_idempotency_keys: {summary['duplicates']['idempotency_key_count']}")
    print(f"current_archived: {str(summary['current_archived']).lower()}")
    if summary["current_matches"]:
        latest = summary["current_matches"][-1]
        print(f"current_archive_event: {latest.get('event')} at {latest.get('captured_at')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only verifier for the Codex/Main mailbox archive.")
    parser.add_argument("--json", action="store_true", help="Print full JSON summary.")
    args = parser.parse_args()

    summary = summarize()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_plain(summary)
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
