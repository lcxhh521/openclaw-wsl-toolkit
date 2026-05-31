#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "context-freshness-gate-smoke"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def check(name: str, ok: bool, failures: list[str]) -> None:
    if not ok:
        failures.append(name)


def human_message(stable_id: str, created_at: str, text: str) -> dict[str, Any]:
    return {
        "schema": "openclaw.agent_room.message.v0",
        "message_event_id": f"{stable_id}:room-message",
        "room_id": "openclaw-evolution",
        "chat_id": "-1009000000001",
        "chat_type": "supergroup",
        "stable_message_id": stable_id,
        "actor_user_id": "smoke-user",
        "target_agents": ["codex"],
        "text": text,
        "created_at": created_at,
    }


def main() -> int:
    resident = load_module("agent_room_resident_bridge", TOOLS / "agent_room_resident_bridge.py")
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    room_root = DRY_RUN / "room"
    messages_path = room_root / "rooms" / "openclaw-evolution" / "messages.jsonl"
    original_room = resident.ROOM
    failures: list[str] = []
    try:
        resident.ROOM = room_root
        append_jsonl(
            messages_path,
            [
                human_message(
                    "group-message:-1009000000001:1",
                    "2026-05-26T00:01:00+08:00",
                    "first user turn",
                )
            ],
        )
        snapshot = resident.room_context_snapshot("openclaw-evolution")
        record = {
            "agent_id": "codex",
            "run_id": "smoke-old-run",
            "task_id": "smoke-old-run",
            "room_id": "openclaw-evolution",
            "started_at": "2026-05-26T00:02:00+08:00",
            "context_snapshot": snapshot,
        }
        check(
            "snapshot captures latest human message at runner start",
            (snapshot.get("latest_human_message") or {}).get("stable_message_id")
            == "group-message:-1009000000001:1",
            failures,
        )
        append_jsonl(
            messages_path,
            [
                human_message(
                    "group-message:-1009000000001:2",
                    "2026-05-26T00:02:00+08:00",
                    "newer user turn",
                )
            ],
        )
        freshness = resident.runner_context_freshness(record)
        check(
            "newer human message marks old runner context stale by event order",
            freshness.get("status") == "stale_context"
            and freshness.get("reason") == "room_state_changed_after_context_snapshot"
            and freshness.get("trigger") == "newer_human_room_message"
            and freshness.get("user_fault") is False
            and (freshness.get("newer_human_message") or {}).get("stable_message_id")
            == "group-message:-1009000000001:2",
            failures,
        )
        task = {
            "task_id": "smoke-old-run",
            "run_id": "smoke-old-run",
            "room_id": "openclaw-evolution",
            "requested_by": "telegram-user",
            "delivery_policy": "targeted_reply",
            "source": {"transport": "telegram", "chat_id": "-1009000000001"},
            "permissions": {"source_edit": True, "telegram_send": False},
        }
        comment = resident.stale_context_comment(task, "codex", freshness)
        may_project, reason = resident.telegram_projection_decision(task, [comment])
        check(
            "stale context comment is not projected to Telegram",
            may_project is False and reason == "stale_context_superseded_by_room_state_update",
            failures,
        )
        check(
            "stale context comment does not blame the user",
            "不是用户连续发送消息的责任" in str(comment.get("body") or ""),
            failures,
        )
        resident.append_agent_comments_to_room("openclaw-evolution", [comment], source="smoke")
        room_messages = read_jsonl(messages_path)
        check(
            "stale context comment does not pollute room context",
            all(str(item.get("actor_agent_id") or "") != "codex" for item in room_messages),
            failures,
        )
    finally:
        resident.ROOM = original_room
    result = {
        "schema": "openclaw.agent_room.context_freshness_gate_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
