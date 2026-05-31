#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mailbox_paths import MAILBOX_ROOT as ROOT
TURN_FILE = ROOT / "turn.json"
ARCHIVE_FILE = ROOT / "archive" / "mailbox-turns.jsonl"
STATE_FILE = ROOT / "context_rollover_state.json"
SUMMARY_DIR = ROOT / "context-rollovers"
MANUAL_HANDOFF = ROOT / "agent-context-rollover-20260525.md"
DEFAULT_THRESHOLD = int(os.environ.get("OPENCLAW_MAILBOX_CONTEXT_ROLLOVER_THRESHOLD", "1000"))
DEFAULT_MAX_SUMMARY_CHARS = int(os.environ.get("OPENCLAW_MAILBOX_CONTEXT_ROLLOVER_PROMPT_CHARS", "4500"))
SCHEMA = "openclaw.codex_main_mailbox.context_rollover.v0"

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize(text: str, max_chars: int) -> str:
    value = str(text or "").replace("\r", "")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda m: m.group(0).split("=", 1)[0].split(":", 1)[0] + "=<redacted>", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if len(value) > max_chars:
        return value[: max_chars - 1].rstrip() + "…"
    return value


def first_meaningful_line(text: str, max_chars: int = 180) -> str:
    lines = [raw.strip() for raw in str(text or "").splitlines()]
    for line in lines:
        if line and line.lower().startswith("status"):
            return sanitize(line, max_chars)
    for line in lines:
        if not line:
            continue
        if line.startswith("```") or line.startswith("# ") or line.startswith("## "):
            continue
        return sanitize(line, max_chars)
    return ""


