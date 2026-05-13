#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
SNAPSHOT_DIR = ROOT / "archive" / "snapshots"
TRASH_DIR = ROOT / "archive" / "snapshots-trash"
JSONL_FILE = ROOT / "archive" / "mailbox-turns.jsonl"
KNOWN_GAPS_FILE = ROOT / "archive" / "known-gaps.json"
VERIFY_SCRIPT = ROOT / "verify_mailbox_archive.py"

PRESERVE_EVENT_PATTERNS = [
    "known_gap",
    "gap",
    "repair",
    "manual_repair",
    "verifier",
    "verify",
    "failed",
    "failure",
    "blocker",
    "urgent_blocker",
    "stale",
    "regressed",
    "no_advance",
    "timeout",
    "error",
    "exception",
    "collision",
    "recovery",
    "debug",
    "archive_hardening",
    "baseline",
    "protocol",
    "writer_wrapper",
    "adapter",
    "migration",
    "schema",
    "retention",
    "cleanup",
    "alex",
    "approval",
    "approved",
    "rejected",
    "decision",
    "policy",
]


@dataclass
class Snapshot:
    path: Path
    seq: int | None
    event: str
    captured_at: datetime
    idempotency_key: str
    reasons: list[str] = field(default_factory=list)


def parse_iso(value: str) -> datetime | None:
    value = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def metadata_from_snapshot(path: Path) -> Snapshot:
    text = read_text(path)
    seq_match = re.search(r"^# Mailbox turn snapshot seq\s+(.+)$", text, re.MULTILINE)
    event_match = re.search(r"^- event:\s+`([^`]*)`", text, re.MULTILINE)
    captured_match = re.search(r"^- captured_at:\s+`([^`]*)`", text, re.MULTILINE)
    key_match = re.search(r"^- idempotency_key:\s+`([^`]*)`", text, re.MULTILINE)

    seq = parse_int(seq_match.group(1).strip()) if seq_match else parse_int(path.name.split("-", 1)[0])
    event = event_match.group(1) if event_match else "unknown"
    captured = parse_iso(captured_match.group(1)) if captured_match else None
    if captured is None:
        captured = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()
    key = key_match.group(1) if key_match else ""
    return Snapshot(path=path, seq=seq, event=event, captured_at=captured, idempotency_key=key)


