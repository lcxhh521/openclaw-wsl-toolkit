#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT as ROOT
TURN_FILE = ROOT / "turn.json"
CODEX_FILE = ROOT / "codex_to_main.md"
MAIN_FILE = ROOT / "main_to_codex.md"
STATE_FILE = ROOT / ".openclaw_main_watcher_state.json"
LOCK_FILE = ROOT / ".openclaw_main_watcher.lock"
LOG_FILE = ROOT / "openclaw-main-mailbox-watch.log"
FOREGROUND_GUARD_FILE = ROOT / "foreground_guard.json"
RUN_LOG_DIR = ROOT / "watch-runs"
ACTIVE_RUN_FILE = RUN_LOG_DIR / "active-run.json"
ARCHIVE_SCRIPT = CODE_ROOT / "archive_mailbox_turn.py"
WRITE_TURN_SCRIPT = CODE_ROOT / "write_mailbox_turn.py"
CONTEXT_ROLLOVER_SCRIPT = CODE_ROOT / "context_rollover.py"
CONTEXT_ROLLOVER_STATE_FILE = ROOT / "context_rollover_state.json"
CONTEXT_ROLLOVER_PROMPT_CHARS = int(os.environ.get("OPENCLAW_MAILBOX_CONTEXT_ROLLOVER_PROMPT_CHARS", "4500"))
CONTEXT_ROLLOVER_TIMEOUT_SECONDS = int(os.environ.get("OPENCLAW_MAILBOX_CONTEXT_ROLLOVER_TIMEOUT_SECONDS", "8"))
CONTEXT_ROLLOVER_EPOCH_SESSION_ENABLED = str(
    os.environ.get("OPENCLAW_MAILBOX_CONTEXT_EPOCH_SESSION_ENABLED", "1")
).strip().lower() not in ("0", "false", "off", "no")
CONTEXT_ROLLOVER_SESSION_PREFIX = os.environ.get(
    "OPENCLAW_MAILBOX_CONTEXT_SESSION_PREFIX",
    "openclaw-main-mailbox-context-epoch",
)

