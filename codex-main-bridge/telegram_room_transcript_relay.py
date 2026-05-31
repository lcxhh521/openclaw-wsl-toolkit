#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT

ROOT = CODE_ROOT
MAILBOX = MAILBOX_ROOT
BINDING_FILE = CODE_ROOT / "telegram-room-bindings.json"
TURN_FILE = MAILBOX / "turn.json"
CODEX_FILE = MAILBOX / "codex_to_main.md"
MAIN_FILE = MAILBOX / "main_to_codex.md"
ARCHIVE_FILE = MAILBOX / "archive/mailbox-turns.jsonl"
STATE_FILE = CODE_ROOT / "telegram-room-relay/state.json"
EVENTS_FILE = CODE_ROOT / "telegram-room-relay/events.jsonl"
COMMENTS_FILE = CODE_ROOT / "agent-comments/telegram-room-comments.jsonl"
PENDING_COMMAND_DIR = CODE_ROOT / "telegram-room-relay/pending-commands"
OPENCLAW = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local/bin/openclaw"))
RELIABILITY_FILE = Path(os.environ.get("OPENCLAW_RELIABILITY_FILE", str(Path.home() / ".openclaw/monitor-cache/reliability-sidecar.json")))
DRY_RUN_ROOT = Path(os.environ.get("OPENCLAW_ROOM_RELAY_DRY_RUN_ROOT", str(Path.home() / ".openclaw/workspace/relay-dry-run")))