def load_jsonl_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not JSONL_FILE.exists():
        return records
    for line in JSONL_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def load_known_gap_seqs() -> set[int]:
    if not KNOWN_GAPS_FILE.exists():
        return set()
    try:
        data = json.loads(KNOWN_GAPS_FILE.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return set()
    items = data.get("known_gaps", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return set()
    seqs: set[int] = set()
    for item in items:
        if isinstance(item, dict):
            seq = parse_int(item.get("seq"))
            if seq is not None:
                seqs.add(seq)
    return seqs


def run_verifier() -> tuple[bool, dict[str, Any] | None, str]:
    if not VERIFY_SCRIPT.exists():
        return False, None, "verify_mailbox_archive.py is missing"
    proc = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), "--json"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        summary = json.loads(proc.stdout)
    except Exception:
        return False, None, (proc.stderr or proc.stdout).strip()
    seq = summary.get("seq") if isinstance(summary.get("seq"), dict) else {}
    unacknowledged = seq.get("missing_unacknowledged") or []
    ok = summary.get("status") == "ok" and not unacknowledged
    return bool(ok), summary, proc.stderr.strip()


def event_is_preserved(event: str) -> bool:
    lowered = event.lower()
    return any(pattern in lowered for pattern in PRESERVE_EVENT_PATTERNS)


def snapshot_is_represented(snapshot: Snapshot, jsonl_keys: set[str], jsonl_seq_events: set[tuple[int, str]]) -> bool:
    if snapshot.idempotency_key and snapshot.idempotency_key in jsonl_keys:
        return True
    if snapshot.seq is not None and (snapshot.seq, snapshot.event) in jsonl_seq_events:
        return True
    return False


def analyze(days: int, keep_count: int) -> dict[str, Any]:
    verifier_ok, verifier_summary, verifier_error = run_verifier()
    records = load_jsonl_records()
    jsonl_keys = {str(record.get("idempotency_key")) for record in records if record.get("idempotency_key")}
    jsonl_seq_events = {
        (seq, str(record.get("event", "")))
        for record in records
        for seq in [parse_int(record.get("seq"))]
        if seq is not None
    }
    gap_neighbors: set[int] = set()
    for seq in load_known_gap_seqs():
        gap_neighbors.update({seq - 1, seq, seq + 1})

    snapshots = [metadata_from_snapshot(path) for path in sorted(SNAPSHOT_DIR.glob("*.md"))]
    snapshots.sort(key=lambda item: item.captured_at, reverse=True)
    newest_keep = {snapshot.path for snapshot in snapshots[:keep_count]}
    cutoff = datetime.now().astimezone() - timedelta(days=days)

    keep: list[Snapshot] = []
    candidates: list[Snapshot] = []
    for snapshot in snapshots:
        if snapshot.path in newest_keep:
            snapshot.reasons.append(f"within_newest_{keep_count}")
        if snapshot.captured_at >= cutoff:
            snapshot.reasons.append(f"within_last_{days}_days")
        if event_is_preserved(snapshot.event):
            snapshot.reasons.append("preserved_event")
        if snapshot.seq in gap_neighbors:
            snapshot.reasons.append("known_gap_or_neighbor")
        if not snapshot_is_represented(snapshot, jsonl_keys, jsonl_seq_events):
            snapshot.reasons.append("not_confirmed_in_jsonl")

        if snapshot.reasons:
            keep.append(snapshot)
        else:
            candidates.append(snapshot)

    return {
        "verifier_ok": verifier_ok,
        "verifier_error": verifier_error,
        "verifier": verifier_summary,
        "policy": {
            "days": days,
            "keep_count": keep_count,
            "preserve_event_patterns": PRESERVE_EVENT_PATTERNS,
        },
        "snapshot_count": len(snapshots),
        "keep_count": len(keep),
        "candidate_count": len(candidates),
        "keep": [
            {
                "path": str(snapshot.path),
                "seq": snapshot.seq,
                "event": snapshot.event,
                "captured_at": snapshot.captured_at.isoformat(timespec="seconds"),
                "reasons": snapshot.reasons,
            }
            for snapshot in keep
        ],
        "candidates": [
            {
                "path": str(snapshot.path),
                "seq": snapshot.seq,
                "event": snapshot.event,
                "captured_at": snapshot.captured_at.isoformat(timespec="seconds"),
                "reason": f"older than {days} days and outside newest {keep_count}; represented in JSONL",
            }
            for snapshot in candidates
        ],
    }


def apply_quarantine(candidates: list[dict[str, Any]]) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    target_dir = TRASH_DIR / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": "move_to_quarantine",
        "files": candidates,
    }
    for candidate in candidates:
        source = Path(candidate["path"])
        if source.exists():
            shutil.move(str(source), str(target_dir / source.name))
    (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target_dir


def print_plain(summary: dict[str, Any]) -> None:
    status = "ok" if summary["verifier_ok"] else "blocked"
    print(f"status: {status}")
    if summary["verifier_error"]:
        print(f"verifier_error: {summary['verifier_error']}")
    print(f"snapshots: {summary['snapshot_count']}")
    print(f"keep: {summary['keep_count']}")
    print(f"cleanup_candidates: {summary['candidate_count']}")
    for item in summary["candidates"][:50]:
        print(f"candidate: seq={item['seq']} event={item['event']} path={item['path']}")
    if summary["candidate_count"] > 50:
        print(f"... {summary['candidate_count'] - 50} more candidates")


def main() -> int:
    parser = argparse.ArgumentParser(description="Retention planner for derived mailbox Markdown snapshots.")
    parser.add_argument("--days", type=int, default=14, help="Keep snapshots captured within this many days.")
    parser.add_argument("--keep-count", type=int, default=500, help="Keep at least this many newest snapshots.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only. This is the default unless --apply is set.")
    parser.add_argument("--apply", action="store_true", help="Move cleanup candidates to snapshots-trash quarantine.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args()

    summary = analyze(days=args.days, keep_count=args.keep_count)
    if args.apply:
        if not summary["verifier_ok"]:
            print("Refusing --apply because verifier is not ok.", file=sys.stderr)
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print_plain(summary)
            return 2
        quarantine = apply_quarantine(summary["candidates"])
        summary["applied"] = True
        summary["quarantine_dir"] = str(quarantine)
    else:
        summary["applied"] = False

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_plain(summary)
        if args.apply:
            print(f"quarantine_dir: {summary['quarantine_dir']}")
        else:
            print("dry_run: true")
    return 0 if summary["verifier_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
