#!/usr/bin/env python3
"""Foreground notification gate for OpenClaw/Codex agent-room events.

Dry-run is the default. Production sends require --allow-send and an enabled
room policy or an explicit --target for an operator-approved test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM_FILE = ROOT / "room.json"
EVENT_ROOT = ROOT / "foreground-notify"
EVENTS_JSONL = EVENT_ROOT / "events.jsonl"
DEDUPE_STATE = EVENT_ROOT / "dedupe_state.json"
RESULTS_DIR = EVENT_ROOT / "results"

DEFAULT_OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local" / "bin" / "openclaw"))
DEFAULT_CHANNEL = "telegram"
RATE_LIMIT_WINDOW_MINUTES = 30
RATE_LIMIT_MAX = 3
QUALITY_SURFACES = {
    "quality_change",
    "needs_approval",
    "foreground_policy_changed",
}
MUST_NOTIFY_KINDS = {
    "quality_change",
    "needs_approval",
    "blocked",
    "watchdog_stalled",
    "collaboration_finished",
    "baseline_changed",
    "foreground_policy_changed",
    "production_recovery_failed",
}
ARCHIVE_ONLY_KINDS = {
    "status_heartbeat",
    "probe_success",
    "archive_written",
    "retry_attempt",
    "dedupe_skipped",
    "minor_observability_update",
    "coordination_ack",
    "dry_run_result",
}
ALLOWED_EVENT_KINDS = sorted(MUST_NOTIFY_KINDS | ARCHIVE_ONLY_KINDS)


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc)}
    return data if isinstance(data, dict) else {"_read_error": "top-level JSON is not an object"}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    return " ".join(str(text or "").split())


def as_bool(text: str | bool | None) -> bool:
    if isinstance(text, bool):
        return text
    return str(text or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def redact_target(target: str) -> str:
    target = str(target or "")
    if len(target) <= 4:
        return "*" * len(target)
    return target[:2] + "*" * max(2, len(target) - 4) + target[-2:]


def redact_transport_output(text: str, target: str) -> str:
    output = str(text or "")
    if target:
        output = output.replace(target, redact_target(target))
    return output


def load_policy(room: dict[str, Any]) -> dict[str, Any]:
    policies = room.get("policies") if isinstance(room.get("policies"), dict) else {}
    policy = policies.get("foreground_notify") if isinstance(policies.get("foreground_notify"), dict) else {}
    return {
        "enabled": bool(policy.get("enabled", False)),
        "transport": str(policy.get("transport") or "openclaw_message_send"),
        "openclaw_bin": str(policy.get("openclaw_bin") or DEFAULT_OPENCLAW_BIN),
        "channel": str(policy.get("channel") or DEFAULT_CHANNEL),
        "target": str(policy.get("target") or ""),
        "target_env": str(policy.get("target_env") or "OPENCLAW_FOREGROUND_NOTIFY_TARGET"),
        "default_dry_run": bool(policy.get("default_dry_run", True)),
        "allow_send_requires_flag": bool(policy.get("allow_send_requires_flag", True)),
    }


def resolve_target(args: argparse.Namespace, policy: dict[str, Any]) -> tuple[str, str]:
    if args.target:
        return args.target, "cli"
    if policy.get("target"):
        return str(policy["target"]), "room_policy"
    target_env = str(policy.get("target_env") or "")
    if target_env and os.environ.get(target_env):
        return str(os.environ[target_env]), f"env:{target_env}"
    return "", "missing"


def content_hash(args: argparse.Namespace) -> str:
    basis = {
        "title": normalize(args.title),
        "summary": normalize(args.summary),
        "action_required": normalize(args.action_required),
        "affected_workflows": sorted(args.affected_workflow or []),
        "approval_required": as_bool(args.approval_required),
    }
    return sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True))


def dedupe_key(args: argparse.Namespace, room_id: str, digest: str) -> str:
    workflow_id = ",".join(args.affected_workflow or [])
    parts = [
        room_id,
        args.event_kind,
        args.baseline_id or workflow_id or "",
        args.seq or args.run_id or "",
        args.severity,
        digest,
    ]
    return sha256("\0".join(parts))


def dedupe_window(event_kind: str) -> timedelta | None:
    if event_kind == "quality_change":
        return timedelta(hours=24)
    if event_kind == "needs_approval":
        return timedelta(hours=24)
    if event_kind == "blocked":
        return timedelta(hours=6)
    if event_kind == "watchdog_stalled":
        return timedelta(minutes=60)
    if event_kind == "collaboration_finished":
        return None
    if event_kind == "baseline_changed":
        return None
    if event_kind == "production_recovery_failed":
        return timedelta(hours=6)
    return timedelta(hours=6)


def load_dedupe() -> dict[str, Any]:
    return read_json(DEDUPE_STATE)


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def should_suppress(
    *,
    args: argparse.Namespace,
    dedupe: dict[str, Any],
    key: str,
    room_id: str,
    dry_run: bool,
) -> tuple[bool, str | None]:
    if args.force:
        return False, None
    existing = dedupe.get("events", {}).get(key) if isinstance(dedupe.get("events"), dict) else None
    if isinstance(existing, dict):
        window = dedupe_window(args.event_kind)
        if window is None:
            return True, "dedupe_same_event"
        first_seen = parse_time(str(existing.get("last_seen_at") or existing.get("first_seen_at") or ""))
        if first_seen and now() - first_seen <= window:
            return True, f"dedupe_within_{int(window.total_seconds())}s"

    if dry_run or args.event_kind in QUALITY_SURFACES:
        return False, None

    sent_recently = []
    for item in dedupe.get("recent_sends", []) if isinstance(dedupe.get("recent_sends"), list) else []:
        if not isinstance(item, dict) or item.get("room_id") != room_id:
            continue
        ts = parse_time(str(item.get("sent_at") or ""))
        if ts and now() - ts <= timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES):
            sent_recently.append(item)
    if len(sent_recently) >= RATE_LIMIT_MAX:
        return True, "rate_limited"
    return False, None


def update_dedupe(
    *,
    dedupe: dict[str, Any],
    key: str,
    event_id: str,
    room_id: str,
    sent: bool,
    suppressed_reason: str | None,
) -> dict[str, Any]:
    dedupe.setdefault("schema", "foreground-notify-dedupe.v0")
    events = dedupe.setdefault("events", {})
    item = events.get(key) if isinstance(events.get(key), dict) else {}
    item.setdefault("first_seen_at", now_iso())
    item["last_seen_at"] = now_iso()
    item["event_id"] = event_id
    item["sent"] = bool(sent)
    item["suppressed_reason"] = suppressed_reason
    events[key] = item
    if sent:
        recent = dedupe.setdefault("recent_sends", [])
        recent.append({"room_id": room_id, "event_id": event_id, "sent_at": now_iso()})
        cutoff = now() - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        dedupe["recent_sends"] = [
            r
            for r in recent[-100:]
            if isinstance(r, dict) and parse_time(str(r.get("sent_at") or "")) and parse_time(str(r.get("sent_at") or "")) >= cutoff
        ]
    return dedupe


def build_message(args: argparse.Namespace) -> str:
    kind_label = {
        "quality_change": "质量变更",
        "needs_approval": "需要批准",
        "blocked": "阻塞",
        "watchdog_stalled": "协作停滞",
        "collaboration_finished": "协作完成",
        "baseline_changed": "基线变更",
        "foreground_policy_changed": "前台通知规则变更",
        "production_recovery_failed": "生产恢复失败",
    }.get(args.event_kind, args.event_kind)
    affected = "、".join(args.affected_workflow or []) or args.baseline_id or args.run_id or "agent 协作空间"
    action = args.action_required or ("请确认" if as_bool(args.approval_required) else "知道即可")
    lines = [
        f"【OpenClaw 协作提醒】{args.title}",
        "",
        f"类型：{kind_label}",
        f"影响：{affected}",
        f"结论：{args.summary}",
        f"需要你：{action}",
    ]
    if args.artifact:
        lines.append("详情：" + "；".join(args.artifact[:3]))
    message = "\n".join(lines)
    return message[:1200]


def should_foreground(event_kind: str) -> bool:
    if event_kind in MUST_NOTIFY_KINDS:
        return True
    if event_kind in ARCHIVE_ONLY_KINDS:
        return False
    return False


def run_transport(
    *,
    openclaw_bin: str,
    channel: str,
    target: str,
    message: str,
    dry_run: bool,
) -> tuple[int, str, str]:
    cmd = [
        openclaw_bin,
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-kind", required=True, choices=ALLOWED_EVENT_KINDS)
    parser.add_argument("--severity", choices=["info", "warning", "error", "critical"], default="info")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--affected-workflow", action="append", default=[])
    parser.add_argument("--baseline-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--seq", default="")
    parser.add_argument("--approval-required", default="false")
    parser.add_argument("--action-required", default="")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--target", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    room = read_json(ROOM_FILE)
    room_id = str(room.get("room_id") or "unknown-room")
    policy = load_policy(room)
    target, target_source = resolve_target(args, policy)
    digest = content_hash(args)
    key = dedupe_key(args, room_id, digest)
    event_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{key[:12]}"
    message = build_message(args)

    explicit_dry_run = bool(args.dry_run)
    dry_run = True
    if args.allow_send and not explicit_dry_run:
        dry_run = False
    if explicit_dry_run and args.allow_send:
        dry_run = True

    foreground_required = should_foreground(args.event_kind)
    would_send = foreground_required and bool(target)
    suppressed_reason: str | None = None
    send_error: str | None = None
    rc: int | None = None
    stdout = ""
    stderr = ""
    sent = False

    if not foreground_required:
        suppressed_reason = "archive_only_event_kind"
    elif not target:
        suppressed_reason = "missing_target"
    elif not dry_run and not args.allow_send:
        suppressed_reason = "allow_send_required"
    elif not dry_run and not (policy.get("enabled") or args.target):
        suppressed_reason = "room_policy_disabled"

    dedupe = load_dedupe()
    if suppressed_reason is None:
        suppress, reason = should_suppress(args=args, dedupe=dedupe, key=key, room_id=room_id, dry_run=dry_run)
        if suppress:
            suppressed_reason = reason

    if suppressed_reason is None:
        rc, stdout, stderr = run_transport(
            openclaw_bin=str(policy.get("openclaw_bin") or DEFAULT_OPENCLAW_BIN),
            channel=str(policy.get("channel") or DEFAULT_CHANNEL),
            target=target,
            message=message,
            dry_run=dry_run,
        )
        sent = bool(not dry_run and rc == 0)
        if rc != 0:
            send_error = redact_transport_output(stderr[-2000:] or stdout[-2000:] or f"transport returned {rc}", target)

    result = {
        "schema": "foreground-notify-result.v0",
        "event_id": event_id,
        "created_at": now_iso(),
        "room_id": room_id,
        "event_kind": args.event_kind,
        "severity": args.severity,
        "dedupe_key": key,
        "content_hash": digest,
        "dry_run": dry_run,
        "would_send": would_send,
        "sent": sent,
        "suppressed_reason": suppressed_reason,
        "send_error": send_error,
        "transport": policy.get("transport"),
        "openclaw_bin": policy.get("openclaw_bin"),
        "channel": policy.get("channel"),
        "target_source": target_source,
        "target_redacted": redact_target(target),
        "message_preview": message,
        "artifacts": args.artifact,
        "approval_required": as_bool(args.approval_required),
        "stdout": redact_transport_output(stdout[-2000:], target) if args.verbose else "",
        "stderr": redact_transport_output(stderr[-2000:], target) if args.verbose else "",
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{event_id}.json"
    result["result_path"] = str(result_path)
    write_json_atomic(result_path, result)
    append_jsonl(EVENTS_JSONL, result)
    dedupe = update_dedupe(dedupe=dedupe, key=key, event_id=event_id, room_id=room_id, sent=sent, suppressed_reason=suppressed_reason)
    write_json_atomic(DEDUPE_STATE, dedupe)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not send_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
