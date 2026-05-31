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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-claim-lease-smoke"


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
    task_id = "standing-smoke-claim-lease"

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
            "updated_at": "2026-05-27T15:00:00+08:00",
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
                    "status": "claimed",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "id": "wi-claude-code",
                    "assigned_to": "claude-code",
                    "claimed_by": "claude-code",
                    "status": "claimed",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "claims": [
                {
                    "work_item_id": "wi-codex",
                    "agent_id": "codex",
                    "status": "active",
                    "claimed_at": "2026-05-27T15:00:00+08:00",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "work_item_id": "wi-claude-code",
                    "agent_id": "claude-code",
                    "status": "active",
                    "claimed_at": "2026-05-27T15:00:00+08:00",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
            "created_at": "2026-05-27T15:00:00+08:00",
            "updated_at": "2026-05-27T15:01:00+08:00",
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_claim_lease_smoke")
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
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    recent_tasks = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    task_collaboration = recent_tasks[0].get("collaboration") if recent_tasks and isinstance(recent_tasks[0], dict) else {}
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []

    failures: list[str] = []
    check("active claims are counted", task_collaboration.get("active_claims") == 2, failures)
    check("expired claim is counted", task_collaboration.get("expired_claims") == 1, failures)
    check("expired claim id is surfaced", "wi-codex" in (task_collaboration.get("expired_claim_ids") or []), failures)
    check("expired claim agent is surfaced", (task_collaboration.get("expired_claim_counts_by_agent") or {}).get("codex") == 1, failures)
    check("overview aggregates active claims", overview.get("active_claim_count") == 2, failures)
    check("overview aggregates expired claims", overview.get("expired_claim_count") == 1, failures)
    check("expired claim marks task attention", task_id in (overview.get("needs_collaboration_attention_task_ids") or []), failures)
    check("expired claim task id is surfaced", task_id in (overview.get("claim_lease_expired_task_ids") or []), failures)
    check("expired claim action is assigned", any((item.get("type") == "claim_lease_expired" and item.get("agent_id") == "codex") for item in action_items if isinstance(item, dict)), failures)
    check("markdown status surfaces expired lease", "expired_claim_count: 1" in markdown and "codex/claim_lease_expired" in markdown, failures)
    check("compact status keeps collaboration diagnostics out", "租约过期" not in compact, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_claim_lease_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "compact_status": compact,
        "markdown_status": markdown,
        "collaboration_overview": overview,
        "task_collaboration": task_collaboration,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
