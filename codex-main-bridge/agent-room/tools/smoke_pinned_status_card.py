#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "pinned-status-card-smoke"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


class FakeTelegramCleanup:
    def __init__(self) -> None:
        self.unpinned: list[str] = []

    def unpin_chat_message(self, token: str, chat_id: str, message_id: str) -> dict[str, Any]:
        self.unpinned.append(str(message_id))
        return {"ok": True, "token_seen": bool(token), "chat_id": chat_id, "message_id": message_id}


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    now = datetime.now(timezone.utc).astimezone()
    now_text = now.isoformat(timespec="seconds")
    soft_deadline = (now + timedelta(minutes=3)).isoformat(timespec="seconds")
    hard_deadline = (now + timedelta(minutes=28)).isoformat(timespec="seconds")
    current_task_id = "tg-openclaw-evolution-current-question"
    (room / "active-runners").mkdir(parents=True, exist_ok=True)
    write_json(
        room / "telegram-room-bindings.json",
        {
            "schema": "openclaw.agent_room.telegram_room_bindings.v0",
            "room_id": "openclaw-evolution",
            "telegram_chat_id": "-1009000000001",
            "participants": [
                {"agent_id": "openclaw-main", "role": "coordinator_participant", "telegram_bot": "@lchopenclaw_bot"},
                {"agent_id": "codex", "role": "peer_agent", "telegram_bot": "@lchcodex_bot"},
                {"agent_id": "claude-code", "role": "peer_agent", "telegram_bot": "@lchclaudecode_bot"},
            ],
        },
    )
    write_json(
        room / "telegram_agent_bots.json",
        {
            "schema": "openclaw.agent_room.telegram_agent_bots.v0",
            "bots": [
                {
                    "agent_id": "openclaw-main",
                    "telegram_username": "lchopenclaw_bot",
                    "pinned_status_transport": "openclaw_cli",
                }
            ],
        },
    )
    write_json(
        room / "agent_room_bridge_daemon.status.json",
        {
            "schema": "openclaw.agent_room.bridge_daemon_status.v0",
            "status": "running",
            "tick": 42,
            "last_tick_ok": True,
            "last_tick_finished_at": now_text,
            "telegram_outbound_enabled": True,
            "standing_agenda_tick": {"ok": True, "result": {"status": "suppressed_active_runner"}},
        },
    )
    task_dir = room / "tasks" / current_task_id
    brief_path = task_dir / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(
        "# Telegram Agent Room Task\n\n"
        "通道: this diagnostic heading must not leak into the pinned status card.\n"
        f"raw task id: {current_task_id}\n",
        encoding="utf-8",
    )
    write_json(
        task_dir / "manifest.json",
        {
            "schema": "openclaw.agent_room.task_manifest.v0",
            "task_id": current_task_id,
            "run_id": current_task_id,
            "room_id": "openclaw-evolution",
            "status": "running",
            "created_at": now_text,
            "target_agents": ["codex", "claude-code"],
            "brief_path": str(brief_path),
            "source": {
                "transport": "telegram",
                "chat_id": "-1009000000001",
                "update_id": "smoke",
            },
        },
    )

    collaboration_status = load_module(TOOLS / "collaboration_status.py", "collaboration_status")
    collaboration_status.ROOT = bridge_root
    collaboration_status.ROOM = room
    collaboration_status.ACTIVE_RUNNERS = room / "active-runners"
    collaboration_status.TASKS = room / "tasks"
    collaboration_status.STATUS_DIR = status_dir
    collaboration_status.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    collaboration_status.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    collaboration_status.AGENT_PRESENCE_DIR = room / "agent-presence"

    pinned_status_card = load_module(TOOLS / "pinned_status_card.py", "pinned_status_card")
    pinned_status_card.ROOT = bridge_root
    pinned_status_card.ROOM = room
    pinned_status_card.STATUS_DIR = status_dir
    pinned_status_card.BOT_META = room / "telegram_agent_bots.json"
    pinned_status_card.ROOM_BINDINGS = room / "telegram-room-bindings.json"
    resident_bridge = load_module(TOOLS / "agent_room_resident_bridge.py", "agent_room_resident_bridge_pinned_smoke")

    status = {
        "generated_at": now_text,
        "daemon": {
            "status": "running",
            "tick": 42,
            "last_tick_ok": True,
            "last_tick_finished_at": now_text,
            "telegram_outbound_enabled": True,
            "standing_agenda_tick": {"result": {"status": "suppressed_active_runner"}},
        },
        "per_agent_engagement": {
            "codex": {
                "engagement_state": "working_silent_before_soft_deadline",
                "working_runner_count": 1,
                "active_runner_count": 1,
                "pending_harvest_count": 0,
                "completed_presence_count": 0,
                "needs_attention_count": 0,
                "active_task_ids": [
                    "agentmsg-caa2cba4ffc17e39",
                    current_task_id,
                ],
                "next_soft_deadline_at": soft_deadline,
            },
            "claude-code": {
                "engagement_state": "needs_attention",
                "working_runner_count": 0,
                "active_runner_count": 0,
                "pending_harvest_count": 0,
                "completed_presence_count": 0,
                "needs_attention_count": 1,
                "active_task_ids": [
                    "agentmsg-caa2cba4ffc17e39",
                    current_task_id,
                ],
                "next_hard_deadline_at": hard_deadline,
            },
        },
        "active_runners": [
            {
                "agent_id": "codex",
                "run_id": current_task_id,
                "task_id": current_task_id,
                "room_id": "openclaw-evolution",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": False,
                "runner_state": "working_silent_before_soft_deadline",
                "soft_deadline_at": soft_deadline,
            },
            {
                "agent_id": "claude-code",
                "run_id": current_task_id,
                "task_id": current_task_id,
                "room_id": "openclaw-evolution",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": True,
                "runner_state": "over_soft_deadline_no_output",
                "hard_deadline_at": hard_deadline,
            },
            {
                "agent_id": "codex",
                "run_id": "agentmsg-caa2cba4ffc17e39",
                "task_id": "agentmsg-caa2cba4ffc17e39",
                "room_id": "openclaw-evolution",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": False,
                "runner_state": "working_silent_before_soft_deadline",
                "soft_deadline_at": soft_deadline,
            },
            {
                "agent_id": "claude-code",
                "run_id": "agentmsg-caa2cba4ffc17e39",
                "task_id": "agentmsg-caa2cba4ffc17e39",
                "room_id": "openclaw-evolution",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": True,
                "runner_state": "over_soft_deadline_no_output",
                "hard_deadline_at": hard_deadline,
            },
            {
                "agent_id": "codex",
                "run_id": "dm-codex-background",
                "task_id": "dm-codex-background",
                "room_id": "dm-codex-100000001",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": True,
                "runner_state": "over_soft_deadline_no_output",
                "hard_deadline_at": hard_deadline,
            },
        ],
    }
    card = collaboration_status.fixed_status_card(status, room_id="openclaw-evolution", chat_id="-1009000000001")
    diagnostic_status = dict(status)
    diagnostic_status["collaboration_overview"] = {
        "material_point_count": 5,
        "peer_uptake_count": 2,
        "peer_challenge_count": 1,
        "expired_claim_count": 3,
        "degraded_quorum_task_count": 4,
        "runner_attention_task_count": 2,
    }
    diagnostic_card = collaboration_status.fixed_status_card(
        diagnostic_status,
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
    )
    create_projection = pinned_status_card.build_projection(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        message_id=None,
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )
    edit_projection = pinned_status_card.build_projection(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        message_id="1999",
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )
    foreign_state_path = status_dir / "foreign-pinned-card-state.json"
    write_json(
        foreign_state_path,
        {
            "message_id": "2001",
            "chat_id": "-1009000000001",
            "room_id": "openclaw-evolution",
            "agent_id": "claude-code",
        },
    )
    foreign_state_projection = pinned_status_card.build_projection(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        message_id=None,
        state_path=foreign_state_path,
        agent_id="openclaw-main",
    )
    owner_state_path = status_dir / "owner-pinned-card-state.json"
    write_json(
        owner_state_path,
        {
            "message_id": "2002",
            "chat_id": "-1009000000001",
            "room_id": "openclaw-evolution",
            "agent_id": "openclaw-main",
        },
    )
    auto_owner_projection = pinned_status_card.build_projection(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        message_id=None,
        state_path=owner_state_path,
        agent_id="auto",
    )
    transient_edit_failure = {
        "ok": False,
        "description": "URLError: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol>",
    }
    stale_edit_failure = {
        "ok": False,
        "description": "Bad Request: message to edit not found",
    }
    failures: list[str] = []
    rows = card.get("rows") or []
    check("fixed card has exactly tri-agent rows", [row.get("agent_id") for row in rows] == ["openclaw-main", "codex", "claude-code"], failures)
    check("openclaw-main row is online", rows[0].get("state") == "online", failures)
    check("codex row says working", rows[1].get("state") == "working", failures)
    check("claude-code row says needs attention", rows[2].get("state") == "needs_attention", failures)
    check("current room telegram task is shown before agentmsg task", str(rows[1].get("current_task") or "").startswith("tg-openclaw-evolution-current-question"), failures)
    card_text = str(card.get("text") or "")
    card_lines = [line for line in card_text.splitlines() if line.strip()]
    check("fixed card uses minimal display profile", card.get("display_profile") == "minimal_one_glance", failures)
    check("fixed card is exactly five lines", len(card_lines) == 5, failures)
    check(
        "fixed card text keeps tri-agent first-screen shape",
        len(card_lines) >= 4
        and card_lines[1].startswith("main ")
        and card_lines[2].startswith("Codex ")
        and card_lines[3].startswith("Claude Code "),
        failures,
    )
    check("fixed card omits long diagnostic columns", "通道:" not in card_text and "下一步:" not in card_text and "协作:" not in card_text, failures)
    check("fixed card omits collaboration diagnostics", all(marker not in card_text for marker in ("租约过期", "降级", "runner异常")), failures)
    check("fixed card omits raw task ids", "tg-" not in card_text and "agentmsg-" not in card_text and "collab-" not in card_text, failures)
    check("fixed card text mentions no-spam edit boundary", "只编辑这一条，不刷屏" in card_text, failures)
    diagnostic_card_text = str(diagnostic_card.get("text") or "")
    check("fixed card ignores collaboration overview diagnostic line", "协作:" not in diagnostic_card_text, failures)
    check("fixed card ignores collaboration overview diagnostic counters", all(marker not in diagnostic_card_text for marker in ("租约过期", "降级", "runner异常")), failures)
    check("no message id plans send then pin", [row.get("method") for row in create_projection.get("actions") or []] == ["sendMessage", "pinChatMessage"], failures)
    check("message id plans edit only", [row.get("method") for row in edit_projection.get("actions") or []] == ["editMessageText"], failures)
    check("foreign peer-owned state does not force main edit", [row.get("method") for row in foreign_state_projection.get("actions") or []] == ["sendMessage", "pinChatMessage"], failures)
    check("foreign peer-owned state records ignored reason", (foreign_state_projection.get("ignored_state") or {}).get("reason") == "state_message_owned_by_different_bot_identity", failures)
    check("auto owner resolves to main status-card identity", auto_owner_projection.get("agent_id") == "openclaw-main", failures)
    check("auto owner edits the main-owned existing message", [row.get("method") for row in auto_owner_projection.get("actions") or []] == ["editMessageText"], failures)
    check("pin permission requirement is explicit", "can_pin_messages" in json.dumps(create_projection.get("permission_requirements"), ensure_ascii=False), failures)
    check(
        "transient edit failure is not allowed to recreate pinned card",
        pinned_status_card.edit_failure_kind(transient_edit_failure) == "transient_failure"
        and not pinned_status_card.edit_failure_allows_recreate(transient_edit_failure),
        failures,
    )
    check(
        "stale edit failure is allowed to recreate pinned card",
        pinned_status_card.edit_failure_kind(stale_edit_failure) == "stale_message"
        and pinned_status_card.edit_failure_allows_recreate(stale_edit_failure),
        failures,
    )
    write_json(
        status_dir / "pinned-card-old-status.json",
        {"message_id": "3000", "text": "📌 OpenClaw 状态 old duplicate"},
    )
    write_json(
        status_dir / "pinned-card-keep-status.json",
        {"message_id": "3001", "text": "📌 OpenClaw 状态 keep"},
    )
    fake_cleanup = FakeTelegramCleanup()
    original_cli_available = pinned_status_card.openclaw_cli_available
    original_run_openclaw_message = pinned_status_card.run_openclaw_message
    try:
        pinned_status_card.openclaw_cli_available = lambda: False
        pinned_status_card.run_openclaw_message = lambda *args, **kwargs: {"ok": False, "error": "unexpected_cli_call"}
        cleanup_without_cli = pinned_status_card.cleanup_extra_status_card_pins(
            chat_id="-1009000000001",
            keep_message_id="3001",
            tar=fake_cleanup,
            token="fake-token",
        )
    finally:
        pinned_status_card.openclaw_cli_available = original_cli_available
        pinned_status_card.run_openclaw_message = original_run_openclaw_message
    check(
        "cleanup falls back to local known status ids when pins listing is unavailable",
        cleanup_without_cli.get("ok")
        and cleanup_without_cli.get("candidate_source") == "local_known_status_card_ids_cli_unavailable"
        and cleanup_without_cli.get("unpin_message_ids") == ["3000"]
        and fake_cleanup.unpinned == ["3000"],
        failures,
    )

    transient_state_path = status_dir / "pinned-card-transient-state.json"
    write_json(
        transient_state_path,
        {
            "message_id": "4001",
            "chat_id": "-1009000000001",
            "room_id": "openclaw-evolution",
            "agent_id": "openclaw-main",
        },
    )
    live_calls: list[list[str]] = []

    def fake_openclaw_message(args: list[str], **kwargs: Any) -> dict[str, Any]:
        live_calls.append(list(args))
        action = args[0] if args else ""
        if action == "pins":
            return {
                "ok": True,
                "payload": {
                    "result": [
                        {"message_id": "4000", "text": "📌 OpenClaw 状态 old duplicate"},
                        {"message_id": "4001", "text": "📌 OpenClaw 状态 keep"},
                    ]
                },
            }
        if action == "unpin":
            return {"ok": True, "payload": {"ok": True, "message_id": args[-1]}}
        if action == "edit":
            return {"ok": False, "stderr_tail": "Too Many Requests: retry after 3"}
        if action == "send":
            return {"ok": False, "error": "send should not be called on transient edit failure"}
        return {"ok": False, "error": f"unexpected action: {action}"}

    try:
        pinned_status_card.openclaw_cli_available = lambda: True
        pinned_status_card.run_openclaw_message = fake_openclaw_message
        transient_live = pinned_status_card.execute_live_openclaw_cli(
            room_id="openclaw-evolution",
            chat_id="-1009000000001",
            state_path=transient_state_path,
            agent_id="openclaw-main",
            preflight={"room_binding_bot": "@lchopenclaw_bot"},
        )
    finally:
        pinned_status_card.openclaw_cli_available = original_cli_available
        pinned_status_card.run_openclaw_message = original_run_openclaw_message
    transient_step_names = [str(row.get("step") or "") for row in transient_live.get("steps") or []]
    send_called = any(call and call[0] == "send" for call in live_calls)
    unpin_4000_called = any(call and call[0] == "unpin" and call[-1] == "4000" for call in live_calls)
    check(
        "pre-update cleanup runs before edit when an existing status card id is known",
        "extra_status_card_pin_cleanup_pre_update" in transient_step_names
        and "openclaw.message.edit" in transient_step_names
        and transient_step_names.index("extra_status_card_pin_cleanup_pre_update") < transient_step_names.index("openclaw.message.edit"),
        failures,
    )
    check(
        "transient edit failure keeps existing state and does not send replacement",
        transient_live.get("error") == "edit_existing_failed_no_recreate"
        and transient_live.get("edit_failure_kind") == "transient_failure"
        and transient_live.get("duplicate_prevention") == "kept_existing_status_card_state"
        and not send_called,
        failures,
    )
    check(
        "pre-update cleanup unpins old duplicate status card before transient failure exits",
        unpin_4000_called
        and (transient_live.get("pin_cleanup_pre_update") or {}).get("unpin_message_ids") == ["4000"],
        failures,
    )
    dry_run_plan = resident_bridge.pinned_status_card_command(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        allow_send=False,
    )
    live_plan = resident_bridge.pinned_status_card_command(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        allow_send=True,
    )
    check("resident pinned card consume-only path does not use --live", "--live" not in dry_run_plan.get("command", []), failures)
    check("resident pinned card consume-only path declares no outbound", dry_run_plan.get("telegram_outbound") is False, failures)
    check("resident pinned card live path requires --live", "--live" in live_plan.get("command", []), failures)
    check("resident pinned card live path declares outbound", live_plan.get("telegram_outbound") is True, failures)
    check("resident pinned card command delegates owner resolution to auto", "--agent-id" in dry_run_plan.get("command", []) and "auto" in dry_run_plan.get("command", []), failures)
    check("resident pinned card live command delegates owner resolution to auto", "--agent-id" in live_plan.get("command", []) and "auto" in live_plan.get("command", []), failures)
    check("resident pinned card helper is auto-owner", resident_bridge.pinned_card_agent_id() == "auto", failures)

    result = {
        "schema": "openclaw.agent_room.pinned_status_card_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "card_text": card.get("text"),
        "diagnostic_card_text": diagnostic_card.get("text"),
        "create_actions": [row.get("method") for row in create_projection.get("actions") or []],
        "edit_actions": [row.get("method") for row in edit_projection.get("actions") or []],
        "auto_owner_agent_id": auto_owner_projection.get("agent_id"),
        "auto_owner_actions": [row.get("method") for row in auto_owner_projection.get("actions") or []],
        "resident_dry_run_execution_mode": dry_run_plan.get("execution_mode"),
        "resident_live_execution_mode": live_plan.get("execution_mode"),
        "cleanup_without_cli": cleanup_without_cli,
        "transient_live_error": transient_live.get("error"),
        "transient_live_steps": transient_step_names,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