MAX_MESSAGE_CHARS = 3300
MAX_CHUNKS_PER_TURN = 4
SUMMARY_MAX_CHARS = 1200
SUMMARY_MAX_BULLETS = 5
OPENCLAW_SEND_TIMEOUT_SECONDS = 20
BACKOFF_BASE_SECONDS = 120
BACKOFF_MAX_SECONDS = 900
RELIABILITY_MAX_AGE_SECONDS = 180
RELIABILITY_BLOCK_REASONS = {
    "telegram_delivery_or_fetch_failures",
    "stuck_recovery_signal",
}
RELIABILITY_WARN_ONLY_REASONS = {
    "event_loop_liveness_warning",
}
MOJIBAKE_MARKERS = ("锛", "鈥", "涓", "鎴", "鍙", "绛", "璇", "鏄", "瀵", "杩")
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|botToken)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
]
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|bypass|override)\b.*\b(rule|instruction|policy|safety)\b"),
    re.compile(r"(?i)\b(write|modify|advance)\b.*\b(turn\.json|canonical mailbox|mailbox)\b"),
    re.compile(r"(?i)\b(send|print|show|dump|expose)\b.*\b(secret|token|api key|credential)\b"),
    re.compile(r"忽略.*(规则|指令|安全|策略)"),
    re.compile(r"(写入|修改|推进).*(turn\.json|mailbox|canonical)"),
    re.compile(r"(发送|展示|输出|泄露).*(secret|token|密钥|凭证)"),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_dt() -> datetime:
    return datetime.now().astimezone()


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def maybe_repair_mojibake(text: str) -> str:
    if sum(text.count(marker) for marker in MOJIBAKE_MARKERS) < 3:
        return text
    try:
        repaired = text.encode("gb18030", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return text
    if len(repaired.strip()) < max(20, len(text.strip()) // 4):
        return text
    old_markers = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    new_markers = sum(repaired.count(marker) for marker in MOJIBAKE_MARKERS)
    return repaired if new_markers < old_markers else text


def redact(text: str) -> str:
    value = maybe_repair_mojibake(str(text or ""))
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value.strip()


def redact_chat_id(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 6:
        return text
    return text[:4] + "*" * max(2, len(text) - 8) + text[-4:]


def has_prompt_injection_marker(text: str) -> bool:
    value = str(text or "")
    return any(pattern.search(value) for pattern in PROMPT_INJECTION_PATTERNS)


def read_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    if not EVENTS_FILE.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in EVENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def relay_event_summary(state: dict[str, Any]) -> dict[str, Any]:
    events = read_recent_events()
    last_sent: dict[str, Any] | None = None
    last_suppressed: dict[str, Any] | None = None
    last_dry_run: dict[str, Any] | None = None
    suppressed_count = 0
    for event in events:
        name = str(event.get("event") or "")
        if name == "turn_relay_sent":
            last_sent = event
        elif name in {"turn_relay_dry_run", "outbound_preview_written"}:
            last_dry_run = event
        elif name == "suppressed":
            suppressed_count += 1
            last_suppressed = event
    return {
        "last_sent_seq": state.get("last_sent_seq") or (last_sent or {}).get("seq"),
        "last_send_ok_at": state.get("last_send_ok_at") or (last_sent or {}).get("ts"),
        "last_send_error": state.get("last_send_error"),
        "last_dry_run_seq": (last_dry_run or {}).get("seq"),
        "last_dry_run_at": (last_dry_run or {}).get("ts"),
        "suppressed_count": int(state.get("suppressed_count") or suppressed_count),
        "last_suppressed_reason": state.get("last_suppressed_reason") or (last_suppressed or {}).get("reason"),
        "last_suppressed_at": (last_suppressed or {}).get("ts"),
    }


def read_telegram_last_outbound_at() -> tuple[Any, str]:
    try:
        proc = subprocess.run(
            [OPENCLAW, "channels", "status", "--json", "--timeout", "8000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return None, f"channels status unavailable: {exc}"
    if proc.returncode != 0:
        return None, redact((proc.stderr or proc.stdout)[-500:])
    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        return None, f"channels status json parse failed: {exc}"
    accounts = (((data.get("channelAccounts") or {}).get("telegram")) or [])
    source = accounts[0] if accounts and isinstance(accounts[0], dict) else ((data.get("channels") or {}).get("telegram") or {})
    if not isinstance(source, dict):
        source = {}
    return source.get("lastOutboundAt"), ""


def infer_outbound_state(payload: dict[str, Any]) -> tuple[str, str]:
    room = payload.get("room") if isinstance(payload.get("room"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    mailbox = payload.get("mailbox") if isinstance(payload.get("mailbox"), dict) else {}
    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else {}
    reliability = payload.get("reliability_gate") if isinstance(payload.get("reliability_gate"), dict) else {}
    latest_seq = mailbox.get("latest_record_seq")
    if not room.get("enabled"):
        return "disabled", "room outbound is disabled"
    delivery = room.get("delivery") if isinstance(room.get("delivery"), dict) else {}
    if delivery.get("dry_run_required_before_group_send"):
        return "dry_run", "dry_run_required_before_group_send is enabled; real Telegram outbound is gated until Alex review"
    if room.get("paused"):
        return "disabled", "room relay is paused"
    if room.get("muted"):
        return "disabled", "room relay is muted"
    if state.get("next_attempt_after"):
        return "suppressed_rate_limit", f"backoff active until {state.get('next_attempt_after')}"
    if reliability.get("allowed") is False:
        return "suppressed_gate", reliability.get("reason") or "reliability gate denied outbound"
    if state.get("last_send_error"):
        return "transport_unavailable", str(state.get("last_send_error"))[:300]
    if pending.get("count") == 0:
        if state.get("last_sent_seq") == latest_seq and latest_seq:
            return "sent", f"latest qualifying event seq {latest_seq} was sent"
        if state.get("last_dry_run_seq") == latest_seq and latest_seq:
            return "dry_run", f"latest qualifying event seq {latest_seq} only has dry-run preview"
        if state.get("last_seen_seq") == latest_seq and latest_seq:
            return "mark_current_seen_only", f"relay has marked seq {latest_seq} seen and has no pending high-value event"
        return "no_qualifying_event", "no high-value event is pending for group relay"
    if state.get("lastDryRunAt"):
        return "dry_run", "pending event has dry-run state; real send is not implied"
    return "no_qualifying_event", "pending exists but no send has been attempted in this status call"


def status_human_summary(payload: dict[str, Any]) -> list[str]:
    room = payload.get("room") if isinstance(payload.get("room"), dict) else {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else {}
    ingress = payload.get("ingress_state") if isinstance(payload.get("ingress_state"), dict) else {}
    reliability = payload.get("reliability_gate") if isinstance(payload.get("reliability_gate"), dict) else {}
    lines = [
        "room: {room_id} / enabled={enabled} / real_outbound={real_outbound} / summary_only={summary_only} / paused={paused} / muted={muted}".format(
            room_id=room.get("room_id"),
            enabled=room.get("enabled"),
            real_outbound=room.get("real_outbound_enabled"),
            summary_only=room.get("summary_only"),
            paused=room.get("paused"),
            muted=room.get("muted"),
        ),
        "outbound: last_seen_seq={last_seen_seq}, last_sent_seq={last_sent_seq}, pending={pending_count}, last_send_ok_at={last_send_ok_at}".format(
            last_seen_seq=state.get("last_seen_seq"),
            last_sent_seq=state.get("last_sent_seq"),
            pending_count=pending.get("count"),
            last_send_ok_at=state.get("last_send_ok_at"),
        ),
        "outbound_state: {state} / {reason}".format(
            state=payload.get("outbound_state"),
            reason=payload.get("outbound_reason"),
        ),
        "suppression: count={count}, last_reason={reason}".format(
            count=state.get("suppressed_count"),
            reason=state.get("last_suppressed_reason") or "none",
        ),
        "ingress: active={active}, mode={mode}".format(
            active=ingress.get("active"),
            mode=ingress.get("mode"),
        ),
        "reliability_gate: allowed={allowed}, reason={reason}".format(
            allowed=reliability.get("allowed"),
            reason=reliability.get("reason") or (reliability.get("payload") or {}).get("warning") or "none",
        ),
    ]
    return [redact(line) for line in lines]


def record_suppressed(state: dict[str, Any], reason: str, detail: dict[str, Any] | None = None) -> None:
    state["updated_at"] = now_iso()
    state["suppressed_count"] = int(state.get("suppressed_count") or 0) + 1
    state["last_suppressed_reason"] = str(reason or "")[-500:]
    write_json_atomic(STATE_FILE, state)
    event = {"ts": now_iso(), "event": "suppressed", "reason": str(reason or "")[-500:]}
    if detail:
        event["detail"] = detail
    append_jsonl(EVENTS_FILE, event)


def load_binding() -> tuple[dict[str, Any] | None, str]:
    data = read_json(BINDING_FILE)
    bindings = data.get("bindings") if isinstance(data.get("bindings"), list) else []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        delivery = item.get("delivery") if isinstance(item.get("delivery"), dict) else {}
        if (
            item.get("room_id") == "openclaw-evolution"
            and item.get("telegram_chat_id")
            and delivery.get("telegram_outbound_enabled") is True
            and delivery.get("send_summaries_to_group") is True
        ):
            return item, ""
    return None, "openclaw-evolution binding not active for telegram outbound"


def current_turn_record() -> dict[str, Any] | None:
    turn = read_json(TURN_FILE)
    if not turn.get("seq") or turn.get("last_writer") not in {"codex", "main"}:
        return None
    writer = str(turn.get("last_writer"))
    text = read_text(CODEX_FILE if writer == "codex" else MAIN_FILE)
    return {
        "seq": int(turn.get("seq")),
        "last_writer": writer,
        "captured_at": str(turn.get("updated_at") or now_iso()),
        "text": text,
        "source": "current_turn",
    }


def latest_seq(records: list[dict[str, Any]]) -> int:
    value = 0
    for record in records:
        try:
            value = max(value, int(record.get("seq") or 0))
        except Exception:
            continue
    return value


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one record per mailbox seq/writer, preferring current_turn over archive.

    The relay can see the same seq from the append-only archive and from the
    current mailbox files. Sending both is noisy and was the concrete failure
    mode observed when seq 770 appeared twice in dry-run output.
    """
    priority = {"archive": 0, "current_turn": 1}
    chosen: dict[tuple[int, str], tuple[int, int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        try:
            seq = int(record.get("seq") or 0)
        except Exception:
            continue
        writer = str(record.get("last_writer") or "")
        if seq <= 0 or writer not in {"codex", "main"}:
            continue
        key = (seq, writer)
        rank = priority.get(str(record.get("source") or ""), 0)
        previous = chosen.get(key)
        if previous is None or (rank, index) >= (previous[0], previous[1]):
            chosen[key] = (rank, index, record)
    return [item[2] for item in sorted(chosen.values(), key=lambda row: (row[2]["seq"], row[2]["last_writer"]))]


def collect_records() -> list[dict[str, Any]]:
    records = archive_records()
    current = current_turn_record()
    if current:
        records.append(current)
    return dedupe_records(records)


def archive_records(limit: int = 80) -> list[dict[str, Any]]:
    if not ARCHIVE_FILE.exists():
        return []
    lines = ARCHIVE_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    latest: dict[tuple[int, str, str], dict[str, Any]] = {}
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        writer = str(item.get("last_writer") or "")
        if writer not in {"codex", "main"}:
            continue
        try:
            seq = int(item.get("seq"))
        except Exception:
            continue
        text = item.get("codex_to_main") if writer == "codex" else item.get("main_to_codex")
        if not isinstance(text, str) or not text.strip():
            continue
        digest = sha256(text)
        latest[(seq, writer, digest)] = {
            "seq": seq,
            "last_writer": writer,
            "captured_at": str(item.get("captured_at") or ""),
            "text": text,
            "source": "archive",
        }
    return sorted(latest.values(), key=lambda row: (row["seq"], row["last_writer"], row["captured_at"]))


def split_chunks(text: str, max_chars: int = MAX_MESSAGE_CHARS, max_chunks: int = MAX_CHUNKS_PER_TURN) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while remaining and len(chunks) < max_chunks:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            remaining = ""
            break
        cut = remaining.rfind("\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining and chunks:
        chunks[-1] += "\n\n[后续内容已留在本地归档，可在群里要求展开。]"
    return chunks


def compact_line(line: str, max_chars: int = 160) -> str:
    value = re.sub(r"\s+", " ", str(line or "")).strip()
    value = value.strip("-•* ")
    value = re.sub(r"^#+\s*", "", value).strip()
    if len(value) > max_chars:
        return value[: max_chars - 1].rstrip() + "…"
    return value


def first_heading(lines: list[str]) -> str:
    for line in lines:
        value = line.strip()
        if value.startswith("#"):
            title = compact_line(value, 120)
            if title:
                return title
    for line in lines:
        title = compact_line(line, 120)
        if title and not title.startswith("`"):
            return title
    return "mailbox turn"


def status_marker(lines: list[str]) -> str:
    for line in lines:
        value = line.strip()
        if value.startswith("`") and value.endswith("`") and 3 <= len(value) <= 120:
            return value.strip("`").strip()
    for token in ("task_completion", "architecture_review_request", "coordination", "status_request", "urgent_blocker"):
        if token in "\n".join(lines[:12]):
            return token
    return ""


def summary_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    preferred = ("Alex", "当前", "结论", "建议", "请", "下一步", "已", "目标", "风险", "P0", "P1", "P2")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_bullet = stripped.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.)、]\s+", stripped)
        if not is_bullet:
            continue
        text = compact_line(stripped, 170)
        if not text or text.lower().startswith(("path:", "source:")):
            continue
        if any(key in text for key in preferred):
            bullets.append(text)
        elif len(bullets) < 2:
            bullets.append(text)
        if len(bullets) >= SUMMARY_MAX_BULLETS:
            break
    if bullets:
        return bullets
    for line in lines:
        text = compact_line(line, 170)
        if text and not text.startswith(("```", "`")):
            bullets.append(text)
        if len(bullets) >= min(3, SUMMARY_MAX_BULLETS):
            break
    return bullets


def summarize_turn(record: dict[str, Any], text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Deterministic summary card for Telegram group output.

    The binding is summary-only. Do not forward the raw mailbox body to the
    external group; keep full detail in local archive/mailbox instead.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    sender = "Codex" if record["last_writer"] == "codex" else "OpenClaw main"
    title = first_heading(lines)
    marker = status_marker(lines)
    bullets = summary_bullets(lines)
    output = [
        f"[OpenClaw进化][协作摘要 seq {record['seq']}][{sender}]",
        f"主题：{title}",
    ]
    if marker:
        output.append(f"状态：{marker}")
    if bullets:
        output.append("要点：")
        output.extend(f"- {item}" for item in bullets[:SUMMARY_MAX_BULLETS])
    output.append("完整内容已留在本地 mailbox/archive；需要时可让 main 展开。")
    value = "\n".join(output)
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "…"
    return value


def format_turn(record: dict[str, Any], chunk: str, chunk_index: int, chunk_total: int) -> str:
    suffix = f"\n[摘要分片 {chunk_index}/{chunk_total}]" if chunk_total > 1 else ""
    return f"{chunk}{suffix}"


def send_message(target: str, message: str, dry_run: bool) -> dict[str, Any]:
    cmd = [
        OPENCLAW,
        "message",
        "send",
        "--channel",
        "telegram",
        f"--target={target}",
        "--json",
        "--message",
        message,
    ]
    if dry_run:
        cmd.insert(-2, "--dry-run")
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=OPENCLAW_SEND_TIMEOUT_SECONDS,
        check=False,
    )
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }
    try:
        result["json"] = json.loads(proc.stdout)
    except Exception:
        pass
    return result


def backoff_is_active(state: dict[str, Any]) -> tuple[bool, str]:
    next_attempt = parse_dt(state.get("next_attempt_after"))
    if not next_attempt:
        return False, ""
    remaining = (next_attempt - now_dt()).total_seconds()
    if remaining <= 0:
        return False, ""
    return True, f"gateway/send backoff active for {int(remaining)}s"


def mark_backoff(state: dict[str, Any], reason: str) -> None:
    failures = int(state.get("consecutive_failures") or 0) + 1
    delay = min(BACKOFF_BASE_SECONDS * (2 ** max(0, failures - 1)), BACKOFF_MAX_SECONDS)
    state.update(
        {
            "updated_at": now_iso(),
            "consecutive_failures": failures,
            "last_failure_reason": reason[-500:],
            "next_attempt_after": (now_dt() + timedelta(seconds=delay)).isoformat(timespec="seconds"),
        }
    )


def clear_backoff(state: dict[str, Any]) -> None:
    state["consecutive_failures"] = 0
    state.pop("last_failure_reason", None)
    state.pop("next_attempt_after", None)


def reliability_gate() -> tuple[bool, str, dict[str, Any]]:
    data = read_json(RELIABILITY_FILE)
    if not data:
        return True, "", {"warning": "missing_reliability_snapshot"}

    generated = parse_dt(data.get("generated_at"))
    if generated:
        age = (now_dt() - generated).total_seconds()
        if age > RELIABILITY_MAX_AGE_SECONDS:
            return True, "", {"warning": f"reliability snapshot stale for {int(age)}s", "generated_at": data.get("generated_at")}

    overall = str(data.get("overall") or "unknown")
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    logs = data.get("recent_gateway_logs") if isinstance(data.get("recent_gateway_logs"), dict) else {}
    counts = logs.get("counts") if isinstance(logs.get("counts"), dict) else {}
    blocked = [str(item) for item in reasons if str(item) in RELIABILITY_BLOCK_REASONS]
    warn_only = [str(item) for item in reasons if str(item) in RELIABILITY_WARN_ONLY_REASONS]
    if overall == "high" or blocked:
        return (
            False,
            f"transport degraded: overall={overall}, blocked_reasons={','.join(blocked[:4])}",
            {"overall": overall, "reasons": reasons[:8], "blocked_reasons": blocked, "warn_only_reasons": warn_only, "counts": counts},
        )
    if int(counts.get("fetch_timeout") or 0) >= 2 or int(counts.get("stuck_recovery") or 0) >= 1:
        return (
            False,
            "transport degraded: recent gateway fetch timeout or stuck recovery",
            {"overall": overall, "reasons": reasons[:8], "counts": counts},
        )
    return True, "", {"overall": overall, "reasons": reasons[:8], "warn_only_reasons": warn_only, "counts": counts}


def status_payload() -> dict[str, Any]:
    binding, binding_reason = load_binding()
    state = read_json(STATE_FILE)
    records = collect_records()
    sent = set(state.get("sent_keys") if isinstance(state.get("sent_keys"), list) else [])
    last_seen_seq = int(state.get("last_seen_seq") or 0)
    event_summary = relay_event_summary(state)
    pending: list[dict[str, Any]] = []
    for record in records:
        text = redact(record.get("text", ""))
        if not text:
            continue
        key = f"{record['seq']}:{record['last_writer']}:{sha256(text)}"
        if key in sent or int(record["seq"]) <= last_seen_seq:
            continue
        pending.append(
            {
                "seq": int(record["seq"]),
                "writer": record["last_writer"],
                "source": record.get("source"),
                "chunks": len(split_chunks(summarize_turn(record, text), SUMMARY_MAX_CHARS, 2)),
                "chars": len(text),
                "summary_chars": len(summarize_turn(record, text)),
            }
        )
    reliability_allowed, reliability_reason, reliability = reliability_gate()
    current = current_turn_record()
    delivery = binding.get("delivery") if binding and isinstance(binding.get("delivery"), dict) else {}
    ingress = binding.get("ingress") if binding and isinstance(binding.get("ingress"), dict) else {}
    last_outbound_at, last_outbound_error = read_telegram_last_outbound_at()
    payload = {
        "ok": bool(binding),
        "binding_active": bool(binding),
        "binding_reason": binding_reason,
        "room": {
            "room_id": binding.get("room_id") if binding else None,
            "title": binding.get("title") if binding else None,
            "telegram_chat_id": redact_chat_id(binding.get("telegram_chat_id")) if binding else None,
            "status": binding.get("status") if binding else None,
            "enabled": bool(delivery.get("telegram_outbound_enabled")) if binding else False,
            "real_outbound_enabled": bool(delivery.get("telegram_outbound_enabled") and not delivery.get("dry_run_required_before_group_send")) if binding else False,
            "summary_only": bool(delivery.get("send_summaries_to_group") and not delivery.get("send_full_transcript")) if binding else False,
            "paused": bool(state.get("paused")),
            "muted": bool(state.get("muted")),
            "delivery": delivery if binding else None,
            "ingress": ingress if binding else None,
        },
        "state": {
            "initialized_at": state.get("initialized_at"),
            "updated_at": state.get("updated_at"),
            "last_seen_seq": last_seen_seq,
            "last_sent_seq": event_summary.get("last_sent_seq"),
            "last_send_ok_at": event_summary.get("last_send_ok_at"),
            "last_send_error": event_summary.get("last_send_error"),
            "lastDryRunAt": event_summary.get("last_dry_run_at"),
            "lastSuppressedAt": event_summary.get("last_suppressed_at"),
            "lastOutboundAt": last_outbound_at,
            "lastOutboundAt_error": last_outbound_error,
            "sent_keys": len(state.get("sent_keys") if isinstance(state.get("sent_keys"), list) else []),
            "consecutive_failures": int(state.get("consecutive_failures") or 0),
            "next_attempt_after": state.get("next_attempt_after"),
            "last_failure_reason": state.get("last_failure_reason"),
            "suppressed_count": event_summary.get("suppressed_count"),
            "last_suppressed_reason": event_summary.get("last_suppressed_reason"),
        },
        "mailbox": {
            "current_seq": int(current["seq"]) if current else None,
            "current_writer": current.get("last_writer") if current else None,
            "latest_record_seq": latest_seq(records),
            "record_count": len(records),
        },
        "pending": {
            "count": len(pending),
            "latest_seq": max([item["seq"] for item in pending], default=None),
            "preview": pending[-5:],
        },
        "ingress_state": {
            "ordinary_message": ingress.get("ordinary_message") if ingress else None,
            "mention_bot": ingress.get("mention_bot") if ingress else None,
            "slash_command": ingress.get("slash_command") if ingress else None,
            "active": False,
            "mode": "dry_run_or_pending_artifact_only",
        },
        "reliability_gate": {
            "allowed": reliability_allowed,
            "reason": reliability_reason,
            "payload": reliability,
        },
    }
    outbound_state, outbound_reason = infer_outbound_state(payload)
    payload["outbound_state"] = outbound_state
    payload["outbound_reason"] = outbound_reason
    payload["human_summary"] = status_human_summary(payload)
    return payload


def mark_current_seen() -> dict[str, Any]:
    """Advance relay state to the latest local mailbox seq without sending.

    This is the safe resume-from-now option after downtime: it prevents old
    backlog from leaking into the group while preserving future relay behavior.
    """
    state = read_json(STATE_FILE)
    records = collect_records()
    current_latest = latest_seq(records)
    sent = state.get("sent_keys") if isinstance(state.get("sent_keys"), list) else []
    if not state.get("initialized_at"):
        state["initialized_at"] = now_iso()
    state.update(
        {
            "updated_at": now_iso(),
            "last_seen_seq": current_latest,
            "sent_keys": sorted(set(map(str, sent)))[-500:],
            "consecutive_failures": 0,
        }
    )
    state.pop("last_failure_reason", None)
    state.pop("next_attempt_after", None)
    write_json_atomic(STATE_FILE, state)
    event = {"ts": now_iso(), "event": "marked_current_seen", "last_seen_seq": current_latest, "record_count": len(records)}
    append_jsonl(EVENTS_FILE, event)
    return {"ok": True, "event": event, "state": {"last_seen_seq": current_latest, "updated_at": state.get("updated_at")}}


def parse_ingress_command(text: str) -> dict[str, Any]:
    raw = redact(text)
    stripped = raw.strip()
    result: dict[str, Any] = {
        "ok": True,
        "raw_sha256": sha256(stripped),
        "prompt_injection_marker": has_prompt_injection_marker(stripped),
        "canonical_mailbox_write": False,
        "turn_json_write": False,
        "agent_trigger": False,
    }
    if not stripped:
        result.update({"kind": "empty", "action": "ignore", "reason": "empty message"})
        return result

    room_match = re.match(r"^/room(?:@\S+)?(?:\s+|$)(.*)$", stripped, flags=re.IGNORECASE | re.DOTALL)
    if not room_match:
        result.update(
            {
                "kind": "ordinary_message",
                "action": "comment_only",
                "reason": "ordinary group message is never executed in P0",
                "comment": stripped[:1000],
            }
        )
        return result

    rest = room_match.group(1).strip()
    if not rest:
        result.update({"kind": "room_help", "action": "status", "reason": "empty /room command"})
        return result

    head, _, tail = rest.partition(" ")
    command = head.lower().strip()
    payload = tail.strip()
    if command == "status":
        result.update({"kind": "room_status", "action": "status", "reason": "read-only status request"})
        return result
    if command == "note":
        result.update(
            {
                "kind": "room_note",
                "action": "comment_artifact",
                "reason": "note is stored as comment only and does not trigger agents",
                "comment": payload[:2000],
            }
        )
        return result
    if command == "ask":
        target, _, question = payload.partition(" ")
        target = target.lower().strip()
        if target not in {"main", "codex"} or not question.strip():
            result.update(
                {
                    "kind": "room_ask_invalid",
                    "action": "reject",
                    "reason": "use /room ask main <text> or /room ask codex <text>",
                }
            )
            return result
        result.update(
            {
                "kind": "room_ask",
                "action": "pending_command_artifact",
                "reason": "P0 stores ask as pending command; policy gate decides later",
                "target_agent": target,
                "question": question.strip()[:4000],
            }
        )
        return result
    if command in {"pause", "resume"}:
        result.update(
            {
                "kind": "room_control",
                "action": "pending_command_artifact",
                "reason": "P0 stores control request as pending command; no direct state change in ingress dry-run",
                "control": command,
                "text": payload[:1000],
            }
        )
        return result

    result.update({"kind": "room_unknown", "action": "reject", "reason": f"unknown /room command: {command}"})
    return result


def write_ingress_artifact(parsed: dict[str, Any], source: str, from_user: str, message_id: str) -> dict[str, Any]:
    payload = {
        "ts": now_iso(),
        "source": source,
        "from": redact(from_user),
        "message_id": redact(message_id),
        "room_id": "openclaw-evolution",
        "parsed": parsed,
    }
    action = parsed.get("action")
    if action in {"comment_only", "comment_artifact"}:
        append_jsonl(COMMENTS_FILE, payload)
        return {"written": True, "artifact": str(COMMENTS_FILE), "artifact_type": "comment"}
    if action == "pending_command_artifact":
        PENDING_COMMAND_DIR.mkdir(parents=True, exist_ok=True)
        digest = str(parsed.get("raw_sha256") or sha256(json.dumps(payload, ensure_ascii=False)))[:16]
        path = PENDING_COMMAND_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{digest}.json"
        write_json_atomic(path, payload)
        return {"written": True, "artifact": str(path), "artifact_type": "pending_command"}
    return {"written": False, "artifact": None, "artifact_type": None}


def ingress_dry_run(text: str, source: str, from_user: str, message_id: str, write_artifact: bool) -> dict[str, Any]:
    parsed = parse_ingress_command(text)
    artifact = write_ingress_artifact(parsed, source, from_user, message_id) if write_artifact else {"written": False}
    event = {
        "ts": now_iso(),
        "event": "ingress_dry_run",
        "source": source,
        "from": redact(from_user),
        "message_id": redact(message_id),
        "kind": parsed.get("kind"),
        "action": parsed.get("action"),
        "prompt_injection_marker": parsed.get("prompt_injection_marker"),
        "artifact": artifact,
    }
    append_jsonl(EVENTS_FILE, event)
    return {
        "ok": True,
        "parsed": parsed,
        "artifact": artifact,
        "safety": {
            "canonical_mailbox_write": False,
            "turn_json_write": False,
            "agent_trigger": False,
        },
    }


def build_outbound_preview(max_items: int) -> str:
    state = read_json(STATE_FILE)
    sent = set(state.get("sent_keys") if isinstance(state.get("sent_keys"), list) else [])
    last_seen_seq = int(state.get("last_seen_seq") or 0)
    records = collect_records()
    previews: list[str] = []
    for record in records:
        text = redact(record.get("text", ""))
        if not text:
            continue
        key = f"{record['seq']}:{record['last_writer']}:{sha256(text)}"
        if key in sent or int(record["seq"]) <= last_seen_seq:
            continue
        previews.append(summarize_turn(record, text))
    if not previews:
        return "当前没有待发送的高价值协作摘要。可能原因：mark-current-seen 已从当前恢复、没有 qualifying event、或事件已被 dry-run/sent/deduped。\n"
    return "\n\n---\n\n".join(previews[-max(1, max_items):]) + "\n"


def build_status_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# OpenClaw Telegram Room Relay Dry-Run Status",
        "",
        "## Summary",
    ]
    for line in payload.get("human_summary") or []:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Safety",
            "- P0 ingress is dry-run / pending-artifact-only.",
            "- Ordinary group messages do not trigger agents and do not write turn.json.",
            "- /room ask main/codex creates a pending command artifact only.",
            "- lastOutboundAt=null is not treated as Telegram failure by itself.",
            "",
            "## Output Interpretation",
            f"- outbound_state: `{payload.get('outbound_state')}`",
            f"- outbound_reason: {payload.get('outbound_reason')}",
            f"- lastOutboundAt: `{((payload.get('state') or {}).get('lastOutboundAt'))}`",
            f"- lastDryRunAt: `{((payload.get('state') or {}).get('lastDryRunAt'))}`",
            f"- lastSuppressedAt: `{((payload.get('state') or {}).get('lastSuppressedAt'))}`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_dry_run_artifacts(max_items: int) -> dict[str, Any]:
    DRY_RUN_ROOT.mkdir(parents=True, exist_ok=True)
    records = collect_records()
    latest = latest_seq(records)
    preview_event = {
        "ts": now_iso(),
        "event": "outbound_preview_written",
        "seq": latest,
        "room_id": "openclaw-evolution",
        "artifact_root": str(DRY_RUN_ROOT),
    }
    append_jsonl(EVENTS_FILE, preview_event)
    payload = status_payload()
    status_json = DRY_RUN_ROOT / "status.json"
    status_md = DRY_RUN_ROOT / "status.md"
    preview_md = DRY_RUN_ROOT / "outbound-preview.md"
    events_jsonl = DRY_RUN_ROOT / "events.jsonl"
    pending_jsonl = DRY_RUN_ROOT / "pending-commands.jsonl"
    comments_jsonl = DRY_RUN_ROOT / "comments.jsonl"

    write_json_atomic(status_json, payload)
    status_md.write_text(build_status_markdown(payload), encoding="utf-8")
    preview_md.write_text(build_outbound_preview(max_items), encoding="utf-8")
    recent_events = read_recent_events()
    events_jsonl.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in recent_events[-200:]), encoding="utf-8")

    pending_rows: list[dict[str, Any]] = []
    if PENDING_COMMAND_DIR.exists():
        for path in sorted(PENDING_COMMAND_DIR.glob("*.json"))[-100:]:
            data = read_json(path)
            if data:
                pending_rows.append({"path": str(path), "data": data})
    pending_jsonl.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pending_rows), encoding="utf-8")

    if COMMENTS_FILE.exists():
        comments_jsonl.write_text("\n".join(COMMENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]) + "\n", encoding="utf-8")
    else:
        comments_jsonl.write_text("", encoding="utf-8")

    return {
        "ok": True,
        "artifact_root": str(DRY_RUN_ROOT),
        "files": {
            "status_json": str(status_json),
            "status_md": str(status_md),
            "outbound_preview_md": str(preview_md),
            "events_jsonl": str(events_jsonl),
            "pending_commands_jsonl": str(pending_jsonl),
            "comments_jsonl": str(comments_jsonl),
        },
        "outbound_state": payload.get("outbound_state"),
        "outbound_reason": payload.get("outbound_reason"),
        "pending_count": (payload.get("pending") or {}).get("count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay mailbox conversation turns to the bound Telegram room.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print relay binding/state/pending/reliability status as JSON and exit.")
    parser.add_argument("--mark-current-seen", action="store_true", help="Advance relay state to the latest local mailbox seq without sending anything.")
    parser.add_argument("--send-current", action="store_true", help="Send the current turn even when state is empty.")
    parser.add_argument("--max-items", type=int, default=4)
    parser.add_argument("--ingress-dry-run", action="store_true", help="Parse one Telegram group ingress message without touching canonical mailbox.")
    parser.add_argument("--ingress-text", default="", help="Text to parse for --ingress-dry-run.")
    parser.add_argument("--ingress-source", default="manual", help="Ingress source label for dry-run evidence.")
    parser.add_argument("--ingress-from", default="", help="Sender label/user id for dry-run evidence; will be redacted.")
    parser.add_argument("--ingress-message-id", default="", help="Telegram message id for dry-run evidence.")
    parser.add_argument("--write-ingress-artifact", action="store_true", help="Write comment/pending command artifacts for ingress dry-run; never writes turn.json.")
    parser.add_argument("--write-dry-run-artifacts", action="store_true", help="Write status/preview/events dry-run artifacts without sending Telegram messages.")
    parser.add_argument("--artifact-root", default="", help="Override dry-run artifact root.")
    args = parser.parse_args()

    global DRY_RUN_ROOT
    if args.artifact_root:
        DRY_RUN_ROOT = Path(args.artifact_root)

    if args.ingress_dry_run:
        print(json.dumps(ingress_dry_run(args.ingress_text, args.ingress_source, args.ingress_from, args.ingress_message_id, args.write_ingress_artifact), ensure_ascii=False, indent=2))
        return 0

    if args.write_dry_run_artifacts:
        print(json.dumps(write_dry_run_artifacts(args.max_items), ensure_ascii=False, indent=2))
        return 0

    if args.status:
        print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
        return 0

    if args.mark_current_seen:
        print(json.dumps(mark_current_seen(), ensure_ascii=False, indent=2))
        return 0

    binding, reason = load_binding()
    state = read_json(STATE_FILE)
    if not binding:
        record_suppressed(state, reason)
        return 0

    active, backoff_reason = backoff_is_active(state)
    if active:
        record_suppressed(state, backoff_reason)
        return 0

    delivery = binding.get("delivery") if isinstance(binding.get("delivery"), dict) else {}
    if delivery.get("dry_run_required_before_group_send") and not args.dry_run:
        record_suppressed(state, "dry_run_required_before_group_send")
        print(json.dumps({"ok": True, "events": [], "suppressed": "dry_run_required_before_group_send"}, ensure_ascii=False))
        return 0

    if not args.dry_run:
        healthy, health_reason, health_payload = reliability_gate()
        if not healthy:
            record_suppressed(state, health_reason, {"health": health_payload})
            return 0

    target = str(binding.get("telegram_chat_id"))
    sent = set(state.get("sent_keys") if isinstance(state.get("sent_keys"), list) else [])
    last_seen_seq = int(state.get("last_seen_seq") or 0)

    records = collect_records()
    current = current_turn_record()

    if not state.get("initialized_at") and current and not args.send_current:
        state = {
            "initialized_at": now_iso(),
            "updated_at": now_iso(),
            "last_seen_seq": int(current["seq"]),
            "sent_keys": sorted(sent)[-500:],
            "consecutive_failures": 0,
        }
        write_json_atomic(STATE_FILE, state)
        append_jsonl(
            EVENTS_FILE,
            {
                "ts": now_iso(),
                "event": "initialized_future_only",
                "last_seen_seq": int(current["seq"]),
            },
        )
        print(json.dumps({"ok": True, "events": [], "last_seen_seq": int(current["seq"])}, ensure_ascii=False))
        return 0

    candidates: list[dict[str, Any]] = []
    for record in records:
        text = redact(record.get("text", ""))
        if not text:
            continue
        key = f"{record['seq']}:{record['last_writer']}:{sha256(text)}"
        if key in sent:
            continue
        if record["seq"] <= last_seen_seq and not args.send_current:
            continue
        record = dict(record)
        record["text"] = text
        record["key"] = key
        candidates.append(record)

    if not candidates and not state:
        if current and not args.send_current:
            state = {"initialized_at": now_iso(), "last_seen_seq": int(current["seq"]), "sent_keys": []}
            write_json_atomic(STATE_FILE, state)
        return 0

    candidates = sorted(candidates, key=lambda row: (row["seq"], row["last_writer"]))[-max(1, args.max_items):]
    events: list[dict[str, Any]] = []
    for record in candidates:
        outbound_text = summarize_turn(record, record["text"])
        chunks = split_chunks(outbound_text, SUMMARY_MAX_CHARS, 2)
        message_ids: list[str] = []
        failed = False
        for idx, chunk in enumerate(chunks, start=1):
            try:
                result = send_message(target, format_turn(record, chunk, idx, len(chunks)), args.dry_run)
            except subprocess.TimeoutExpired as exc:
                result = {
                    "returncode": 124,
                    "stdout": str(exc.stdout or "")[-2000:],
                    "stderr": f"openclaw message send timed out after {OPENCLAW_SEND_TIMEOUT_SECONDS}s",
                }
            payload = result.get("json") if isinstance(result.get("json"), dict) else {}
            msg_id = ""
            if isinstance(payload.get("payload"), dict):
                msg_id = str(payload["payload"].get("messageId") or "")
            if msg_id:
                message_ids.append(msg_id)
            if result["returncode"] != 0:
                append_jsonl(EVENTS_FILE, {"ts": now_iso(), "event": "send_failed", "record": record["key"], "result": result})
                mark_backoff(state, str(result.get("stderr") or result.get("stdout") or result.get("returncode")))
                state["last_send_error"] = str(result.get("stderr") or result.get("stdout") or result.get("returncode"))[-500:]
                failed = True
                break
        if failed:
            break
        else:
            if not args.dry_run:
                sent.add(str(record["key"]))
                last_seen_seq = max(last_seen_seq, int(record["seq"]))
                state["last_sent_seq"] = int(record["seq"])
                state["last_send_ok_at"] = now_iso()
                state["last_send_message_ids"] = message_ids[-5:]
                state.pop("last_send_error", None)
                clear_backoff(state)
            events.append(
                {
                    "ts": now_iso(),
                    "event": "turn_relay_sent" if not args.dry_run else "turn_relay_dry_run",
                    "room_id": "openclaw-evolution",
                    "seq": record["seq"],
                    "writer": record["last_writer"],
                    "target": target,
                    "message_ids": message_ids,
                    "chunks": len(chunks),
                    "original_chars": len(record.get("text") or ""),
                    "summary_chars": len(outbound_text),
                    "source": record.get("source"),
                }
            )

    state.update({"updated_at": now_iso(), "last_seen_seq": last_seen_seq, "sent_keys": sorted(sent)[-500:]})
    write_json_atomic(STATE_FILE, state)
    for event in events:
        append_jsonl(EVENTS_FILE, event)
    print(json.dumps({"ok": True, "events": events, "last_seen_seq": last_seen_seq}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
