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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-ledger-quality-gate-smoke"


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
    task_id = "standing-smoke-ledger-quality-gate"
    review_task_id = "standing-smoke-ledger-artifacts-needs-review"
    partial_task_id = "standing-smoke-ledger-partial-uptake-needs-review"
    review_status_only_task_id = "standing-smoke-review-status-degraded-quorum"
    task_path = room / "tasks" / task_id / "manifest.json"
    review_task_path = room / "tasks" / review_task_id / "manifest.json"
    partial_task_path = room / "tasks" / partial_task_id / "manifest.json"
    status_only_task_path = room / "tasks" / review_status_only_task_id / "manifest.json"

    write_json(
        task_path,
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "requested_by": "agent-room-standing-mainline",
            "lane": "standing_mainline_discussion",
            "status": "partial",
            "review_status": "degraded_quorum",
            "quality_gate_status": "degraded_quorum",
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "agent-room-standing-mainline"},
            "runner_summary": {
                "completed_agents": ["claude-code"],
                "degraded_quorum": True,
                "collaboration_quality_gate": {
                    "status": "degraded_quorum",
                    "reason": "missing_local_agent_results",
                    "missing_agents": ["codex"],
                },
            },
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
            "created_at": "2026-05-27T19:30:00+08:00",
            "updated_at": "2026-05-27T19:31:00+08:00",
        },
    )
    write_json(
        room / "collaboration-ledgers" / f"{task_id}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "wi-codex", "assigned_to": "codex", "claimed_by": "codex", "status": "completed"},
                {"id": "wi-claude", "assigned_to": "claude-code", "claimed_by": "claude-code", "status": "completed"},
            ],
            "claims": [
                {"work_item_id": "wi-codex", "agent_id": "codex", "status": "completed"},
                {"work_item_id": "wi-claude", "agent_id": "claude-code", "status": "completed"},
            ],
            "artifacts": [
                {"id": "art-001", "work_item_id": "wi-codex", "agent_id": "codex", "path": "agent-room/tools/collaboration_status.py"},
                {"id": "art-002", "work_item_id": "wi-claude", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"},
            ],
            "blockers": [],
            "handoffs": [],
            "points": [
                {
                    "id": "pt-001",
                    "agent_id": "claude-code",
                    "kind": "evidence",
                    "text": "fixed card generator retained a diagnostics branch",
                    "status": "incorporated",
                    "uptake_status_by_agent": {"codex": {"status": "incorporated"}},
                }
            ],
            "uptakes": [
                {
                    "id": "uptake-001",
                    "point_id": "pt-001",
                    "point_agent_id": "claude-code",
                    "by_agent": "codex",
                    "status": "incorporated",
                    "reason": "Codex implemented the fixed-card/source-status split.",
                }
            ],
            "created_at": "2026-05-27T19:30:00+08:00",
            "updated_at": "2026-05-27T19:33:00+08:00",
        },
    )
    write_json(
        review_task_path,
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": review_task_id,
            "run_id": review_task_id,
            "room_id": "openclaw-evolution",
            "requested_by": "agent-room-standing-mainline",
            "lane": "standing_mainline_discussion",
            "status": "partial",
            "review_status": "degraded_quorum",
            "quality_gate_status": "degraded_quorum",
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "agent-room-standing-mainline"},
            "runner_summary": {
                "completed_agents": ["claude-code"],
                "degraded_quorum": True,
                "collaboration_quality_gate": {
                    "status": "degraded_quorum",
                    "reason": "missing_local_agent_results",
                    "missing_agents": ["codex"],
                },
            },
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
            "created_at": "2026-05-27T19:29:00+08:00",
            "updated_at": "2026-05-27T19:31:00+08:00",
        },
    )
    write_json(
        room / "collaboration-ledgers" / f"{review_task_id}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": review_task_id,
            "run_id": review_task_id,
            "room_id": "openclaw-evolution",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "wi-codex", "assigned_to": "codex", "claimed_by": "codex", "status": "completed"},
                {"id": "wi-claude", "assigned_to": "claude-code", "claimed_by": "claude-code", "status": "completed"},
            ],
            "claims": [
                {"work_item_id": "wi-codex", "agent_id": "codex", "status": "completed"},
                {"work_item_id": "wi-claude", "agent_id": "claude-code", "status": "completed"},
            ],
            "artifacts": [
                {"id": "art-001", "work_item_id": "wi-codex", "agent_id": "codex", "path": "agent-room/artifacts/codex-review.md"},
                {"id": "art-002", "work_item_id": "wi-claude", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"},
            ],
            "blockers": [],
            "handoffs": [],
            "points": [
                {"id": "pt-001", "agent_id": "codex", "kind": "summary", "text": "summary still needs peer uptake", "status": "open"}
            ],
            "uptakes": [],
            "created_at": "2026-05-27T19:29:00+08:00",
            "updated_at": "2026-05-27T19:33:00+08:00",
        },
    )
    write_json(
        partial_task_path,
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": partial_task_id,
            "run_id": partial_task_id,
            "room_id": "openclaw-evolution",
            "requested_by": "agent-room-standing-mainline",
            "lane": "standing_mainline_discussion",
            "status": "partial",
            "review_status": "degraded_quorum",
            "quality_gate_status": "degraded_quorum",
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "agent-room-standing-mainline"},
            "runner_summary": {
                "completed_agents": ["codex", "claude-code"],
                "degraded_quorum": True,
                "collaboration_quality_gate": {
                    "status": "degraded_quorum",
                    "reason": "missing_local_agent_results",
                    "missing_agents": ["claude-code"],
                },
            },
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
            "created_at": "2026-05-27T19:28:00+08:00",
            "updated_at": "2026-05-27T19:31:00+08:00",
        },
    )
    write_json(
        room / "collaboration-ledgers" / f"{partial_task_id}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": partial_task_id,
            "run_id": partial_task_id,
            "room_id": "openclaw-evolution",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "wi-codex", "assigned_to": "codex", "claimed_by": "codex", "status": "completed"},
                {"id": "wi-claude", "assigned_to": "claude-code", "claimed_by": "claude-code", "status": "completed"},
            ],
            "claims": [
                {"work_item_id": "wi-codex", "agent_id": "codex", "status": "completed"},
                {"work_item_id": "wi-claude", "agent_id": "claude-code", "status": "completed"},
            ],
            "artifacts": [
                {"id": "art-001", "work_item_id": "wi-codex", "agent_id": "codex", "path": "agent-room/artifacts/codex-review.md"},
                {"id": "art-002", "work_item_id": "wi-claude", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"},
            ],
            "blockers": [],
            "handoffs": [],
            "points": [
                {"id": "pt-001", "agent_id": "codex", "kind": "evidence", "text": "Codex landed status evidence"},
                {"id": "pt-002", "agent_id": "codex", "kind": "risk", "text": "another material point still needs Claude uptake"},
            ],
            "uptakes": [
                {
                    "id": "uptake-001",
                    "point_id": "pt-001",
                    "point_agent_id": "codex",
                    "by_agent": "claude-code",
                    "status": "accepted",
                    "reason": "Claude accepted the first point only.",
                }
            ],
            "created_at": "2026-05-27T19:28:00+08:00",
            "updated_at": "2026-05-27T19:33:00+08:00",
        },
    )
    write_json(
        status_only_task_path,
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": review_status_only_task_id,
            "run_id": review_status_only_task_id,
            "room_id": "openclaw-evolution",
            "requested_by": "agent-room-standing-mainline",
            "lane": "standing_mainline_discussion",
            "status": "partial",
            "review_status": "degraded_quorum",
            "quality_gate_status": "peer_reviewed",
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "agent-room-standing-mainline"},
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
            "created_at": "2026-05-27T19:27:00+08:00",
            "updated_at": "2026-05-27T19:31:00+08:00",
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_ledger_quality_gate_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"

    status = status_tool.build_status(include_background=True)
    snapshot_paths = status_tool.write_task_status_snapshots(status)
    task_rows = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    row = next((item for item in task_rows if isinstance(item, dict) and item.get("task_id") == task_id), {})
    review_row = next((item for item in task_rows if isinstance(item, dict) and item.get("task_id") == review_task_id), {})
    partial_row = next((item for item in task_rows if isinstance(item, dict) and item.get("task_id") == partial_task_id), {})
    status_only_row = next((item for item in task_rows if isinstance(item, dict) and item.get("task_id") == review_status_only_task_id), {})
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    snapshot = json.loads((status_dir / f"{task_id}.json").read_text(encoding="utf-8"))
    review_snapshot = json.loads((status_dir / f"{review_task_id}.json").read_text(encoding="utf-8"))
    partial_snapshot = json.loads((status_dir / f"{partial_task_id}.json").read_text(encoding="utf-8"))
    ledger_gate = row.get("ledger_quality_gate") if isinstance(row, dict) else {}
    review_gate = review_row.get("ledger_quality_gate") if isinstance(review_row, dict) else {}
    partial_gate = partial_row.get("ledger_quality_gate") if isinstance(partial_row, dict) else {}
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []

    failures: list[str] = []
    check("stale manifest gate is preserved for audit", row.get("manifest_quality_gate_status") == "degraded_quorum", failures)
    check("ledger uptake overrides status quality gate", row.get("quality_gate_status") == "peer_reviewed", failures)
    check("ledger quality reason is explicit", (ledger_gate or {}).get("reason") == "ledger_point_uptake_recorded", failures)
    check("overview degraded quorum count reflects review-status degraded task", overview.get("degraded_quorum_task_count") == 1, failures)
    check(
        "overview includes review-status degraded quorum in degraded task set",
        review_status_only_task_id in (overview.get("degraded_quorum_task_ids") or []),
        failures,
    )
    check(
        "overview flags review-status degraded quorum as repair-needed task",
        status_only_row.get("review_status") == "degraded_quorum" and int(overview.get("needs_collaboration_repair_count") or 0) == 1,
        failures,
    )
    check(
        "overview emits collaboration repair action for review-status degraded quorum",
        any(
            item.get("type") == "collaboration_repair_needed"
            and item.get("task_id") == review_status_only_task_id
            and item.get("agent_id") == "openclaw-main"
            for item in action_items
            if isinstance(item, dict)
        ),
        failures,
    )
    check("overview counts task as peer reviewed", overview.get("peer_reviewed_task_count") == 1, failures)
    check("overview counts unfinished closures as needing review", overview.get("needs_collaboration_review_count") == 2, failures)
    check("snapshot omits degraded quorum after ledger closure", snapshot.get("degraded_quorum") is None, failures)
    check("snapshot still exposes completed ledger", (snapshot.get("ledger") or {}).get("status") == "completed", failures)
    check("ledger artifacts without uptake need review", review_row.get("quality_gate_status") == "needs_collaboration_review" and (review_gate or {}).get("reason") == "ledger_points_missing_peer_uptake", failures)
    check("artifact-only review does not preserve stale degraded quorum", review_row.get("degraded_quorum") is False and review_snapshot.get("degraded_quorum") is None, failures)
    check("partial peer uptake stays in review", partial_row.get("quality_gate_status") == "needs_collaboration_review" and (partial_gate or {}).get("reason") == "ledger_points_missing_peer_uptake", failures)
    check("partial peer uptake does not preserve stale degraded quorum", partial_row.get("degraded_quorum") is False and partial_snapshot.get("degraded_quorum") is None, failures)
    check(
        "partial missing uptake action item targets peer",
        any(
            item.get("type") == "peer_uptake_needed"
            and item.get("task_id") == partial_task_id
            and item.get("agent_id") == "claude-code"
            and item.get("point_id") == "pt-002"
            for item in action_items
            if isinstance(item, dict)
        ),
        failures,
    )
    check("task status snapshot was written", str(status_dir / f"{task_id}.json") in snapshot_paths, failures)
    check("review task status snapshot was written", str(status_dir / f"{review_task_id}.json") in snapshot_paths, failures)
    check("partial task status snapshot was written", str(status_dir / f"{partial_task_id}.json") in snapshot_paths, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_ledger_quality_gate_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "row_quality_gate_status": row.get("quality_gate_status"),
        "manifest_quality_gate_status": row.get("manifest_quality_gate_status"),
        "ledger_quality_gate": ledger_gate,
        "partial_quality_gate": partial_gate,
        "overview": overview,
        "snapshot_degraded_quorum": snapshot.get("degraded_quorum"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
