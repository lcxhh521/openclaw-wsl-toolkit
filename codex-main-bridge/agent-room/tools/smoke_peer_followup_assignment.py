#!/usr/bin/env python3
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import agent_room_resident_bridge as resident
import agent_task_runner as runner


def check(checks: list[str], name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def configure_temp_room(root: Path) -> dict[str, Any]:
    saved = {
        "resident_ROOT": resident.ROOT,
        "resident_ROOM": resident.ROOM,
        "resident_STATE": resident.STATE,
        "resident_RUNS": resident.RUNS,
        "resident_ACTIVE_RUNNERS": resident.ACTIVE_RUNNERS,
        "resident_FINISHED_RUNNERS": resident.FINISHED_RUNNERS,
        "resident_COLLABORATION_STATUS": resident.COLLABORATION_STATUS,
        "runner_ROOT": runner.ROOT,
        "runner_ROOM": runner.ROOM,
        "runner_COMMENT_ROOT": runner.COMMENT_ROOT,
        "runner_COLLAB_LEDGER_DIR": runner.COLLAB_LEDGER_DIR,
    }
    room = root / "agent-room"
    resident.ROOT = root
    resident.ROOM = room
    resident.STATE = room / "telegram_agent_bridge_poll_state.json"
    resident.RUNS = room / "resident-runs"
    resident.ACTIVE_RUNNERS = room / "active-runners"
    resident.FINISHED_RUNNERS = room / "finished-runners"
    resident.COLLABORATION_STATUS = room / "collaboration-status"
    runner.ROOT = root
    runner.ROOM = room
    runner.COMMENT_ROOT = root / "agent-comments"
    runner.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    return saved


def restore_globals(saved: dict[str, Any]) -> None:
    resident.ROOT = saved["resident_ROOT"]
    resident.ROOM = saved["resident_ROOM"]
    resident.STATE = saved["resident_STATE"]
    resident.RUNS = saved["resident_RUNS"]
    resident.ACTIVE_RUNNERS = saved["resident_ACTIVE_RUNNERS"]
    resident.FINISHED_RUNNERS = saved["resident_FINISHED_RUNNERS"]
    resident.COLLABORATION_STATUS = saved["resident_COLLABORATION_STATUS"]
    runner.ROOT = saved["runner_ROOT"]
    runner.ROOM = saved["runner_ROOM"]
    runner.COMMENT_ROOT = saved["runner_COMMENT_ROOT"]
    runner.COLLAB_LEDGER_DIR = saved["runner_COLLAB_LEDGER_DIR"]


def parent_task_fixture(root: Path) -> dict[str, Any]:
    task_id = "tg-openclaw-evolution-peer-followup-assignment-smoke"
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": "openclaw-evolution",
        "requested_by": "telegram-user",
        "target_agents": ["codex", "claude-code"],
        "delivery_policy": "broadcast_all_agents_decide",
        "reply_policy": "mentions_choose_first_response_owner; all_agents_observe; speak_when_addressed_or_material; otherwise NO_COMMENT",
        "permissions": {
            "source_edit": True,
            "telegram_send": False,
            "notion_publish": False,
            "github_push": False,
            "secrets_access": False,
            "global_state_change": True,
            "quality_surface_change": False,
        },
        "source": {
            "transport": "telegram",
            "chat_id": "-1009000000001",
            "update_id": "smoke-peer-followup-assignment",
        },
        "status": "completed",
        "created_at": "2026-05-27T18:40:00+08:00",
        "updated_at": "2026-05-27T18:40:00+08:00",
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "mode": "dynamic_claims",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "room_response_codex", "status": "completed", "assigned_to": "codex"},
                {"id": "room_response_claude-code", "status": "completed", "assigned_to": "claude-code"},
            ],
            "claims": [],
            "handoffs": [],
            "artifacts": [],
            "blockers": [],
            "max_rounds": 1,
            "created_at": "2026-05-27T18:40:00+08:00",
        },
    }
    resident.write_json(root / "agent-room" / "tasks" / task_id / "manifest.json", task)
    return task


def main() -> int:
    checks: list[str] = []
    temp = Path(tempfile.mkdtemp(prefix="openclaw-peer-followup-assignment-smoke-"))
    root = temp / "codex-main-bridge"
    saved = configure_temp_room(root)
    try:
        parent = parent_task_fixture(root)
        comment = {
            "schema": "openclaw.agent_room.comment.v0",
            "agent_id": "claude-code",
            "run_id": parent["run_id"],
            "task_id": parent["task_id"],
            "room_id": parent["room_id"],
            "kind": "evidence",
            "title": "claude-code external fallback completed",
            "body": "当前协作账本的所有权规则与房间原有的并行协作设定存在；请 Codex 补验证证据。",
            "created_at": "2026-05-27T18:41:00+08:00",
        }
        followup = resident.create_collab_followup_task(parent, comment)
        check(checks, "follow-up task is created", isinstance(followup, dict))
        if not isinstance(followup, dict):
            raise AssertionError("follow-up task missing")

        work_items = followup.get("collaboration", {}).get("work_items", [])
        check(checks, "exactly one peer follow-up item for single target", len(work_items) == 1)
        item = work_items[0] if work_items else {}
        check(checks, "peer follow-up work item is assigned to codex", item.get("assigned_to") == "codex")
        check(checks, "source peer remains recorded separately", item.get("source_agent_id") == "claude-code")

        prompt = runner.task_prompt(followup, "codex", followup.get("permissions") or {})
        check(checks, "runner prompt shows codex ownership", "分配给: codex" in prompt)
        check(checks, "runner prompt no longer shows unassigned work item", "分配给: None" not in prompt)
        check(checks, "runner prompt lists codex responsible item", "## 你当前负责的Work Items" in prompt)

        print("smoke_peer_followup_assignment: PASS")
        for name in checks:
            print(f"- {name}")
        return 0
    finally:
        restore_globals(saved)
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
