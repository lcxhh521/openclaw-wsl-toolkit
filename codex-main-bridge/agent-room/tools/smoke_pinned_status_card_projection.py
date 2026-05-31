#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "pinned-status-card-projection-smoke"


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


def install_roots(module: Any, bridge_root: Path, room: Path, status_dir: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    if hasattr(module, "ACTIVE_RUNNERS"):
        module.ACTIVE_RUNNERS = room / "active-runners"
    if hasattr(module, "TASKS"):
        module.TASKS = room / "tasks"
    if hasattr(module, "STATUS_DIR"):
        module.STATUS_DIR = status_dir
    if hasattr(module, "COLLAB_LEDGER_DIR"):
        module.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    if hasattr(module, "DAEMON_STATUS"):
        module.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    if hasattr(module, "AGENT_PRESENCE_DIR"):
        module.AGENT_PRESENCE_DIR = room / "agent-presence"
    if hasattr(module, "DEFAULT_STATE_PATH"):
        module.DEFAULT_STATE_PATH = status_dir / "pinned-card-state.json"
    if hasattr(module, "BOT_META"):
        module.BOT_META = room / "telegram_agent_bots.json"
    if hasattr(module, "ROOM_BINDINGS"):
        module.ROOM_BINDINGS = room / "telegram-room-bindings.json"


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    chat_id = "-1009000000001"

    write_json(
        room / "telegram-room-bindings.json",
        {
            "schema": "openclaw.agent_room.telegram_room_bindings.v0",
            "room_id": "openclaw-evolution",
            "telegram_chat_id": chat_id,
            "participants": [
                {
                    "agent_id": "openclaw-main",
                    "role": "coordinator_participant",
                    "telegram_bot": "@lchopenclaw_bot",
                },
                {"agent_id": "codex", "role": "peer_agent", "telegram_bot": "@lchcodex_bot"},
            ],
        },
    )
    write_json(
        room / "telegram_agent_bots.json",
        {
            "schema": "openclaw.agent_room.telegram_agent_bots.v0",
            "bots": [
                {
                    "agent_id": "codex",
                    "telegram_username": "lchcodex_bot",
                    "token_secret_ref": "env:CODEX_AGENT_TELEGRAM_BOT_TOKEN",
                }
            ],
        },
    )
    write_json(
        room / "agent_room_bridge_daemon.status.json",
        {
            "schema": "openclaw.agent_room.bridge_daemon_status.v0",
            "tick": 1,
            "last_tick_ok": True,
            "last_tick_finished_at": "2026-05-27T17:35:00+08:00",
            "telegram_outbound_enabled": True,
            "standing_agenda_tick": {"result": {"status": "suppressed_fresh_user_task"}},
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status")
    pinned = load_module(TOOLS / "pinned_status_card.py", "pinned_status_card_under_test")
    install_roots(status_tool, bridge_root, room, status_dir)
    install_roots(pinned, bridge_root, room, status_dir)
    pinned.collaboration_status = status_tool

    missing_projection = pinned.build_projection(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        message_id=None,
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )
    peer_projection = pinned.build_projection(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        message_id=None,
        state_path=status_dir / "pinned-card-state.json",
        agent_id="codex",
    )
    missing_live = pinned.execute_live(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )

    meta = json.loads((room / "telegram_agent_bots.json").read_text(encoding="utf-8"))
    meta["bots"].append(
        {
            "agent_id": "openclaw-main",
            "telegram_username": "lchopenclaw_bot",
            "token_secret_ref": "env:OPENCLAW_MAIN_TELEGRAM_BOT_TOKEN",
            "secret_verified": False,
            "pinned_status_transport": "openclaw_cli",
        }
    )
    write_json(room / "telegram_agent_bots.json", meta)
    unverified_projection = pinned.build_projection(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        message_id="1999",
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )
    meta["bots"][-1]["secret_verified"] = True
    write_json(room / "telegram_agent_bots.json", meta)
    ready_projection = pinned.build_projection(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        message_id="1999",
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
    )
    write_json(
        status_dir / "pinned-card-state.json",
        {
            "message_id": "2001",
            "chat_id": chat_id,
            "room_id": "openclaw-evolution",
            "agent_id": "codex",
            "telegram_bot": "@lchcodex_bot",
        },
    )
    auto_projection = pinned.build_projection(
        room_id="openclaw-evolution",
        chat_id=chat_id,
        message_id=None,
        state_path=status_dir / "pinned-card-state.json",
        agent_id="auto",
    )

    missing_preflight = missing_projection.get("activation_preflight") or {}
    peer_preflight = peer_projection.get("activation_preflight") or {}
    unverified_preflight = unverified_projection.get("activation_preflight") or {}
    ready_preflight = ready_projection.get("activation_preflight") or {}
    auto_preflight = auto_projection.get("activation_preflight") or {}
    failures: list[str] = []
    check(
        "create projection still plans send and pin without live Telegram",
        [row.get("method") for row in (missing_projection.get("actions") or [])] == ["sendMessage", "pinChatMessage"]
        and missing_projection.get("telegram_outbound") is False,
        failures,
    )
    check(
        "missing openclaw-main metadata blocks activation before token lookup",
        missing_projection.get("activation_ready") is False
        and "missing_agent_room_bot_metadata" in (missing_preflight.get("blockers") or []),
        failures,
    )
    check(
        "live path returns structured preflight error instead of unknown-agent traceback",
        missing_live.get("ok") is False
        and missing_live.get("error") == "activation_preflight_failed"
        and "missing_agent_room_bot_metadata" in ((missing_live.get("activation_preflight") or {}).get("blockers") or []),
        failures,
    )
    check(
        "explicit peer bot is blocked from fixed-card ownership",
        peer_projection.get("agent_id") == "codex"
        and peer_projection.get("activation_ready") is False
        and "status_card_owner_must_be_openclaw-main" in (peer_preflight.get("blockers") or []),
        failures,
    )
    check(
        "openclaw-main pinned card uses native OpenClaw transport instead of copied token",
        unverified_projection.get("activation_ready") is True
        and unverified_preflight.get("transport") == "openclaw_cli"
        and "secret_not_verified" not in (unverified_preflight.get("blockers") or []),
        failures,
    )
    check(
        "registered metadata makes edit projection activation-ready but still local-only",
        ready_projection.get("activation_ready") is True
        and ready_preflight.get("can_attempt_live") is True
        and [row.get("method") for row in (ready_projection.get("actions") or [])] == ["editMessageText"]
        and ready_projection.get("telegram_outbound") is False,
        failures,
    )
    check(
        "preflight keeps group admin permission as a separate live verification gate",
        bool(ready_preflight.get("permission_check_still_required")),
        failures,
    )
    check(
        "auto status owner ignores peer-owned state and resolves to main",
        auto_projection.get("agent_id") == "openclaw-main"
        and auto_projection.get("requested_agent_id") == "auto"
        and auto_projection.get("message_id") is None
        and auto_projection.get("mode") == "create_then_pin"
        and (auto_projection.get("ignored_state") or {}).get("reason") == "state_message_owned_by_different_bot_identity"
        and auto_preflight.get("can_attempt_live") is True,
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.pinned_status_card_projection_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "missing_projection": {
            "mode": missing_projection.get("mode"),
            "activation_ready": missing_projection.get("activation_ready"),
            "blockers": missing_preflight.get("blockers"),
            "actions": [row.get("method") for row in (missing_projection.get("actions") or [])],
        },
        "missing_live": {
            "ok": missing_live.get("ok"),
            "error": missing_live.get("error"),
            "blockers": ((missing_live.get("activation_preflight") or {}).get("blockers") or []),
        },
        "peer_projection": {
            "agent_id": peer_projection.get("agent_id"),
            "activation_ready": peer_projection.get("activation_ready"),
            "blockers": peer_preflight.get("blockers"),
        },
        "unverified_projection": {
            "mode": unverified_projection.get("mode"),
            "activation_ready": unverified_projection.get("activation_ready"),
            "transport": unverified_preflight.get("transport"),
            "blockers": unverified_preflight.get("blockers"),
        },
        "ready_projection": {
            "mode": ready_projection.get("mode"),
            "activation_ready": ready_projection.get("activation_ready"),
            "actions": [row.get("method") for row in (ready_projection.get("actions") or [])],
            "permission_check_still_required": ready_preflight.get("permission_check_still_required"),
        },
        "auto_projection": {
            "agent_id": auto_projection.get("agent_id"),
            "requested_agent_id": auto_projection.get("requested_agent_id"),
            "mode": auto_projection.get("mode"),
            "message_id": auto_projection.get("message_id"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
