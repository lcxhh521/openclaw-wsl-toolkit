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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-action-item-priority-smoke"


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


def configure_status_module(module: Any, bridge_root: Path, room: Path, status_dir: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    module.ACTIVE_RUNNERS = room / "active-runners"
    module.TASKS = room / "tasks"
    module.STATUS_DIR = status_dir
    module.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    module.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    module.AGENT_PRESENCE_DIR = room / "agent-presence"


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    uptake_task_id = "standing-smoke-action-priority-uptake-gap"
    blocker_task_ids = [
        "standing-smoke-action-priority-blocker-noise-a",
        "standing-smoke-action-priority-blocker-noise-b",
    ]

    write_json(
        room / "tasks" / uptake_task_id / "manifest.json",
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": uptake_task_id,
            "run_id": uptake_task_id,
            "room_id": "openclaw-evolution",
            "status": "partial",
            "review_status": "requested",
            "quality_gate_status": "needs_collaboration_review",
            "target_agents": ["codex", "claude-code"],
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "participants": ["codex", "claude-code"],
            },
            "source": {"transport": "agent-room-standing-mainline"},
        },
    )
    write_json(
        room / "collaboration-ledgers" / f"{uptake_task_id}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": uptake_task_id,
            "run_id": uptake_task_id,
            "room_id": "openclaw-evolution",
            "status": "open",
            "participants": ["codex", "claude-code"],
            "work_items": [],
            "claims": [],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [
                {
                    "id": "pt-needs-peer",
                    "agent_id": "codex",
                    "kind": "evidence",
                    "text": "status surface must not hide the active peer uptake gap behind old blocker review items",
                },
            ],
            "uptakes": [],
        },
    )

    for blocker_group, blocker_task_id in enumerate(blocker_task_ids):
        write_json(
            room / "tasks" / blocker_task_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": blocker_task_id,
                "run_id": blocker_task_id,
                "room_id": "openclaw-evolution",
                "status": "blocked",
                "review_status": "requested",
                "quality_gate_status": "degraded_quorum",
                "target_agents": ["codex", "claude-code"],
                "collaboration": {
                    "schema": "openclaw.agent_room.collaboration.v0",
                    "status": "blocked",
                    "participants": ["codex", "claude-code"],
                },
                "source": {"transport": "agent-room-standing-mainline"},
            },
        )
        write_json(
            room / "collaboration-ledgers" / f"{blocker_task_id}.json",
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "task_id": blocker_task_id,
                "run_id": blocker_task_id,
                "room_id": "openclaw-evolution",
                "status": "blocked",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [],
                "blockers": [
                    {
                        "id": f"blk-noise-{blocker_group}-{idx:02d}",
                        "work_item_id": f"wi-noise-{blocker_group}-{idx:02d}",
                        "agent_id": "agent-room-standing-agenda",
                        "reason": "old standing closure blocker",
                        "status": "open",
                    }
                    for idx in range(8)
                ],
                "handoffs": [],
                "points": [],
                "uptakes": [],
            },
        )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_action_item_priority_smoke")
    configure_status_module(status_tool, bridge_root, room, status_dir)

    status = status_tool.build_status(include_background=True)
    markdown = status_tool.render_markdown_status(status)
    signature = status_tool.status_watch_signature(status)
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    action_types = [str(item.get("type") or "") for item in action_items if isinstance(item, dict)]
    signature_actions = ((signature.get("collaboration_health") or {}).get("action_items") or [])

    failures: list[str] = []
    check("all action items are counted", overview.get("action_item_count") == 20, failures)
    check("overview keeps all collected actions before render truncation", len(action_items) == 20, failures)
    check(
        "peer uptake action is prioritized ahead of repair and blocker noise",
        action_types[:4] == [
            "peer_uptake_needed",
            "collaboration_repair_needed",
            "collaboration_repair_needed",
            "collaboration_review_needed",
        ],
        failures,
    )
    check("rendered markdown surfaces peer uptake before blocker review", markdown.find("claude-code/peer_uptake_needed") >= 0 and markdown.find("claude-code/peer_uptake_needed") < markdown.find("openclaw-main/blocker_review_needed"), failures)
    check("signature uses prioritized action items", signature_actions and str(signature_actions[0]).startswith("peer_uptake_needed:"), failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_action_item_priority_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "action_types": action_types,
        "signature_actions": signature_actions,
        "markdown_action_section": "\n".join(
            line for line in markdown.splitlines() if "needed" in line or "action_item" in line
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
