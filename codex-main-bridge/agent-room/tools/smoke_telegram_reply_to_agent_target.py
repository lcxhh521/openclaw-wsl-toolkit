#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[3]
TOOLS = WORKSPACE / "codex-main-bridge" / "agent-room" / "tools"
BRIDGE = TOOLS / "telegram_agent_bridge.py"


def load_bridge() -> Any:
    spec = importlib.util.spec_from_file_location("telegram_agent_bridge", BRIDGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BRIDGE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_smoke(out_dir: Path) -> dict[str, Any]:
    bridge = load_bridge()
    chat_id = "-1009000000001"

    def fake_load_bot_index() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_username = {
            "lchopenclaw_bot": {"agent_id": "openclaw-main"},
            "lchcodex_bot": {"agent_id": "codex"},
            "lchclaudecode_bot": {"agent_id": "claude-code"},
        }
        by_agent = {str(value["agent_id"]): value for value in by_username.values()}
        return by_username, by_agent

    def fake_load_bindings() -> dict[str, dict[str, Any]]:
        return {
            chat_id: {
                "room_id": "openclaw-evolution",
                "title": "OpenClaw Evolution",
                "participants": ["openclaw-main", "codex", "claude-code"],
            }
        }

    bridge.load_bot_index = fake_load_bot_index
    bridge.load_bindings = fake_load_bindings

    base_chat = {
        "id": int(chat_id),
        "type": "supergroup",
        "title": "OpenClaw Evolution",
    }
    user = {"id": 100000001, "is_bot": False, "username": "alex"}
    updates = [
        {
            "update_id": 191612,
            "message": {
                "message_id": 1916,
                "chat": base_chat,
                "from": user,
                "text": "quoted reply should address the replied agent without an @ token",
                "reply_to_message": {
                    "message_id": 1915,
                    "from": {
                        "id": 424242,
                        "is_bot": True,
                        "username": "lchcodex_bot",
                        "first_name": "Codex",
                    },
                    "text": "previous Codex room reply",
                },
            },
            "receiver_agent_id": "codex",
            "receiver_bot_username": "lchcodex_bot",
        },
        {
            "update_id": 191613,
            "message": {
                "message_id": 1917,
                "chat": base_chat,
                "from": user,
                "text": "quoted reply to main should trigger room coordination",
                "reply_to_message": {
                    "message_id": 1914,
                    "from": {
                        "id": 434343,
                        "is_bot": True,
                        "username": "lchopenclaw_bot",
                        "first_name": "OpenClaw Main",
                    },
                    "text": "previous OpenClaw main room reply",
                },
            },
            "receiver_agent_id": "codex",
            "receiver_bot_username": "lchcodex_bot",
        },
        {
            "update_id": 191614,
            "message": {
                "message_id": 1918,
                "chat": base_chat,
                "from": user,
                "text": "quoted reply to a normal user should not become agent-directed",
                "reply_to_message": {
                    "message_id": 1913,
                    "from": {
                        "id": 515151,
                        "is_bot": False,
                        "username": "another_user",
                        "first_name": "Another",
                    },
                    "text": "previous normal user room message",
                },
            },
            "receiver_agent_id": "codex",
            "receiver_bot_username": "lchcodex_bot",
        },
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = bridge.normalize_updates(updates, out_dir)
    tasks = read_jsonl(out_dir / "tasks.jsonl")
    messages = read_jsonl(out_dir / "messages.jsonl")
    events = read_jsonl(out_dir / "events.jsonl")

    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    def message_by_id(message_id: str) -> dict[str, Any]:
        return next((row for row in messages if row.get("telegram_message_id") == message_id), {})

    def event_by_message_id(message_id: str) -> dict[str, Any]:
        return next((row for row in events if row.get("telegram_message_id") == message_id), {})

    def task_by_message_id(message_id: str) -> dict[str, Any]:
        suffix = f":{message_id}"
        return next((row for row in tasks if str(row.get("source", {}).get("update_id") or "").endswith(suffix)), {})

    codex_task = task_by_message_id("1916")
    codex_message = message_by_id("1916")
    codex_event = event_by_message_id("1916")
    main_task = task_by_message_id("1917")
    main_message = message_by_id("1917")
    main_event = event_by_message_id("1917")
    normal_task = task_by_message_id("1918")
    normal_message = message_by_id("1918")
    normal_event = event_by_message_id("1918")

    check("three tasks created", len(tasks) == 3)

    check("codex reply targets replied agent", codex_task.get("target_agents") == ["codex"])
    check("codex reply uses targeted reply policy", codex_task.get("delivery_policy") == "targeted_reply")
    check("codex reply first response owner is replied agent", codex_task.get("first_response_owner") == "codex")
    check("codex reply task records reply context", codex_task.get("telegram_reply_context", {}).get("reply_to_sender_agent_id") == "codex")
    check("codex reply source records reply targets", codex_task.get("source", {}).get("reply_to_agent_targets") == ["codex"])
    check("codex reply message records reply targets", codex_message.get("reply_to_agent_targets") == ["codex"])
    check("codex reply event classified as reply-to-agent", codex_event.get("event_type") == "agent_reply_to_message")

    check("main reply routes to local runtime peers", main_task.get("target_agents") == ["codex", "claude-code"])
    check("main reply uses bot-to-bot delivery", main_task.get("delivery_policy") == "broadcast_all_agents_decide")
    check("main reply has no single first owner", main_task.get("first_response_owner") is None)
    check("main reply records main bot context", main_task.get("telegram_reply_context", {}).get("reply_to_sender_agent_id") == "openclaw-main")
    check("main reply records bot-to-bot trigger", main_task.get("bot_to_bot_trigger", {}).get("trigger") == "telegram_reply_to_message")
    check("main reply event classified as bot-to-bot trigger", main_event.get("event_type") == "bot_to_bot_trigger")
    check("main reply candidates are bot-to-bot triggered", {c.get("status") for c in main_event.get("agent_candidates") or []} == {"bot_to_bot_triggered"})

    check("normal user reply does not create reply context", normal_task.get("telegram_reply_context") is None)
    check("normal user reply has no reply targets", normal_message.get("reply_to_agent_targets") == [])
    check("normal user reply is room broadcast", normal_event.get("event_type") == "room_broadcast")
    check("normal user reply has no first owner", normal_task.get("first_response_owner") is None)

    brief_path = out_dir / "briefs" / f"{codex_task.get('task_id')}.md" if codex_task.get("task_id") else None
    brief_text = brief_path.read_text(encoding="utf-8") if brief_path and brief_path.exists() else ""
    check("brief includes reply context section", "## Telegram reply context" in brief_text)
    check("brief states reply-to addressing equivalence", "equivalent to an explicit agent mention" in brief_text)

    return {
        "schema": "openclaw.telegram_reply_to_agent_target_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "summary": summary,
        "task_ids": [task.get("task_id") for task in tasks],
        "out_dir": str(out_dir),
        "checked": {
            "codex_reply": {
                "target_agents": codex_task.get("target_agents"),
                "delivery_policy": codex_task.get("delivery_policy"),
                "first_response_owner": codex_task.get("first_response_owner"),
                "event_type": codex_event.get("event_type"),
                "reply_to_agent_targets": codex_message.get("reply_to_agent_targets"),
            },
            "main_reply": {
                "target_agents": main_task.get("target_agents"),
                "delivery_policy": main_task.get("delivery_policy"),
                "first_response_owner": main_task.get("first_response_owner"),
                "event_type": main_event.get("event_type"),
                "bot_to_bot_trigger": main_task.get("bot_to_bot_trigger", {}).get("trigger"),
            },
            "normal_user_reply": {
                "target_agents": normal_task.get("target_agents"),
                "delivery_policy": normal_task.get("delivery_policy"),
                "first_response_owner": normal_task.get("first_response_owner"),
                "event_type": normal_event.get("event_type"),
                "reply_to_agent_targets": normal_message.get("reply_to_agent_targets"),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, help="Directory for dry-run artifacts.")
    args = parser.parse_args()
    if args.out_dir:
        out_dir = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="openclaw-reply-to-agent-smoke."))
    result = run_smoke(out_dir)
    result_path = out_dir / "smoke_result.json"
    result["result_path"] = str(result_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
