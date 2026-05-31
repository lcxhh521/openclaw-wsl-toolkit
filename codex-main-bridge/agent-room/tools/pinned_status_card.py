#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import collaboration_status


ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
STATUS_DIR = ROOM / "collaboration-status"
DEFAULT_STATE_PATH = STATUS_DIR / "pinned-card-state.json"
FALLBACK_STATUS_CARD_AGENT_ID = "openclaw-main"
DEFAULT_AGENT_ID = os.environ.get("OPENCLAW_PINNED_STATUS_AGENT_ID", FALLBACK_STATUS_CARD_AGENT_ID)
STATUS_CARD_OWNER_BLOCKER = f"status_card_owner_must_be_{FALLBACK_STATUS_CARD_AGENT_ID}"
BOT_META = ROOM / "telegram_agent_bots.json"
ROOM_BINDINGS = ROOM / "telegram-room-bindings.json"
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/home/lcxhh/.local/bin/openclaw")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def room_binding_bot_for_agent(agent_id: str) -> str | None:
    bindings = read_json(ROOM_BINDINGS, {})
    if not isinstance(bindings, dict):
        return None
    for row in bindings.get("participants") or []:
        if isinstance(row, dict) and row.get("agent_id") == agent_id:
            bot = str(row.get("telegram_bot") or "").strip()
            return bot or None
    return None


def room_bound_agent_ids(room_id: str) -> list[str]:
    bindings = read_json(ROOM_BINDINGS, {})
    if not isinstance(bindings, dict):
        return []
    binding_room_id = str(bindings.get("room_id") or "")
    if binding_room_id and binding_room_id != room_id:
        return []
    agents: list[str] = []
    for row in bindings.get("participants") or []:
        if not isinstance(row, dict):
            continue
        agent_id = str(row.get("agent_id") or "").strip()
        if agent_id and agent_id not in agents:
            agents.append(agent_id)
    return agents


def bot_rows() -> list[dict[str, Any]]:
    meta = read_json(BOT_META, {})
    if not isinstance(meta, dict):
        return []
    return [row for row in (meta.get("bots") or []) if isinstance(row, dict)]


def bot_metadata_for_agent(agent_id: str) -> dict[str, Any] | None:
    for row in bot_rows():
        if row.get("agent_id") == agent_id:
            return row
    return None


def activation_ready_for_agent(agent_id: str, chat_id: str) -> bool:
    return bool(activation_preflight(agent_id, chat_id).get("can_attempt_live"))


def status_card_agent_candidates(room_id: str) -> list[str]:
    room_agents = set(room_bound_agent_ids(room_id))
    candidates: list[str] = []
    for row in bot_rows():
        agent_id = str(row.get("agent_id") or "").strip()
        if not agent_id:
            continue
        if room_agents and agent_id not in room_agents:
            continue
        if agent_id not in candidates:
            candidates.append(agent_id)
    for agent_id in room_bound_agent_ids(room_id):
        if agent_id not in candidates:
            candidates.append(agent_id)
    return candidates


def state_owner_agent_id(state: Any, *, room_id: str, chat_id: str) -> str:
    if not isinstance(state, dict):
        return ""
    message_id = str(state.get("message_id") or "").strip()
    agent_id = str(state.get("agent_id") or "").strip()
    if not message_id or not agent_id:
        return ""
    state_room_id = str(state.get("room_id") or "").strip()
    state_chat_id = str(state.get("chat_id") or "").strip()
    if state_room_id and state_room_id != room_id:
        return ""
    if state_chat_id and state_chat_id != chat_id:
        return ""
    return agent_id


def resolve_status_card_agent_id(agent_id: str, *, chat_id: str, room_id: str, state_path: Path) -> str:
    requested = str(agent_id or "").strip()
    if requested and requested.lower() != "auto":
        return requested

    return FALLBACK_STATUS_CARD_AGENT_ID


def pinned_status_transport_for_agent(agent_id: str, bot_meta: dict[str, Any] | None = None) -> str:
    if isinstance(bot_meta, dict):
        explicit = str(bot_meta.get("pinned_status_transport") or "").strip()
        if explicit:
            return explicit
        if agent_id == "openclaw-main":
            return "openclaw_cli"
    return "telegram_bot_api"


def openclaw_cli_available() -> bool:
    candidate = Path(OPENCLAW_BIN)
    if candidate.is_absolute():
        return candidate.exists() and os.access(candidate, os.X_OK)
    return bool(shutil.which(OPENCLAW_BIN))


def parse_json_from_output(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
    return None


def run_openclaw_message(args: list[str], *, timeout: int = 45) -> dict[str, Any]:
    command = [OPENCLAW_BIN, "message", *args, "--json"]
    try:
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__ + ": " + str(exc), "command": command[:3] + ["..."]}
    payload = parse_json_from_output(proc.stdout)
    payload_ok = True
    if isinstance(payload, dict) and payload.get("ok") is False:
        payload_ok = False
    return {
        "ok": proc.returncode == 0 and payload_ok,
        "exit_code": proc.returncode,
        "payload": payload,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "command": command[:3] + ["..."],
    }


