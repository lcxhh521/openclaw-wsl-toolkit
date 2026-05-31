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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "pinned-status-card-edit-failure-policy"


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


class FakeTelegram:
    def __init__(self, *, edit_result: dict[str, Any]) -> None:
        self.edit_result = edit_result
        self.calls: list[str] = []
        self.pinned_message_id = "2109"

    def bot_token(self, agent_id: str) -> str:
        self.calls.append(f"bot_token:{agent_id}")
        return "fake-token"

    def edit_message_text(self, token: str, chat_id: str, message_id: str, text: str) -> dict[str, Any]:
        self.calls.append(f"edit:{message_id}")
        return self.edit_result

    def send_message(self, token: str, chat_id: str, text: str) -> dict[str, Any]:
        self.calls.append("send")
        return {"ok": True, "result": {"message_id": 2117}}

    def pin_chat_message(self, token: str, chat_id: str, message_id: str, disable_notification: bool = True) -> dict[str, Any]:
        self.calls.append(f"pin:{message_id}")
        self.pinned_message_id = str(message_id)
        return {"ok": True, "result": True}

    def _telegram_api_call(self, token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(method)
        return {"ok": True, "result": {"pinned_message": {"message_id": int(self.pinned_message_id)}}}


def seed_room(room: Path, status_dir: Path) -> None:
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
                    "telegram_bot": "@lchopenclaw_bot",
                    "token_secret_ref": "env:OPENCLAW_MAIN_TELEGRAM_BOT_TOKEN",
                    "secret_verified": True,
                    "pinned_status_transport": "telegram_bot_api",
                }
            ],
        },
    )
    write_json(
        room / "agent_room_bridge_daemon.status.json",
        {
            "schema": "openclaw.agent_room.bridge_daemon_status.v0",
            "status": "running",
            "tick": 231,
            "last_tick_ok": True,
            "telegram_outbound_enabled": True,
        },
    )
    write_json(
        status_dir / "pinned-card-state.json",
        {
            "message_id": "2109",
            "chat_id": "-1009000000001",
            "room_id": "openclaw-evolution",
            "agent_id": "openclaw-main",
            "telegram_bot": "@lchopenclaw_bot",
            "transport": "telegram_bot_api",
        },
    )


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    seed_room(room, status_dir)

    collaboration_status = load_module(TOOLS / "collaboration_status.py", "collaboration_status")
    pinned = load_module(TOOLS / "pinned_status_card.py", "pinned_status_card_under_test")
    install_roots(collaboration_status, bridge_root, room, status_dir)
    install_roots(pinned, bridge_root, room, status_dir)
    pinned.collaboration_status = collaboration_status

    transient_api = FakeTelegram(
        edit_result={
            "ok": False,
            "description": "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol>",
        }
    )
    transient_result = pinned.execute_live(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
        _telegram_helpers=transient_api,
    )
    transient_state = json.loads((status_dir / "pinned-card-state.json").read_text(encoding="utf-8"))

    write_json(status_dir / "pinned-card-state.json", {**transient_state, "message_id": "2109"})
    stale_api = FakeTelegram(edit_result={"ok": False, "description": "Bad Request: message to edit not found"})
    stale_result = pinned.execute_live(
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
        state_path=status_dir / "pinned-card-state.json",
        agent_id="openclaw-main",
        _telegram_helpers=stale_api,
    )
    stale_state = json.loads((status_dir / "pinned-card-state.json").read_text(encoding="utf-8"))

    failures: list[str] = []
    check(
        "transient edit failure is classified without recreation",
        transient_result.get("error") == "edit_existing_failed_no_recreate"
        and transient_result.get("edit_failure_kind") == "transient_failure",
        failures,
    )
    check("transient path does not send a duplicate card", "send" not in transient_api.calls, failures)
    check("transient path does not pin a replacement card", not any(call.startswith("pin:") for call in transient_api.calls), failures)
    check("transient path preserves existing message id", transient_state.get("message_id") == "2109", failures)
    check(
        "stale edit failure may recreate and pin",
        stale_result.get("ok") is True
        and stale_result.get("message_id") == "2117"
        and "send" in stale_api.calls
        and "pin:2117" in stale_api.calls,
        failures,
    )
    check("stale path updates state to replacement message", stale_state.get("message_id") == "2117", failures)

    result = {
        "schema": "openclaw.agent_room.pinned_status_card_edit_failure_policy_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "transient_result": {
            "ok": transient_result.get("ok"),
            "error": transient_result.get("error"),
            "edit_failure_kind": transient_result.get("edit_failure_kind"),
            "message_id": transient_result.get("message_id"),
            "calls": transient_api.calls,
        },
        "stale_result": {
            "ok": stale_result.get("ok"),
            "error": stale_result.get("error"),
            "message_id": stale_result.get("message_id"),
            "calls": stale_api.calls,
        },
    }
    write_json(DRY_RUN / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
