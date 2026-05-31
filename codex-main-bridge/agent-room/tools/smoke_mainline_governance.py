#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import mainline_governance
import pinned_status_card
import telegram_agent_bridge


def check(checks: list[str], name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def configure_bridge_root(root: Path) -> dict[str, object]:
    saved = {
        "ROOT": telegram_agent_bridge.ROOT,
        "ROOM": telegram_agent_bridge.ROOM,
        "BOT_META": telegram_agent_bridge.BOT_META,
        "BINDINGS": telegram_agent_bridge.BINDINGS,
        "FIXTURES": telegram_agent_bridge.FIXTURES,
        "DRY_RUNS": telegram_agent_bridge.DRY_RUNS,
    }
    telegram_agent_bridge.ROOT = root
    telegram_agent_bridge.ROOM = root / "agent-room"
    telegram_agent_bridge.BOT_META = telegram_agent_bridge.ROOM / "telegram_agent_bots.json"
    telegram_agent_bridge.BINDINGS = root / "telegram-room-bindings.json"
    telegram_agent_bridge.FIXTURES = telegram_agent_bridge.ROOM / "fixtures" / "telegram-agent-bridge"
    telegram_agent_bridge.DRY_RUNS = telegram_agent_bridge.ROOM / "dry-runs" / "telegram-agent-bridge"
    return saved


def restore_bridge_root(saved: dict[str, object]) -> None:
    for name, value in saved.items():
        setattr(telegram_agent_bridge, name, value)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    checks: list[str] = []
    root = Path(tempfile.mkdtemp(prefix="openclaw-mainline-governance-smoke-"))
    saved = configure_bridge_root(root)
    try:
        write_json(telegram_agent_bridge.BOT_META, {"schema": "smoke", "bots": []})
        write_json(
            telegram_agent_bridge.BINDINGS,
            {
                "schema": "smoke",
                "bindings": [
                    {
                        "telegram_chat_id": "-100",
                        "room_id": "openclaw-evolution",
                        "participants": ["openclaw-main", "codex", "claude-code"],
                    }
                ],
            },
        )

        task = telegram_agent_bridge.build_task(
            "openclaw-evolution",
            "-100",
            "请把 Agent Room 协作体系落实到主线，不要偏离主线。",
            ["codex", "claude-code"],
            requested_by="telegram-user",
            source_update_id="smoke-mainline-governance",
        )
        check(
            checks,
            "telegram task manifest is stamped with required governance fields",
            task.get("mainline_id") == "agent_room_infrastructure"
            and task.get("owner") == "openclaw-main"
            and task.get("dedupe_key")
            and task.get("governance_state") == "triage"
            and (task.get("governance_validation") or {}).get("ok") is True
            and task.get("governance_contract_path") == mainline_governance.CONTRACT_PATH
            and task.get("drift_check_passed") is True
            and "drift_check" not in task,
        )

        out_dir = root / "agent-room" / "dry-runs" / "governance-no-mention"
        updates = [
            {
                "update_id": 1,
                "receiver_agent_id": "codex",
                "message": {
                    "message_id": 10,
                    "chat": {"id": -100, "type": "supergroup", "title": "OpenClaw"},
                    "from": {"id": 100000001},
                    "text": "请把协作体系落实到 task manifest 和 drift-check，不要偏离主线。",
                },
            }
        ]
        result = telegram_agent_bridge.normalize_updates(updates, out_dir)
        rows = [
            json.loads(line)
            for line in (out_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        routed = rows[0] if rows else {}
        check(
            checks,
            "no-mention collaboration mainline message creates a concrete routed task",
            result.get("tasks") == 1
            and routed.get("target_agents") == ["codex", "claude-code"]
            and routed.get("delivery_policy") == "targeted_reply",
        )

        invalid_card = {
            "schema": "openclaw.agent_room.fixed_status_card.v0",
            "text": "\n".join(
                [
                    "📌 OpenClaw 状态 12:00:00",
                    "main 🟢在线 · 入口/状态卡正常",
                    "Codex ⚪空闲 · 空闲",
                    "Claude Code ⚪空闲 · 空闲",
                    "mainline_id should stay out of pinned card",
                ]
            ),
            "rows": [
                {"agent_id": "openclaw-main"},
                {"agent_id": "codex"},
                {"agent_id": "claude-code"},
            ],
        }
        validation = pinned_status_card.validate_fixed_status_card(invalid_card)
        check(
            checks,
            "pinned card validation rejects governance diagnostics",
            validation.get("ok") is False and "forbidden_marker:mainline_id" in (validation.get("reasons") or []),
        )

        print(json.dumps({"ok": True, "checks": checks, "tokens_printed": False}, ensure_ascii=False, indent=2))
        return 0
    finally:
        restore_bridge_root(saved)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
