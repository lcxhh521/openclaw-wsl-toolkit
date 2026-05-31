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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "repetition-followup-guard-smoke"


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


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    room.mkdir(parents=True, exist_ok=True)
    (room / "rooms" / "openclaw-evolution").mkdir(parents=True, exist_ok=True)
    write_json(room / "rooms" / "openclaw-evolution" / "room.json", {})
    write_json(room / "config" / "standing-agenda.json", {"collaboration_tick": {"enabled": False}})

    mod = load_module(TOOLS / "agent_room_resident_bridge.py", "agent_room_resident_bridge_repetition_smoke")
    mod.ROOT = bridge_root
    mod.ROOM = room
    mod.TASKS_JSONL = room / "tasks.jsonl"
    mod.COMMENT_ROOT = room / "agent-comments"
    mod.ACTIVE_RUNNERS = room / "active-runners"
    mod.FINISHED_RUNNERS = room / "finished-runners"

    base_parent_task = {
        "task_id": "tg-openclaw-evolution-repeat-smoke",
        "run_id": "tg-openclaw-evolution-repeat-smoke",
        "room_id": "openclaw-evolution",
        "requested_by": "telegram-user",
        "target_agents": ["codex", "claude-code"],
        "delivery_policy": "broadcast_all_agents_decide",
        "source": {"transport": "telegram", "chat_id": "-1009000000001"},
        "brief_path": str(room / "tasks" / "tg-openclaw-evolution-repeat-smoke" / "brief.md"),
    }
    parent_task = dict(base_parent_task)
    parent_task["collaboration_control"] = {
        "disable_peer_followup": True,
        "reason": "main_decided_this_turn_is_noise_control_not_collab_work",
    }
    Path(parent_task["brief_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(parent_task["brief_path"]).write_text(
        "# Telegram Agent Room Task\n\n## User message\n\n我感觉你们总是在重复讨论\n",
        encoding="utf-8",
    )
    comment = {
        "agent_id": "claude-code",
        "run_id": "tg-openclaw-evolution-repeat-smoke-deepseek-v4-flash",
        "room_id": "openclaw-evolution",
        "title": "收到，重复讨论的根因",
        "body": "Alex 说得对，我们总是在重复讨论。建议 Codex 继续 review 并补充。",
        "blockers": [],
    }
    should_blocked = mod.should_create_collab_followup(parent_task, comment, {"codex", "claude-code"})
    created_blocked = mod.create_collab_followup_task(parent_task, comment)

    # The same words must not be a global ban.  Without the explicit task-level
    # control signal, the normal materiality gate remains responsible for deciding
    # whether collaboration is useful.
    allowed_parent_task = dict(base_parent_task)
    should_allowed = mod.should_create_collab_followup(allowed_parent_task, comment, {"codex", "claude-code"})

    failures: list[str] = []
    if should_blocked:
        failures.append("explicit collaboration_control.disable_peer_followup was ignored")
    if created_blocked is not None:
        failures.append("create_collab_followup_task created a task despite explicit disable_peer_followup")
    if not should_allowed:
        failures.append("repetition wording alone was treated as a global no-followup keyword ban")

    result = {
        "schema": "openclaw.agent_room.repetition_followup_guard_smoke.v1",
        "ok": not failures,
        "failures": failures,
        "blocked_case": {
            "should_create_collab_followup": should_blocked,
            "created_task": created_blocked,
        },
        "allowed_without_explicit_control": should_allowed,
        "dry_run": str(DRY_RUN),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