def load_archive_records(source_seq: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not ARCHIVE_FILE.exists():
        return records
    with ARCHIVE_FILE.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            seq = record.get("seq")
            if isinstance(seq, int) and source_seq is not None and seq > source_seq:
                continue
            records.append(record)
    return records


def archive_seq_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    seqs = sorted({r.get("seq") for r in records if isinstance(r.get("seq"), int)})
    if not seqs:
        return {"min": None, "max": None, "unique_count": 0, "missing": []}
    full = set(range(seqs[0], seqs[-1] + 1))
    missing = sorted(full.difference(seqs))
    return {"min": seqs[0], "max": seqs[-1], "unique_count": len(seqs), "missing": missing}


def record_status_line(record: dict[str, Any]) -> str:
    for key in ("codex_to_main", "main_to_codex"):
        line = first_meaningful_line(str(record.get(key) or ""), 180)
        if line:
            return line
    return ""


def high_signal_records(records: list[dict[str, Any]], limit: int = 36) -> list[dict[str, Any]]:
    important_markers = re.compile(
        r"(?i)(alex|approval|approved|blocked|blocker|failed|failure|root|incident|policy|principle|rollover|context|market|night|publish|telegram|notion|quota|cooldown|recovery|checkpoint|completion|fast|landing|iterate|架构|原则|失败|修复|确认|纠正|阻塞|发布|晚报|快速|迭代)"
    )
    selected: list[dict[str, Any]] = []
    for record in records:
        hay = " ".join(
            str(record.get(k) or "")
            for k in ("event", "actor", "note", "codex_to_main", "main_to_codex")
        )
        if important_markers.search(hay):
            selected.append(record)
    tail = records[-20:]
    by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    for record in [*selected[-limit:], *tail]:
        seq = record.get("seq") if isinstance(record.get("seq"), int) else -1
        by_key[(seq, str(record.get("actor") or ""), str(record.get("event") or ""))] = record
    return sorted(by_key.values(), key=lambda r: (int(r.get("seq") or 0), str(r.get("captured_at") or "")))[-limit:]


def compact_manual_handoff(max_chars: int = 1800) -> str:
    if not MANUAL_HANDOFF.exists():
        return ""
    text = MANUAL_HANDOFF.read_text(encoding="utf-8", errors="replace")
    return sanitize(text, max_chars)


def build_summary(*, context_epoch: int, source_seq: int, threshold: int) -> tuple[str, dict[str, Any]]:
    records = load_archive_records(source_seq)
    stats = archive_seq_stats(records)
    high_signal = high_signal_records(records)
    generated_at = now_iso()
    missing = stats.get("missing") or []
    missing_display = ", ".join(str(x) for x in missing[:30]) if missing else "none"
    if len(missing) > 30:
        missing_display += f", ... (+{len(missing) - 30} more)"

    lines: list[str] = [
        "# Codex/Main Mailbox Context Rollover Summary",
        "",
        f"- schema: `{SCHEMA}`",
        f"- context_epoch: `{context_epoch}`",
        f"- rollover_source_seq: `{source_seq}`",
        f"- threshold: `{threshold}` turns",
        f"- generated_at: `{generated_at}`",
        f"- archive: `{ARCHIVE_FILE}`",
        f"- archive_seq_range: `{stats.get('min')}`..`{stats.get('max')}`; unique=`{stats.get('unique_count')}`; missing=`{missing_display}`",
        "",
        "## Operating contract for the new context",
        "- Treat the archive as canonical history; this summary is the bounded baseline, not a replacement for evidence lookup.",
        "- Do not inject raw pre-rollover backlog by default; fetch exact archive snapshots only when needed.",
        "- Keep `turn.json.seq` monotonic; rollover changes context epoch, not mailbox sequence identity.",
        "- Preserve safety boundaries: no secrets, no Telegram/Notion/GitHub external writes, and no prompt/model/quality/publish changes without explicit approval.",
        "- Optimize for fast landing that runs the real workflow, emits evidence, stays reversible, and iterates from actual events.",
        "",
    ]

    manual = compact_manual_handoff()
    if manual:
        lines.extend(
            [
                "## Existing manual handoff artifact",
                f"Path: `{MANUAL_HANDOFF}`",
                "",
                manual,
                "",
            ]
        )

    lines.extend(["## Recent/high-signal mailbox turns", ""])
    if high_signal:
        for record in high_signal:
            seq = record.get("seq")
            captured_at = sanitize(str(record.get("captured_at") or ""), 40)
            actor = sanitize(str(record.get("actor") or ""), 60)
            event = sanitize(str(record.get("event") or ""), 80)
            note = sanitize(str(record.get("note") or ""), 180)
            status = record_status_line(record)
            bullet = f"- seq `{seq}` {captured_at} actor=`{actor}` event=`{event}`"
            if note:
                bullet += f" note={note!r}"
            lines.append(bullet)
            if status:
                lines.append(f"  - status: {status}")
    else:
        lines.append("- No archive records available for deterministic summary.")
    lines.append("")

    lines.extend(
        [
            "## Lookup rule",
            "When a later turn needs exact details, search/read `archive/mailbox-turns.jsonl` or `archive/snapshots/` by seq/event instead of relying on stale hidden context.",
            "",
        ]
    )

    text = "\n".join(lines)
    metadata = {
        "schema": SCHEMA,
        "context_epoch": context_epoch,
        "rollover_source_seq": source_seq,
        "threshold": threshold,
        "generated_at": generated_at,
        "archive_file": str(ARCHIVE_FILE),
        "archive_seq": stats,
        "high_signal_count": len(high_signal),
        "manual_handoff_path": str(MANUAL_HANDOFF) if MANUAL_HANDOFF.exists() else None,
    }
    return text, metadata


def current_seq() -> int:
    turn = read_json(TURN_FILE, {}) or {}
    try:
        return int(turn.get("seq") or 0)
    except Exception:
        return 0


def desired_epoch(seq: int, threshold: int) -> int:
    if seq < threshold:
        return 0
    return seq // threshold


def rollover_decision(seq: int, threshold: int, previous_epoch: int, previous_source: int, summary_exists: bool, force: bool) -> tuple[bool, int, int, str, int | None]:
    """Decide whether to generate a new bounded context baseline.

    The first rollover can use absolute sequence buckets. After a baseline has
    been created, the next rollover should be based on turns since that baseline,
    not the absolute mailbox seq. This avoids a late rollover at seq 2238 causing
    the next one at seq 3000 after only 762 turns.
    """
    threshold = max(1, int(threshold))
    if force:
        next_epoch = max(previous_epoch + 1, desired_epoch(seq, threshold), 1) if previous_source else max(desired_epoch(seq, threshold), 1)
        return True, next_epoch, seq, "forced", None
    if previous_source > 0 and summary_exists:
        next_seq = previous_source + threshold
        if seq >= next_seq:
            increments = max(1, (seq - previous_source) // threshold)
            return True, previous_epoch + increments, seq, "threshold_since_previous_rollover", next_seq
        return False, previous_epoch, previous_source, "below_threshold_since_previous_rollover", next_seq
    epoch = desired_epoch(seq, threshold)
    if epoch > 0 and (epoch > previous_epoch or not summary_exists):
        return True, epoch, seq, "absolute_threshold_initial_rollover", threshold
    return False, previous_epoch, previous_source, "below_initial_threshold", threshold


def ensure_rollover(*, seq: int | None, threshold: int, force: bool = False) -> dict[str, Any]:
    seq = current_seq() if seq is None else int(seq)
    state = read_json(STATE_FILE, {}) or {}
    previous_epoch = int(state.get("context_epoch") or 0) if isinstance(state, dict) else 0
    previous_source = int(state.get("rollover_source_seq") or 0) if isinstance(state, dict) else 0
    summary_path = Path(str(state.get("summary_path") or "")) if isinstance(state, dict) and state.get("summary_path") else None
    summary_exists = bool(summary_path and summary_path.exists())
    should_generate, epoch, source_seq, reason, next_rollover_seq = rollover_decision(
        seq, threshold, previous_epoch, previous_source, summary_exists, force
    )
    if not should_generate:
        turns_since = seq - previous_source if previous_source else seq
        if isinstance(state, dict) and state:
            state.setdefault("schema", SCHEMA)
            state["checked_at"] = now_iso()
            state["current_seq"] = seq
            state["threshold"] = threshold
            state["turns_since_rollover"] = turns_since
            state["next_rollover_seq"] = next_rollover_seq
            state["rollover_decision"] = reason
            write_json_atomic(STATE_FILE, state)
            return state
        empty = {
            "schema": SCHEMA,
            "context_epoch": 0,
            "current_seq": seq,
            "threshold": threshold,
            "turns_since_rollover": turns_since,
            "next_rollover_seq": next_rollover_seq,
            "active": False,
            "reason": reason,
            "checked_at": now_iso(),
        }
        write_json_atomic(STATE_FILE, empty)
        return empty

    # Rollover source is the current mailbox seq at generation time. Future turns
    # use this bounded baseline while exact history remains in the archive.
    summary_text, metadata = build_summary(context_epoch=epoch, source_seq=source_seq, threshold=threshold)
    sha = sha256_text(summary_text)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SUMMARY_DIR / f"context-epoch-{epoch:04d}-through-seq-{source_seq}.md"
    write_text_atomic(out_path, summary_text)
    state = {
        **metadata,
        "active": True,
        "current_seq": seq,
        "summary_path": str(out_path),
        "summary_sha256": sha,
        "summary_chars": len(summary_text),
        "previous_context_epoch": previous_epoch,
        "previous_rollover_source_seq": previous_source or None,
        "turns_since_rollover": 0,
        "next_rollover_seq": source_seq + max(1, threshold),
        "rollover_decision": reason,
        "state_file": str(STATE_FILE),
        "updated_at": now_iso(),
    }
    write_json_atomic(STATE_FILE, state)
    return state


def prompt_block(*, max_chars: int, ensure: bool, seq: int | None, threshold: int) -> str:
    state = ensure_rollover(seq=seq, threshold=threshold) if ensure else (read_json(STATE_FILE, {}) or {})
    if not state or not state.get("active") or not state.get("summary_path"):
        return ""
    path = Path(str(state.get("summary_path")))
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    excerpt = sanitize(text, max_chars)
    return "\n".join(
        [
            "## Mailbox context rollover baseline",
            f"context_epoch: {state.get('context_epoch')}",
            f"rollover_source_seq: {state.get('rollover_source_seq')}",
            f"summary_path: {state.get('summary_path')}",
            f"summary_sha256: {state.get('summary_sha256')}",
            "Use this as bounded baseline; do not rely on raw pre-rollover transcript unless you explicitly inspect archive evidence.",
            "",
            excerpt,
        ]
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mailbox context rollover state/summary helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    ensure_p = sub.add_parser("ensure", help="Generate/update rollover summary if threshold requires it.")
    ensure_p.add_argument("--current-seq", type=int, default=None)
    ensure_p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    ensure_p.add_argument("--force", action="store_true")
    ensure_p.add_argument("--json", action="store_true")

    block_p = sub.add_parser("prompt-block", help="Print bounded prompt block for active rollover context.")
    block_p.add_argument("--current-seq", type=int, default=None)
    block_p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    block_p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_SUMMARY_CHARS)
    block_p.add_argument("--ensure", action="store_true")

    status_p = sub.add_parser("status", help="Print rollover state JSON.")
    status_p.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "ensure":
        state = ensure_rollover(seq=args.current_seq, threshold=args.threshold, force=args.force)
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        else:
            print(f"context_epoch={state.get('context_epoch')} active={state.get('active')} summary_path={state.get('summary_path')}")
        return 0
    if args.command == "prompt-block":
        block = prompt_block(max_chars=args.max_chars, ensure=args.ensure, seq=args.current_seq, threshold=args.threshold)
        if block:
            print(block)
        return 0
    if args.command == "status":
        state = read_json(STATE_FILE, {}) or {}
        print(json.dumps(state, ensure_ascii=False, indent=2) if args.json else state)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
