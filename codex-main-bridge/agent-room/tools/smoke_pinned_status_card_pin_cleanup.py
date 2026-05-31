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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "pinned-status-card-pin-cleanup-smoke"


def load_module(path: Path, name: str) -> Any:
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


class FakeTelegram:
    def __init__(self) -> None:
        self.unpinned: list[str] = []

    def unpin_chat_message(self, token: str, chat_id: str, message_id: str | int) -> dict[str, Any]:
        self.unpinned.append(str(message_id))
        return {"ok": True, "result": True}


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    DRY_RUN.mkdir(parents=True, exist_ok=True)
    pinned = load_module(TOOLS / "pinned_status_card.py", "pinned_status_card_cleanup_under_test")
    pinned.ROOM = DRY_RUN / "agent-room"
    pinned.STATUS_DIR = pinned.ROOM / "collaboration-status"
    pinned.STATUS_DIR.mkdir(parents=True, exist_ok=True)
    calls: list[list[str]] = []
    pins_payload = {
        "ok": True,
        "result": [
            {
                "message_id": 2117,
                "text": "📌 OpenClaw 状态 20:09:08\nmain 🟢在线\n细节在本地 status；这里只编辑这一条，不刷屏。",
            },
            {
                "message_id": 2069,
                "text": "📌 OpenClaw 状态 18:50:00\nmain 🟢在线\n细节在本地 status；这里只编辑这一条，不刷屏。",
            },
            {
                "message_id": 1800,
                "text": "A human-maintained pinned note that must not be touched.",
            },
        ],
    }

    def fake_run_openclaw_message(args: list[str], *, timeout: int = 45) -> dict[str, Any]:
        calls.append(args)
        if args and args[0] == "pins":
            return {"ok": True, "payload": pins_payload}
        if args and args[0] == "unpin":
            return {"ok": True, "payload": {"result": True}}
        return {"ok": False, "error": "unexpected command"}

    pinned.openclaw_cli_available = lambda: True
    pinned.run_openclaw_message = fake_run_openclaw_message

    fake_telegram = FakeTelegram()
    bot_cleanup = pinned.cleanup_extra_status_card_pins(
        chat_id="-1009000000001",
        keep_message_id="2117",
        tar=fake_telegram,
        token="redacted-token",
    )
    cli_cleanup = pinned.cleanup_extra_status_card_pins(
        chat_id="-1009000000001",
        keep_message_id="2117",
    )

    (pinned.ROOM / "artifacts").mkdir(parents=True, exist_ok=True)
    (pinned.ROOM / "artifacts" / "pinned-card-previous-status.json").write_text(
        json.dumps({"message_id": "2026", "note": "known previous status card message"}, ensure_ascii=False),
        encoding="utf-8",
    )
    resident_run = pinned.ROOM / "resident-runs" / "20260527-194611"
    resident_run.mkdir(parents=True, exist_ok=True)
    (resident_run / "result.json").write_text(
        json.dumps({"pinned_card": {"message_id": "2109", "steps": ["editMessageText", "pinChatMessage"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    daemon_tick = pinned.ROOM / "daemon-runs" / "telegram-agent-bridge" / "20260527-185321" / "tick-000232"
    daemon_tick.mkdir(parents=True, exist_ok=True)
    (daemon_tick / "pinned-card-tick.json").write_text(
        json.dumps({"result": {"message_id": "2034", "steps": ["sendMessage", "pinChatMessage"]}}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_run_openclaw_message_no_pins(args: list[str], *, timeout: int = 45) -> dict[str, Any]:
        calls.append(args)
        if args and args[0] == "pins":
            return {"ok": False, "stderr_tail": "GatewayClientRequestError: Error: Unsupported Telegram action: list-pins"}
        return {"ok": False, "error": "unexpected command"}

    pinned.run_openclaw_message = fake_run_openclaw_message_no_pins
    fallback_telegram = FakeTelegram()
    fallback_cleanup = pinned.cleanup_extra_status_card_pins(
        chat_id="-1009000000001",
        keep_message_id="2117",
        tar=fallback_telegram,
        token="redacted-token",
    )

    failures: list[str] = []
    check("bot cleanup succeeds", bot_cleanup.get("ok") is True, failures)
    check("bot cleanup only unpins old status card", fake_telegram.unpinned == ["2069"], failures)
    check("bot cleanup keeps canonical pin", "2117" not in fake_telegram.unpinned, failures)
    check("bot cleanup ignores non-status pinned note", "1800" not in fake_telegram.unpinned, failures)
    check("cli cleanup succeeds", cli_cleanup.get("ok") is True, failures)
    check(
        "cli cleanup only calls unpin for old status card",
        [call for call in calls if call and call[0] == "unpin"] == [
            ["unpin", "--channel", "telegram", "--target", "-1009000000001", "--message-id", "2069"]
        ],
        failures,
    )
    check("fallback cleanup succeeds without list-pins", fallback_cleanup.get("ok") is True, failures)
    check("fallback uses local known status card ids", fallback_cleanup.get("candidate_source") == "local_known_status_card_ids_after_pin_list_failed", failures)
    check(
        "fallback unpins locally known old status cards from artifacts and run history",
        set(fallback_telegram.unpinned) == {"2026", "2109", "2034"},
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.pinned_status_card_pin_cleanup_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "bot_cleanup": {
            "status": bot_cleanup.get("status"),
            "candidate_message_ids": bot_cleanup.get("candidate_message_ids"),
            "unpin_message_ids": bot_cleanup.get("unpin_message_ids"),
        },
        "cli_cleanup": {
            "status": cli_cleanup.get("status"),
            "candidate_message_ids": cli_cleanup.get("candidate_message_ids"),
            "unpin_message_ids": cli_cleanup.get("unpin_message_ids"),
        },
        "fallback_cleanup": {
            "status": fallback_cleanup.get("status"),
            "candidate_source": fallback_cleanup.get("candidate_source"),
            "candidate_message_ids": fallback_cleanup.get("candidate_message_ids"),
            "unpin_message_ids": fallback_cleanup.get("unpin_message_ids"),
        },
        "calls": calls,
    }
    (DRY_RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