def extract_message_id(value: Any) -> str:
    preferred = ("message_id", "messageId", "messageID")
    if isinstance(value, dict):
        for key in preferred:
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        result = value.get("result")
        if result is not None:
            found = extract_message_id(result)
            if found:
                return found
        message = value.get("message")
        if message is not None:
            found = extract_message_id(message)
            if found:
                return found
        for item in value.values():
            found = extract_message_id(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = extract_message_id(item)
            if found:
                return found
    return ""


def result_description(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("description", "error", "stderr_tail", "stdout_tail"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        payload = value.get("payload")
        if isinstance(payload, dict):
            return result_description(payload)
        api_result = value.get("api")
        if isinstance(api_result, dict):
            return result_description(api_result)
    return ""


def edit_message_not_modified(value: Any) -> bool:
    return "message is not modified" in result_description(value).lower()


STALE_EDIT_FAILURE_MARKERS = (
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "message_id_invalid",
    "message identifier is not specified",
)

TRANSIENT_EDIT_FAILURE_MARKERS = (
    "urlopen error",
    "unexpected_eof",
    "eof occurred",
    "ssl:",
    "timed out",
    "timeout",
    "connection",
    "temporarily unavailable",
    "too many requests",
    "retry after",
    "bad gateway",
    "gateway timeout",
    "internal server error",
)


def edit_failure_kind(value: Any) -> str:
    if edit_message_not_modified(value):
        return "not_modified"
    description = result_description(value).lower()
    if any(marker in description for marker in STALE_EDIT_FAILURE_MARKERS):
        return "stale_message"
    if any(marker in description for marker in TRANSIENT_EDIT_FAILURE_MARKERS):
        return "transient_failure"
    return "unknown_failure"


def edit_failure_allows_recreate(value: Any) -> bool:
    return edit_failure_kind(value) == "stale_message"


def telegram_get_chat(tar: Any, token: str, chat_id: str) -> dict[str, Any]:
    return tar._telegram_api_call(token, "getChat", {"chat_id": chat_id})


def pinned_message_id_from_get_chat(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    result = value.get("result") if isinstance(value.get("result"), dict) else value
    if not isinstance(result, dict):
        return ""
    pinned = result.get("pinned_message") or result.get("pinnedMessage")
    if isinstance(pinned, dict):
        message_id = pinned.get("message_id") or pinned.get("messageId")
        if message_id is not None and str(message_id).strip():
            return str(message_id).strip()
    return ""


def ensure_telegram_pin(tar: Any, token: str, chat_id: str, message_id: str | int) -> dict[str, Any]:
    expected = str(message_id).strip()
    steps: list[dict[str, Any]] = []

    before = telegram_get_chat(tar, token, chat_id)
    before_id = pinned_message_id_from_get_chat(before)
    steps.append({"step": "getChatPinnedBefore", "ok": before.get("ok"), "pinned_message_id": before_id})
    if before.get("ok") and before_id == expected:
        return {"ok": True, "already_pinned": True, "pinned_message_id": before_id, "steps": steps}

    pin_result = tar.pin_chat_message(token, chat_id, expected, disable_notification=True)
    steps.append({"step": "pinChatMessage", "ok": pin_result.get("ok"), "api": pin_result})

    after = telegram_get_chat(tar, token, chat_id)
    after_id = pinned_message_id_from_get_chat(after)
    ok = bool(after.get("ok") and after_id == expected)
    steps.append({"step": "getChatPinnedAfter", "ok": after.get("ok"), "pinned_message_id": after_id, "expected_message_id": expected})
    return {"ok": ok, "already_pinned": False, "pinned_message_id": after_id, "expected_message_id": expected, "steps": steps}


STATUS_CARD_PIN_MARKERS = (
    "📌 OpenClaw 状态",
    "📌 OpenClaw Room 状态",
    "OpenClaw 状态",
    "OpenClaw Room 状态",
    "细节在本地 status",
    "这里只编辑这一条",
)


def dedupe_text_values(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def collect_message_ids(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"message_id", "messageId", "messageID", "pinned_message_id", "pinnedMessageId"}:
                text = str(item or "").strip()
                if text:
                    ids.append(text)
            if isinstance(item, (dict, list)):
                ids.extend(collect_message_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(collect_message_ids(item))
    return dedupe_text_values(ids)


def status_card_pin_text(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "caption", "message"):
            item = value.get(key)
            if isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def looks_like_status_card_pin(value: Any) -> bool:
    text = status_card_pin_text(value)
    return any(marker in text for marker in STATUS_CARD_PIN_MARKERS)


def status_card_pin_message_ids(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        if looks_like_status_card_pin(value):
            ids.extend(collect_message_ids(value))
        for item in value.values():
            if isinstance(item, (dict, list)):
                ids.extend(status_card_pin_message_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(status_card_pin_message_ids(item))
    return dedupe_text_values(ids)


def unpin_telegram_message(tar: Any, token: str, chat_id: str, message_id: str) -> dict[str, Any]:
    if hasattr(tar, "unpin_chat_message"):
        return tar.unpin_chat_message(token, chat_id, message_id)
    return tar._telegram_api_call(token, "unpinChatMessage", {"chat_id": chat_id, "message_id": message_id})


BENIGN_UNPIN_FAILURE_MARKERS = (
    "message is not pinned",
    "message to unpin not found",
    "message not found",
    "message_id_invalid",
)

LOCAL_PIN_ID_RE = re.compile(r"\b(?:message_id|actual_pinned_message_id|pinned_message_id)\s*[=:]\s*[\"']?(\d+)\b")
LOCAL_STATUS_CARD_HISTORY_LIMIT = 2000


def unpin_ok_or_not_needed(value: Any) -> bool:
    if isinstance(value, dict) and value.get("ok"):
        return True
    description = result_description(value).lower()
    return any(marker in description for marker in BENIGN_UNPIN_FAILURE_MARKERS)


def recent_local_paths(paths: list[Path], *, limit: int = LOCAL_STATUS_CARD_HISTORY_LIMIT) -> list[Path]:
    return sorted(paths)[-limit:]


def status_card_run_message_ids(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    ids: list[str] = []
    for key in ("message_id", "actual_pinned_message_id", "pinned_message_id"):
        item = value.get(key)
        if item is not None and str(item).strip():
            ids.append(str(item).strip())
    return dedupe_text_values(ids)


def local_status_card_history_message_ids() -> list[str]:
    paths: list[Path] = []
    paths.extend(recent_local_paths(list((ROOM / "resident-runs").glob("*/result.json"))))
    paths.extend(recent_local_paths(list((ROOM / "daemon-runs" / "telegram-agent-bridge").glob("*/tick-*/pinned-card-tick.json"))))
    ids: list[str] = []
    for path in paths:
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            continue
        pinned_card = payload.get("pinned_card")
        if isinstance(pinned_card, dict):
            ids.extend(status_card_run_message_ids(pinned_card))
        result = payload.get("result")
        if isinstance(result, dict):
            ids.extend(status_card_run_message_ids(result))
    return dedupe_text_values(ids)


def local_known_status_card_message_ids() -> list[str]:
    paths = list(STATUS_DIR.glob("pinned-card*.json"))
    artifacts = ROOM / "artifacts"
    if artifacts.exists():
        paths.extend(sorted(artifacts.glob("pinned-card*.json")))
        paths.extend(sorted(artifacts.glob("pinned-card*.md")))
    ids: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if path.suffix == ".json":
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
            if payload is not None:
                ids.extend(collect_message_ids(payload))
        ids.extend(match.group(1) for match in LOCAL_PIN_ID_RE.finditer(text))
    ids.extend(local_status_card_history_message_ids())
    return dedupe_text_values(ids)


def pin_cleanup_state() -> dict[str, Any]:
    state = read_json(STATUS_DIR / "pinned-card-cleanup-state.json", {})
    return state if isinstance(state, dict) else {}


def remember_cleaned_pin_ids(ids: list[str]) -> None:
    if not ids:
        return
    state = pin_cleanup_state()
    cleaned = dedupe_text_values([*list(state.get("cleaned_message_ids") or []), *ids])
    state.update({
        "schema": "openclaw.agent_room.pinned_status_card_pin_cleanup_state.v0",
        "updated_at": now_iso(),
        "cleaned_message_ids": cleaned[-200:],
    })
    write_json(STATUS_DIR / "pinned-card-cleanup-state.json", state)


def cleanup_extra_status_card_pins(
    *,
    chat_id: str,
    keep_message_id: str | int,
    tar: Any | None = None,
    token: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    keep = str(keep_message_id or "").strip()
    result: dict[str, Any] = {
        "schema": "openclaw.agent_room.pinned_status_card_pin_cleanup.v0",
        "created_at": now_iso(),
        "chat_id": chat_id,
        "keep_message_id": keep,
        "ok": False,
        "status": "not_run",
        "steps": [],
        "candidate_message_ids": [],
        "unpin_message_ids": [],
    }
    if not keep:
        result["status"] = "missing_keep_message_id"
        return result

    cli_available = openclaw_cli_available()
    cleaned_ids = set(str(value) for value in (pin_cleanup_state().get("cleaned_message_ids") or []))
    if cli_available:
        pins_result = run_openclaw_message([
            "pins", "--channel", "telegram", "--target", chat_id, "--limit", str(limit),
        ])
    else:
        pins_result = {
            "ok": False,
            "error": "openclaw_cli_unavailable",
            "description": "openclaw CLI unavailable; falling back to locally known status-card message ids",
        }
    result["steps"].append({"step": "openclaw.message.pins", "ok": pins_result.get("ok"), "cli": pins_result})
    if pins_result.get("ok"):
        pins_payload = pins_result.get("payload") if pins_result.get("payload") is not None else pins_result
        candidates = [message_id for message_id in status_card_pin_message_ids(pins_payload) if message_id != keep]
        candidate_source = "listed_status_card_pins"
    else:
        local_ids = local_known_status_card_message_ids()
        candidates = [message_id for message_id in local_ids if message_id != keep and message_id not in cleaned_ids]
        candidate_source = "local_known_status_card_ids_after_pin_list_failed" if cli_available else "local_known_status_card_ids_cli_unavailable"
        result["local_known_message_ids"] = local_ids
        result["pin_list_error"] = result_description(pins_result)
    result["candidate_message_ids"] = candidates
    result["candidate_source"] = candidate_source
    if not candidates:
        if pins_result.get("ok"):
            result["status"] = "no_extra_status_card_pins"
            result["ok"] = True
        elif result.get("local_known_message_ids"):
            result["status"] = "pin_list_failed_local_candidates_already_cleaned"
            result["ok"] = True
        else:
            result["status"] = "pin_list_failed_no_local_candidates"
            result["ok"] = False
        return result

    if not ((tar is not None and token) or cli_available):
        result["status"] = "pin_cleanup_no_unpin_transport"
        result["ok"] = False
        return result

    result["unpin_message_ids"] = candidates
    all_ok = True
    cleaned_now: list[str] = []
    for message_id in candidates:
        if tar is not None and token:
            unpin_result = unpin_telegram_message(tar, token, chat_id, message_id)
            step_ok = unpin_ok_or_not_needed(unpin_result)
            step = {"step": "telegram.unpinChatMessage", "message_id": message_id, "ok": step_ok, "api": unpin_result}
        else:
            unpin_result = run_openclaw_message([
                "unpin", "--channel", "telegram", "--target", chat_id, "--message-id", message_id,
            ])
            step_ok = unpin_ok_or_not_needed(unpin_result)
            step = {"step": "openclaw.message.unpin", "message_id": message_id, "ok": step_ok, "cli": unpin_result}
        result["steps"].append(step)
        if not step.get("ok"):
            all_ok = False
        else:
            cleaned_now.append(message_id)
    remember_cleaned_pin_ids(cleaned_now)
    result["ok"] = all_ok
    result["status"] = "extra_status_card_pins_unpinned" if all_ok else "extra_status_card_pin_cleanup_failed"
    return result


def append_pin_cleanup_step(result: dict[str, Any], cleanup: dict[str, Any], *, step: str) -> None:
    result["steps"].append(
        {
            "step": step,
            "ok": cleanup.get("ok"),
            "status": cleanup.get("status"),
            "candidate_source": cleanup.get("candidate_source"),
            "candidate_message_ids": cleanup.get("candidate_message_ids"),
            "unpin_message_ids": cleanup.get("unpin_message_ids"),
        }
    )


STATUS_CARD_FORBIDDEN_MARKERS = (
    "# Telegram Agent Room Task",
    "## User message",
    "## Incoming message triage",
    "## Boundaries",
    "协作:",
    "租约过期",
    "降级",
    "runner异常",
    "degraded_quorum",
    "needs_attention",
    "target_agents:",
    "update_id:",
    "Full boundary list:",
    "reply_policy",
    "broadcast_targets",
    "canonical_state_advanced",
    "active_runner_default",
    "mainline_id",
    "problem_statement",
    "expected_user_value",
    "definition_of_done",
    "approval_gate",
    "dedupe_key",
    "drift_check_passed",
)


def validate_fixed_status_card(card: dict[str, Any]) -> dict[str, Any]:
    text = str((card or {}).get("text") or "").strip()
    rows = (card or {}).get("rows") if isinstance((card or {}).get("rows"), list) else []
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    reasons: list[str] = []
    if (card or {}).get("schema") != "openclaw.agent_room.fixed_status_card.v0":
        reasons.append("wrong_schema")
    if not text.startswith("📌 OpenClaw 状态 "):
        reasons.append("missing_status_header")
    if len(nonempty_lines) > 5:
        reasons.append("too_many_visible_lines")
    if len(text) > 700:
        reasons.append("too_long_for_one_glance_card")
    for marker in STATUS_CARD_FORBIDDEN_MARKERS:
        if marker in text:
            reasons.append(f"forbidden_marker:{marker}")
    required_labels = ("main", "Codex", "Claude Code")
    for label in required_labels:
        if label not in text:
            reasons.append(f"missing_agent_label:{label}")
    row_ids = [str(row.get("agent_id") or "") for row in rows if isinstance(row, dict)]
    if row_ids[:3] != ["openclaw-main", "codex", "claude-code"]:
        reasons.append("wrong_row_order")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "line_count": len(nonempty_lines),
        "text_length": len(text),
    }


def activation_preflight(agent_id: str, chat_id: str) -> dict[str, Any]:
    bot_meta = bot_metadata_for_agent(agent_id)
    transport = pinned_status_transport_for_agent(agent_id, bot_meta)
    token_ref = str((bot_meta or {}).get("token_secret_ref") or "").strip()
    secret_verified = (bot_meta or {}).get("secret_verified") if isinstance(bot_meta, dict) else None
    blockers: list[str] = []
    if agent_id != FALLBACK_STATUS_CARD_AGENT_ID:
        blockers.append(STATUS_CARD_OWNER_BLOCKER)
    if not bot_meta:
        blockers.append("missing_agent_room_bot_metadata")
    elif transport == "telegram_bot_api":
        if not token_ref:
            blockers.append("missing_token_secret_ref")
        if secret_verified is False:
            blockers.append("secret_not_verified")
    elif transport == "openclaw_cli":
        if not openclaw_cli_available():
            blockers.append("missing_openclaw_cli")
    else:
        blockers.append(f"unsupported_transport:{transport}")
    if not str(chat_id or "").strip():
        blockers.append("missing_chat_id")
    return {
        "schema": "openclaw.agent_room.pinned_status_card_activation_preflight.v0",
        "agent_id": agent_id,
        "chat_id": chat_id,
        "bot_meta_path": str(BOT_META),
        "room_bindings_path": str(ROOM_BINDINGS),
        "room_binding_bot": room_binding_bot_for_agent(agent_id),
        "registered_in_agent_room_bot_meta": bool(bot_meta),
        "transport": transport,
        "openclaw_bin": OPENCLAW_BIN if transport == "openclaw_cli" else None,
        "token_secret_ref_present": bool(token_ref),
        "secret_verified": secret_verified,
        "can_attempt_live": not blockers,
        "blockers": blockers,
        "permission_check_still_required": [
            "bot membership/admin state in the target Telegram group",
            "can_pin_messages for first activation; editMessageText for later updates",
        ],
    }


def pin_edit_actions(chat_id: str, text: str, message_id: str | None) -> list[dict[str, Any]]:
    text_payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if message_id:
        return [
            {
                "method": "editMessageText",
                "payload": {
                    **text_payload,
                    "message_id": message_id,
                },
                "purpose": "update the existing fixed status card without sending a new room message",
            }
        ]
    return [
        {
            "method": "sendMessage",
            "payload": text_payload,
            "purpose": "create the fixed status card message once",
        },
        {
            "method": "pinChatMessage",
            "payload": {
                "chat_id": chat_id,
                "message_id": "<sendMessage.result.message_id>",
                "disable_notification": True,
            },
            "depends_on": "sendMessage.result.message_id",
            "purpose": "pin the fixed status card after first creation",
        },
    ]


def permission_check_actions(chat_id: str) -> list[dict[str, Any]]:
    return permission_check_actions_for_agent(chat_id, "auto")


def permission_check_actions_for_agent(chat_id: str, agent_id: str) -> list[dict[str, Any]]:
    bot_label = room_binding_bot_for_agent(agent_id) or agent_id or "selected status-card bot"
    return [
        {
            "method": "getChatAdministrators",
            "payload": {"chat_id": chat_id, "return_bots": True},
            "acceptance": f"{bot_label} appears as administrator",
        },
        {
            "method": "getChatMember",
            "payload": {"chat_id": chat_id, "user_id": f"<{agent_id}_bot_user_id>"},
            "acceptance": "status is administrator and can_pin_messages is true for this group/supergroup",
        },
    ]


def state_message_for_agent(state: Any, agent_id: str) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(state, dict):
        return "", None
    message_id = str(state.get("message_id") or "").strip()
    if not message_id:
        return "", None
    state_agent_id = str(state.get("agent_id") or "").strip()
    if state_agent_id and state_agent_id != agent_id:
        return "", {
            "message_id": message_id,
            "agent_id": state_agent_id,
            "reason": "state_message_owned_by_different_bot_identity",
        }
    return message_id, None


def build_projection(*, room_id: str, chat_id: str, message_id: str | None, state_path: Path, agent_id: str) -> dict[str, Any]:
    requested_agent_id = agent_id
    agent_id = resolve_status_card_agent_id(agent_id, chat_id=chat_id, room_id=room_id, state_path=state_path)
    status = collaboration_status.build_status(room_id=room_id, include_background=True)
    status["fixed_status_card"] = collaboration_status.fixed_status_card(status, room_id=room_id, chat_id=chat_id)
    card = status["fixed_status_card"]
    text = str(card.get("text") or "")
    content_validation = validate_fixed_status_card(card)
    existing_state = read_json(state_path, {})
    existing_message_id, ignored_state = state_message_for_agent(existing_state, agent_id)
    effective_message_id = message_id or existing_message_id or None
    actions = pin_edit_actions(chat_id, text, effective_message_id) if content_validation.get("ok") else []
    preflight = activation_preflight(agent_id, chat_id)
    if not content_validation.get("ok"):
        preflight = dict(preflight)
        preflight["can_attempt_live"] = False
        preflight["blockers"] = [*list(preflight.get("blockers") or []), "invalid_status_card_text"]
    return {
        "schema": "openclaw.agent_room.pinned_status_card_projection.v0",
        "created_at": now_iso(),
        "room_id": room_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "requested_agent_id": requested_agent_id,
        "state_path": str(state_path),
        "mode": "edit_existing" if effective_message_id else "create_then_pin",
        "message_id": effective_message_id,
        "ignored_state": ignored_state,
        "activation_ready": bool(preflight.get("can_attempt_live")),
        "activation_preflight": preflight,
        "content_validation": content_validation,
        "telegram_outbound": False,
        "dry_run": True,
        "status_card_text_path": str(STATUS_DIR / "fixed-card-preview.txt"),
        "permission_requirements": {
            "send_or_edit": "bot must be able to send messages; editMessageText updates messages sent by the bot",
            "pin": "selected bot must be administrator with can_pin_messages in a group/supergroup; channels require can_edit_messages",
            "verification": permission_check_actions_for_agent(chat_id, agent_id),
        },
        "actions": actions,
        "fixed_status_card": card,
        "tokens_printed": False,
    }


def execute_live_openclaw_cli(*, room_id: str, chat_id: str, state_path: Path, agent_id: str, preflight: dict[str, Any]) -> dict[str, Any]:
    existing_state = read_json(state_path, {}) or {}
    existing_message_id, ignored_state = state_message_for_agent(existing_state, agent_id)

    status = collaboration_status.build_status(room_id=room_id, include_background=True)
    status["fixed_status_card"] = collaboration_status.fixed_status_card(status, room_id=room_id, chat_id=chat_id)
    card = status["fixed_status_card"]
    text = str(card.get("text") or "")
    content_validation = validate_fixed_status_card(card)

    result: dict[str, Any] = {
        "schema": "openclaw.agent_room.pinned_status_card_live.v0",
        "created_at": now_iso(),
        "room_id": room_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "transport": "openclaw_cli",
        "activation_preflight": preflight,
        "content_validation": content_validation,
        "had_message_id": bool(existing_message_id),
        "ignored_state": ignored_state,
        "steps": [],
        "ok": False,
    }

    if not content_validation.get("ok"):
        result["error"] = "invalid_status_card_text"
        return result

    if existing_message_id:
        cleanup = cleanup_extra_status_card_pins(
            chat_id=chat_id,
            keep_message_id=existing_message_id,
        )
        result["pin_cleanup_pre_update"] = cleanup
        append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup_pre_update")

        edit_result = run_openclaw_message([
            "edit", "--channel", "telegram", "--target", chat_id,
            "--message-id", existing_message_id, "--message", text,
        ])
        edit_noop = edit_message_not_modified(edit_result)
        edit_synced = bool(edit_result.get("ok") or edit_noop)
        edit_step: dict[str, Any] = {"step": "openclaw.message.edit", "ok": edit_synced, "cli": edit_result}
        if edit_noop:
            edit_step["no_op"] = True
        result["steps"].append(edit_step)
        if edit_synced:
            existing_state["message_id"] = existing_message_id
            existing_state["chat_id"] = chat_id
            existing_state["room_id"] = room_id
            existing_state["agent_id"] = agent_id
            existing_state["telegram_bot"] = preflight.get("room_binding_bot")
            existing_state["updated_at"] = now_iso()
            existing_state["transport"] = "openclaw_cli"
            write_json(state_path, existing_state)
            if text:
                (STATUS_DIR / "fixed-card-preview.txt").write_text(text + "\n", encoding="utf-8")
            result["ok"] = True
            result["message_id"] = existing_message_id
            cleanup = cleanup_extra_status_card_pins(
                chat_id=chat_id,
                keep_message_id=existing_message_id,
            )
            result["pin_cleanup"] = cleanup
            append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup")
            if edit_noop:
                result["steps"].append({"step": "editMessageTextNoop", "ok": True, "reason": "message_not_modified"})
            return result
        failure_kind = edit_failure_kind(edit_result)
        if not edit_failure_allows_recreate(edit_result):
            result["message_id"] = existing_message_id
            result["error"] = "edit_existing_failed_no_recreate"
            result["edit_failure_kind"] = failure_kind
            result["duplicate_prevention"] = "kept_existing_status_card_state"
            result["steps"].append(
                {
                    "step": "edit_failed_no_recreate",
                    "reason": edit_result.get("stderr_tail") or edit_result.get("stdout_tail"),
                    "failure_kind": failure_kind,
                }
            )
            return result
        result["steps"].append(
            {
                "step": "edit_failed_fallback_to_send_pin",
                "reason": edit_result.get("stderr_tail") or edit_result.get("stdout_tail"),
                "failure_kind": failure_kind,
            }
        )

    send_result = run_openclaw_message([
        "send", "--channel", "telegram", "--target", chat_id,
        "--message", text, "--pin", "--silent",
    ], timeout=60)
    result["steps"].append({"step": "openclaw.message.send_pin", "ok": send_result.get("ok"), "cli": send_result})
    if not send_result.get("ok"):
        result["error"] = "send_pin_failed"
        return result

    new_message_id = extract_message_id(send_result.get("payload")) or extract_message_id(send_result)
    if not new_message_id:
        pins_result = run_openclaw_message([
            "pins", "--channel", "telegram", "--target", chat_id, "--limit", "5",
        ])
        result["steps"].append({"step": "openclaw.message.pins", "ok": pins_result.get("ok"), "cli": pins_result})
        new_message_id = extract_message_id(pins_result.get("payload")) or extract_message_id(pins_result)

    if not new_message_id:
        result["error"] = "send_pin_ok_but_no_message_id"
        return result

    new_state = {
        "message_id": new_message_id,
        "chat_id": chat_id,
        "room_id": room_id,
        "agent_id": agent_id,
        "telegram_bot": preflight.get("room_binding_bot"),
        "transport": "openclaw_cli",
        "updated_at": now_iso(),
    }
    write_json(state_path, new_state)
    if text:
        (STATUS_DIR / "fixed-card-preview.txt").write_text(text + "\n", encoding="utf-8")
    result["ok"] = True
    result["message_id"] = new_message_id
    result["pin_ok"] = True
    cleanup = cleanup_extra_status_card_pins(
        chat_id=chat_id,
        keep_message_id=new_message_id,
    )
    result["pin_cleanup"] = cleanup
    append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup")
    return result


def execute_live(*, room_id: str, chat_id: str, state_path: Path, agent_id: str, _telegram_helpers: Any | None = None) -> dict[str, Any]:
    """Actually send or edit the pinned status card via Telegram Bot API.

    Flow:
    1. Build the status card text.
    2. If we have a saved message_id from state, edit that message.
    3. If no saved message_id, sendMessage then pinChatMessage.
    4. Persist the message_id in state for future edits.
    """
    requested_agent_id = agent_id
    agent_id = resolve_status_card_agent_id(agent_id, chat_id=chat_id, room_id=room_id, state_path=state_path)

    if _telegram_helpers is None:
        # Import telegram helpers from sibling module.
        import importlib.util
        reply_path = Path(__file__).resolve().parent / "telegram_agent_reply.py"
        spec = importlib.util.spec_from_file_location("telegram_agent_reply", str(reply_path))
        tar = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tar)
    else:
        tar = _telegram_helpers

    preflight = activation_preflight(agent_id, chat_id)
    if not preflight.get("can_attempt_live"):
        return {
            "schema": "openclaw.agent_room.pinned_status_card_live.v0",
            "created_at": now_iso(),
            "room_id": room_id,
            "chat_id": chat_id,
            "agent_id": agent_id,
            "requested_agent_id": requested_agent_id,
            "ok": False,
            "error": "activation_preflight_failed",
            "activation_preflight": preflight,
        }

    if preflight.get("transport") == "openclaw_cli":
        return execute_live_openclaw_cli(
            room_id=room_id,
            chat_id=chat_id,
            state_path=state_path,
            agent_id=agent_id,
            preflight=preflight,
        )

    try:
        token = tar.bot_token(agent_id)
    except RuntimeError as exc:
        return {
            "schema": "openclaw.agent_room.pinned_status_card_live.v0",
            "created_at": now_iso(),
            "room_id": room_id,
            "chat_id": chat_id,
            "agent_id": agent_id,
            "requested_agent_id": requested_agent_id,
            "ok": False,
            "error": "token_resolution_failed",
            "error_detail": str(exc),
            "activation_preflight": preflight,
        }
    if not token:
        return {"ok": False, "error": "no_bot_token", "agent_id": agent_id}

    existing_state = read_json(state_path, {}) or {}
    existing_message_id, ignored_state = state_message_for_agent(existing_state, agent_id)

    status = collaboration_status.build_status(room_id=room_id, include_background=True)
    status["fixed_status_card"] = collaboration_status.fixed_status_card(status, room_id=room_id, chat_id=chat_id)
    card = status["fixed_status_card"]
    text = str(card.get("text") or "")
    content_validation = validate_fixed_status_card(card)

    result: dict[str, Any] = {
        "schema": "openclaw.agent_room.pinned_status_card_live.v0",
        "created_at": now_iso(),
        "room_id": room_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "requested_agent_id": requested_agent_id,
        "activation_preflight": preflight,
        "content_validation": content_validation,
        "had_message_id": bool(existing_message_id),
        "ignored_state": ignored_state,
        "steps": [],
        "ok": False,
    }

    if not content_validation.get("ok"):
        result["error"] = "invalid_status_card_text"
        return result

    if existing_message_id:
        cleanup = cleanup_extra_status_card_pins(
            chat_id=chat_id,
            keep_message_id=existing_message_id,
            tar=tar,
            token=token,
        )
        result["pin_cleanup_pre_update"] = cleanup
        append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup_pre_update")

        # Edit existing pinned message
        edit_result = tar.edit_message_text(token, chat_id, existing_message_id, text)
        edit_noop = edit_message_not_modified(edit_result)
        edit_synced = bool(edit_result.get("ok") or edit_noop)
        edit_step: dict[str, Any] = {"step": "editMessageText", "ok": edit_synced, "api": edit_result}
        if edit_noop:
            edit_step["no_op"] = True
        result["steps"].append(edit_step)
        if edit_synced:
            result["message_id"] = existing_message_id
            if edit_noop:
                result["steps"].append({"step": "editMessageTextNoop", "ok": True, "reason": "message_not_modified"})
            pin_check = ensure_telegram_pin(tar, token, chat_id, existing_message_id)
            result["steps"].extend(pin_check.get("steps") or [])
            result["pin_ok"] = bool(pin_check.get("ok"))
            result["actual_pinned_message_id"] = pin_check.get("pinned_message_id")
            result["ok"] = bool(pin_check.get("ok"))
            existing_state["pin_verified"] = bool(pin_check.get("ok"))
            existing_state["pin_verified_by"] = "telegram.getChat"
            if pin_check.get("ok"):
                existing_state["pinned_at"] = now_iso()
                cleanup = cleanup_extra_status_card_pins(
                    chat_id=chat_id,
                    keep_message_id=existing_message_id,
                    tar=tar,
                    token=token,
                )
                result["pin_cleanup"] = cleanup
                append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup")
            else:
                result["error"] = "pin_verification_failed"
                existing_state["pin_verification_error"] = {
                    "checked_at": now_iso(),
                    "expected_message_id": existing_message_id,
                    "actual_pinned_message_id": pin_check.get("pinned_message_id"),
                }
        else:
            failure_kind = edit_failure_kind(edit_result)
            if not edit_failure_allows_recreate(edit_result):
                result["message_id"] = existing_message_id
                result["error"] = "edit_existing_failed_no_recreate"
                result["edit_failure_kind"] = failure_kind
                result["duplicate_prevention"] = "kept_existing_status_card_state"
                result["steps"].append(
                    {
                        "step": "edit_failed_no_recreate",
                        "reason": result_description(edit_result),
                        "failure_kind": failure_kind,
                    }
                )
                return result
            # Only a definitive stale-message error may create a replacement card.
            result["steps"].append(
                {
                    "step": "edit_failed_fallback_to_send",
                    "reason": result_description(edit_result),
                    "failure_kind": failure_kind,
                }
            )
            existing_message_id = ""

    if not existing_message_id:
        # Send new message then pin
        send_result = tar.send_message(token, chat_id, text)
        result["steps"].append({"step": "sendMessage", "ok": send_result.get("ok"), "api": send_result})
        if not send_result.get("ok"):
            result["error"] = "send_failed"
            return result

        new_message_id = str((send_result.get("result") or {}).get("message_id", ""))
        if not new_message_id:
            result["error"] = "send_ok_but_no_message_id"
            return result

        pin_check = ensure_telegram_pin(tar, token, chat_id, new_message_id)
        result["steps"].extend(pin_check.get("steps") or [])

        pin_ok = bool(pin_check.get("ok"))
        new_state = {
            "message_id": new_message_id,
            "chat_id": chat_id,
            "room_id": room_id,
            "agent_id": agent_id,
            "telegram_bot": preflight.get("room_binding_bot"),
            "transport": "telegram_bot_api",
            "pin_verified": pin_ok,
            "pin_verified_by": "telegram.getChat",
            "actual_pinned_message_id": pin_check.get("pinned_message_id"),
            "updated_at": now_iso(),
        }
        if pin_ok:
            new_state["pinned_at"] = now_iso()
            cleanup = cleanup_extra_status_card_pins(
                chat_id=chat_id,
                keep_message_id=new_message_id,
                tar=tar,
                token=token,
            )
            result["pin_cleanup"] = cleanup
            append_pin_cleanup_step(result, cleanup, step="extra_status_card_pin_cleanup")
        else:
            result["error"] = "pin_verification_failed"
            new_state["pin_verification_error"] = {
                "checked_at": now_iso(),
                "expected_message_id": new_message_id,
                "actual_pinned_message_id": pin_check.get("pinned_message_id"),
            }
        write_json(state_path, new_state)
        result["ok"] = pin_ok
        result["message_id"] = new_message_id
        result["pin_ok"] = pin_ok
        result["actual_pinned_message_id"] = pin_check.get("pinned_message_id")

    # Update state timestamp on successful edit too
    if existing_message_id and result["ok"]:
        existing_state["message_id"] = existing_message_id
        existing_state["chat_id"] = chat_id
        existing_state["room_id"] = room_id
        existing_state["agent_id"] = agent_id
        existing_state["telegram_bot"] = preflight.get("room_binding_bot")
        existing_state["transport"] = "telegram_bot_api"
        existing_state["updated_at"] = now_iso()
        write_json(state_path, existing_state)

    # Also update local preview file
    if text:
        (STATUS_DIR / "fixed-card-preview.txt").write_text(text + "\n", encoding="utf-8")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build projection or execute live pinned status card for Agent Room.")
    parser.add_argument("--room-id", default="openclaw-evolution")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--message-id", default="")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--out", default=str(STATUS_DIR / "pinned-card-dry-run.json"))
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="Bot identity used for live send/edit; status board owner must be openclaw-main")
    parser.add_argument("--live", action="store_true", help="Actually send/edit the pinned status card via Telegram API")
    args = parser.parse_args()

    if not args.chat_id:
        raise SystemExit("--chat-id is required for a pinned status card projection")

    if args.live:
        result = execute_live(
            room_id=args.room_id,
            chat_id=args.chat_id,
            state_path=Path(args.state_path),
            agent_id=args.agent_id,
        )
        out_path = Path(args.out).parent / "pinned-card-live.json"
        write_json(out_path, result)
        print(json.dumps({
            "ok": result.get("ok"),
            "path": str(out_path),
            "agent_id": result.get("agent_id"),
            "message_id": result.get("message_id"),
            "error": result.get("error"),
            "steps": [s.get("step") for s in result.get("steps") or []],
        }, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    projection = build_projection(
        room_id=args.room_id,
        chat_id=args.chat_id,
        message_id=args.message_id or None,
        state_path=Path(args.state_path),
        agent_id=args.agent_id,
    )
    out_path = Path(args.out)
    write_json(out_path, projection)
    fixed_card = projection.get("fixed_status_card") if isinstance(projection.get("fixed_status_card"), dict) else {}
    if fixed_card.get("text"):
        (STATUS_DIR / "fixed-card-preview.txt").write_text(str(fixed_card.get("text")) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "path": str(out_path),
        "mode": projection.get("mode"),
        "agent_id": projection.get("agent_id"),
        "activation_ready": projection.get("activation_ready"),
        "actions": [row.get("method") for row in projection.get("actions") or []],
        "tokens_printed": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
