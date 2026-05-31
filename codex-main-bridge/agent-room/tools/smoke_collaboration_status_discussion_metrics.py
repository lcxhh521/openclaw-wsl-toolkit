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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-discussion-metrics-smoke"


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
    task_id = "standing-smoke-discussion-metrics"

    write_json(
        room / "tasks" / task_id / "manifest.json",
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "partial",
            "review_status": "requested",
            "quality_gate_status": "needs_collaboration_review",
            "target_agents": ["codex", "claude-code"],
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "participants": ["codex", "claude-code"],
                "points": [],
                "uptakes": [],
                "handoffs": [],
                "blockers": [],
            },
            "updated_at": "2026-05-27T13:00:00+08:00",
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
            "work_items": [],
            "claims": [],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [
                {"id": "pt-001", "agent_id": "codex", "kind": "proposal", "text": "status surface should show uptake"},
                {"id": "pt-002", "agent_id": "claude-code", "kind": "risk", "text": "fallback cannot write tools"},
                {"id": "pt-003", "agent_id": "codex", "kind": "summary", "text": "integrated summary remains pending peer uptake"},
                {"id": "pt-004", "agent_id": "codex", "kind": "summary", "text": "integrated summary with peer uptake"},
                {"id": "pt-005", "agent_id": "codex", "kind": "summary", "text": "challenged summary still needs integrated closure"},
            ],
            "uptakes": [
                {
                    "id": "uptake-001",
                    "point_id": "pt-001",
                    "point_agent_id": "codex",
                    "by_agent": "claude-code",
                    "status": "incorporated",
                    "reason": "peer signal changed status design",
                },
                {
                    "id": "uptake-002",
                    "point_id": "pt-002",
                    "point_agent_id": "claude-code",
                    "by_agent": "codex",
                    "status": "challenged",
                    "reason": "Codex can inspect local ledger even if peer fallback cannot",
                },
                {
                    "id": "uptake-003",
                    "point_id": "pt-004",
                    "point_agent_id": "codex",
                    "by_agent": "claude-code",
                    "status": "incorporated",
                    "reason": "Claude Code accepted the integrated summary as closure basis",
                },
                {
                    "id": "uptake-004",
                    "point_id": "pt-005",
                    "point_agent_id": "codex",
                    "by_agent": "claude-code",
                    "status": "challenged",
                    "reason": "Claude Code challenged the closure summary and needs main integration",
                },
            ],
            "created_at": "2026-05-27T13:00:00+08:00",
            "updated_at": "2026-05-27T13:01:00+08:00",
        },
    )
    write_json(
        room / "active-runners" / f"codex-{task_id}.json",
        {
            "agent_id": "codex",
            "run_id": task_id,
            "task_id": task_id,
            "room_id": "openclaw-evolution",
            "pid": 0,
            "runner_dir": str(room / "runner-runs" / task_id / "codex"),
            "task": {
                "task_id": task_id,
                "run_id": task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "source": {"transport": "agent-room-standing-mainline"},
            },
        },
    )
    for idx in range(10):
        filler_id = f"newer-filler-task-{idx:02d}"
        write_json(
            room / "tasks" / filler_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": filler_id,
                "run_id": filler_id,
                "room_id": "openclaw-evolution",
                "status": "completed",
                "target_agents": ["codex"],
                "collaboration": {"schema": "openclaw.agent_room.collaboration.v0", "participants": ["codex"]},
                "updated_at": f"2026-05-27T13:02:{idx:02d}+08:00",
                "source": {"transport": "agent-room-standing-mainline"},
            },
        )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_discussion_metrics_smoke")
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
    watch_first = status_tool.write_watch_artifacts(status, compact)
    watch_second = status_tool.write_watch_artifacts(status, compact)
    watch_text = (status_dir / "watch.txt").read_text(encoding="utf-8") if (status_dir / "watch.txt").exists() else ""
    transition_lines = (status_dir / "transitions.jsonl").read_text(encoding="utf-8").splitlines() if (status_dir / "transitions.jsonl").exists() else []
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    current_signature = status_tool.status_watch_signature(status)
    task_rows = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    recent_task_ids = [str(row.get("task_id") or "") for row in task_rows if isinstance(row, dict)]
    task_collab = task_rows[0].get("collaboration") if task_rows and isinstance(task_rows[0], dict) else {}
    task_progress = task_rows[0].get("per_agent_progress") if task_rows and isinstance(task_rows[0], dict) else {}
    snapshot_collab = snapshot.get("collaboration_health") if isinstance(snapshot.get("collaboration_health"), dict) else {}
    snapshot_progress = snapshot.get("per_agent_collaboration_progress") if isinstance(snapshot.get("per_agent_collaboration_progress"), dict) else {}
    recent_threads = overview.get("recent_material_threads") if isinstance(overview.get("recent_material_threads"), list) else []
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    overview_progress = overview.get("per_agent_discussion_progress") if isinstance(overview.get("per_agent_discussion_progress"), dict) else {}
    signature = status_tool.status_watch_signature({
        "generated_at": "2026-05-27T13:00:00+08:00",
        "visibility_state": "active_with_local_output",
        "active_task_ids": ["standing-smoke-watch-task-a"],
        "per_agent_engagement": {
            "codex": {
                "engagement_state": "working_with_local_output",
                "working_runner_count": 1,
                "active_runner_count": 1,
                "pending_harvest_count": 0,
                "completed_presence_count": 0,
                "needs_attention_count": 0,
                "active_task_ids": ["standing-smoke-watch-task-a"],
            }
        },
        "collaboration_overview": {},
    })
    attention_signature = status_tool.status_watch_signature({
        "generated_at": "2026-05-27T13:00:00+08:00",
        "visibility_state": "active_with_local_output",
        "active_task_ids": ["standing-smoke-watch-task-a"],
        "per_agent_engagement": {},
        "collaboration_overview": {
            "needs_collaboration_attention_task_ids": ["standing-smoke-watch-task-b"],
            "runner_attention_task_count": 1,
        },
    })

    failures: list[str] = []
    check("pinned active task remains in recent collaboration overview", task_id in recent_task_ids, failures)
    check("ledger points override stale manifest points", task_collab.get("material_points") == 5, failures)
    check("peer uptake count is surfaced", task_collab.get("peer_uptakes") == 4, failures)
    check("peer challenge count is surfaced", task_collab.get("peer_challenges") == 2, failures)
    check("summary points count as integration signals", task_collab.get("summary_points") == 3, failures)
    check("summary peer uptake is surfaced", task_collab.get("summary_peer_uptakes") == 2, failures)
    check("summary without peer uptake is surfaced", task_collab.get("summary_points_without_peer_uptake") == 1, failures)
    check("integrated summary count is surfaced", task_collab.get("integrated_summaries") == 1, failures)
    check("pending summary integration is surfaced", task_collab.get("summary_needs_integration") == 2, failures)
    check("pending peer uptake is surfaced", task_collab.get("points_without_peer_uptake") == 1, failures)
    check("overview aggregates material points", overview.get("material_point_count") == 5, failures)
    check("overview aggregates peer uptake", overview.get("peer_uptake_count") == 4, failures)
    check("overview aggregates peer challenges", overview.get("peer_challenge_count") == 2, failures)
    check("overview aggregates integrated summaries", overview.get("integrated_summary_count") == 1, failures)
    check("overview records summary peer uptake gap tasks", overview.get("tasks_missing_summary_uptake_count") == 1, failures)
    check("overview records summary peer uptake gap task id", task_id in (overview.get("tasks_missing_summary_uptake_ids") or []), failures)
    check("overview records summary integration gap", task_id in (overview.get("tasks_needing_summary_integration_ids") or []), failures)
    check("overview records task missing peer uptake", task_id in (overview.get("tasks_missing_peer_uptake_ids") or []), failures)
    check("per-agent point counts are visible", (overview.get("per_agent_material_points") or {}).get("codex") == 4, failures)
    check("per-agent challenge counts are visible", (overview.get("per_agent_peer_challenges") or {}).get("codex") == 1, failures)
    check("task row carries codex discussion progress", (task_progress.get("codex") or {}).get("points_produced") == 4 and (task_progress.get("codex") or {}).get("peer_points_uptaken") == 1, failures)
    check("task row carries peer discussion progress", (task_progress.get("claude-code") or {}).get("points_produced") == 1 and (task_progress.get("claude-code") or {}).get("peer_points_uptaken") == 3, failures)
    check("overview aggregates per-agent discussion progress", (overview_progress.get("codex") or {}).get("points_with_peer_uptake") == 3 and (overview_progress.get("claude-code") or {}).get("peer_challenges") == 1, failures)
    check("overview records discussion progress states", ((overview_progress.get("codex") or {}).get("liveness_vs_progress_counts") or {}).get("producing_and_reviewing") == 1, failures)
    check("compact status keeps diagnostics out of fixed card", "协作:" not in compact and "任务待接收" not in compact, failures)
    check("markdown status surfaces material point count", "material_point_count: 5" in markdown, failures)
    check("markdown status surfaces peer uptake count", "peer_uptake_count: 4" in markdown, failures)
    check("markdown status surfaces peer challenge count", "peer_challenge_count: 2" in markdown, failures)
    check("markdown status surfaces integrated summaries", "integrated_summary_count: 1" in markdown, failures)
    check("markdown status surfaces summary integration gaps", "tasks_needing_summary_integration_count: 1" in markdown, failures)
    check("markdown status surfaces missing peer uptake", "tasks_missing_peer_uptake_count: 1" in markdown, failures)
    check("markdown status surfaces point-to-uptake thread", "pt-002 claude-code/risk" in markdown and "codex=challenged" in markdown, failures)
    check("overview carries recent material threads", any((thread.get("point_id") == "pt-002" and thread.get("peer_uptakes")) for thread in recent_threads if isinstance(thread, dict)), failures)
    check("overview carries pending uptake agent", any((thread.get("point_id") == "pt-003" and "claude-code" in (thread.get("pending_uptake_agents") or [])) for thread in recent_threads if isinstance(thread, dict)), failures)
    check("overview action item count is surfaced", overview.get("action_item_count") == 4, failures)
    check("overview assigns review gap to main", any((item.get("type") == "collaboration_review_needed" and item.get("agent_id") == "openclaw-main") for item in action_items if isinstance(item, dict)), failures)
    check("overview assigns pending uptake to peer", any((item.get("type") == "peer_uptake_needed" and item.get("agent_id") == "claude-code" and item.get("point_id") == "pt-003") for item in action_items if isinstance(item, dict)), failures)
    check("overview assigns summary integration to main", any((item.get("type") == "summary_integration_needed" and item.get("agent_id") == "openclaw-main" and item.get("point_id") == "pt-005") for item in action_items if isinstance(item, dict)), failures)
    check("overview assigns runner attention to main", any((item.get("type") == "runner_attention_needed" and item.get("agent_id") == "openclaw-main") for item in action_items if isinstance(item, dict)), failures)
    check("markdown status surfaces action items", "action_item_count: 4" in markdown and "openclaw-main/summary_integration_needed" in markdown, failures)
    check("markdown status surfaces per-agent discussion progress", "per_agent_discussion_progress:" in markdown and "codex: points=4" in markdown, failures)
    check("task snapshot carries collaboration health", snapshot_collab.get("summary_needs_integration") == 2 and str(status_dir / f"{task_id}.json") in snapshot_paths, failures)
    check("task snapshot carries per-agent discussion progress", (snapshot_progress.get("claude-code") or {}).get("points_with_peer_uptake") == 1, failures)
    check("watch signature includes discussion counters", (current_signature.get("collaboration_health") or {}).get("peer_uptake_count") == 4, failures)
    check("watch signature includes per-agent discussion progress", (((current_signature.get("collaboration_health") or {}).get("per_agent_discussion_progress") or {}).get("codex") or {}).get("peer_points_uptaken") == 1, failures)
    check("watch signature includes missing uptake counter", (current_signature.get("collaboration_health") or {}).get("tasks_missing_peer_uptake_count") == 1, failures)
    check("watch signature includes summary integration counter", (current_signature.get("collaboration_health") or {}).get("tasks_needing_summary_integration_count") == 1, failures)
    check("watch signature includes action items", (current_signature.get("collaboration_health") or {}).get("action_item_count") == 4, failures)
    check("watch artifact includes compact status", compact.splitlines()[0] in watch_text, failures)
    check("watch artifact records first status transition", watch_first.get("changed") is True and watch_first.get("transition_count") == 1, failures)
    check("watch artifact suppresses unchanged transition spam", watch_second.get("changed") is False and watch_second.get("transition_count") == 1 and len(transition_lines) == 1, failures)
    check("watch signature preserves per-agent active task ids", signature.get("agents", {}).get("codex", {}).get("task_ids") == ["standing-smoke-watch-task-a"], failures)
    check("watch signature preserves collaboration attention task ids", attention_signature.get("needs_attention_task_ids") == ["standing-smoke-watch-task-b"], failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_discussion_metrics_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "compact_status": compact,
        "watch": watch_second,
        "collaboration_overview": overview,
        "task_collaboration": task_collab,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
