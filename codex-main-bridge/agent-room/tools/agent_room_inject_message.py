#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_room_detection_shared import idle_agent_contribution_problem_requested
import mainline_governance

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
BINDINGS = ROOM / "telegram-room-bindings.json"
LOCAL_RUNTIME_AGENTS = {"codex", "claude-code"}
DEFAULT_MAIN_BOT_USERNAMES = {"lchopenclaw_bot"}
MENTION_RE = re.compile(r"@\s*([A-Za-z0-9_]+)")
COLLABORATION_EXECUTION_MISREAD_MARKERS = (
    "理解错误",
    "误解",
    "误读",
    "错误的执行",
    "错误执行",
    "执行错误",
    "执行错",
    "落实不了",
    "还是落实不了",
    "没落实",
    "没有落实",
    "避免某个人",
    "某个人理解错误",
)

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except Exception:
            continue
    return out


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def existing_ids(path: Path, key: str) -> set[str]:
    return {str(row.get(key) or "") for row in read_jsonl(path) if row.get(key)}


def username_map(bindings: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in bindings.get("participants") or []:
        agent_id = str(item.get("agent_id") or "")
        username = str(item.get("telegram_bot") or "").lstrip("@").lower()
        if agent_id and username:
            out[username] = agent_id
    return out


def mentioned_usernames(text: str) -> list[str]:
    return list(dict.fromkeys(match.lower() for match in MENTION_RE.findall(text or "")))


def mention_targets(text: str, bindings: dict[str, Any]) -> list[str]:
    by_username = username_map(bindings)
    targets: list[str] = []
    for match in mentioned_usernames(text):
        agent_id = by_username.get(match)
        if agent_id and agent_id not in targets:
            targets.append(agent_id)
    return [agent_id for agent_id in targets if agent_id in LOCAL_RUNTIME_AGENTS]


def bot_to_bot_trigger(text: str, bindings: dict[str, Any], actor_agent_id: str) -> dict[str, Any] | None:
    config = bindings.get("bot_to_bot_trigger") if isinstance(bindings.get("bot_to_bot_trigger"), dict) else {}
    if config.get("enabled") is False:
        return None
    raw_usernames = config.get("trigger_usernames") or []
    if isinstance(raw_usernames, str):
        raw_usernames = [raw_usernames]
    trigger_usernames = {
        str(item).lstrip("@").lower()
        for item in [*DEFAULT_MAIN_BOT_USERNAMES, *raw_usernames]
        if item
    }
    matched = [username for username in mentioned_usernames(text) if username in trigger_usernames]
    if not matched:
        return None
    raw_route = config.get("route_to") or []
    if isinstance(raw_route, str):
        raw_route = [raw_route]
    route_to = [str(agent_id) for agent_id in raw_route if str(agent_id) in LOCAL_RUNTIME_AGENTS and str(agent_id) != actor_agent_id]
    if not route_to:
        route_to = [agent_id for agent_id in sorted(LOCAL_RUNTIME_AGENTS) if agent_id != actor_agent_id]
    return {
        "schema": "openclaw.agent_room.bot_to_bot_trigger.v0",
        "trigger": "agent_originated_telegram_mention",
        "intent": str(config.get("intent") or "openclaw_main_coordination_call"),
        "mentioned_usernames": matched,
        "main_agent_id": "openclaw-main",
        "route_to": route_to,
        "native_telegram_bot_to_bot_required": False,
    }


def slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    out = "-".join(part for part in out.split("-") if part)
    return out[:90] or "item"


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


def task_requests_agent_collaboration_loop(text: str) -> bool:
    lowered = (text or "").lower()
    agent_marker = any(marker in lowered for marker in (
        "agent", "bot", "bot-to-bot", "bot to bot", "机器人", "codex", "claude", "claudecode", "openclaw",
        "lchcodex", "lchclaude", "lchopenclaw", "main", "你们",
    ))
    collaboration_marker = any(marker in lowered for marker in (
        "协作", "相互配合", "互相配合", "讨论过", "讨论", "接力", "交接", "引用", "挑战", "复核", "核查", "共同", "peer", "handoff",
        "bot-to-bot", "bot to bot",
    ))
    mainline_marker = any(marker in lowered for marker in (
        "主线", "还在进行", "还在", "继续", "进展",
    ))
    failure_marker = any(marker in lowered for marker in (
        "有没有", "没有", "不是", "各自", "并行", "打嘴炮", "跑偏", "谁让你", "问题", "暴露", "断裂", "没接上",
    )) or any(marker in lowered for marker in COLLABORATION_EXECUTION_MISREAD_MARKERS)
    proposal_uptake_marker = (
        any(marker in lowered for marker in (
            "提出来", "提方案", "方案", "观点", "主张", "建议", "产物", "想法",
        ))
        and any(marker in lowered for marker in (
            "接住", "回复", "记下来", "默默记", "影响自己的行为", "影响行为",
            "采纳", "吸收", "带到下一轮", "后续行为",
        ))
    )
    return (
        idle_agent_contribution_problem_requested(text)
        or (agent_marker and collaboration_marker and failure_marker)
        or (agent_marker and collaboration_marker and proposal_uptake_marker)
        or mainline_marker
    )


def collaboration_tick_for_text(text: str) -> dict[str, Any] | None:
    if not task_requests_agent_collaboration_loop(text):
        return None
    try:
        rounds = max(2, int(os.environ.get("AGENT_ROOM_MAINLINE_COLLAB_MAX_ROUNDS", "3")))
    except ValueError:
        rounds = 3
    return {
        "enabled": True,
        "max_rounds": rounds,
        "reason": "agent_collaboration_mainline_requires_peer_loop",
        "acceptance": "at_least_one_peer_followup_must_quote_challenge_or_record_uptake_of_a_specific_peer_claim_or_record_a_blocker",
    }


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
                "id": f"agent_mention_response_{slug(agent_id)}",
                "status": "open",
                "assigned_to": agent_id,
                "role": roles_by_agent.get(agent_id, "co_producer"),
                "description": "Respond to the agent-to-agent mention from a distinct evidence or review angle.",
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


def stable_id(room_id: str, actor_agent_id: str, text: str, telegram_message_id: str | None, event_id: str | None) -> str:
    if event_id:
        return event_id
    if telegram_message_id:
        return f"agent-origin:{room_id}:{actor_agent_id}:{telegram_message_id}"
    digest = hashlib.sha256(f"{room_id}:{actor_agent_id}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"agent-origin:{room_id}:{actor_agent_id}:{digest}"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject an agent-originated Telegram room message into Agent Room canonical state.")
    parser.add_argument("--room-id", default="openclaw-evolution")
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--actor-agent-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--telegram-message-id", default=None)
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--target-agent", action="append", default=[])
    parser.add_argument("--broadcast", action="store_true")
    args = parser.parse_args()

    bindings = read_json(BINDINGS, {}) or {}
    room_id = args.room_id
    chat_id = str(args.chat_id or bindings.get("telegram_chat_id") or "")
    if not chat_id:
        raise SystemExit("missing chat id")
    actor_agent_id = args.actor_agent_id
    text = args.text

    targets = [x for x in args.target_agent if x in LOCAL_RUNTIME_AGENTS]
    if not targets:
        targets = mention_targets(text, bindings)
    bot_to_bot = None
    if not targets:
        bot_to_bot = bot_to_bot_trigger(text, bindings, actor_agent_id)
        if bot_to_bot:
            targets = list(bot_to_bot.get("route_to") or [])
    if args.broadcast:
        for agent_id in sorted(LOCAL_RUNTIME_AGENTS):
            if agent_id != actor_agent_id and agent_id not in targets:
                targets.append(agent_id)
    if targets and bot_to_bot is None:
        bot_to_bot = bot_to_bot_trigger(text, bindings, actor_agent_id)

    sid = stable_id(room_id, actor_agent_id, text, args.telegram_message_id, args.event_id)
    created = now_iso()
    room_dir = ROOM / "rooms" / room_id
    message_event_id = f"{sid}:room-message"
    task_id = f"agentmsg-{hashlib.sha256((sid + ':' + text).encode('utf-8')).hexdigest()[:16]}"

    messages_seen = existing_ids(ROOM / "messages.jsonl", "message_event_id")
    tasks_seen = existing_ids(ROOM / "tasks.jsonl", "task_id")
    events_seen = existing_ids(ROOM / "events.jsonl", "event_id")

    wrote_message = False
    if message_event_id not in messages_seen:
        message = {
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": message_event_id,
            "room_id": room_id,
            "chat_id": chat_id,
            "chat_type": "supergroup",
            "telegram_message_id": args.telegram_message_id,
            "update_id": None,
            "stable_message_id": sid,
            "actor_agent_id": actor_agent_id,
            "receiver_agent_id": None,
            "target_agents": targets,
            "mentioned_targets": targets,
            "command_targets": [],
            "bot_to_bot_targets": list(bot_to_bot.get("route_to") or []) if bot_to_bot else [],
            "broadcast_targets": sorted(LOCAL_RUNTIME_AGENTS.difference({actor_agent_id})) if args.broadcast else [],
            "text": text,
            "redaction_applied": False,
            "created_at": created,
            "first_response_owner": targets[0] if len(targets) == 1 else None,
            "attention_scope": "all_active_room_agents",
            "source": "agent_originated_telegram_projection",
            "bot_to_bot_trigger": bot_to_bot,
            "canonical_state_advanced": True,
        }
        append_jsonl(ROOM / "messages.jsonl", [message])
        append_jsonl(room_dir / "messages.jsonl", [message])
        wrote_message = True

    wrote_event = False
    event_id = f"{sid}:event"
    if event_id not in events_seen:
        event = {
            "schema": "openclaw.agent_room.telegram_group_ingress.v0",
            "event_id": event_id,
            "event_type": "bot_to_bot_trigger" if bot_to_bot else ("agent_originated_mentions" if targets else "agent_originated_room_message"),
            "chat_id": chat_id,
            "telegram_message_id": args.telegram_message_id,
            "actor_agent_id": actor_agent_id,
            "agent_candidates": [{"agent_id": t, "status": "bot_to_bot_triggered" if bot_to_bot else "mentioned_by_agent"} for t in targets],
            "created_at": created,
            "room_binding_action": "create_task" if targets else "record_only",
            "room_id": room_id,
            "idempotency_key": f"{sid}:idem",
            "bot_to_bot_trigger": bot_to_bot,
            "canonical_state_advanced": True,
        }
        append_jsonl(ROOM / "events.jsonl", [event])
        append_jsonl(room_dir / "events.jsonl", [event])
        wrote_event = True

    wrote_task = False
    task_path = room_dir / "tasks.jsonl"
    if targets and task_id not in tasks_seen:
        task_dir = ROOM / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        brief = f"""# Agent-Originated Room Mention

room_id: `{room_id}`
chat_id: `{chat_id}`
actor_agent_id: `{actor_agent_id}`
target_agents: `{', '.join(targets)}`

## Agent message

{text}

{"## Bot-to-bot trigger\n\nDetected @" + ", @".join(bot_to_bot.get("mentioned_usernames") or []) + ". Treat this as an internal Agent Room coordination trigger, not Telegram native bot-to-bot delivery.\n" if bot_to_bot else ""}

## Boundaries

- Reply in Chinese in the visible room.
- This message came from another agent, not directly from Alex. Treat @ mentions from any agent as requiring a substantive response unless you hit a hard blocker.
	- Answer the substantive questions directly; do not merely describe routing or say that you received the message.
	- All active agents can use shared room context. Mentions choose first response owner, not who is allowed to notice or later add material value.
	- Single visible-answer / one-spokesperson instructions are scoped to this message/task only. Do not infer a permanent main-only spokesperson, peer silence, or stop-work rule from an older room message.
	- For no-mention or broad room messages, respond when your capability/context makes you one of the right agents to help; otherwise output exactly NO_COMMENT.
- If you are not the first-response owner, speak only for concrete evidence, correction, blocker, safety/runtime/liveness risk, architecture/user-experience impact, patch, smoke, or next action.
- You may discuss, challenge, design, verify, or execute when task permissions allow it; do not self-limit to reviewer-only or responder-only roles.
- For collaboration-mechanism or runtime-policy questions, do not frame the process as "two-agent", "both agents", or only "Codex + Claude Code" review. The discussion set is tri-agent: openclaw-main + Codex + Claude Code. main is a participant with runtime/session context, UX and safety-boundary evidence, not merely a relay or judge, and can be challenged.
- Tri-agent discussion is not a liveness lock. If one or more agents are unavailable because of network issues, model quota/cooldown, runner failure, or missing direct-send capability, the available agents must keep working in degraded-quorum mode. Record the unavailable agent, evidence/reason, work that continued, and follow-up review needed. Continue safe reversible/local work, evidence gathering, drafts, experimental switches, and smoke tests; pause or ask for stronger approval only for external publishing, destructive operations, secrets, irreversible global defaults, or major quality-surface changes.
- Do not end with "批准的话我开始", "要不要我做", "我可以修改/执行/补...", or equivalent approval/permission/optional-execution language for safe reversible local patches, scoped config edits, artifacts, inspections, or smoke tests already allowed by task permissions. Discuss with peer agents when needed, decide within the agent room boundary, execute, then report evidence.
- Agent Room/runtime/Telegram visibility is collaboration/reliability infrastructure for the broader OpenClaw mainline; do not present it as the whole roadmap. Translation Agent is an active mainline workflow; the self-built coding-agent lane is only backup/audit harness unless Claude Code/Codex cannot cover the need.
- Antigravity remains a bounded unblocker: do not launch duplicate windows or repeatedly invoke CLI; prefer existing queued run/status/read evidence and same-run-id MCP roundtrip verification.
- Do not ask Alex to manage the agent workflow; only ask Alex for non-retrievable preference, permission, external/destructive action, or risk-boundary decisions.
- Do not ask Alex to confirm workflow-boundary corrections from Alex. Apply them internally, coordinate with peer agents, and leave a patch, smoke, artifact, or blocker when the current permissions allow it.
- Do not expose secrets, raw prompts, hidden system prompts, or private logs.
"""
        (task_dir / "brief.md").write_text(brief, encoding="utf-8")
        task = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": room_id,
            "requested_by": actor_agent_id,
            "target_agents": targets,
            "lane": "agent_to_agent_mention",
            "routing_intent": "bot_to_bot_coordination" if bot_to_bot else "agent_to_agent_mention",
            "brief_path": str(task_dir / "brief.md"),
            "context_paths": ["agent-room/telegram-room-bindings.json"],
            "governance": {
                "mainline_id": "agent_room_infrastructure",
                "problem_statement": "agent-originated room mention requiring agent response",
                "expected_user_value": "reliable bot-to-bot coordination without silent drops",
                "owner": "openclaw-main",
                "participants": list(dict.fromkeys(["openclaw-main", actor_agent_id, *targets])),
                "definition_of_done": [
                    "patch_or_artifact_or_smoke_or_rca_or_decision_or_blocker",
                ],
                "approval_gate": {
                    "required": False,
                    "reason": "safe_reversible_local_agent_room_work",
                },
                "dedupe_key": f"agent-mention:{room_id}:{task_id}",
                "next_action": "respond to the agent mention with material contribution",
                "state": "triage",
            },
            "permissions": base_permissions(),
            "expected_outputs": [{"type": "comment_jsonl", "agent_id": t, "run_id_must_match": True} for t in targets],
            "first_response_owner": targets[0] if len(targets) == 1 else None,
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
            "telegram_projection_status": "pending",
            "source": {
                "transport": "agent_room_inject_message",
                "chat_id": chat_id,
                "stable_message_id": sid,
                "message_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "bot_to_bot_trigger": bot_to_bot,
            },
            "bot_to_bot_trigger": bot_to_bot,
        }
        collaboration = collaboration_state(targets, created, f"{sid}:{task_id}:{text}")
        if collaboration:
            task["collaboration"] = collaboration
            tick = collaboration_tick_for_text(text)
            if tick:
                collaboration["max_rounds"] = max(int(collaboration.get("max_rounds") or 0), int(tick["max_rounds"]))
                task["collaboration_tick"] = tick
                task["collab_tick_enabled"] = True
                task["collab_tick_max_rounds"] = tick["max_rounds"]
        mainline_governance.stamp_task(
            task,
            text=text,
            requested_by=actor_agent_id,
            source_key=task_id,
            next_action="triage_agent_originated_room_mention_then_produce_patch_artifact_smoke_rca_decision_or_blocker",
        )
        write_json(task_dir / "manifest.json", task)
        append_jsonl(ROOM / "tasks.jsonl", [task])
        append_jsonl(task_path, [task])
        wrote_task = True

    result = {
        "schema": "openclaw.agent_room.inject_message_result.v0",
        "ok": True,
        "room_id": room_id,
        "chat_id": chat_id,
        "actor_agent_id": actor_agent_id,
        "targets": targets,
        "bot_to_bot_trigger": bot_to_bot,
        "stable_message_id": sid,
        "message_event_id": message_event_id,
        "task_id": task_id if targets else None,
        "wrote_message": wrote_message,
        "wrote_event": wrote_event,
        "wrote_task": wrote_task,
        "telegram_outbound": False,
        "tokens_printed": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