OPENCLAW = os.environ.get("OPENCLAW_BIN", str(Path.home() / ".local" / "bin" / "openclaw"))
GATEWAY_SERVICE_NAME = os.environ.get("OPENCLAW_GATEWAY_SERVICE", "openclaw-gateway.service")
SESSIONS_FILE = Path(os.environ.get("OPENCLAW_MAIN_SESSIONS_JSON", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json")))
MAILBOX_MAIN_SESSION_KEY = "agent:main:main"
MAIN_SESSION_KEY = os.environ.get("OPENCLAW_MAIN_SESSION_KEY", "")
FALLBACK_MAIN_SESSION_ID = "c7d56b53-b915-45d6-9614-129f2633bc22"
RETRY_AFTER_SECONDS = 10 * 60
SESSION_LOCK_RETRY_AFTER_SECONDS = 90
MAX_TRIGGER_ATTEMPTS = 3
AGENT_COMMAND_TIMEOUT_SECONDS = 300
AGENT_WAIT_TIMEOUT_SECONDS = AGENT_COMMAND_TIMEOUT_SECONDS + 30
GATEWAY_PREFLIGHT_TIMEOUT_SECONDS = 12
GATEWAY_RESTART_AFTER_SECONDS = 5 * 60
FOREGROUND_BACKLOG_GUARD_ENABLED = str(
    os.environ.get("OPENCLAW_MAILBOX_FOREGROUND_BACKLOG_GUARD", "1")
).strip().lower() not in ("0", "false", "off", "no")
FOREGROUND_BACKLOG_LOOKBACK_MINUTES = int(
    os.environ.get("OPENCLAW_MAILBOX_FOREGROUND_BACKLOG_LOOKBACK_MINUTES", "8")
)
FOREGROUND_BACKLOG_JOURNAL_LINES = int(
    os.environ.get("OPENCLAW_MAILBOX_FOREGROUND_BACKLOG_JOURNAL_LINES", "160")
)
FOREGROUND_BACKLOG_QUIET_SECONDS = int(
    os.environ.get("OPENCLAW_MAILBOX_FOREGROUND_QUIET_SECONDS", "120")
)
FOREGROUND_BACKLOG_QUIET_JOURNAL_LINES = int(
    os.environ.get("OPENCLAW_MAILBOX_FOREGROUND_QUIET_JOURNAL_LINES", "120")
)
RETRYABLE_STARTUP_FAILURE_MARKERS = [
    "gatewaytransporterror",
    "gateway closed",
    "gateway unavailable",
    "failed to resolve secrets",
    "active gateway snapshot",
    "secrets from the active gateway snapshot",
    "econnrefused",
    "connection refused",
    "connect econnrefused",
    "socket hang up",
    "1006",
]
RETRYABLE_SESSION_LOCK_FAILURE_MARKERS = [
    "sessionwritelocktimeouterror",
    "session file locked",
]
RETRYABLE_QUOTA_FAILURE_MARKERS = [
    "api rate limit reached",
    "rate limit exceeded",
    "usage_limit_reached",
    "usage_limit_exceeded",
    "you've hit your usage limit",
    "hit your usage limit",
    "usage limit",
    "quota_exhausted",
    "quota_exceeded",
    "insufficient_quota",
    "provider openai-codex is in cooldown",
    "all profiles unavailable",
    "too many requests",
    "http 429",
    "status 429",
    "429 too many requests",
    "rate_limit",
    "gateway_model_lane_busy",
    "gateway_unreachable_before_model_call",
    "lane_not_acquired",
]
RESETTABLE_FAILURE_CLASSES = {"startup_transport", "quota_cooldown", "session_lock_busy"}


SUSTAINED_LANE_ALERT_FILE = ROOT / "sustained-lane-alert.json"
SUSTAINED_LANE_STALE_SECONDS = int(os.environ.get("OPENCLAW_SUSTAINED_LANE_STALE_SECONDS", "600"))
SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS = int(os.environ.get("OPENCLAW_SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS", "3"))
SUSTAINED_LANE_WRITE_DIAGNOSTIC = str(
    os.environ.get("OPENCLAW_SUSTAINED_LANE_WRITE_DIAGNOSTIC", "0")
).strip().lower() in ("1", "true", "yes", "on")

# --- Main Ark Fallback ---
MAIN_ARK_FALLBACK_ENABLED = str(os.environ.get("OPENCLAW_MAIN_ARK_FALLBACK_ENABLED", "1")).strip().lower() not in (
    "0", "false", "off", "no",
)
MAIN_ARK_MODEL = os.environ.get("OPENCLAW_MAIN_ARK_MODEL", "minimax-m2.7")
MAIN_ARK_DEFAULT_FALLBACK_MODELS = ["minimax-m2.7", "deepseek-v4-pro", "glm-5.1", "kimi-k2.6"]
MAIN_ARK_FINAL_FALLBACK_ENABLED = str(
    os.environ.get("OPENCLAW_MAIN_FINAL_FALLBACK_ENABLED", "1")
).strip().lower() not in ("0", "false", "off", "no")
MAIN_ARK_FINAL_FALLBACK_RETRY_DELAY_SECONDS = int(
    os.environ.get("OPENCLAW_MAIN_ARK_FINAL_FALLBACK_RETRY_DELAY_SECONDS", "900")
)
MAIN_ARK_FINAL_FALLBACK_MAX_PENDING = int(os.environ.get("OPENCLAW_MAIN_ARK_FINAL_FALLBACK_MAX_PENDING", "16"))
MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM = int(os.environ.get("OPENCLAW_MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM", "6"))
MAIN_ARK_FALLBACK_MODELS_RAW = os.environ.get("OPENCLAW_MAIN_ARK_FALLBACK_MODELS", "")
MAIN_ARK_MODEL_COOLDOWN_SECONDS = int(os.environ.get("OPENCLAW_MAIN_ARK_MODEL_COOLDOWN_MINUTES") or "30") * 60
MAIN_ARK_FALLBACK_MAX_TOKENS = int(os.environ.get("OPENCLAW_MAIN_ARK_FALLBACK_MAX_TOKENS") or "8192")
MAIN_ARK_FALLBACK_TIMEOUT = int(os.environ.get("OPENCLAW_MAIN_ARK_FALLBACK_TIMEOUT") or "300")
MAIN_ARK_FALLBACK_TOTAL_BUDGET_SECONDS = int(os.environ.get("OPENCLAW_MAIN_ARK_FALLBACK_TOTAL_BUDGET_SECONDS") or "600")
MAIN_PRIMARY_MODEL = os.environ.get("OPENCLAW_MAIN_PRIMARY_MODEL", "openai-codex/gpt-5.5")
MAIN_AGENT_ID = "openclaw-main"
AGENT_ROOM_STATUS_FILE = ROOT / "agent-room" / "agent_room_status.json"
MAIN_NO_TOOL_FALLBACK_TERMINAL_STATUSES = {"retry_exhausted"}
# After main's GPT quota recovers, the next mailbox trigger will try GPT first
# (auto-switch-back). To avoid burning Ark when GPT is available but slow, we
# track quota_state and only fall back when it is "depleted".
MAIN_QUOTA_STATE_KEY = "main_quota_state"
MAIN_ARK_MODEL_FAILURES_KEY = "main_ark_model_failures"
MAIN_ARK_RETIRED_MODEL_REPLACEMENTS = {
    "deepseek-v3.2": "deepseek-v4-pro",
    "glm-4.7": "glm-5.1",
    "kimi-k2.5": "kimi-k2.6",
    "minimax-m2.5": "minimax-m2.7",
}
MAIN_ARK_ENV_FILES = [
    Path.home() / ".openclaw" / "secrets" / "volcengine.env",
    Path.home() / ".openclaw" / ".env",
]


def load_ark_env_into_process() -> list[str]:
    """Load API keys from env files into the current process environment.

    The mailbox watcher runs from a systemd timer and does not inherit the
    interactive shell's environment. Ark fallback calls direct_provider_worker,
    which reads VOLCANO_ENGINE_API_KEY from os.environ. Without this loader,
    the key is missing and every Ark fallback fails with missing_api_key.
    """
    loaded: list[str] = []
    for path in MAIN_ARK_ENV_FILES:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        loaded.append(str(path))
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not key.isidentifier():
                continue
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    return loaded


def split_model_list(raw: str) -> list[str]:
    models: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        model = part.strip()
        if model:
            models.append(model)
    return models


def normalize_main_ark_model(model: str) -> str:
    value = str(model or "").strip()
    tail = value.rsplit("/", 1)[-1].lower()
    return MAIN_ARK_RETIRED_MODEL_REPLACEMENTS.get(tail, value)


def register_main_no_tool_fallback(state: dict, seq: str, reason: str, detail: str = "", model: str = "") -> dict:
    if not MAIN_ARK_FINAL_FALLBACK_ENABLED:
        return {}
    queue = state.get("main_local_no_tool_fallback_queue")
    if not isinstance(queue, list):
        queue = []
    else:
        queue = [item for item in queue if isinstance(item, dict)]

    seq_key = str(seq)
    previous = next((item for item in queue if str(item.get("seq", "")) == seq_key), {})
    try:
        retry_count = int(previous.get("retry_count", 0) or 0) if isinstance(previous, dict) else 0
    except (TypeError, ValueError):
        retry_count = 0
    now = now_iso()
    retry_at_epoch = int(time.time()) + MAIN_ARK_FINAL_FALLBACK_RETRY_DELAY_SECONDS
    record = {
        "seq": seq_key,
        "status": "queued",
        "first_queued_at": str(previous.get("first_queued_at") or previous.get("queued_at") or now) if isinstance(previous, dict) else now,
        "queued_at": now,
        "retry_at_epoch": retry_at_epoch,
        "retry_count": retry_count,
        "reason": reason[:128],
        "model": str(model or "").strip(),
        "detail": str(detail or "")[:700],
    }
    if MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM > 0 and retry_count >= MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM:
        record.update(
            {
                "status": "retry_exhausted",
                "retry_at_epoch": 0,
                "retry_exhausted_at": now,
                "max_retries": MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM,
                "terminal_reason": reason[:128],
            }
        )

    queue = [item for item in queue if str(item.get("seq", "")) != seq_key]
    queue.append(record)
    if MAIN_ARK_FINAL_FALLBACK_MAX_PENDING > 0:
        queue = queue[-MAIN_ARK_FINAL_FALLBACK_MAX_PENDING:]

    state["main_local_no_tool_fallback_queue"] = queue
    state["main_local_no_tool_fallback_active"] = any(
        str(item.get("status") or "") not in MAIN_NO_TOOL_FALLBACK_TERMINAL_STATUSES
        for item in queue
    )
    state["main_local_no_tool_fallback_last_entry"] = record
    retry_candidates = [
        int(item.get("retry_at_epoch", 0))
        for item in queue
        if str(item.get("status") or "") not in MAIN_NO_TOOL_FALLBACK_TERMINAL_STATUSES
        and isinstance(item.get("retry_at_epoch"), int)
        and item.get("retry_at_epoch") > 0
    ]
    if not retry_candidates:
        state.pop("main_local_no_tool_fallback_next_retry_epoch", None)
    else:
        state["main_local_no_tool_fallback_next_retry_epoch"] = min(retry_candidates)
    return record


def normalize_main_no_tool_fallback_queue(state: dict, current_seq: str = "") -> list[dict]:
    queue = state.get("main_local_no_tool_fallback_queue")
    if not isinstance(queue, list):
        queue = []

    current_seq_int = seq_int(current_seq) if current_seq else None
    normalized: list[dict] = []
    for item in queue:
        if not isinstance(item, dict):
            continue
        item_seq = str(item.get("seq", "")).strip()
        item_seq_int = seq_int(item_seq)
        if current_seq_int is not None and item_seq_int is not None and item_seq_int < current_seq_int:
            continue
        normalized.append(item)

    state["main_local_no_tool_fallback_queue"] = normalized
    state["main_local_no_tool_fallback_active"] = any(
        str(item.get("status") or "") not in MAIN_NO_TOOL_FALLBACK_TERMINAL_STATUSES
        for item in normalized
    )
    if normalized:
        state["main_local_no_tool_fallback_last_entry"] = normalized[-1]
        retry_candidates = []
        for item in normalized:
            if str(item.get("status") or "") in MAIN_NO_TOOL_FALLBACK_TERMINAL_STATUSES:
                continue
            try:
                retry_at = int(item.get("retry_at_epoch", 0) or 0)
            except (TypeError, ValueError):
                retry_at = 0
            if retry_at > 0:
                retry_candidates.append(retry_at)
        if retry_candidates:
            state["main_local_no_tool_fallback_next_retry_epoch"] = min(retry_candidates)
        else:
            state.pop("main_local_no_tool_fallback_next_retry_epoch", None)
    else:
        state.pop("main_local_no_tool_fallback_last_entry", None)
        state.pop("main_local_no_tool_fallback_next_retry_epoch", None)
    return normalized


def clear_main_no_tool_fallback_for_seq(state: dict, seq: str, status: str) -> None:
    queue = normalize_main_no_tool_fallback_queue(state, seq)
    seq_key = str(seq)
    remaining = [item for item in queue if str(item.get("seq", "")) != seq_key]
    state["main_local_no_tool_fallback_queue"] = remaining
    state["main_local_no_tool_fallback_active"] = bool(remaining)
    state["main_local_no_tool_fallback_last_cleared_seq"] = seq_key
    state["main_local_no_tool_fallback_last_cleared_status"] = status
    state["main_local_no_tool_fallback_last_cleared_at"] = now_iso()
    normalize_main_no_tool_fallback_queue(state, seq)


def main_no_tool_fallback_retry_wait_seconds(state: dict, seq: str) -> int:
    queue = normalize_main_no_tool_fallback_queue(state, seq)
    seq_key = str(seq)
    now_epoch = int(time.time())
    for item in queue:
        if str(item.get("seq", "")) != seq_key:
            continue
        try:
            retry_at_epoch = int(item.get("retry_at_epoch", 0) or 0)
        except (TypeError, ValueError):
            retry_at_epoch = 0
        if retry_at_epoch > now_epoch:
            wait_seconds = retry_at_epoch - now_epoch
            state["main_local_no_tool_fallback_wait_seconds"] = wait_seconds
            state["main_local_no_tool_fallback_wait_seq"] = seq_key
            state["main_local_no_tool_fallback_wait_observed_at"] = now_iso()
            return wait_seconds
        item["status"] = "retry_due"
        item["retry_due_at"] = now_iso()
        state["main_local_no_tool_fallback_last_entry"] = item
        state["main_local_no_tool_fallback_last_due_seq"] = seq_key
        state["main_local_no_tool_fallback_last_due_at"] = item["retry_due_at"]
        return 0
    state.pop("main_local_no_tool_fallback_wait_seconds", None)
    return 0


def mark_main_no_tool_fallback_retry_exhausted(state: dict, seq: str, detail: str = "") -> dict:
    queue = normalize_main_no_tool_fallback_queue(state, seq)
    seq_key = str(seq)
    now = now_iso()
    for item in queue:
        if str(item.get("seq", "")) != seq_key:
            continue
        item["status"] = "retry_exhausted"
        item["retry_at_epoch"] = 0
        item["retry_exhausted_at"] = now
        item["max_retries"] = MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM
        if detail:
            item["retry_exhausted_detail"] = detail[:700]
        state["main_local_no_tool_fallback_last_entry"] = item
        state["main_local_no_tool_fallback_last_exhausted_seq"] = seq_key
        state["main_local_no_tool_fallback_last_exhausted_at"] = now
        normalize_main_no_tool_fallback_queue(state, seq)
        return item
    return {}


def main_no_tool_fallback_retry_budget_exhausted(state: dict, seq: str) -> dict:
    if MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM <= 0:
        return {}
    queue = normalize_main_no_tool_fallback_queue(state, seq)
    seq_key = str(seq)
    for item in queue:
        if str(item.get("seq", "")) != seq_key:
            continue
        if str(item.get("status") or "") == "retry_exhausted":
            return item
        try:
            retry_count = int(item.get("retry_count", 0) or 0)
        except (TypeError, ValueError):
            retry_count = 0
        if retry_count >= MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM:
            return mark_main_no_tool_fallback_retry_exhausted(
                state,
                seq,
                detail="max final local fallback retries reached",
            )
    return {}


def mark_main_no_tool_fallback_retrying(state: dict, seq: str) -> dict:
    """Mark a queued no-tool fallback turn as being consumed by this tick.

    The previous fallback patch made terminal Ark failure observable by writing
    ``main_local_no_tool_fallback_queue``. This helper is the other half of the
    contract: when the retry window is due, the watcher records that it is
    actively consuming the queued item before attempting the normal GPT/Ark
    path again. The queue is only cleared after the mailbox turn advances.
    """
    queue = normalize_main_no_tool_fallback_queue(state, seq)
    seq_key = str(seq)
    for item in queue:
        if str(item.get("seq", "")) != seq_key:
            continue
        try:
            retry_count = int(item.get("retry_count", 0) or 0)
        except (TypeError, ValueError):
            retry_count = 0
        if (
            MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM > 0
            and retry_count >= MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM
        ):
            mark_main_no_tool_fallback_retry_exhausted(
                state,
                seq,
                detail="max final local fallback retries reached before retry",
            )
            return {}
        item["retry_count"] = retry_count + 1
        item["status"] = "retrying"
        item["retry_started_at"] = now_iso()
        state["main_local_no_tool_fallback_last_entry"] = item
        state["main_local_no_tool_fallback_last_retry_seq"] = seq_key
        state["main_local_no_tool_fallback_last_retry_at"] = item["retry_started_at"]
        return item
    return {}


# DEPRECATED: drain_no_tool_fallback_queue was replaced by inline retry gate.
# The hot path now handles queue consumption directly in main() (around line 1453-1477):
#   - Deferred retries skip the trigger path entirely ( ark_retry_wait_seconds > 0 branch)
#   - Due retries call mark_main_no_tool_fallback_retrying() before continuing
# This function is kept as a read-only diagnostic placeholder and is NOT called
# from any hot path. Safe to delete once the replacement is proven stable.
def drain_no_tool_fallback_queue(state: dict) -> list[dict]:
    """DEPRECATED: retained as a no-op diagnostic stub.

    Queue consumption logic moved inline into the watcher main() trigger path.
    See mark_main_no_tool_fallback_retrying() and the retry gate at line 1453.
    """
    return []


def dedupe_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for model in models:
        normalized = normalize_main_ark_model(model)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def main_ark_candidate_models() -> list[str]:
    configured = split_model_list(MAIN_ARK_FALLBACK_MODELS_RAW)
    candidates = configured if configured else list(MAIN_ARK_DEFAULT_FALLBACK_MODELS)
    primary = normalize_main_ark_model(MAIN_ARK_MODEL)
    if primary and (not candidates or normalize_main_ark_model(candidates[0]).lower() != primary.lower()):
        candidates = [primary, *candidates]
    return dedupe_models(candidates)


def is_deepseek_like_model(model: str) -> bool:
    return normalize_main_ark_model(model).strip().lower().startswith("deepseek")


def main_ark_fallback_target(model: str) -> tuple[str, str]:
    normalized = normalize_main_ark_model(model)
    if is_deepseek_like_model(model):
        return "openai-compatible", normalized
    return "ark-coding-plan", normalized


def main_ark_error_status(exc: object) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        direct_status = detail.get("status")
        if isinstance(direct_status, int):
            return direct_status
    return None


def main_ark_error_text(exc: object) -> str:
    parts = [str(getattr(exc, "kind", "") or ""), str(exc or "")]
    detail = getattr(exc, "detail", None)
    if detail:
        try:
            parts.append(json.dumps(detail, ensure_ascii=False, sort_keys=True))
        except Exception:
            parts.append(str(detail))
    return "\n".join(part for part in parts if part)


def main_ark_error_retryable(exc: object) -> bool:
    kind = str(getattr(exc, "kind", "") or "").lower()
    if kind in {"missing_api_key", "empty_prompt", "invalid_json"}:
        return False
    if kind in {"provider_timeout", "provider_network_error"}:
        return True
    status = main_ark_error_status(exc)
    if status == 429 or (status is not None and 500 <= status <= 599):
        return True
    text = main_ark_error_text(exc).lower()
    retryable_markers = [
        "429",
        "too many requests",
        "rate limit",
        "quota",
        "usage limit",
        "accountquotaexceeded",
        "model unavailable",
        "model is unavailable",
        "model not available",
        "model_not_found",
        "model not found",
        "not exist",
        "overloaded",
        "temporarily unavailable",
        "timeout",
    ]
    return any(marker in text for marker in retryable_markers)


def main_ark_failure_reason(exc: object) -> str:
    status = main_ark_error_status(exc)
    text = main_ark_error_text(exc).lower()
    if "usage quota" in text or "usage limit" in text or "accountquotaexceeded" in text or "quota" in text:
        return "usage_limit"
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if status is not None and 500 <= status <= 599:
        return "model_overloaded"
    if "model not found" in text or "model_not_found" in text or "model unavailable" in text or "not available" in text:
        return "model_unavailable"
    return str(getattr(exc, "kind", "") or "direct_provider_error")


def parse_main_ark_retry_time(exc: object) -> str:
    text = main_ark_error_text(exc)
    reset_match = re.search(
        r"(?:reset|try again)\s+at\s+(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2})\s+([+-]\d{4})(?:\s+[A-Z]{2,5})?",
        text,
        re.I,
    )
    if reset_match:
        raw = f"{reset_match.group(1)} {reset_match.group(2)}"
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z").astimezone().isoformat(timespec="seconds")
        except Exception:
            pass
    return ""


def main_ark_model_on_cooldown(state: dict, model: str) -> tuple[bool, str]:
    failures = state.get(MAIN_ARK_MODEL_FAILURES_KEY)
    if not isinstance(failures, dict):
        return False, ""
    record = failures.get(model)
    if not isinstance(record, dict) or not record.get("retryable"):
        return False, ""
    cooldown_until_epoch = iso_to_epoch(record.get("cooldown_until"))
    if cooldown_until_epoch:
        if time.time() < cooldown_until_epoch:
            return True, str(record.get("reason") or record.get("kind") or "cooldown")
        return False, ""
    failed_epoch = iso_to_epoch(record.get("failed_at"))
    if not failed_epoch:
        return False, ""
    elapsed = time.time() - failed_epoch
    if elapsed < MAIN_ARK_MODEL_COOLDOWN_SECONDS:
        return True, str(record.get("kind") or "recent_failure")
    return False, ""


def record_main_ark_model_failure(state: dict, model: str, exc: object, retryable: bool) -> None:
    failures = state.get(MAIN_ARK_MODEL_FAILURES_KEY)
    if not isinstance(failures, dict):
        failures = {}
    cooldown_until = parse_main_ark_retry_time(exc)
    if retryable and not cooldown_until:
        cooldown_until = datetime.fromtimestamp(time.time() + MAIN_ARK_MODEL_COOLDOWN_SECONDS).astimezone().isoformat(timespec="seconds")
    failures[model] = {
        "failed_at": now_iso(),
        "kind": str(getattr(exc, "kind", "") or type(exc).__name__),
        "status": main_ark_error_status(exc),
        "retryable": retryable,
        "reason": main_ark_failure_reason(exc),
        "cooldown_until": cooldown_until,
    }
    state[MAIN_ARK_MODEL_FAILURES_KEY] = failures


def record_main_ark_model_success(state: dict, model: str) -> None:
    failures = state.get(MAIN_ARK_MODEL_FAILURES_KEY)
    if isinstance(failures, dict):
        failures.pop(model, None)
        state[MAIN_ARK_MODEL_FAILURES_KEY] = failures
    state["main_ark_fallback_last_success_at"] = now_iso()
    state["main_ark_fallback_last_success_model"] = model


def quota_record_active(record: dict | None) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("quota_state") != "exhausted":
        return False
    until_epoch = iso_to_epoch(record.get("cooldown_until"))
    return not until_epoch or time.time() < until_epoch


def update_agent_room_quota_status(
    agent_id: str,
    model: str,
    quota_state: str,
    *,
    reason: str = "",
    cooldown_until: str = "",
    fallback_available: bool | None = None,
    active_model: str = "",
    run_id: str = "",
) -> None:
    """Project main/agent quota status into the shared Agent Room status plane."""
    status = read_json(AGENT_ROOM_STATUS_FILE)
    agents = status.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        status["agents"] = agents
    agent = agents.get(agent_id)
    if not isinstance(agent, dict):
        agent = {}
        agents[agent_id] = agent
    models = agent.get("models")
    if not isinstance(models, dict):
        models = {}
        agent["models"] = models
    record = {
        "model": model,
        "quota_state": quota_state,
        "updated_at": now_iso(),
    }
    if reason:
        record["reason"] = reason
    if cooldown_until:
        record["cooldown_until"] = cooldown_until
        record["estimated_recovery"] = cooldown_until
    if fallback_available is not None:
        record["fallback_available"] = bool(fallback_available)
    if active_model:
        record["active_model"] = active_model
    if run_id:
        record["last_run_id"] = run_id
    previous = models.get(model)
    if isinstance(previous, dict) and previous.get("first_notification_sent"):
        record["first_notification_sent"] = previous.get("first_notification_sent")
        record["first_notification_sent_at"] = previous.get("first_notification_sent_at")
    models[model] = record
    exhausted = [value for value in models.values() if quota_record_active(value)]
    agent["quota_state"] = "exhausted" if exhausted and not fallback_available else ("fallback_active" if exhausted else "available")
    agent["fallback_active"] = bool(fallback_available and exhausted)
    if active_model:
        agent["active_model"] = active_model
    agent["updated_at"] = now_iso()
    write_json_atomic(AGENT_ROOM_STATUS_FILE, status)


def safe_update_agent_room_quota_status(*args: object, **kwargs: object) -> None:
    try:
        update_agent_room_quota_status(*args, **kwargs)
    except Exception as exc:
        log(f"agent_room_status_update_failed error={type(exc).__name__}")


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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def context_rollover_prompt_block(seq: str) -> tuple[str, dict]:
    """Return bounded rollover context for main prompt injection.

    This keeps the mailbox sequence monotonic while giving each new main turn a
    visible epoch/summary baseline. Failure is non-fatal: mailbox delivery must
    continue even if the rollover helper is broken.
    """
    if not CONTEXT_ROLLOVER_SCRIPT.exists():
        return "", {}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(CONTEXT_ROLLOVER_SCRIPT),
                "prompt-block",
                "--ensure",
                "--current-seq",
                str(seq),
                "--max-chars",
                str(CONTEXT_ROLLOVER_PROMPT_CHARS),
            ],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CONTEXT_ROLLOVER_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception as exc:
        log(f"context_rollover_failed seq={seq} error={type(exc).__name__}:{exc}")
        return "", {}
    if proc.returncode != 0:
        log(
            f"context_rollover_nonzero seq={seq} returncode={proc.returncode} "
            f"stderr={proc.stderr[-300:]!r}"
        )
        return "", read_json(CONTEXT_ROLLOVER_STATE_FILE)
    block = (proc.stdout or "").strip()
    state = read_json(CONTEXT_ROLLOVER_STATE_FILE)
    if block:
        log(
            "context_rollover_injected "
            f"seq={seq} epoch={state.get('context_epoch')} "
            f"source_seq={state.get('rollover_source_seq')} "
            f"summary={state.get('summary_path')}"
        )
    return block, state


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


BACKGROUND_COORDINATION_MARKERS = [
    "coordination_agent_room",
    "agent_room",
    "agent room",
    "mainline-governance",
    "mailbox epoch",
    "mailbox namespace",
    "anti-cargo-cult",
    "background collaboration",
    "后台协作",
    "架构协作",
    "协作体系",
    "协作空间",
]


def allows_background_coordination_progress(turn: dict, codex_text: str) -> tuple[bool, str]:
    """Return whether this turn may proceed while the Telegram front door is busy.

    The foreground guard exists to keep Alex's private/main Telegram experience
    responsive. It should not become a global stop-the-world switch for explicit
    background architecture coordination. This classifier is intentionally
    conservative: it relies on explicit turn fields or coordination markers in
    Codex's mailbox note/body, and its decision is recorded in watcher state.
    """
    explicit = str(turn.get("background_progress") or turn.get("background_ok") or "").strip().lower()
    if explicit in ("1", "true", "yes", "background", "coordination"):
        return True, f"turn_field:{explicit}"
    parts = [
        str(turn.get("note") or ""),
        str(turn.get("mailbox_kind") or ""),
        codex_text[:5000],
    ]
    combined = "\n".join(parts).lower()
    for marker in BACKGROUND_COORDINATION_MARKERS:
        if marker in combined:
            return True, f"marker:{marker}"
    return False, ""


def record_foreground_priority_bypass(state: dict, seq: str, reason: str, bypass_reason: str) -> None:
    state.update(
        {
            "last_status": "foreground_priority_bypassed_background_coordination",
            "last_bypassed_seq": seq,
            "last_bypassed_at": now_iso(),
            "last_bypassed_reason": reason[:500],
            "last_bypassed_by": bypass_reason[:200],
            "foreground_priority_guard": {
                "enabled": True,
                "lookback_minutes": FOREGROUND_BACKLOG_LOOKBACK_MINUTES,
                "quiet_seconds": FOREGROUND_BACKLOG_QUIET_SECONDS,
                "source": "gateway_journal",
                "background_coordination_bypass": True,
            },
        }
    )
    write_json_atomic(STATE_FILE, state)
    log(f"bypass_foreground_priority seq={seq} by={bypass_reason} detail={reason[:300]}")


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


def foreground_backlog_active() -> tuple[bool, str]:
    """Defer low-priority mailbox injection while Telegram front door is busy.

    The mailbox watcher talks to OpenClaw main through an explicit background
    session. If Telegram direct/group messages are already queued or in a model
    call, another mailbox trigger competes with Alex's foreground chat and makes
    the bot look dead. This check is intentionally local and read-only: it reads
    recent gateway journal liveness lines and never calls gateway RPCs.
    It also applies a short quiet window after any Telegram front-door
    activity, because a background turn can otherwise slip into the small gap
    between Alex's foreground messages and the next direct/group model call.
    """
    if not FOREGROUND_BACKLOG_GUARD_ENABLED:
        return False, ""
    try:
        recent = subprocess.run(
            [
                "journalctl",
                "--user",
                "-u",
                GATEWAY_SERVICE_NAME,
                "--since",
                f"{FOREGROUND_BACKLOG_QUIET_SECONDS} seconds ago",
                "--no-pager",
                "-n",
                str(FOREGROUND_BACKLOG_QUIET_JOURNAL_LINES),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
        )
    except Exception as exc:
        recent = None

    if recent is not None and recent.returncode in (0, 1):
        recent_hits: list[str] = []
        for line in recent.stdout.splitlines():
            lower = line.lower()
            is_main_telegram_work = (
                "agent:main:telegram:" in lower
                and ("model_call" in lower or "queued=" in lower)
            )
            is_telegram_send = "message.action" in lower and "channel=telegram" in lower
            if is_telegram_send and (
                "unsupported telegram action" in lower
                or "list-pins" in lower
                or "errorcode=unavailable" in lower
            ):
                is_telegram_send = False
            if not is_main_telegram_work and not is_telegram_send:
                continue
            compact = re.sub(r"\s+", " ", line.strip())
            recent_hits.append(compact[-260:])
        if recent_hits:
            detail = recent_hits[-1]
            return True, "telegram_frontdoor_recent_activity:" + detail

    try:
        result = subprocess.run(
            [
                "journalctl",
                "--user",
                "-u",
                GATEWAY_SERVICE_NAME,
                "--since",
                f"{FOREGROUND_BACKLOG_LOOKBACK_MINUTES} minutes ago",
                "--no-pager",
                "-n",
                str(FOREGROUND_BACKLOG_JOURNAL_LINES),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
        )
    except Exception as exc:
        return False, f"journal_unavailable:{exc}"

    if result.returncode not in (0, 1):
        return False, f"journal_returncode:{result.returncode}"

    hits: list[str] = []
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "agent:main:telegram:" not in lower:
            continue
        if "model_call" not in lower and "queued=" not in lower:
            continue
        if "agent:main:explicit:openclaw-main-mailbox" in lower and "agent:main:telegram:" not in lower:
            continue
        compact = re.sub(r"\s+", " ", line.strip())
        hits.append(compact[-260:])

    if not hits:
        return False, ""
    detail = hits[-1]
    return True, "telegram_frontdoor_busy:" + detail


def defer_foreground_priority(state: dict, seq: str, reason: str) -> None:
    record_sustained_soft_gate(state, seq, "foreground_priority", reason)
    state.update(
        {
            "last_status": "deferred_foreground_priority",
            "last_deferred_seq": seq,
            "last_deferred_at": now_iso(),
            "last_deferred_reason": reason[:500],
            "foreground_priority_guard": {
                "enabled": True,
                "lookback_minutes": FOREGROUND_BACKLOG_LOOKBACK_MINUTES,
                "quiet_seconds": FOREGROUND_BACKLOG_QUIET_SECONDS,
                "source": "gateway_journal",
            },
        }
    )
    write_json_atomic(STATE_FILE, state)
    log(f"skip seq={seq} reason=foreground_priority detail={reason[:300]}")




def mailbox_epoch_key() -> str:
    pointer = read_json(CODE_ROOT / "active_mailbox.json")
    value = pointer.get("active_epoch") or pointer.get("active_data_root") or str(ROOT)
    return str(value)


SILENT_WAIT_MARKERS = (
    "status: waiting_approval_silent",
    "status: acknowledged_silent_wait",
    "status: waiting_silent_ack",
    "keep-waiting/noop",
    "keep-waiting / noop",
    "protocol keep-waiting",
    "继续静默等待",
    "不要回复下一轮 keep-waiting",
)


def is_silent_wait_noop(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in SILENT_WAIT_MARKERS)


def close_silent_wait_noop_turn(seq: str, state: dict) -> tuple[bool, str]:
    """Close a keep-waiting/noop turn without waking the main model.

    The main watcher owns `needs_reply=main`; when Codex explicitly says this is
    a silent/noop wait, the correct terminal state is `needs_reply=none`, not an
    endless suppressed waiting loop.
    """
    latest = read_json(TURN_FILE)
    if str(latest.get("seq", "")) != str(seq) or latest.get("needs_reply") != "main":
        return False, "stale_turn"
    content = (
        "status: silent_wait_closed\n\n"
        "本轮是 keep-waiting/noop 协议回合；main 不调用模型、不做诊断/设计/实现，"
        "只关闭该轮等待，避免状态卡误报卡死。\n"
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(WRITE_TURN_SCRIPT),
                "--writer",
                "main",
                "--needs-reply",
                "none",
                "--content-file",
                tmp_path,
                "--note",
                f"Main closed silent-wait/noop seq {seq} without model call.",
                "--event",
                "main_silent_wait_noop_closed",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    finally:
        try:
            Path(tmp_path).unlink()
        except FileNotFoundError:
            pass
    state.update(
        {
            "last_status": "silent_wait_noop_closed" if proc.returncode == 0 else "silent_wait_noop_close_failed",
            "last_silent_wait_seq": str(seq),
            "last_silent_wait_closed_at": now_iso(),
            "last_silent_wait_writer_returncode": proc.returncode,
            "last_silent_wait_writer_stdout_tail": (proc.stdout or "")[-1000:],
            "last_silent_wait_writer_stderr_tail": (proc.stderr or "")[-1000:],
        }
    )
    write_json_atomic(STATE_FILE, state)
    return proc.returncode == 0, f"writer_returncode={proc.returncode}"


def sustained_lane_key(seq: str, gate_kind: str) -> str:
    return f"{mailbox_epoch_key()}::{ROOT}::{seq}::{gate_kind}"


def record_sustained_soft_gate(state: dict, seq: str, gate_kind: str, detail: str = "") -> dict:
    """Record a soft gate without mutating mailbox turns.

    This is intentionally alert-only. It makes repeated soft stalls observable
    while avoiding diagnostic-turn flood/CAS risks in the first P0 patch.
    """
    now = now_iso()
    key = sustained_lane_key(seq, gate_kind)
    gates = state.get("sustained_lane_gates")
    if not isinstance(gates, dict):
        gates = {}
    record = gates.get(key)
    if not isinstance(record, dict):
        record = {
            "schema": "openclaw.codex_main.sustained_lane_gate.v0",
            "epoch": mailbox_epoch_key(),
            "mailbox_root": str(ROOT),
            "seq": str(seq),
            "gate_kind": gate_kind,
            "first_at": now,
            "count": 0,
        }
    record["count"] = int(record.get("count", 0) or 0) + 1
    record["last_at"] = now
    record["last_detail"] = str(detail or "")[:700]
    record["class"] = "soft"
    record["write_diagnostic_enabled"] = SUSTAINED_LANE_WRITE_DIAGNOSTIC
    gates[key] = record
    # Keep the state bounded; the key embeds epoch/root/seq/gate to avoid
    # rollover collisions, but stale historical counters should not grow forever.
    if len(gates) > 80:
        items = sorted(gates.items(), key=lambda item: str(item[1].get("last_at") or ""))[-80:]
        gates = dict(items)
    state["sustained_lane_gates"] = gates
    state["sustained_lane_last_gate"] = record
    try:
        first_epoch = datetime.fromisoformat(str(record.get("first_at"))).timestamp()
    except Exception:
        first_epoch = time.time()
    age_seconds = max(0, int(time.time() - first_epoch))
    threshold_reached = (
        record["count"] >= SUSTAINED_LANE_SOFT_GATE_MAX_SKIPS
        or age_seconds >= SUSTAINED_LANE_STALE_SECONDS
    )
    record["age_seconds"] = age_seconds
    record["threshold_reached"] = bool(threshold_reached)
    record["recommended_action"] = (
        "review_or_enable_second_package_diagnostic_turn"
        if threshold_reached
        else "wait_or_retry_without_user_visible_mutation"
    )
    if threshold_reached:
        dedupe_key = sustained_lane_key(seq, gate_kind)
        alert = {
            "schema": "openclaw.codex_main.sustained_lane_alert.v0",
            "created_at": now,
            "generated_at": now,
            "mailbox_root": str(ROOT),
            "epoch": mailbox_epoch_key(),
            "seq": str(seq),
            "gate_kind": gate_kind,
            "status": "soft_stalled",
            "state": "SOFT_STALLED",
            "blocks_transport": False,
            "recommended_action": record.get("recommended_action"),
            "evidence_paths": [str(STATE_FILE), str(LOG_FILE), str(TURN_FILE)],
            "dedupe_key": dedupe_key,
            "derived_cache": True,
            "telegram_send_performed": False,
            "gate": record,
            "write_diagnostic_enabled": SUSTAINED_LANE_WRITE_DIAGNOSTIC,
            "note": "P0 is alert-only; diagnostic mailbox turns require a separate reviewed patch.",
        }
        write_json_atomic(SUSTAINED_LANE_ALERT_FILE, alert)
        log(
            f"sustained_lane_soft_stalled seq={seq} gate={gate_kind} "
            f"count={record['count']} age_seconds={age_seconds}"
        )
    return record

def classify_retryable_startup_failure(text: str) -> str:
    lowered = (text or "").lower()
    for marker in RETRYABLE_SESSION_LOCK_FAILURE_MARKERS:
        if marker in lowered:
            return "session_lock_busy"
    for marker in RETRYABLE_STARTUP_FAILURE_MARKERS:
        if marker in lowered:
            return "startup_transport"
    for marker in RETRYABLE_QUOTA_FAILURE_MARKERS:
        if marker in lowered:
            return "quota_cooldown"
    return ""


def classify_failure_from_run_log(path_value: object) -> str:
    if not path_value:
        return ""
    try:
        path = Path(str(path_value))
        text = path.read_text(encoding="utf-8", errors="replace")[-12000:]
    except Exception:
        return ""
    return classify_retryable_startup_failure(text)


def should_reset_attempts_after_retryable_failure(
    attempts: int,
    route_changed: bool,
    last_failure_class: str,
) -> bool:
    return (
        attempts >= MAX_TRIGGER_ATTEMPTS
        and not route_changed
        and last_failure_class in RESETTABLE_FAILURE_CLASSES
    )


def run_main_via_ark_fallback(
    message: str,
    seq: str,
    state: dict,
) -> tuple[bool, str]:
    """Attempt to serve the mailbox turn via Ark direct provider.

    Returns (success, detail_string).
    On success, writes the reply to MAIN_FILE and updates turn.json.
    On failure, returns False with error details; caller should record the failure.
    """
    if not MAIN_ARK_FALLBACK_ENABLED:
        return False, "ark_fallback_disabled"

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    try:
        # Add workspace scripts to path for direct_provider_lane import
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from direct_provider_lane import run_direct_provider_text_prompt, DirectProviderError
    except ImportError:
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_import_failed",
            detail="direct_provider_lane import missing",
        )
        return False, "ark_fallback_import_failed"

    codex_text = read_text(CODEX_FILE)
    if not codex_text:
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_no_codex_file",
            detail=f"codex_file={CODEX_FILE}",
        )
        return False, "ark_fallback_no_codex_file"

    system_prompt = (
        "你是 OpenClaw main agent 的 Ark 降级通道。"
        "你的任务是阅读 Codex 的消息并写出合适的回复，写入 mailbox 文件。"
        "回复需要简洁、聚焦 Telegram 用户体验可靠性。"
        "如果无法完成请求，写一个简短的 blocker 说明。"
        "不要编辑源代码，除非 Alex 明确要求。"
    )
    full_prompt = f"## Codex 消息 (seq={seq})\n\n{codex_text}\n\n## 指令\n\n{message}"

    task_id = f"main-ark-fallback-seq-{seq}"
    task_type = "openclaw_main_ark_fallback"

    candidates = main_ark_candidate_models()
    attempts: list[dict] = []
    skipped: list[dict] = []
    result: dict = {}
    selected_model = ""
    fallback_start = time.time()
    total_budget = MAIN_ARK_FALLBACK_TOTAL_BUDGET_SECONDS

    for model in candidates:
        fallback_provider, request_model = main_ark_fallback_target(model)
        elapsed_budget = time.time() - fallback_start
        if elapsed_budget >= total_budget:
            skipped.append({"model": model, "reason": "total_budget_exceeded", "detail": f"elapsed={elapsed_budget:.0f}s budget={total_budget}s"})
            continue
        # Cap per-model timeout to remaining budget so the loop cannot
        # exceed total_budget regardless of per-model MAIN_ARK_FALLBACK_TIMEOUT.
        remaining_budget = total_budget - elapsed_budget
        per_model_timeout = min(MAIN_ARK_FALLBACK_TIMEOUT, int(remaining_budget))

        if fallback_provider == "ark-coding-plan":
            on_cooldown, cooldown_reason = main_ark_model_on_cooldown(state, model)
        else:
            on_cooldown, cooldown_reason = False, ""
        if on_cooldown:
            failure_record = (state.get(MAIN_ARK_MODEL_FAILURES_KEY) or {}).get(model) or {}
            skipped.append({"model": model, "reason": "cooldown", "detail": cooldown_reason, "cooldown_until": failure_record.get("cooldown_until")})
            safe_update_agent_room_quota_status(
                MAIN_AGENT_ID,
                model,
                "exhausted",
                reason=str(failure_record.get("reason") or cooldown_reason or "cooldown"),
                cooldown_until=str(failure_record.get("cooldown_until") or ""),
                fallback_available=True,
                run_id=seq,
            )
            continue
        attempt_task_id = f"{task_id}-{re.sub(r'[^A-Za-z0-9._-]+', '-', model)[:48]}"
        try:
            result = run_direct_provider_text_prompt(
                prompt=full_prompt,
                task_id=attempt_task_id,
                task_type=task_type,
                model=request_model,
                provider_profile=fallback_provider,
                system=system_prompt,
                max_tokens=MAIN_ARK_FALLBACK_MAX_TOKENS,
                temperature=0.2,
                timeout=per_model_timeout,
            )
            selected_model = model
            attempts.append({"model": model, "provider_profile": fallback_provider, "status": "succeeded"})
            if fallback_provider == "ark-coding-plan":
                record_main_ark_model_success(state, model)
                safe_update_agent_room_quota_status(
                    MAIN_AGENT_ID,
                    model,
                    "available",
                    fallback_available=True,
                    active_model=model,
                    run_id=seq,
                )
            else:
                state["main_openai_compatible_fallback_last_used_at"] = now_iso()
                state["main_openai_compatible_fallback_last_model"] = request_model
            break
        except DirectProviderError as exc:
            retryable = main_ark_error_retryable(exc)
            if fallback_provider == "ark-coding-plan":
                record_main_ark_model_failure(state, model, exc, retryable)
                failure_record = (state.get(MAIN_ARK_MODEL_FAILURES_KEY) or {}).get(model) or {}
            else:
                failure_record = {"kind": exc.kind, "status": main_ark_error_status(exc), "reason": main_ark_failure_reason(exc)}
            attempts.append(
                {
                    "model": model,
                    "provider_profile": fallback_provider,
                    "status": "failed",
                    "kind": exc.kind,
                    "http_status": main_ark_error_status(exc),
                    "reason": failure_record.get("reason") or main_ark_failure_reason(exc),
                    "cooldown_until": failure_record.get("cooldown_until"),
                    "retryable": retryable,
                }
            )
            if retryable and fallback_provider == "ark-coding-plan":
                safe_update_agent_room_quota_status(
                    MAIN_AGENT_ID,
                    model,
                    "exhausted",
                    reason=str(failure_record.get("reason") or main_ark_failure_reason(exc)),
                    cooldown_until=str(failure_record.get("cooldown_until") or ""),
                    fallback_available=True,
                    run_id=seq,
                )
            log(f"ark_fallback_model_failed seq={seq} model={model} kind={exc.kind} retryable={retryable}")

            # For external DeepSeek fallback, continue to next configured model
            # instead of immediately bailing the whole fallback loop.
            if fallback_provider == "openai-compatible" and str(exc.kind) in {"missing_api_key", "missing_base_url"}:
                skipped.append(
                    {
                        "model": model,
                        "provider_profile": fallback_provider,
                        "reason": str(failure_record.get("reason") or exc.kind),
                    }
                )
                continue
            if fallback_provider == "openai-compatible":
                continue
            if retryable:
                continue
            register_main_no_tool_fallback(
                state,
                seq,
                "ark_fallback_non_retryable_model_error",
                detail=f"model={model},kind={exc.kind},message={main_ark_error_text(exc)}",
                model=model,
            )
            state["main_ark_fallback_candidate_models"] = candidates
            state["main_ark_fallback_model_attempts"] = attempts
            state["main_ark_fallback_skipped_models"] = skipped
            return False, f"ark_fallback_direct_provider_error:model={model}:kind={exc.kind}"
        except Exception as exc:
            register_main_no_tool_fallback(
                state,
                seq,
                "ark_fallback_exception",
                detail=f"model={model},type={type(exc).__name__}",
                model=model,
            )
            state["main_ark_fallback_candidate_models"] = candidates
            state["main_ark_fallback_model_attempts"] = attempts
            state["main_ark_fallback_skipped_models"] = skipped
            return False, f"ark_fallback_exception:model={model}:type={type(exc).__name__}"

    state["main_ark_fallback_candidate_models"] = candidates
    state["main_ark_fallback_model_attempts"] = attempts
    state["main_ark_fallback_skipped_models"] = skipped

    if not selected_model:
        safe_update_agent_room_quota_status(
            MAIN_AGENT_ID,
            MAIN_PRIMARY_MODEL,
            "exhausted",
            reason="quota_cooldown_no_fallback_available",
            fallback_available=False,
            run_id=seq,
        )
        budget_skipped = [s for s in skipped if s.get("reason") == "total_budget_exceeded"]
        if budget_skipped and not attempts:
            register_main_no_tool_fallback(
                state,
                seq,
                "ark_fallback_total_budget_exceeded",
                detail=f"budget={total_budget}s skipped_models={','.join(s.get('model','') for s in budget_skipped)}",
            )
            return False, "ark_fallback_total_budget_exceeded"
        if skipped and not attempts:
            register_main_no_tool_fallback(
                state,
                seq,
                "ark_fallback_all_models_on_cooldown",
                detail="all ark candidates are in cooldown",
            )
            return False, "ark_fallback_all_models_on_cooldown"
        failed = ",".join(
            f"{item.get('model')}:{item.get('kind') or item.get('status')}"
            for item in attempts[-4:]
        )
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_all_models_failed",
            detail=failed,
            model=",".join(candidates),
        )
        return False, f"ark_fallback_all_models_failed:{failed or 'no_candidates'}"

    reply_text = result.get("text", "").strip()
    if not reply_text:
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_empty_reply",
            detail=f"model={selected_model}",
            model=selected_model,
        )
        return False, f"ark_fallback_empty_reply:model={selected_model}"

    turn = read_json(TURN_FILE)
    if str(turn.get("seq", "")) != seq or turn.get("needs_reply") != "main":
        # Turn was modified while the fallback model was running; don't leave a
        # mailbox reply for a stale turn.
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_stale_turn",
            detail=f"model={selected_model}",
            model=selected_model,
        )
        return False, "ark_fallback_stale_turn"

    # Write reply to mailbox file
    try:
        MAIN_FILE.write_text(reply_text + "\n", encoding="utf-8")
    except Exception as exc:
        return False, f"ark_fallback_write_failed:{type(exc).__name__}"

    # Update turn.json: main has replied, advance to Codex's turn
    turn = read_json(TURN_FILE)
    if str(turn.get("seq", "")) != seq or turn.get("needs_reply") != "main":
        # Turn was modified by another process; don't overwrite
        try:
            main_file_backup = MAIN_FILE.with_suffix(".md.ark-fallback-stale-rollback")
            MAIN_FILE.rename(main_file_backup)
        except Exception:
            pass
        return False, "ark_fallback_stale_turn"

    try:
        turn.update({
            "last_writer": "main",
            "needs_reply": "codex",
            "seq": int(seq) + 1,
            "updated_at": now_iso(),
            "main_backend": "ark_fallback",
            "main_ark_model": selected_model,
            "main_ark_model_candidates": candidates,
        })
        write_json_atomic(TURN_FILE, turn)
    except Exception as exc:
        # Roll back mailbox write since turn didn't advance
        try:
            MAIN_file_backup = MAIN_FILE.with_suffix(".md.ark-fallback-rollback")
            MAIN_FILE.rename(MAIN_file_backup)
        except Exception:
            pass
        register_main_no_tool_fallback(
            state,
            seq,
            "ark_fallback_turn_update_failed",
            detail=f"type={type(exc).__name__},model={selected_model}",
            model=selected_model,
        )
        return False, f"ark_fallback_turn_update_failed:{type(exc).__name__}"

    # Record in state that we used Ark fallback
    success_at = now_iso()
    state[MAIN_QUOTA_STATE_KEY] = "depleted_ark_active"
    state["main_ark_fallback_last_used_at"] = success_at
    state["main_ark_fallback_last_success_at"] = success_at
    state["main_ark_fallback_last_success_seq"] = seq
    state["main_ark_fallback_last_success_model"] = selected_model
    state["main_ark_fallback_last_success_reply_chars"] = len(reply_text)
    state["main_ark_fallback_last_seq"] = seq
    state["main_ark_fallback_last_model"] = selected_model

    log(f"ark_fallback_success seq={seq} model={selected_model} reply_chars={len(reply_text)}")
    return True, f"ark_fallback_ok:model={selected_model}"


