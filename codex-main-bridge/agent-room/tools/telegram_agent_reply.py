#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
ENV_FILE = Path.home() / ".openclaw" / "secrets" / "agent-room-telegram-bots.env"
META = ROOM / "telegram_agent_bots.json"
ROOM_BINDINGS = ROOM / "telegram-room-bindings.json"
ROOT_BINDINGS = ROOT / "telegram-room-bindings.json"
COMMENTS = ROOT / "agent-comments"
OUTBOX = ROOM / "telegram-agent-reply"
LOCAL_RUNTIME_AGENTS = {"codex", "claude-code"}
DEFAULT_MAIN_BOT_USERNAMES = {"lchopenclaw_bot"}
MENTION_RE = re.compile(r"@\s*([a-zA-Z0-9_]{3,64})")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_optional(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            value = read_json(path)
            if isinstance(value, dict):
                return value
    except Exception:
        pass
    return {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    out = "-".join(part for part in out.split("-") if part)
    return out[:90] or "room"


def base_permissions() -> dict[str, bool]:
    return {
        "source_edit": True,
        "telegram_send": False,
        "notion_publish": False,
        "github_push": False,
        "secrets_access": False,
        "global_state_change": True,
        "quality_surface_change": False,
    }


def bot_username_index() -> dict[str, str]:
    out: dict[str, str] = {
        "lchopenclaw_bot": "openclaw-main",
        "lchopenclaw": "openclaw-main",
        "openclaw": "openclaw-main",
        "main": "openclaw-main",
    }
    if META.exists():
        meta = read_json(META)
        for bot in meta.get("bots", []):
            agent_id = str(bot.get("agent_id") or "")
            for key in [bot.get("telegram_username_verified"), bot.get("telegram_username")]:
                username = str(key or "").lstrip("@").lower()
                if username and agent_id:
                    out[username] = agent_id
    return out


def mentioned_usernames(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(1).lower() for match in MENTION_RE.finditer(text or "")))


def mention_targets(text: str, source_agent_id: str) -> list[str]:
    by_username = bot_username_index()
    targets: list[str] = []
    for username in mentioned_usernames(text):
        agent_id = by_username.get(username)
        if agent_id and agent_id != source_agent_id and agent_id not in targets:
            targets.append(agent_id)
    return targets


def configured_bot_to_bot_trigger(room_id: str) -> dict[str, Any]:
    configs: list[dict[str, Any]] = []
    room_bindings = read_json_optional(ROOM_BINDINGS)
    if isinstance(room_bindings.get("bot_to_bot_trigger"), dict):
        configs.append(dict(room_bindings["bot_to_bot_trigger"]))
    root_bindings = read_json_optional(ROOT_BINDINGS)
    for binding in root_bindings.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        if room_id and str(binding.get("room_id") or "") != room_id:
            continue
        ingress = binding.get("ingress") if isinstance(binding.get("ingress"), dict) else {}
        if isinstance(ingress.get("bot_to_bot_trigger"), dict):
            configs.append(dict(ingress["bot_to_bot_trigger"]))
    config: dict[str, Any] = {"enabled": True}
    usernames: list[str] = []
    for item in configs:
        config.update(item)
        raw_usernames = item.get("trigger_usernames") or item.get("main_bot_usernames") or []
        if isinstance(raw_usernames, str):
            usernames.append(raw_usernames)
        elif isinstance(raw_usernames, list):
            usernames.extend(str(value) for value in raw_usernames)
    config["trigger_usernames"] = sorted({
        value.lstrip("@").lower()
        for value in [*DEFAULT_MAIN_BOT_USERNAMES, *usernames]
        if value
    })
    return config


def bot_to_bot_trigger(text: str, source_agent_id: str, room_id: str) -> dict[str, Any] | None:
    config = configured_bot_to_bot_trigger(room_id)
    if config.get("enabled") is False:
        return None
    trigger_usernames = set(config.get("trigger_usernames") or [])
    matched = [username for username in mentioned_usernames(text) if username in trigger_usernames]
    if not matched:
        return None
    raw_route = config.get("route_to") or config.get("target_agents") or []
    if isinstance(raw_route, str):
        raw_route = [raw_route]
    route_to = [
        str(agent_id)
        for agent_id in raw_route
        if str(agent_id) in LOCAL_RUNTIME_AGENTS and str(agent_id) != source_agent_id
    ]
    if not route_to:
        route_to = [agent_id for agent_id in sorted(LOCAL_RUNTIME_AGENTS) if agent_id != source_agent_id]
    if not route_to:
        return None
    return {
        "schema": "openclaw.agent_room.bot_to_bot_trigger.v0",
        "trigger": "agent_visible_telegram_mention",
        "intent": str(config.get("intent") or "openclaw_main_coordination_call"),
        "mentioned_usernames": list(dict.fromkeys(matched)),
        "main_agent_id": "openclaw-main",
        "route_to": route_to,
        "delivery_policy": str(config.get("delivery_policy") or "broadcast_all_agents_decide"),
        "native_telegram_bot_to_bot_required": False,
    }


def expected_outputs_for(agent_ids: list[str]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        if agent_id == "claude-code":
            lane = "agent-comments/claude.jsonl"
        elif agent_id == "openclaw-main":
            lane = "agent-comments/openclaw-main.jsonl"
        else:
            lane = f"agent-comments/{slug(agent_id)}.jsonl"
        outputs.append({
            "type": "comment_jsonl",
            "path": lane,
            "agent_id": agent_id,
            "run_id_must_match": True,
            "canonical_import_required": True,
        })
    return outputs


def rotated_roles(local_targets: list[str], rotation_key: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    role_names = ["lead", "co_producer"]
    if not local_targets:
        return [], {}
    digest = hashlib.sha256(f"{rotation_key}:{','.join(local_targets)}".encode("utf-8")).hexdigest()
    start = int(digest[:2], 16) % len(local_targets)
    rotated_targets = local_targets[start:] + local_targets[:start]
    by_agent = {
        agent_id: role_names[idx] if idx < len(role_names) else "co_producer"
        for idx, agent_id in enumerate(rotated_targets)
    }
    return [{"agent_id": agent_id, "role": by_agent[agent_id]} for agent_id in local_targets], by_agent


def collaboration_state(target_agents: list[str], created_at: str, rotation_key: str) -> dict[str, Any] | None:
    local_targets = [agent_id for agent_id in target_agents if agent_id in LOCAL_RUNTIME_AGENTS]
    if len(local_targets) <= 1:
        return None
    roles, roles_by_agent = rotated_roles(local_targets, rotation_key)
    return {
        "schema": "openclaw.agent_room.collaboration.v0",
        "mode": "dynamic_claims",
        "status": "open",
        "participants": local_targets,
        "role_policy": {
            "strategy": "deterministic_rotation",
            "rotation_key_sha256": hashlib.sha256(rotation_key.encode("utf-8")).hexdigest(),
        },
        "roles": roles,
        "work_items": [
            {
                "id": f"bot_mention_response_{slug(agent_id)}",
                "status": "open",
                "assigned_to": agent_id,
                "role": roles_by_agent.get(agent_id, "co_producer"),
                "description": "Respond to the bot-to-bot mention from a distinct evidence or review angle.",
            }
            for agent_id in local_targets
        ],
        "claims": [],
        "handoffs": [],
        "artifacts": [],
        "blockers": [],
        "max_rounds": 1,
        "created_at": created_at,
    }


def task_exists(task_id: str) -> bool:
    for path in [ROOM / "tasks.jsonl", ROOM / "rooms"]:
        if path == ROOM / "tasks.jsonl" and path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("task_id") or "") == task_id:
                    return True
    return False


def bot_mention_targets_for_text(text: str, source_agent_id: str, room_id: str) -> tuple[list[str], dict[str, Any] | None]:
    """Return route targets for bot-visible @mentions without relying on send state."""
    trigger = bot_to_bot_trigger(text, source_agent_id, room_id)
    targets = list(trigger.get("route_to") or []) if trigger else mention_targets(text, source_agent_id)
    return targets, trigger


def source_projection_status(text_before_projection: str, delivered_text: str, projection: dict[str, Any]) -> str:
    if projection.get("projection_error"):
        return "projection_error_plain_fallback"
    if "...[truncated]" in (delivered_text or "") and len(text_before_projection or "") > len(delivered_text or ""):
        return "projected_truncated"
    return "projected_visible"


def source_visible_mention_status(source_text: str, delivered_text: str) -> str:
    source_mentions = set(mentioned_usernames(source_text))
    if not source_mentions:
        return "no_source_mentions"
    delivered_mentions = set(mentioned_usernames(delivered_text))
    missing = source_mentions - delivered_mentions
    return "mentions_visible" if not missing else "mentions_not_visible_after_projection_or_truncation"


def create_bot_mention_tasks(
    comment: dict[str, Any],
    source_agent_id: str,
    chat_id: str,
    run_id: str,
    sent_message_id: Any,
    visible_text: str,
    source_send_status: str = "sent",
    source_projection_status: str | None = None,
    source_visible_mention_status: str | None = None,
    delivered_text_preview: str | None = None,
) -> list[dict[str, Any]]:
    """Bind bot reply @mentions to internal Agent Room tasks.

    Telegram does not reliably deliver bot-originated @mentions to other bots.
    This function treats the intended @ as UI syntax while creating a canonical
    bot-to-bot task inside Agent Room. It uses the pre-projection source text so
    HTML rendering, truncation, duplicate suppression, or send failure cannot
    silently erase the downstream collaboration edge.
    """
    room_id = str(comment.get("room_id") or "openclaw-evolution")
    targets, trigger = bot_mention_targets_for_text(visible_text, source_agent_id, room_id)
    if not targets:
        return []
    created = now_iso()
    source_key = f"{source_agent_id}:{run_id}:{sent_message_id}:{','.join(targets)}:{visible_text}"
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
    task_id = f"botmention-{slug(room_id)}-{digest}"
    if task_exists(task_id):
        return []

    brief_dir = ROOM / "bot-mention-briefs" / task_id
    brief_path = brief_dir / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    send_status_note = ""
    if source_send_status == "failed":
        send_status_note = "注意：`source_send_status=failed` 表示上游回复可能没有被 Alex 看到；回应时不要假设群里已有那条可见消息。"
    elif source_send_status == "duplicate_suppressed":
        send_status_note = "注意：`source_send_status=duplicate_suppressed` 表示本轮没有再次发 Telegram；这是为了补齐/去重内部协作链路。"
    elif source_send_status != "sent":
        send_status_note = f"注意：`source_send_status={source_send_status}`，不要假设群里已有完整可见消息。"
    projection_note = ""
    if source_projection_status and source_projection_status != "projected_visible":
        projection_note += f"\nsource_projection_status: `{source_projection_status}`"
    if source_visible_mention_status and source_visible_mention_status != "mentions_visible":
        projection_note += f"\nsource_visible_mention_status: `{source_visible_mention_status}`"
    if delivered_text_preview:
        projection_note += f"\n\n## Delivered text preview\n\n{delivered_text_preview[:800]}\n"
    brief = (
        "# Agent Room Bot-to-Bot @ Mention\n\n"
        f"room_id: `{room_id}`\n"
        f"chat_id: `{chat_id}`\n"
        f"source_agent: `{source_agent_id}`\n"
        f"source_run_id: `{run_id}`\n"
        f"source_telegram_message_id: `{sent_message_id}`\n"
        f"source_send_status: `{source_send_status}`\n"
        f"target_agents: `{', '.join(targets)}`"
        f"{projection_note}\n\n"
        "## Source message before Telegram projection\n\n"
        f"{visible_text}\n\n"
        "## Instruction\n\n"
        "这是一条由 Agent Room 内部从 bot 可见消息里的 @mention 意图生成的可靠 bot-to-bot 协作任务。"
        + ("@lchopenclaw_bot 已按 bot_to_bot_trigger 配置路由到本地 peer，不依赖 Telegram 原生 bot-to-bot 投递。" if trigger else "")
        + send_status_note
        + "请用中文回应被 @ 的实质内容；如果没有新增价值，输出 NO_COMMENT。"
        + "不要把 Telegram 投递细节当成主要回复内容。\n"
    )
    brief_path.write_text(brief, encoding="utf-8")
    permissions = base_permissions()
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": room_id,
        "requested_by": "agent-room-bot-mention",
        "target_agents": targets,
        "lane": "agent_to_agent_mention" if trigger else "advisory",
        "routing_intent": "bot_to_bot_coordination" if trigger else "direct_agent_mention",
        "brief_path": str(brief_path),
        "context_paths": [],
        "permissions": permissions,
        "agent_room_profile": "material-bot-mention",
        "expected_outputs": expected_outputs_for(targets),
        "status": "queued",
        "review_status": "requested",
        "blocked_reason": None,
        "result_paths": [],
        "canonical_imported": True,
        "created_at": created,
        "updated_at": created,
        "lease": {"owner": None, "heartbeat_at": None, "expires_at": None},
        "heartbeat": {"last_seen_at": None},
        "retry_budget": {"max_attempts": 1, "attempt": 0},
        "manual_boundary": True,
        "quality_gate_status": "not_applicable",
        "side_effect_gate_status": "closed",
        "telegram_projection_status": "suppressed",
        "source": {
            "transport": "agent-room-bot-mention",
            "chat_id": chat_id,
            "update_id": f"bot-message:{source_agent_id}:{run_id}:{sent_message_id}",
            "source_agent_id": source_agent_id,
            "source_run_id": run_id,
            "source_telegram_message_id": str(sent_message_id or ""),
            "source_send_status": source_send_status,
            "source_projection_status": source_projection_status,
            "source_visible_mention_status": source_visible_mention_status,
            "message_text_sha256": hashlib.sha256(visible_text.encode("utf-8")).hexdigest(),
            "delivered_text_preview_sha256": hashlib.sha256((delivered_text_preview or "").encode("utf-8")).hexdigest() if delivered_text_preview else None,
            "bot_to_bot_trigger": trigger,
        },
        "delivery_policy": str((trigger or {}).get("delivery_policy") or "broadcast_all_agents_decide") if trigger else "targeted_reply",
        "reply_policy": "mentions_choose_first_response_owner; all_agents_observe; speak_when_addressed_or_material; otherwise NO_COMMENT",
        "bot_to_bot_trigger": trigger,
    }
    collaboration = collaboration_state(targets, created, source_key)
    if collaboration:
        task["collaboration"] = collaboration
    manifest = ROOM / "tasks" / task_id / "manifest.json"
    write_json(manifest, task)
    append_jsonl(ROOM / "tasks.jsonl", [task])
    append_jsonl(ROOM / "rooms" / room_id / "tasks.jsonl", [task])
    return [task]


def should_route_bot_mentions_after_reply(
    result: dict[str, Any],
    *,
    allow_send: bool,
    projection_mode: str,
    has_routeable_mentions: bool = True,
    suppress_send: bool = False,
    duplicate_suppressed: bool = False,
) -> bool:
    if projection_mode != "normal" or suppress_send or not has_routeable_mentions:
        return False
    if result.get("sent"):
        return True
    if duplicate_suppressed:
        return True
    return bool(allow_send and result.get("would_send") and not result.get("sent"))


def bot_token(agent_id: str) -> str:
    meta = read_json(META)
    env = load_env(ENV_FILE)
    for bot in meta.get("bots", []):
        if bot.get("agent_id") == agent_id:
            ref = str(bot.get("token_secret_ref") or "")
            name = ref.removeprefix("env:")
            token = env.get(name)
            if not token:
                raise RuntimeError(f"missing token env {name}")
            return token
    raise RuntimeError(f"unknown agent {agent_id}")


def latest_comment(agent_id: str, run_id: str | None) -> dict[str, Any]:
    path = COMMENTS / ("claude.jsonl" if agent_id == "claude-code" else f"{agent_id}.jsonl")
    best: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if run_id and record.get("run_id") != run_id:
            continue
        best = record
    if not best:
        raise RuntimeError("no matching comment")
    return best


def _send_message_once(token: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=data,
        headers={"User-Agent": "openclaw-agent-room-reply/0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"ok": False, "description": body}
        data["http_status"] = exc.code
        return data
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def _send_chat_action_once(token: str, payload: dict[str, str]) -> dict[str, Any]:
    """Send a Telegram chat action (e.g. 'typing') to indicate the bot is processing.

    This addresses the UX gap where Alex sees no feedback while waiting for a
    slow model inference or runner handoff. sendChatAction shows a "typing..."
    indicator in the Telegram UI without sending visible message content.
    """
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendChatAction",
        data=data,
        headers={"User-Agent": "openclaw-agent-room-reply/0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"ok": False, "description": body}
        data["http_status"] = exc.code
        return data
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def send_chat_action(token: str, chat_id: str, action: str = "typing") -> dict[str, Any]:
    """Send a Telegram chat action. Common actions: typing, upload_document, upload_photo."""
    return _send_chat_action_once(token, {"chat_id": chat_id, "action": action})


def _telegram_api_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Generic Telegram Bot API call via urllib."""
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/" + method,
        data=data,
        headers={"User-Agent": "openclaw-agent-room-reply/0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"ok": False, "description": body}
        data["http_status"] = exc.code
        return data
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def send_message(token: str, chat_id: str, text: str, *, parse_mode: str | None = None, plain_fallback: str | None = None) -> dict[str, Any]:
    payload: dict[str, str] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    api = _send_message_once(token, payload)
    if api.get("ok") or not parse_mode or plain_fallback is None:
        return api
    fallback = _send_message_once(token, {
        "chat_id": chat_id,
        "text": plain_fallback,
        "disable_web_page_preview": "true",
    })
    if fallback.get("ok"):
        fallback["parse_mode_fallback_from"] = parse_mode
        fallback["parse_mode_error"] = {
            "http_status": api.get("http_status"),
            "error_code": api.get("error_code"),
            "description": api.get("description"),
        }
        return fallback
    return api


def edit_message_text(token: str, chat_id: str, message_id: str | int, text: str) -> dict[str, Any]:
    """Edit an existing message sent by the bot. Used for pinned status card updates."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": str(message_id),
        "text": text,
        "disable_web_page_preview": True,
    }
    return _telegram_api_call(token, "editMessageText", payload)


def pin_chat_message(token: str, chat_id: str, message_id: str | int, *, disable_notification: bool = True) -> dict[str, Any]:
    """Pin a message in a group/supergroup. Bot must be admin with can_pin_messages."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": str(message_id),
        "disable_notification": disable_notification,
    }
    return _telegram_api_call(token, "pinChatMessage", payload)


def unpin_chat_message(token: str, chat_id: str, message_id: str | int) -> dict[str, Any]:
    """Unpin a specific message in a group/supergroup."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": str(message_id),
    }
    return _telegram_api_call(token, "unpinChatMessage", payload)


RAW_STATUS_MARKERS = (
    '"status"',
    '"accepted"',
    "entry_artifacts_missing",
    "failed_gate",
    "runner_failed_before_run_dir",
    "missing_required_artifacts",
    "worker_timeout",
)

DUPLICATE_RUN_DIR_BODY_MARKERS = (
    "run directory already exists",
    "duplicate_run_dir",
    "duplicate run-dir",
    "idempotency recovery",
    "reused without provider call",
    "already has a terminal result",
)

INTERNAL_ROOM_BRIEF_MARKERS = (
    "# Telegram Agent Room Task",
    "## Incoming message triage",
    "## Boundaries",
    "target_agents:",
    "reply_policy",
    "broadcast_targets",
    "canonical_state_advanced",
    "Full boundary list:",
)
INTERNAL_POLICY_LEAK_MARKERS = (
    "当前协作账本的所有权规则",
    "当前协作账本的所有权限规则",
    "房间原有的并行协作设定",
    "brief-only-fast-reply",
)
def normalize_room_visible_text(text: str) -> str:
    """Clean transport artifacts without rewriting an agent's visible style."""
    if not text:
        return text
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"[ \t]+(\n)", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_claude_code_visible_text(text: str) -> str:
    """Clean Ark transport artifacts while preserving Claude Code's phrasing."""
    if not text:
        return text
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


CODE_SPAN_OR_BLOCK_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
PAIRED_BOLD_RE = re.compile(r"(?<!\*)\*\*([^\n*](?:[^\n]*?[^\n*])?)\*\*(?!\*)")
PAIRED_SINGLE_STAR_RE = re.compile(r"(?<![\w*/])\*([^\s*\n](?:[^*\n]*?[^\s*\n])?)\*(?![\w*/.-])")
FENCED_CODE_RE = re.compile(r"```([^`\n]*)\n?([\s\S]*?)```")
INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
HEADING_LINE_RE = re.compile(r"^(\s{0,3})#{1,6}\s+(.+?)\s*#*\s*$")
HR_LINE_RE = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")
# Unordered list: lines starting with -, *, or + after optional whitespace
UNORDERED_LIST_RE = re.compile(r"^(\s{0,3})([-*+])\s+(.+)$")
# Ordered list: lines starting with digits + . after optional whitespace
ORDERED_LIST_RE = re.compile(r"^(\s{0,3})(\d+[.)])\s+(.+)$")
# Markdown inline link: [text](url)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Maximum inline code block length before extraction (chars of code content)
INLINE_CODE_BLOCK_THRESHOLD = 300


def extract_code_blocks(text: str, threshold: int = INLINE_CODE_BLOCK_THRESHOLD) -> tuple[str, list[dict[str, str]]]:
    """Extract fenced code blocks exceeding threshold; replace with compact references.

    Large code blocks blow up the Telegram HTML payload past the 3500-char
    limit, causing a fallback to plain text that shows raw backticks.  By
    extracting blocks above *threshold* characters and substituting a short
    placeholder, the main message stays inside the HTML budget while the
    full code is preserved in the returned list for artifact storage.

    Returns (rewritten_text, extracted_blocks) where each block dict has
    keys: language, content, placeholder.
    """
    if not text:
        return text, []
    blocks: list[dict[str, str]] = []
    out_parts: list[str] = []
    cursor = 0
    block_idx = 0
    for match in FENCED_CODE_RE.finditer(text):
        out_parts.append(text[cursor:match.start()])
        language = match.group(1).strip()
        content = match.group(2)
        if len(content) > threshold:
            block_idx += 1
            lang_label = f" {language}" if language else ""
            placeholder = f"[代码块{lang_label} #{block_idx} — 见下方 artifact]"
            blocks.append({
                "language": language,
                "content": content,
                "placeholder": placeholder,
                "index": block_idx,
            })
            out_parts.append(placeholder)
        else:
            out_parts.append(match.group(0))
        cursor = match.end()
    out_parts.append(text[cursor:])
    return "".join(out_parts), blocks


def _telegram_html_inline_text(text: str) -> str:
    escaped = html.escape(text, quote=False)
    previous = None
    while previous != escaped:
        previous = escaped
        escaped = PAIRED_BOLD_RE.sub(r"<b>\1</b>", escaped)
    # Single-star Markdown is ambiguous in code-ish chat (`*args`, `path/*.py`).
    # Do not use Telegram Markdown parsing; leave single stars literal.
    return escaped


def _telegram_html_inline(text: str) -> str:
    code_fragments: list[str] = []
    link_fragments: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        token = f"\ue000OC_CODE_{len(code_fragments)}\ue001"
        code_fragments.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return token

    def stash_link(match: re.Match[str]) -> str:
        token = f"\ue000OC_LINK_{len(link_fragments)}\ue001"
        link_text = html.escape(match.group(1), quote=False)
        link_url = html.escape(match.group(2), quote=True)
        link_fragments.append(f'<a href="{link_url}">{link_text}</a>')
        return token

    # Protect code spans first (they should not have links processed inside)
    protected = INLINE_CODE_RE.sub(stash_code, text)
    # Then protect Markdown links
    protected = MARKDOWN_LINK_RE.sub(stash_link, protected)
    rendered = _telegram_html_inline_text(protected)
    for index, fragment in enumerate(link_fragments):
        rendered = rendered.replace(f"\ue000OC_LINK_{index}\ue001", fragment)
    for index, fragment in enumerate(code_fragments):
        rendered = rendered.replace(f"\ue000OC_CODE_{index}\ue001", fragment)
    return rendered


def _telegram_html_text_segment(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        heading = HEADING_LINE_RE.match(line)
        if heading:
            lines.append(f"<b>{_telegram_html_inline(heading.group(2).strip())}</b>")
            continue
        if HR_LINE_RE.match(line):
            lines.append("────────")
            continue
        stripped = line.lstrip()
        # Unordered list: replace -, *, + bullet with Unicode bullet •
        ul_match = UNORDERED_LIST_RE.match(line)
        if ul_match:
            indent = ul_match.group(1)
            content = ul_match.group(3)
            # Convert leading spaces to indentation; 2 spaces = one level
            depth = len(indent) // 2
            prefix = "  " * depth + "• "
            lines.append(f"{prefix}{_telegram_html_inline(content)}")
            continue
        # Ordered list: preserve number, add slight visual alignment
        ol_match = ORDERED_LIST_RE.match(line)
        if ol_match:
            indent = ol_match.group(1)
            number = ol_match.group(2)
            content = ol_match.group(3)
            depth = len(indent) // 2
            prefix = "  " * depth + number + " "
            lines.append(f"{prefix}{_telegram_html_inline(content)}")
            continue
        if stripped.startswith(">"):
            content = stripped[1:].strip()
            lines.append(f"<blockquote>{_telegram_html_inline(content)}</blockquote>")
            continue
        lines.append(_telegram_html_inline(line))
    return "\n".join(lines)


def telegram_html_projection(text: str) -> str:
    """Project Markdown-ish room text to Telegram-safe HTML.

    OpenClaw's native Telegram channel renders outbound text as HTML. Agent
    Room peer bots call Telegram directly, so they need the same projection;
    otherwise Alex sees raw `##`, `**...**`, and backticks in group chat.
    Raw comments/artifacts remain unchanged; this is send-layer rendering only.
    """
    if not text:
        return text
    out: list[str] = []
    cursor = 0
    for match in FENCED_CODE_RE.finditer(text):
        out.append(_telegram_html_text_segment(text[cursor:match.start()]))
        language = re.sub(r"[^A-Za-z0-9_+-]", "", match.group(1).strip())
        code = html.escape(match.group(2), quote=False)
        if language:
            out.append(f'<pre><code class="language-{language}">{code}</code></pre>')
        else:
            out.append(f"<pre>{code}</pre>")
        cursor = match.end()
    out.append(_telegram_html_text_segment(text[cursor:]))
    return "".join(out).strip()


def _truncate_markdownish_for_projection(text: str, max_source_chars: int) -> str:
    """Trim source text before HTML projection without leaving fenced code open."""
    if len(text) <= max_source_chars:
        return text
    prefix = text[:max_source_chars].rstrip()
    suffix = "\n...[truncated]"
    if prefix.count("```") % 2 == 1:
        prefix += "\n```"
    return prefix + suffix


def telegram_html_projection_limited(text: str, max_len: int = 3500) -> str:
    """Return Telegram HTML under max_len, preserving code rendering.

    Do not fall back to raw plain text just because a message is long: Claude
    Code often emits code blocks and diffs, and plain delivery exposes fences,
    stars, and backticks. Instead truncate source text, close an open fenced
    block if needed, then project again to Telegram HTML.
    """
    projected = telegram_html_projection(text)
    if len(projected) <= max_len:
        return projected
    low = 0
    high = min(len(text), max_len)
    best = telegram_html_projection("...[truncated]")
    while low <= high:
        mid = (low + high) // 2
        candidate = telegram_html_projection(_truncate_markdownish_for_projection(text, mid))
        if len(candidate) <= max_len:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def telegram_plain_text_projection(text: str) -> str:
    """Hide Markdown-only bold delimiters for Telegram plain-text delivery.

    Agent comments keep their Markdown in local artifacts. This projection is
    only for the current sendMessage path, which does not set parse_mode.
    """
    if not text:
        return text
    parts = CODE_SPAN_OR_BLOCK_RE.split(text)
    for index in range(0, len(parts), 2):
        lines: list[str] = []
        for line in parts[index].splitlines():
            heading = HEADING_LINE_RE.match(line)
            if heading:
                line = heading.group(2).strip()
            elif HR_LINE_RE.match(line):
                line = "────────"
            elif line.lstrip().startswith(">"):
                line = line.lstrip()[1:].strip()
            else:
                # Convert unordered list bullets for plain text too
                ul_match = UNORDERED_LIST_RE.match(line)
                if ul_match:
                    indent = ul_match.group(1)
                    content = ul_match.group(3)
                    depth = len(indent) // 2
                    line = "  " * depth + "• " + content
                else:
                    ol_match = ORDERED_LIST_RE.match(line)
                    if ol_match:
                        indent = ol_match.group(1)
                        number = ol_match.group(2)
                        content = ol_match.group(3)
                        depth = len(indent) // 2
                        line = "  " * depth + number + " " + content
            lines.append(line)
        parts[index] = "\n".join(lines)
        previous = None
        while previous != parts[index]:
            previous = parts[index]
            parts[index] = PAIRED_BOLD_RE.sub(r"\1", parts[index])
        parts[index] = PAIRED_SINGLE_STAR_RE.sub(r"\1", parts[index])
        # Convert Markdown links to plain "text (url)" for fallback
        parts[index] = MARKDOWN_LINK_RE.sub(r"\1 (\2)", parts[index])
    return "".join(parts).strip()


def last_resort_plain_text_projection(text: str) -> str:
    """Return a safe Telegram payload without using Markdown/HTML projection.

    This is the final guard for Alex-visible room replies. Projection bugs must
    be recorded, but they must not make a direct @mention disappear.
    """
    cleaned = normalize_room_visible_text(text or "")
    cleaned = cleaned.replace("<", "＜").replace(">", "＞")
    return cleaned.strip() or "Agent Room 本轮没有形成可发送正文。"


def build_telegram_projection(text: str) -> dict[str, Any]:
    """Build Telegram payloads while isolating projection failures.

    Normal path returns HTML text plus a plain fallback. If any projection helper
    raises, return a plain-text payload and preserve the exception in metadata.
    """
    try:
        html_text = telegram_html_projection_limited(text)
        plain_source = _truncate_markdownish_for_projection(text, 3400) if len(text) > 3400 else text
        plain_fallback_text = telegram_plain_text_projection(plain_source)
        if len(plain_fallback_text) > 3500:
            plain_fallback_text = plain_fallback_text[:3400].rstrip() + "\n...[truncated]"
        return {
            "text": html_text,
            "plain_fallback_text": plain_fallback_text,
            "parse_mode": "HTML",
            "telegram_text_projected": html_text != text or plain_fallback_text != text,
            "projection_error": None,
        }
    except Exception as exc:
        plain = last_resort_plain_text_projection(text)
        return {
            "text": plain,
            "plain_fallback_text": plain,
            "parse_mode": None,
            "telegram_text_projected": plain != text,
            "projection_error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }


def unsafe_body_reason(body: str) -> str | None:
    stripped = body.strip()
    if not stripped:
        return "empty_body"
    first_line = next((line.strip() for line in stripped.splitlines() if line.strip()), "")
    if "????" in stripped:
        return "unreadable_or_mojibake_body"
    lowered = stripped.lower()
    codex_transcript_markers = [
        "openai codex v",
        "workdir:",
        "provider: openai",
        "approval: never",
        "you are the selected agent in alex's openclaw agent room",
        "--output-last-message",
    ]
    if lowered.startswith("openai codex v") or (
        "workdir:" in lowered
        and "model:" in lowered
        and "provider:" in lowered
        and ("usage limit" in lowered or "you are the selected agent" in lowered or "approval:" in lowered)
    ):
        return "raw_codex_cli_transcript"
    if any(marker in lowered for marker in codex_transcript_markers[:1]) and "usage limit" in lowered:
        return "raw_codex_cli_transcript"
    if any(marker in stripped for marker in INTERNAL_ROOM_BRIEF_MARKERS):
        return "raw_agent_room_brief_body"
    if any(marker in stripped for marker in INTERNAL_POLICY_LEAK_MARKERS):
        return "raw_agent_room_policy_body"
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if (
            isinstance(parsed, dict)
            and isinstance(parsed.get("body"), str)
            and any(key in parsed for key in ("agent_id", "kind", "title", "blockers"))
        ):
            return None
        if isinstance(parsed, dict) and {"status", "run_id"}.intersection(parsed):
            return "raw_internal_json_body"
    # Do not suppress normal Chinese analysis merely because it mentions a
    # historical marker such as `entry_artifacts_missing`. Only block strings
    # that look like machine status payloads rather than human prose.
    looks_like_status_payload = (
        first_line.startswith(("status:", "accepted:", "run_id:", "{", "["))
        or first_line.startswith('"status"')
        or bool(re.search(r'^\s*[\[{]?\s*"status"\s*:', first_line))
    )
    if looks_like_status_payload and any(marker in stripped for marker in RAW_STATUS_MARKERS):
        return "raw_internal_status_body"
    return None


def is_duplicate_run_dir_body(body: str) -> bool:
    """Detect duplicate run-dir / idempotency recovery in body text.

    This gate works independently of comment.blockers or title format,
    closing the gap identified in projection-runtime-alignment audit
    where duplicate_run_dir comments could leak if the title didn't
    match is_internal_runner_artifact_title().
    """
    lowered = (body or "").strip().lower()
    return any(marker in lowered for marker in DUPLICATE_RUN_DIR_BODY_MARKERS)


ROUTINE_APPROVAL_REQUEST_RE = re.compile(
    r"(批准的话[^。！？\n]*(?:开始|执行|落地|补|改|做)|"
    r"(?:需要|请|麻烦)(?:你|Alex)?(?:确认|批准)|"
    r"(?:要不要|是否要|需不需要)(?:我|我们)?|"
    r"我可以[^。！？\n]{0,40}(?:开始|执行|落地|补|改|做|继续)[^。！？\n]{0,8}(?:吗|？|\?))"
)
ROUTINE_OPTIONAL_EXECUTION_TAIL_RE = re.compile(
    r"(?:我|我们)可以[^。！？\n]{0,100}(?:修改|开始|执行|落地|补|改|做|继续|跑|检查|实现)"
)
STRONG_APPROVAL_BOUNDARY_RE = re.compile(
    r"(外部|公开|发布|push|PR|pull request|deploy|部署|删除|清空|drop|destroy|stop|停机|迁移|migrate|"
    r"secrets?|token|api key|API key|密钥|密码|隐私|私有|付款|交易|买入|卖出|不可逆|全局默认|质量面切换)"
    ,
    re.IGNORECASE,
)
NON_RETRIEVABLE_PREFERENCE_RE = re.compile(
    r"(偏好|喜好|口吻|风格|命名|名字|标题|取舍|选项|哪一个|哪个|哪种|你希望|你想|"
    r"无法推断|不可推断|non[- ]?retrievable|preference|prefer|choice|choose)",
    re.IGNORECASE,
)


def routine_approval_request_reason(text: str) -> str | None:
    """Detect routine workflow approvals that should not be projected.

    Alex should only be asked for non-retrievable preferences or risky external /
    destructive / secret-bearing actions.  Safe reversible local patches, config
    edits, artifacts, inspections, and smoke tests should be done by the agents
    and reported with evidence.  This projection guard prevents stale prompt
    wording such as "批准的话我开始" from reaching the room again.
    """
    body = str(text or "").strip()
    if not body:
        return None
    tail = body[-600:]
    if ROUTINE_APPROVAL_REQUEST_RE.search(body):
        if STRONG_APPROVAL_BOUNDARY_RE.search(body) or NON_RETRIEVABLE_PREFERENCE_RE.search(body):
            return None
        return "routine_approval_request_to_alex"
    # Also suppress proposal-only endings like "我可以在权限内修改..." for
    # routine local work. These are not explicit questions, but in the room they
    # still offload the execute/don't-execute decision to Alex instead of letting
    # agents discuss, choose, patch, and smoke within their granted boundary.
    if ROUTINE_OPTIONAL_EXECUTION_TAIL_RE.search(tail):
        if STRONG_APPROVAL_BOUNDARY_RE.search(tail):
            return None
        return "routine_optional_execution_to_alex"
    return None


def readable_blocker(agent_id: str, run_id: str | None, reason: str, body: str) -> str:
    status = None
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            status = parsed.get("status")
    except Exception:
        status = None
    reason_zh = {
        "empty_body": "没有可发布正文",
        "unreadable_or_mojibake_body": "回复疑似编码损坏或不可读",
        "raw_internal_status_body": "内部运行状态被误当成回复",
        "raw_internal_json_body": "内部 JSON 状态被误当成回复",
        "raw_codex_cli_transcript": "Codex CLI 原始运行日志被误当成回复",
        "raw_agent_room_brief_body": "Agent Room 内部 brief 被误当成回复",
        "raw_agent_room_policy_body": "Agent Room 内部策略/账本片段被误当成回复",
    }.get(reason, reason)
    detail = f"，状态：{status}" if status else ""
    return (
        f"{agent_id} 本轮没有形成可直接发布的正常发言，已拦截原始内部错误/运行日志，避免把 JSON、乱码或 CLI transcript 继续发到群里。"
        f"原因：{reason_zh}{detail}。"
        f"run_id: {run_id or 'unknown'}。"
    )


def parse_embedded_room_json(text: str, preferred_agent_id: str | None = None) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped).strip()
    candidates = [stripped, *extract_json_object_candidates(stripped)]
    best: dict[str, Any] = {}
    for candidate in candidates:
        value = parse_json_object_lenient(candidate)
        if not isinstance(value.get("body"), str):
            continue
        if preferred_agent_id and value.get("agent_id") == preferred_agent_id:
            return value
        if not best:
            best = value
    return best


def parse_json_object_lenient(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception:
        repaired = escape_raw_newlines_in_json_strings(text)
        if repaired == text:
            return {}
        try:
            value = json.loads(repaired)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def extract_json_object_candidates(text: str) -> list[str]:
    out: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:idx + 1])
                start = None
    return out


def escape_raw_newlines_in_json_strings(text: str) -> str:
    """Repair model-emitted JSON where string values contain literal newlines."""
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            out.append(ch)
            in_string = False
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return "".join(out)


def is_generic_execution_title(agent_id: str, title: str) -> bool:
    normalized = " ".join(str(title or "").strip().lower().split())
    agent = str(agent_id or "").strip().lower()
    agent_aliases = {agent}
    if agent == "claude-code":
        agent_aliases.add("claude code")
    if agent == "openclaw-main":
        agent_aliases.add("openclaw main")
    generic_titles: set[str] = set()
    for alias in agent_aliases:
        generic_titles.update({
            f"{alias} execution completed",
            f"{alias} execution failed",
            f"{alias} ark execution completed",
            f"{alias} ark execution failed",
        })
    return normalized in generic_titles


def strip_leading_generic_execution_line(agent_id: str, text: str) -> str:
    lines = [line for line in str(text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) <= 1:
        return "\n".join(lines).strip()
    first = lines[0].strip().rstrip("：:")
    if is_generic_execution_title(agent_id, first):
        return "\n".join(lines[1:]).strip()
    return "\n".join(lines).strip()


def _is_coordination_plumbing_run(run_id: str | None, comment: dict[str, Any]) -> bool:
    """Return True for internal coordination turns that must stay local.

    This guard is deliberately narrow: standing mainline tasks can produce
    material progress and must remain projectable when the task allows it.
    """
    run = str(run_id or comment.get("run_id") or comment.get("task_id") or "").strip().lower()
    source = comment.get("source") if isinstance(comment.get("source"), dict) else {}
    transport = str(source.get("transport") or comment.get("source_transport") or comment.get("transport") or "").strip()
    requested_by = str(comment.get("requested_by") or "").strip()
    lane = str(comment.get("lane") or "").strip()
    projection_status = str(comment.get("telegram_projection_status") or "").strip()

    if projection_status.startswith("user_visible_") or comment.get("standing_visible_allowed") is True:
        return False
    if run.startswith("context-rebase-") or transport == "agent-room-context-rebase" or requested_by == "agent-room-context-rebase":
        return True
    if transport == "agent-room-collab-followup" or requested_by == "agent-room-collab-followup" or lane == "peer_collaboration_followup":
        return comment.get("peer_followup_visible_allowed") is not True
    return False


def humanize_internal_summary(agent_id: str, run_id: str | None, comment: dict[str, Any], body: str, unsafe_reason: str | None) -> str:
    """Convert internal agent-room output into a short user-readable message.

    The room should show useful agent progress, not raw JSON, ledger failures,
    or process chatter. This keeps the corresponding agent bot as the speaker
    while removing implementation plumbing from the visible group.
    """
    parsed = parse_embedded_room_json(body, preferred_agent_id=agent_id)
    parsed_body = str(parsed.get("body") or "").strip() if parsed else ""
    if parsed_body:
        title = str(parsed.get("title") or comment.get("title") or "进度更新").strip()
        clean_body = parsed_body
        blockers = parsed.get("blockers") if isinstance(parsed.get("blockers"), list) else []
    else:
        title = str(comment.get("title") or "进度更新").strip()
        clean_body = body.strip()
        blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []

    internal_runner_title = is_internal_runner_artifact_title(title)
    if is_internal_runner_failure_comment(comment, title, clean_body, blockers):
        # A failed runner/no-parsed-output record is runtime plumbing, not a
        # peer-agent contribution. Keep it in local artifacts/ledgers and let
        # main summarize systemic issues; do not make the Claude/Codex bot post
        # "did not produce parsed JSON" style messages to Alex's room.
        return "NO_COMMENT"
    if internal_runner_title:
        # Completed runs can still have a useful natural-language body even
        # when they missed the strict JSON contract. In that case keep the body
        # but drop the implementation-title so it does not leak as chat text.
        title = ""

    blocked_reason = str(comment.get("blocked_reason") or "")
    if "not in collaboration participants" in blocked_reason or "collaboration_claim_failed" in blockers:
        # Collaboration ledger/accounting failures are runtime plumbing. They
        # must stay in local artifacts for operators instead of being projected
        # as if Codex/Claude Code voluntarily contributed to the room.
        return "NO_COMMENT"
    if run_id and _is_coordination_plumbing_run(run_id, comment):
        # Context-rebase evaluations and non-visible structural peer followups
        # are agent-to-agent coordination, not user-facing answers. They belong
        # in local artifacts, not the visible group.
        return "NO_COMMENT"
    if unsafe_reason and not parsed_body:
        return readable_blocker(agent_id, run_id, unsafe_reason, body)

    clean_body = normalize_room_visible_text(clean_body)
    if agent_id == "claude-code":
        clean_body = normalize_claude_code_visible_text(clean_body)
    clean_body = strip_leading_generic_execution_line(agent_id, clean_body)
    title = normalize_room_visible_text(title)
    if agent_id == "claude-code":
        title = normalize_claude_code_visible_text(title)

    if clean_body == "NO_COMMENT" or clean_body.startswith("NO_COMMENT\n"):
        return "NO_COMMENT"

    # Keep internal summaries short and outcome-oriented. The bot identity is
    # visible in Telegram already, so do not prefix with the agent name.
    if title and not is_generic_execution_title(agent_id, title) and title not in clean_body[:120]:
        text = f"{title}\n{clean_body}"
    else:
        text = clean_body
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    text = strip_leading_generic_execution_line(agent_id, text)
    if blockers:
        blocker_text = "、".join(str(item) for item in blockers[:3])
        if blocker_text and "阻塞" not in text[:200]:
            text += f"\n当前阻塞：{blocker_text}"
    return text[:1200].strip() or "我这轮没有形成需要展示的新结论。"


def is_internal_runner_artifact_title(title: str) -> bool:
    normalized = " ".join(str(title or "").strip().lower().split())
    if not normalized:
        return False
    return any(marker in normalized for marker in (
        "room runner did not produce parsed room json",
        "runner did not produce a publishable reply",
        "room runner returned internal status",
        "ark execution failed",
        "execution failed",
    ))


def is_internal_runner_failure_comment(comment: dict[str, Any], title: str, body: str, blockers: list[Any]) -> bool:
    normalized_title = " ".join(str(title or "").strip().lower().split())
    normalized_body = " ".join(str(body or "").strip().lower().split())
    blocker_values = {str(item).strip().lower() for item in blockers if str(item).strip()}
    projection_status = str(comment.get("telegram_projection_status") or "").strip().lower()
    if projection_status in {
        "visible_failure_pending",
        "visible_failure_delivered",
        "user_visible_quota_exhausted",
        "user_visible_quota_notification",
    }:
        return False
    if projection_status == "user_visible_runner_failure":
        return str(comment.get("visibility_reason") or "").strip() != "telegram_user_task_liveness_contract"
    if projection_status in {
        "local_only_runner_failure",
        "suppressed_runner_failure",
        "local_only_quota_silenced",
        "suppressed_quota_silenced",
        "local_only_retryable_runner_failure",
        "local_only_deferred_liveness_signal",
        "deferred_liveness_signal",
    }:
        return True
    failure_blockers = {
        "failed",
        "codex_cli_failed",
        "runner_failed",
        "runner_process_missing",
        "runner_timeout",
        "worker_timeout",
        "raw_internal_status_body",
        "raw_internal_json_body",
        "duplicate_run_dir",
    }
    if blocker_values.intersection(failure_blockers) and is_internal_runner_artifact_title(title):
        return True
    # Body-level duplicate_run_dir / idempotency detection independent of
    # blockers list and title format (closes projection-runtime-alignment gap #1).
    if "duplicate_run_dir" in blocker_values or is_duplicate_run_dir_body(body):
        return True
    if is_internal_runner_artifact_title(title) and any(marker in normalized_body for marker in (
        "没有形成可直接发布",
        "没有形成可发布正文",
        "no publishable reply",
        "no parsed room json",
        "状态为 failed",
    )):
        return True
    if normalized_title in {"claude-code ark execution failed", "codex execution failed"} and blocker_values:
        return True
    if normalized_title == "codex cli execution blocked" and "codex_cli_failed" in blocker_values:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one Agent Room comment through its Telegram agent bot.")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--projection-mode", choices=["normal", "internal-summary"], default="normal")
    parser.add_argument("--only-chat-action", action="store_true", help="Send only a Telegram chat action such as typing; do not read or project a comment.")
    parser.add_argument("--chat-action", default="typing", help="Telegram chat action to send when --only-chat-action is set.")
    parser.add_argument("--direct-text-file", default=None, help="Project and optionally send a local text file without reading an agent comment.")
    args = parser.parse_args()

    if args.only_chat_action:
        action = str(args.chat_action or "typing").strip() or "typing"
        run_id = args.run_id or hashlib.sha256(f"{args.agent_id}:{args.chat_id}:{action}".encode("utf-8")).hexdigest()[:16]
        result = {
            "schema": "openclaw.agent_room.telegram_agent_chat_action.v0",
            "agent_id": args.agent_id,
            "chat_id": args.chat_id,
            "run_id": run_id,
            "action": action,
            "would_send": bool(args.allow_send),
            "sent": False,
            "suppressed_reason": None if args.allow_send else "send_not_allowed",
            "tokens_printed": False,
        }
        if args.allow_send:
            api = send_chat_action(bot_token(args.agent_id), args.chat_id, action)
            result["sent"] = bool(api.get("ok"))
            if not result["sent"]:
                result["telegram_error"] = {
                    "http_status": api.get("http_status"),
                    "error_code": api.get("error_code"),
                    "description": api.get("description"),
                }
        OUTBOX.mkdir(parents=True, exist_ok=True)
        write_json(OUTBOX / f"{args.agent_id}-{run_id}-chat-action.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.direct_text_file:
        text_body = Path(args.direct_text_file).read_text(encoding="utf-8").strip()
        normalized_text_body = normalize_room_visible_text(text_body)
        if args.agent_id == "claude-code":
            normalized_text_body = normalize_claude_code_visible_text(normalized_text_body)
        prefix = str(args.prefix or "").strip()
        text = f"{prefix}\n{normalized_text_body}" if prefix else normalized_text_body
        projection = build_telegram_projection(text)
        plain_fallback_text = projection["plain_fallback_text"]
        projected_text = projection["text"]
        parse_mode = projection["parse_mode"]
        if len(plain_fallback_text) > 3500:
            plain_fallback_text = plain_fallback_text[:3400].rstrip() + "\n...[truncated]"
        if len(projected_text) > 3500:
            try:
                projected_text = telegram_html_projection_limited(projected_text, 3500)
                parse_mode = "HTML"
            except Exception as exc:
                projected_text = plain_fallback_text[:3400].rstrip() + "\n...[truncated]"
                parse_mode = None
                if not projection.get("projection_error"):
                    projection["projection_error"] = {"type": exc.__class__.__name__, "message": str(exc)}
        run_id = args.run_id or hashlib.sha256(f"{args.agent_id}:{args.chat_id}:{text}".encode("utf-8")).hexdigest()[:16]
        out_path = OUTBOX / f"{args.agent_id}-{run_id}.json"
        prior = read_json(out_path) if out_path.exists() else {}
        duplicate_suppressed = bool(
            prior.get("sent")
            and str(prior.get("chat_id")) == str(args.chat_id)
            and str(prior.get("run_id")) == str(run_id)
        )
        suppress_reason = "empty_direct_text" if not normalized_text_body else ("duplicate_reply_already_sent" if duplicate_suppressed else None)
        result = {
            "schema": "openclaw.agent_room.telegram_agent_reply.v0",
            "agent_id": args.agent_id,
            "chat_id": args.chat_id,
            "run_id": run_id,
            "would_send": bool(normalized_text_body) and not duplicate_suppressed,
            "sent": False,
            "suppressed_reason": suppress_reason,
            "body_transformed_reason": None,
            "projection_mode": "direct-text",
            "room_text_normalized": "telegram_html_markdown_projected" if projection["telegram_text_projected"] else None,
            "prior_telegram_message_id": prior.get("telegram_message_id") if duplicate_suppressed else None,
            "tokens_printed": False,
            "text_preview": projected_text[:300],
            "plain_fallback_preview": plain_fallback_text[:300],
            "parse_mode": parse_mode,
            "projection_error": projection.get("projection_error"),
            "direct_text_file": str(args.direct_text_file),
        }
        if args.allow_send and result["would_send"]:
            api = send_message(bot_token(args.agent_id), args.chat_id, projected_text, parse_mode=parse_mode, plain_fallback=plain_fallback_text if parse_mode else None)
            result["sent"] = bool(api.get("ok"))
            result["telegram_message_id"] = (api.get("result") or {}).get("message_id")
            if api.get("parse_mode_fallback_from"):
                result["parse_mode_fallback_from"] = api.get("parse_mode_fallback_from")
                result["parse_mode_error"] = api.get("parse_mode_error")
            if not result["sent"]:
                result["telegram_error"] = {
                    "http_status": api.get("http_status"),
                    "error_code": api.get("error_code"),
                    "description": api.get("description"),
                }
        OUTBOX.mkdir(parents=True, exist_ok=True)
        write_json(out_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (not args.allow_send or duplicate_suppressed or result["sent"] or suppress_reason == "empty_direct_text") else 1

    comment = latest_comment(args.agent_id, args.run_id)
    body = str(comment.get('body','')).strip()
    unsafe_reason = unsafe_body_reason(body)
    title = str(comment.get("title") or "")
    blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []
    suppress_send = body == "NO_COMMENT" or body.startswith("NO_COMMENT\n")
    if is_internal_runner_failure_comment(comment, title, body, blockers):
        # Defense-in-depth for normal Telegram projections: runner failures and
        # model cooldown dumps belong in local artifacts/main summaries, not as
        # visible bot replies.
        suppress_send = True
    if args.projection_mode == "internal-summary":
        text_body = humanize_internal_summary(args.agent_id, args.run_id or comment.get("run_id"), comment, body, unsafe_reason)
        unsafe_reason = None
        suppress_send = text_body == "NO_COMMENT" or text_body.startswith("NO_COMMENT\n")
    else:
        text_body = readable_blocker(args.agent_id, args.run_id or comment.get("run_id"), unsafe_reason, body) if unsafe_reason else body
    routine_approval_reason = routine_approval_request_reason(text_body)
    if routine_approval_reason:
        suppress_send = True
    original_text_body = text_body
    normalized_text_body = normalize_room_visible_text(text_body)
    if args.agent_id == "claude-code":
        normalized_text_body = normalize_claude_code_visible_text(normalized_text_body)
    prefix = str(args.prefix or "").strip()
    text = f"{prefix}\n{text_body}" if prefix else text_body
    if normalized_text_body != text_body:
        text_body = normalized_text_body
        text = f"{prefix}\n{text_body}" if prefix else text_body
    bot_mention_source_text = text
    room_id = str(comment.get("room_id") or "openclaw-evolution")
    bot_mention_targets, _bot_mention_trigger = bot_mention_targets_for_text(bot_mention_source_text, args.agent_id, room_id)
    has_routeable_bot_mentions = bool(bot_mention_targets)
    projection = build_telegram_projection(text)
    plain_fallback_text = projection["plain_fallback_text"]
    text = projection["text"]
    parse_mode = projection["parse_mode"]
    telegram_text_projected = bool(projection["telegram_text_projected"])
    if len(plain_fallback_text) > 3500:
        plain_fallback_text = plain_fallback_text[:3400].rstrip() + "\n...[truncated]"
    if len(text) > 3500:
        # Defense-in-depth: prefer safe HTML truncation, but never let this
        # projection guard reintroduce a silent direct-mention failure.
        try:
            text = telegram_html_projection_limited(text, 3500)
            parse_mode = "HTML"
        except Exception as exc:
            text = plain_fallback_text[:3400].rstrip() + "\n...[truncated]"
            parse_mode = None
            if not projection.get("projection_error"):
                projection["projection_error"] = {"type": exc.__class__.__name__, "message": str(exc)}
    run_id = args.run_id or comment.get("run_id")
    out_path = OUTBOX / f"{args.agent_id}-{run_id}.json"
    prior = read_json(out_path) if out_path.exists() else {}
    duplicate_suppressed = bool(
        prior.get("sent")
        and str(prior.get("chat_id")) == str(args.chat_id)
        and str(prior.get("run_id")) == str(run_id)
    )
    result = {
        "schema": "openclaw.agent_room.telegram_agent_reply.v0",
        "agent_id": args.agent_id,
        "chat_id": args.chat_id,
        "run_id": run_id,
        "would_send": not suppress_send and not duplicate_suppressed,
        "sent": False,
        "suppressed_reason": (
            routine_approval_reason
            if routine_approval_reason
            else ("agent_no_comment" if suppress_send else ("duplicate_reply_already_sent" if duplicate_suppressed else None))
        ),
        "body_transformed_reason": unsafe_reason,
        "projection_mode": args.projection_mode,
        "room_text_normalized": (
            "telegram_html_markdown_projected"
            if telegram_text_projected
            else (
                "claude_plain_chat_normalized"
                if args.agent_id == "claude-code" and normalized_text_body != original_text_body
                else ("decorative_symbols_removed" if normalized_text_body != original_text_body else None)
            )
        ),
        "prior_telegram_message_id": prior.get("telegram_message_id") if duplicate_suppressed else None,
        "tokens_printed": False,
        "text_preview": text[:300],
        "plain_fallback_preview": plain_fallback_text[:300],
        "parse_mode": parse_mode,
        "projection_error": projection.get("projection_error"),
        "bot_mention_intent_targets": bot_mention_targets,
        "bot_mention_source_text_sha256": hashlib.sha256(bot_mention_source_text.encode("utf-8")).hexdigest() if has_routeable_bot_mentions else None,
    }
    if args.allow_send and not suppress_send and not duplicate_suppressed:
        api = send_message(bot_token(args.agent_id), args.chat_id, text, parse_mode=parse_mode, plain_fallback=plain_fallback_text if parse_mode else None)
        result["sent"] = bool(api.get("ok"))
        result["telegram_message_id"] = (api.get("result") or {}).get("message_id")
        if api.get("parse_mode_fallback_from"):
            result["parse_mode_fallback_from"] = api.get("parse_mode_fallback_from")
            result["parse_mode_error"] = api.get("parse_mode_error")
        if not result["sent"]:
            result["telegram_error"] = {
                "http_status": api.get("http_status"),
                "error_code": api.get("error_code"),
                "description": api.get("description"),
            }
    routed_bot_mentions: list[dict[str, Any]] = []
    # Treat @mentions in bot-visible messages as UI syntax for reliable
    # internal bot-to-bot routing. If a real send attempt failed, still create
    # the task and mark the source status so downstream agents know Alex may
    # not have seen the upstream reply.
    if result.get("sent"):
        source_send_status = "sent"
    elif duplicate_suppressed:
        source_send_status = "duplicate_suppressed"
    elif args.allow_send and result.get("would_send"):
        source_send_status = "failed"
    else:
        source_send_status = "not_sent"
    delivered_text_for_status = text or plain_fallback_text or ""
    projection_status = source_projection_status(bot_mention_source_text, delivered_text_for_status, projection)
    visible_mention_status = source_visible_mention_status(bot_mention_source_text, delivered_text_for_status)
    should_route_bot_mentions = should_route_bot_mentions_after_reply(
        result,
        allow_send=args.allow_send,
        projection_mode=args.projection_mode,
        has_routeable_mentions=has_routeable_bot_mentions,
        suppress_send=suppress_send,
        duplicate_suppressed=duplicate_suppressed,
    )
    if should_route_bot_mentions:
        routed_bot_mentions = create_bot_mention_tasks(
            comment,
            args.agent_id,
            args.chat_id,
            str(run_id or ""),
            result.get("telegram_message_id") or prior.get("telegram_message_id"),
            bot_mention_source_text,
            source_send_status,
            projection_status,
            visible_mention_status,
            delivered_text_for_status,
        )
    result["bot_mentions_routed"] = [
        {
            "task_id": task.get("task_id"),
            "target_agents": task.get("target_agents"),
            "source_transport": (task.get("source") or {}).get("transport"),
            "source_send_status": (task.get("source") or {}).get("source_send_status"),
            "source_projection_status": (task.get("source") or {}).get("source_projection_status"),
            "source_visible_mention_status": (task.get("source") or {}).get("source_visible_mention_status"),
        }
        for task in routed_bot_mentions
    ]
    OUTBOX.mkdir(parents=True, exist_ok=True)
    write_json(out_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (suppress_send or duplicate_suppressed or not args.allow_send or result["sent"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
