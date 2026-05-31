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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-scope-conflicts-smoke"


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
    task_id = "standing-smoke-scope-conflicts"
    shared_path = "codex-main-bridge/agent-room/tools/collaboration_status.py"
    codex_only_path = "codex-main-bridge/agent-room/tools/smoke_collaboration_status_scope_conflicts.py"

    write_json(
        room / "tasks" / task_id / "manifest.json",
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "running",
            "review_status": "requested",
            "quality_gate_status": "not_applicable",
            "target_agents": ["codex", "claude-code"],
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
                "points": [],
                "uptakes": [],
            },
            "updated_at": "2026-05-29T04:24:00+08:00",
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
            "status": "open",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {
                    "id": "wi-codex",
                    "assigned_to": "codex",
                    "claimed_by": "codex",
                    "status": "completed",
                    "declared_scope": {
                        "scope_type": "file_edit",
                        "paths": [shared_path, codex_only_path],
                    },
                },
                {
                    "id": "wi-claude",
                    "assigned_to": "claude-code",
                    "claimed_by": "claude-code",
                    "status": "completed",
                    "declared_scope": {
                        "scope_type": "file_edit",
                        "paths": [shared_path],
                    },
                },
            ],
            "claims": [
                {"work_item_id": "wi-codex", "agent_id": "codex", "status": "completed"},
                {"work_item_id": "wi-claude", "agent_id": "claude-code", "status": "completed"},
            ],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
            "created_at": "2026-05-29T04:24:00+08:00",
            "updated_at": "2026-05-29T04:24:10+08:00",
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_scope_conflicts_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"
    status_tool.MODEL_QUOTA_SIGNAL = room / "model_quota_signal.json"
    status_tool.AGENT_QUOTA_STATE = room / "agent_quota_state.json"

    status = status_tool.build_status(include_background=True)
    markdown = status_tool.render_markdown_status(status)
    snapshot_paths = status_tool.write_task_status_snapshots(status)
    snapshot = json.loads((status_dir / f"{task_id}.json").read_text(encoding="utf-8"))
    watch_signature = status_tool.status_watch_signature(status)
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    task_rows = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    row = next((item for item in task_rows if isinstance(item, dict) and item.get("task_id") == task_id), {})
    task_collab = row.get("collaboration") if isinstance(row.get("collaboration"), dict) else {}
    actions = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    snapshot_health = snapshot.get("collaboration_health") if isinstance(snapshot.get("collaboration_health"), dict) else {}
    snapshot_ledger_items = (snapshot.get("ledger") or {}).get("work_items") or []

    failures: list[str] = []
    check("task collaboration counts declared scope work items", task_collab.get("declared_scope_work_items") == 2, failures)
    check("task collaboration counts declared path entries", task_collab.get("declared_scope_path_count") == 3, failures)
    check("task collaboration counts unique paths", task_collab.get("declared_scope_unique_path_count") == 2, failures)
    check("task collaboration detects shared path conflict", task_collab.get("scope_conflict_count") == 1, failures)
    check(
        "task collaboration preserves conflict path and agents",
        task_collab.get("scope_conflicts") == [{"path": shared_path, "agents": ["claude-code", "codex"]}],
        failures,
    )
    check("overview aggregates declared scope entries", overview.get("declared_scope_path_count") == 3, failures)
    check("overview aggregates scope conflicts", overview.get("scope_conflict_count") == 1, failures)
    check("overview marks conflict task", task_id in (overview.get("scope_conflict_task_ids") or []), failures)
    check("overview marks conflict as attention", task_id in (overview.get("needs_collaboration_attention_task_ids") or []), failures)
    check("overview counts per-agent declared scope paths", (overview.get("per_agent_declared_scope_paths") or {}).get("codex") == 2, failures)
    check(
        "overview assigns conflict action to each agent",
        {item.get("agent_id") for item in actions if isinstance(item, dict) and item.get("type") == "scope_conflict_review_needed"} == {"codex", "claude-code"},
        failures,
    )
    check("markdown surfaces scope conflict count", "scope_conflict_count: 1" in markdown, failures)
    check("markdown surfaces scope conflict path", f"path={shared_path}" in markdown, failures)
    check("watch signature includes scope conflict", (watch_signature.get("collaboration_health") or {}).get("scope_conflict_count") == 1, failures)
    check("task snapshot carries declared scope health", snapshot_health.get("declared_scope_path_count") == 3, failures)
    check("task snapshot keeps ledger declared_scope", any(isinstance(item, dict) and isinstance(item.get("declared_scope"), dict) for item in snapshot_ledger_items), failures)
    check("task status snapshot was written", str(status_dir / f"{task_id}.json") in snapshot_paths, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_scope_conflicts_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "task_collaboration": task_collab,
        "overview": overview,
        "watch_signature": watch_signature,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