def maybe_restart_gateway_service(state: dict, reason: str) -> str:
    last_restart_epoch = iso_to_epoch(state.get("last_gateway_restart_at"))
    if last_restart_epoch and time.time() - last_restart_epoch < GATEWAY_RESTART_AFTER_SECONDS:
        return "restart_recently_attempted"

    try:
        reset = subprocess.run(
            ["systemctl", "--user", "reset-failed", GATEWAY_SERVICE_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        start = subprocess.run(
            ["systemctl", "--user", "start", GATEWAY_SERVICE_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        detail = f"restart_exception={type(exc).__name__} reason={reason}"
        state.update(
            {
                "last_gateway_restart_at": now_iso(),
                "last_gateway_restart_reason": reason,
                "last_gateway_restart_detail": detail,
            }
        )
        log(f"gateway_restart_attempt {detail}")
        return detail

    detail = (
        f"reset={reset.returncode} start={start.returncode} "
        f"reason={reason}"
    )
    state.update(
        {
            "last_gateway_restart_at": now_iso(),
            "last_gateway_restart_reason": reason,
            "last_gateway_restart_detail": detail,
        }
    )
    log(f"gateway_restart_attempt {detail}")
    return detail


def gateway_preflight(state: dict) -> tuple[bool, str]:
    try:
        service = subprocess.run(
            ["systemctl", "--user", "is-active", GATEWAY_SERVICE_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return False, f"service_check_failed:{type(exc).__name__}"

    service_state = (service.stdout or "").strip()
    if service.returncode != 0 or service_state != "active":
        reason = f"service_not_active:{service_state or service.returncode}"
        if (service_state or "").strip() in {"failed", "inactive"}:
            restart_detail = maybe_restart_gateway_service(state, reason)
            if restart_detail != "restart_recently_attempted":
                time.sleep(2)
                try:
                    service = subprocess.run(
                        ["systemctl", "--user", "is-active", GATEWAY_SERVICE_NAME],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    service_state = (service.stdout or "").strip()
                    if service.returncode == 0 and service_state == "active":
                        state["last_gateway_restart_recovered_at"] = now_iso()
                    else:
                        return False, f"service_not_active_after_restart:{service_state or service.returncode}"
                except Exception as exc:
                    return False, f"service_recheck_failed_after_restart:{type(exc).__name__}"
            else:
                return False, f"{reason};{restart_detail}"
        else:
            return False, reason

    try:
        probe = subprocess.run(
            [OPENCLAW, "gateway", "probe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=GATEWAY_PREFLIGHT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "probe_timeout"
    except Exception as exc:
        return False, f"probe_failed:{type(exc).__name__}"

    if probe.returncode != 0:
        combined = ((probe.stdout or "") + "\n" + (probe.stderr or "")).strip()
        reason = combined.splitlines()[-1][:180] if combined else f"probe_returncode:{probe.returncode}"
        return False, "probe_not_ready:" + reason

    return True, "ready"


def defer_gateway_not_ready(state: dict, seq: str, reason: str) -> None:
    record_sustained_soft_gate(state, seq, "gateway_not_ready", reason)
    state.update(
        {
            "last_status": "deferred_gateway_not_ready",
            "last_deferred_seq": seq,
            "last_deferred_at": now_iso(),
            "last_deferred_reason": reason,
        }
    )
    write_json_atomic(STATE_FILE, state)
    log(f"skip seq={seq} reason=deferred_gateway_not_ready detail={reason}")


def context_epoch_session_id(rollover_state: dict | None) -> str:
    if not CONTEXT_ROLLOVER_EPOCH_SESSION_ENABLED or not isinstance(rollover_state, dict):
        return ""
    if not rollover_state.get("active"):
        return ""
    try:
        epoch = int(rollover_state.get("context_epoch") or 0)
    except (TypeError, ValueError):
        return ""
    if epoch <= 0:
        return ""
    return f"{CONTEXT_ROLLOVER_SESSION_PREFIX}-{epoch:04d}"


def resolve_main_session_id(rollover_state: dict | None = None) -> str:
    epoch_session_id = context_epoch_session_id(rollover_state)
    if epoch_session_id:
        return epoch_session_id
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

        codex_text_for_policy = read_text(CODEX_FILE)
        if is_silent_wait_noop(codex_text_for_policy):
            state = read_json(STATE_FILE)
            ok, detail = close_silent_wait_noop_turn(seq, state)
            if ok:
                log(f"closed seq={seq} reason=silent_wait_noop_no_main_trigger detail={detail}")
                return 0
            state.update(
                {
                    "last_status": "silent_wait_noop_suppressed",
                    "last_silent_wait_seq": seq,
                    "last_silent_wait_suppressed_at": now_iso(),
                    "last_deferred_seq": seq,
                    "last_deferred_at": now_iso(),
                    "last_deferred_reason": "silent_wait_noop_no_main_trigger:" + detail,
                }
            )
            write_json_atomic(STATE_FILE, state)
            log(f"skip seq={seq} reason=silent_wait_noop_no_main_trigger detail={detail}")
            return 0

        background_ok, background_reason = allows_background_coordination_progress(turn, codex_text_for_policy)

        guard_active, guard_reason = foreground_guard_active()
        if guard_active:
            state = read_json(STATE_FILE)
            if background_ok:
                record_foreground_priority_bypass(
                    state,
                    seq,
                    "foreground_guard:" + guard_reason,
                    background_reason,
                )
            else:
                defer_foreground_priority(state, seq, "foreground_guard:" + guard_reason)
                return 0

        backlog_active, backlog_reason = foreground_backlog_active()
        if backlog_active:
            state = read_json(STATE_FILE)
            if background_ok:
                record_foreground_priority_bypass(state, seq, backlog_reason, background_reason)
            else:
                defer_foreground_priority(state, seq, backlog_reason)
                return 0

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
        normalize_main_no_tool_fallback_queue(state, seq)

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
            state = read_json(STATE_FILE)
            record_sustained_soft_gate(state, seq, "previous_trigger_still_running", f"pid={last_pid}")
            write_json_atomic(STATE_FILE, state)
            log(f"skip seq={seq} reason=previous_trigger_still_running pid={last_pid}")
            return 0

        queued_no_tool_fallback = [
            item
            for item in state.get("main_local_no_tool_fallback_queue", [])
            if isinstance(item, dict) and str(item.get("seq", "")) == seq
        ]
        if queued_no_tool_fallback:
            exhausted_record = main_no_tool_fallback_retry_budget_exhausted(state, seq)
            if exhausted_record:
                state["last_status"] = "main_no_tool_fallback_retry_exhausted"
                state["last_deferred_seq"] = seq
                state["last_deferred_at"] = now_iso()
                state["main_ark_fallback_last_detail"] = (
                    f"final_fallback_retry_exhausted:"
                    f"{exhausted_record.get('retry_count', 0)}/"
                    f"{exhausted_record.get('max_retries', MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM)}"
                )
                state["main_post_fallback_no_tool"] = True
                record_sustained_soft_gate(state, seq, "main_no_tool_fallback_retry_exhausted", f"retry_count={exhausted_record.get('retry_count', 0)}")
                write_json_atomic(STATE_FILE, state)
                log(
                    f"skip seq={seq} reason=main_no_tool_fallback_retry_exhausted "
                    f"retry_count={exhausted_record.get('retry_count', 0)}"
                )
                return 0

            ark_retry_wait_seconds = main_no_tool_fallback_retry_wait_seconds(state, seq)
            if ark_retry_wait_seconds > 0:
                state["last_status"] = "deferred_main_no_tool_fallback_retry"
                state["last_deferred_seq"] = seq
                state["last_deferred_at"] = now_iso()
                state["main_ark_fallback_last_detail"] = (
                    f"final_fallback_retry_deferred:{ark_retry_wait_seconds}s"
                )
                state["main_post_fallback_no_tool"] = True
                record_sustained_soft_gate(state, seq, "main_no_tool_fallback_retry_deferred", f"wait_seconds={ark_retry_wait_seconds}")
                write_json_atomic(STATE_FILE, state)
                log(
                    f"skip seq={seq} reason=main_no_tool_fallback_retry_deferred "
                    f"wait_seconds={ark_retry_wait_seconds}"
                )
                return 0

            retry_record = mark_main_no_tool_fallback_retrying(state, seq)
            if retry_record:
                state["last_status"] = "main_no_tool_fallback_retrying"
                state["main_local_no_tool_fallback_retrying_record"] = retry_record
                write_json_atomic(STATE_FILE, state)
                log(
                    f"main_no_tool_fallback_retrying seq={seq} "
                    f"retry_count={retry_record.get('retry_count', 0)}"
                )

        rollover_block, rollover_state = context_rollover_prompt_block(seq)
        main_session_id = resolve_main_session_id(rollover_state)
        route_changed = attempts > 0 and str(state.get("last_triggered_session_id", "")) != main_session_id
        last_failure_class = str(seq_state.get("last_failure_class", "") or "")
        log_failure_class = classify_failure_from_run_log(state.get("last_run_log"))
        if log_failure_class == "session_lock_busy" and last_failure_class != log_failure_class:
            last_failure_class = log_failure_class
            seq_state["last_failure_class"] = last_failure_class
            seq_state["last_failure_reclassified_at"] = now_iso()
            attempts_by_seq[seq] = seq_state
            state["attempts_by_seq"] = attempts_by_seq
            state["last_retryable_failure_class"] = last_failure_class
            state["last_retryable_failure_reclassified_from"] = "run_log"
        elif not last_failure_class and log_failure_class:
            last_failure_class = log_failure_class
            seq_state["last_failure_class"] = last_failure_class
            seq_state["last_failure_backfilled_at"] = now_iso()
            attempts_by_seq[seq] = seq_state
            state["attempts_by_seq"] = attempts_by_seq
            state["last_retryable_failure_class"] = last_failure_class

        if not CODEX_FILE.exists() or CODEX_FILE.stat().st_size == 0:
            record_sustained_soft_gate(state, seq, "missing_codex_file", str(CODEX_FILE))
            write_json_atomic(STATE_FILE, state)
            log(f"skip seq={seq} reason=missing_codex_file")
            return 0

        gateway_ready, gateway_reason = gateway_preflight(state)
        if not gateway_ready:
            defer_gateway_not_ready(state, seq, gateway_reason)
            return 0

        gateway_recovered_after_defer = (
            attempts >= MAX_TRIGGER_ATTEMPTS
            and str(state.get("last_status", "")) == "deferred_gateway_not_ready"
            and str(state.get("last_deferred_seq", "")) == seq
        )

        if attempts >= MAX_TRIGGER_ATTEMPTS and not route_changed:
            if (
                should_reset_attempts_after_retryable_failure(
                    attempts,
                    route_changed,
                    last_failure_class,
                )
                or gateway_recovered_after_defer
            ):
                attempts = 0
                last_epoch = 0
                last_pid = None
                reset_reason = (
                    f"gateway_ready_after_{last_failure_class}"
                    if last_failure_class
                    else "gateway_ready_after_deferred_gateway_not_ready"
                )
                seq_state = {
                    "attempts": 0,
                    "last_epoch": 0,
                    "pid": None,
                    "reset_at": now_iso(),
                    "reset_reason": reset_reason,
                    "last_failure_class": last_failure_class,
                }
                attempts_by_seq[seq] = seq_state
                state["attempts_by_seq"] = attempts_by_seq
                state["last_status"] = "retry_reset_after_retryable_failure"
                state["last_retry_reset_seq"] = seq
                state["last_retry_reset_at"] = now_iso()
                state["last_retry_reset_reason"] = reset_reason
                write_json_atomic(STATE_FILE, state)
                log(f"reset_attempts seq={seq} reason={reset_reason}")
            else:
                record_sustained_soft_gate(state, seq, "max_trigger_attempts", f"attempts={attempts}")
                write_json_atomic(STATE_FILE, state)
                log(f"skip seq={seq} reason=max_trigger_attempts attempts={attempts}")
                return 0

        retry_after_seconds = (
            SESSION_LOCK_RETRY_AFTER_SECONDS
            if last_failure_class == "session_lock_busy"
            else RETRY_AFTER_SECONDS
        )
        seconds_since_last = time.time() - last_epoch if last_epoch else retry_after_seconds
        if attempts > 0 and seconds_since_last < retry_after_seconds and not route_changed:
            record_sustained_soft_gate(state, seq, "retry_backoff", f"seconds_since_last={seconds_since_last:.1f}; retry_after_seconds={retry_after_seconds}")
            write_json_atomic(STATE_FILE, state)
            return 0

        message_parts = [
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
        if rollover_block:
            message_parts.extend(["", rollover_block])
        message = "\n".join(message_parts)

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
        active_run_payload = {
            "status": "running",
            "seq": seq,
            "pid": proc.pid,
            "started_at": now_iso(),
            "deadline_at_epoch": round(started_epoch + AGENT_WAIT_TIMEOUT_SECONDS, 3),
            "command_kind": "openclaw_agent_session_json",
            "session_id": main_session_id,
            "run_log": str(run_log_path),
        }
        if rollover_state:
            active_run_payload["context_rollover"] = {
                "context_epoch": rollover_state.get("context_epoch"),
                "rollover_source_seq": rollover_state.get("rollover_source_seq"),
                "summary_path": rollover_state.get("summary_path"),
                "summary_sha256": rollover_state.get("summary_sha256"),
            }
        write_json_atomic(ACTIVE_RUN_FILE, active_run_payload)

        attempt_record = {
            "attempts": attempts + 1,
            "last_epoch": time.time(),
            "pid": proc.pid,
            "last_triggered_at": now_iso(),
        }
        if last_failure_class:
            attempt_record["last_failure_class"] = last_failure_class
        attempts_by_seq[seq] = attempt_record
        if len(attempts_by_seq) > 20:
            attempts_by_seq = dict(list(attempts_by_seq.items())[-20:])

        state_update = {
            "last_triggered_seq": seq,
            "last_triggered_at": now_iso(),
            "last_triggered_pid": proc.pid,
            "last_triggered_session_id": main_session_id,
            "last_run_log": str(run_log_path),
            "last_status": "triggered",
            "turn_file": str(TURN_FILE),
            "attempts_by_seq": attempts_by_seq,
        }
        if rollover_state:
            state_update["last_context_rollover"] = {
                "context_epoch": rollover_state.get("context_epoch"),
                "rollover_source_seq": rollover_state.get("rollover_source_seq"),
                "summary_path": rollover_state.get("summary_path"),
                "summary_sha256": rollover_state.get("summary_sha256"),
            }
        state.update(state_update)
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
            combined_output = "\n".join([stdout or "", stderr or ""])
            failure_class = classify_retryable_startup_failure(combined_output)
            if failure_class:
                attempts_by_seq = state.get("attempts_by_seq")
                if not isinstance(attempts_by_seq, dict):
                    attempts_by_seq = {}
                seq_state = attempts_by_seq.get(seq)
                if not isinstance(seq_state, dict):
                    seq_state = {}
                seq_state["last_failure_class"] = failure_class
                seq_state["last_failure_at"] = observed_at
                seq_state["last_failure_returncode"] = returncode
                attempts_by_seq[seq] = seq_state
                state["attempts_by_seq"] = attempts_by_seq
                state["last_post_trigger_status"] = "retryable_no_advance"
                state["last_retryable_failure_class"] = failure_class

                # --- Main Ark Fallback: when GPT quota is depleted, try Ark ---
                if failure_class == "quota_cooldown":
                    state[MAIN_QUOTA_STATE_KEY] = "depleted"
                    safe_update_agent_room_quota_status(
                        MAIN_AGENT_ID,
                        MAIN_PRIMARY_MODEL,
                        "exhausted",
                        reason="quota_cooldown",
                        fallback_available=MAIN_ARK_FALLBACK_ENABLED,
                        run_id=seq,
                    )
                if failure_class == "quota_cooldown" and MAIN_ARK_FALLBACK_ENABLED:
                    ark_retry_wait_seconds = main_no_tool_fallback_retry_wait_seconds(state, seq)
                    if ark_retry_wait_seconds > 0:
                        state["main_ark_fallback_last_detail"] = (
                            f"final_fallback_retry_deferred:{ark_retry_wait_seconds}s"
                        )
                        state["last_post_trigger_status"] = "ark_fallback_final_retry_deferred"
                        state["main_post_fallback_no_tool"] = True
                        log(
                            f"ark_fallback_final_retry_deferred seq={seq} "
                            f"wait_seconds={ark_retry_wait_seconds}"
                        )
                    else:
                        retry_record = mark_main_no_tool_fallback_retrying(state, seq)
                        if not retry_record:
                            # Retry budget exhausted before this attempt; skip Ark
                            # to avoid one extra attempt beyond the configured limit.
                            # The seq will be skipped on the next tick by the
                            # main_no_tool_fallback_retry_exhausted guard.
                            state["last_post_trigger_status"] = "ark_fallback_retry_budget_exhausted"
                            state["main_ark_fallback_last_detail"] = (
                                "final_fallback_retry_budget_exhausted_before_attempt"
                            )
                            state["main_post_fallback_no_tool"] = True
                            log(
                                f"ark_fallback_budget_exhausted seq={seq} "
                                f"max_retries={MAIN_ARK_FINAL_FALLBACK_MAX_RETRIES_PER_ITEM}"
                            )
                        else:
                            state["main_local_no_tool_fallback_retrying_record"] = retry_record
                            log(
                                f"main_no_tool_fallback_retrying seq={seq} "
                                f"retry_count={retry_record.get('retry_count', 0)}"
                            )
                            loaded_env = load_ark_env_into_process()
                            log(f"attempting_ark_fallback seq={seq} reason=gpt_quota_depleted env_files_loaded={len(loaded_env)}")
                            ark_ok, ark_detail = run_main_via_ark_fallback(message, seq, state)
                            if ark_ok:
                                state["last_post_trigger_status"] = "ark_fallback_advanced"
                                state["main_ark_fallback_last_detail"] = ark_detail
                                clear_main_no_tool_fallback_for_seq(state, seq, "ark_fallback_advanced")
                                write_json_atomic(STATE_FILE, state)
                                write_json_atomic(
                                    ACTIVE_RUN_FILE,
                                    {
                                        "status": "completed_ark_fallback_advanced",
                                        "seq": seq,
                                        "pid": proc.pid,
                                        "finished_at": now_iso(),
                                        "duration_seconds": round(time.time() - started_epoch, 1),
                                        "returncode": returncode,
                                        "failure_class": failure_class,
                                        "ark_fallback_detail": ark_detail,
                                        "run_log": str(run_log_path),
                                    },
                                )
                                log(f"ark_fallback_advanced seq={seq} detail={ark_detail}")
                                return 0
                            else:
                                state["main_ark_fallback_last_detail"] = ark_detail
                                last_fallback_entry = state.get("main_local_no_tool_fallback_last_entry")
                                if (
                                    isinstance(last_fallback_entry, dict)
                                    and str(last_fallback_entry.get("seq", "")) == seq
                                    and str(last_fallback_entry.get("status") or "") == "retry_exhausted"
                                ):
                                    state["last_post_trigger_status"] = "ark_fallback_final_retry_exhausted"
                                else:
                                    state["last_post_trigger_status"] = "ark_fallback_no_tool_queue"
                                state["main_local_no_tool_fallback_last_detail"] = ark_detail
                                state["main_post_fallback_no_tool"] = True
                                log(f"ark_fallback_failed seq={seq} detail={ark_detail}")
            else:
                state["last_post_trigger_status"] = "no_advance"
            write_json_atomic(STATE_FILE, state)
            write_json_atomic(
                ACTIVE_RUN_FILE,
                {
                    "status": "completed_retryable_no_advance" if failure_class else "completed_no_advance",
                    "seq": seq,
                    "pid": proc.pid,
                    "finished_at": observed_at,
                    "duration_seconds": duration_seconds,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "failure_class": failure_class,
                    "run_log": str(run_log_path),
                },
            )
            log(
                f"post_trigger_no_advance seq={seq} pid={proc.pid} "
                f"returncode={returncode} timed_out={timed_out} "
                f"failure_class={failure_class or '-'} run_log={run_log_path}"
            )
        elif (
            seq_before_int is not None
            and seq_after_int is not None
            and seq_after_int <= seq_before_int
        ):
            state["last_post_trigger_status"] = "stale_or_regressed"
            state["last_post_trigger_after_seq"] = after_seq
            state["last_post_trigger_after_needs_reply"] = after_needs_reply
            if queued_no_tool_fallback:
                state["main_local_no_tool_fallback_last_terminal_seq"] = seq
                state["main_local_no_tool_fallback_last_terminal_status"] = "stale_or_regressed"
                state["main_local_no_tool_fallback_last_terminal_at"] = now_iso()
                clear_main_no_tool_fallback_for_seq(state, seq, "stale_or_regressed")
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
            clear_main_no_tool_fallback_for_seq(state, seq, "advanced")
            # --- Auto switch-back: GPT succeeded, clear depleted state ---
            if state.get(MAIN_QUOTA_STATE_KEY) in ("depleted", "depleted_ark_active"):
                previous_quota_state = state.get(MAIN_QUOTA_STATE_KEY)
                state[MAIN_QUOTA_STATE_KEY] = "available"
                state["main_quota_recovered_at"] = now_iso()
                safe_update_agent_room_quota_status(
                    MAIN_AGENT_ID,
                    MAIN_PRIMARY_MODEL,
                    "available",
                    fallback_available=False,
                    active_model=MAIN_PRIMARY_MODEL,
                    run_id=seq,
                )
                log(f"gpt_quota_recovered seq={seq} previous_state={previous_quota_state}")
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
