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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-blocker-actions-smoke"


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


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    task_id = "standing-smoke-blocker-actions"

    write_json(
        room / "tasks" / task_id / "manifest.json",
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "blocked",
            "target_agents": ["codex", "claude-code"],
            "quality_gate_status": "degraded_quorum",
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "participants": ["codex", "claude-code"],
            },
            "updated_at": "2026-05-29T05:30:00+08:00",
            "source": {"transport": "agent-room-standing-mainline"},
        },
    )
    write_json(
        room / "collaboration-ledgers" / f"{task_id}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "blocked",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "wi-codex", "assigned_to": "codex", "status": "blocked"},
                {"id": "wi-claude", "assigned_to": "claude-code", "status": "blocked"},
            ],
            "claims": [],
            "artifacts": [],
            "blockers": [
                {
                    "id": "blk-codex",
                    "work_item_id": "wi-codex",
                    "agent_id": "codex",
                    "reason": "provider liveness data unavailable",
                    "detail": "status surface must show this blocker as a reviewable closure item",
                    "status": "open",
                },
                {
                    "id": "blk-claude",
                    "work_item_id": "wi-claude",
                    "agent_id": "claude-code",
                    "reason": "fallback lane has no local tools",
                    "detail": "peer cannot produce a patch in the current lane",
                    "status": "open",
                },
            ],
            "handoffs": [],
            "points": [],
            "uptakes": [],
            "created_at": "2026-05-29T05:30:00+08:00",
            "updated_at": "2026-05-29T05:31:00+08:00",
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_blocker_actions_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"

    status = status_tool.build_status(include_background=True)
    compact = status_tool.render_compact_status(status)
    markdown = status_tool.render_markdown_status(status)
    snapshot_paths = status_tool.write_task_status_snapshots(status)
    snapshot = json.loads((status_dir / f"{task_id}.json").read_text(encoding="utf-8"))
    signature = status_tool.status_watch_signature(status)

    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    task_rows = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    task_collab = task_rows[0].get("collaboration") if task_rows and isinstance(task_rows[0], dict) else {}
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    snapshot_collab = snapshot.get("collaboration_health") if isinstance(snapshot.get("collaboration_health"), dict) else {}
    signature_health = signature.get("collaboration_health") if isinstance(signature.get("collaboration_health"), dict) else {}

    failures: list[str] = []
    check("ledger blockers are counted on task row", task_collab.get("blockers") == 2, failures)
    check("open blockers are counted on task row", task_collab.get("open_blockers") == 2, failures)
    check("blocker counts by agent are surfaced", (task_collab.get("blocker_counts_by_agent") or {}).get("codex") == 1 and (task_collab.get("blocker_counts_by_agent") or {}).get("claude-code") == 1, failures)
    check("overview aggregates blockers", overview.get("blocker_count") == 2 and overview.get("open_blocker_count") == 2, failures)
    check("overview records blocker task id", task_id in (overview.get("blocker_task_ids") or []), failures)
    check("overview aggregates per-agent blockers", (overview.get("per_agent_blockers") or {}).get("codex") == 1 and (overview.get("per_agent_blockers") or {}).get("claude-code") == 1, failures)
    check("overview creates blocker review actions", sum(1 for item in action_items if isinstance(item, dict) and item.get("type") == "blocker_review_needed") == 2, failures)
    check("blocker review actions keep source agent evidence", any((item.get("type") == "blocker_review_needed" and item.get("source_agent_id") == "claude-code" and item.get("blocker_id") == "blk-claude") for item in action_items if isinstance(item, dict)), failures)
    check("markdown surfaces blocker counters", "blocker_count: 2" in markdown and "open_blocker_count: 2" in markdown, failures)
    check("markdown surfaces blocker action item", "openclaw-main/blocker_review_needed" in markdown and "blocker=blk-codex" in markdown, failures)
    check("task snapshot carries blocker health", snapshot_collab.get("open_blockers") == 2 and str(status_dir / f"{task_id}.json") in snapshot_paths, failures)
    check("watch signature includes blocker counters", signature_health.get("blocker_count") == 2 and task_id in (signature_health.get("blocker_task_ids") or []), failures)
    check("compact status keeps diagnostics out of fixed card", "blocker" not in compact.lower() and "协作:" not in compact, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_blocker_actions_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "collaboration_overview": overview,
        "task_collaboration": task_collab,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
