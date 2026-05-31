#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "status-card-room-scope-smoke"


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


def runner_record(*, agent_id: str, task_id: str, room_id: str, task: dict[str, Any], pid: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc).astimezone()
    return {
        "schema": "openclaw.agent_room.active_runner.v0",
        "status": "running",
        "agent_id": agent_id,
        "run_id": task_id,
        "task_id": task_id,
        "room_id": room_id,
        "pid": pid,
        "started_at": now.isoformat(timespec="seconds"),
        "soft_deadline_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        "hard_deadline_at": (now + timedelta(minutes=20)).isoformat(timespec="seconds"),
        "runner_dir": str(DRY_RUN / "runner" / task_id / agent_id),
        "task": task,
    }


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    active_dir = room / "active-runners"
    tasks_dir = room / "tasks"
    active_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).astimezone()
    write_json(
        room / "agent_room_bridge_daemon.status.json",
        {
            "schema": "openclaw.agent_room.bridge_daemon_status.v0",
            "status": "running",
            "tick": 7,
            "last_tick_ok": True,
            "last_tick_finished_at": now.isoformat(timespec="seconds"),
            "telegram_outbound_enabled": True,
        },
    )

    room_task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": "tg-openclaw-evolution-current",
        "run_id": "tg-openclaw-evolution-current",
        "room_id": "openclaw-evolution",
        "target_agents": ["codex"],
        "source": {"transport": "telegram", "chat_id": "-1009000000001"},
    }
    dm_task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": "tg-dm-codex-background",
        "run_id": "tg-dm-codex-background",
        "room_id": "dm-codex-424242",
        "target_agents": ["claude-code"],
        "source": {"transport": "telegram", "chat_id": "424242"},
    }
    standing_task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": "standing-openclaw-evolution-background",
        "run_id": "standing-openclaw-evolution-background",
        "room_id": "openclaw-evolution",
        "requested_by": "agent-room-standing-mainline",
        "lane": "standing_mainline_discussion",
        "target_agents": ["claude-code"],
        "standing_mainline": {"schema": "openclaw.agent_room.standing_mainline.v0"},
        "source": {"transport": "agent-room-standing-mainline"},
    }
    visible_standing_task = dict(standing_task)
    visible_standing_task.update({
        "task_id": "standing-openclaw-evolution-visible",
        "run_id": "standing-openclaw-evolution-visible",
        "status_card_room_visible": True,
    })

    for task in (room_task, dm_task, standing_task):
        write_json(tasks_dir / task["task_id"] / "manifest.json", task)

    pid = os.getpid()
    write_json(active_dir / "codex-current.json", runner_record(
        agent_id="codex",
        task_id=room_task["task_id"],
        room_id=room_task["room_id"],
        task=room_task,
        pid=pid,
    ))
    write_json(active_dir / "claude-dm.json", runner_record(
        agent_id="claude-code",
        task_id=dm_task["task_id"],
        room_id=dm_task["room_id"],
        task=dm_task,
        pid=pid,
    ))
    write_json(active_dir / "claude-standing.json", runner_record(
        agent_id="claude-code",
        task_id=standing_task["task_id"],
        room_id=standing_task["room_id"],
        task=standing_task,
        pid=pid,
    ))

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_room_scope_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = active_dir
    status_tool.TASKS = tasks_dir
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"

    status = status_tool.build_status(room_id="openclaw-evolution")
    status["fixed_status_card"] = status_tool.fixed_status_card(
        status,
        room_id="openclaw-evolution",
        chat_id="-1009000000001",
    )
    compact = status_tool.render_compact_status(status)
    rows = status["fixed_status_card"]["rows"]
    active_task_ids = set(status.get("active_task_ids") or [])
    recent_task_ids = {task.get("task_id") for task in status.get("recent_tasks") or []}
    failures: list[str] = []

    check("fixed card uses tri-agent rows", [row.get("agent_id") for row in rows] == ["openclaw-main", "codex", "claude-code"], failures)
    check("main row is based on room daemon runtime", rows[0].get("state") == "online", failures)
    check("current room task is included", room_task["task_id"] in active_task_ids, failures)
    check("private DM task is excluded from active status", dm_task["task_id"] not in active_task_ids, failures)
    check("standing background task is excluded from active status", standing_task["task_id"] not in active_task_ids, failures)
    check("private DM task is excluded from recent tasks", dm_task["task_id"] not in recent_task_ids, failures)
    check("standing background task is excluded from recent tasks", standing_task["task_id"] not in recent_task_ids, failures)
    check("non-participating local agent remains idle", rows[2].get("state") == "idle", failures)
    check("compact renderer reuses fixed card text", compact == status["fixed_status_card"]["text"], failures)
    check("compact text includes main runtime row", "main " in compact, failures)
    check("compact text excludes private DM task", dm_task["task_id"] not in compact, failures)
    check("compact text excludes standing background task", standing_task["task_id"] not in compact, failures)

    write_json(active_dir / "claude-visible-standing.json", runner_record(
        agent_id="claude-code",
        task_id=visible_standing_task["task_id"],
        room_id=visible_standing_task["room_id"],
        task=visible_standing_task,
        pid=pid,
    ))
    visible_rows = status_tool.active_runner_rows(room_id="openclaw-evolution")
    check(
        "explicit room-visible standing task can enter room status",
        visible_standing_task["task_id"] in {row.get("task_id") for row in visible_rows},
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.status_card_room_scope_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "active_task_ids": sorted(active_task_ids),
        "recent_task_ids": sorted(str(task_id) for task_id in recent_task_ids if task_id),
        "card_rows": rows,
        "compact": compact,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
