#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-active-material-silence-smoke"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    room = DRY_RUN / "codex-main-bridge" / "agent-room"
    (room / "collaboration-ledgers").mkdir(parents=True, exist_ok=True)
    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_active_material_silence_smoke")
    status_tool.ROOM = room
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"

    now = datetime.now(timezone.utc).astimezone()
    soft_deadline_future = (now + timedelta(minutes=30)).replace(microsecond=0).isoformat()
    soft_deadline_past = (now - timedelta(minutes=30)).replace(microsecond=0).isoformat()
    task_id = "standing-smoke-active-material-silence"
    task_id_post_soft = "standing-smoke-active-material-silence-post"

    synthetic_runners_pre_soft = [
        {
            "agent_id": "codex",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "alive": True,
            "result_exists": False,
            "ledger_completed": False,
            "needs_attention": False,
            "expected_agents": ["codex", "claude-code"],
            "runner_state": "working_silent_before_soft_deadline",
            "stdout_size": 0,
            "stderr_size": 0,
            "soft_deadline_at": soft_deadline_future,
            "last_chat_action_at": None,
        },
        {
            "agent_id": "claude-code",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "alive": True,
            "result_exists": False,
            "ledger_completed": False,
            "needs_attention": False,
            "expected_agents": ["codex", "claude-code"],
            "runner_state": "working_silent_before_soft_deadline",
            "stdout_size": 0,
            "stderr_size": 0,
            "soft_deadline_at": soft_deadline_future,
            "last_chat_action_at": None,
        },
    ]

    synthetic_runners_post_soft = [
        {
            "agent_id": "codex",
            "task_id": task_id_post_soft,
            "run_id": task_id_post_soft,
            "room_id": "openclaw-evolution",
            "alive": True,
            "result_exists": False,
            "ledger_completed": False,
            "needs_attention": False,
            "expected_agents": ["codex", "claude-code"],
            "runner_state": "working_silent_past_soft_deadline",
            "stdout_size": 0,
            "stderr_size": 0,
            "soft_deadline_at": soft_deadline_past,
            "last_chat_action_at": None,
        },
        {
            "agent_id": "claude-code",
            "task_id": task_id_post_soft,
            "run_id": task_id_post_soft,
            "room_id": "openclaw-evolution",
            "alive": True,
            "result_exists": False,
            "ledger_completed": False,
            "needs_attention": False,
            "expected_agents": ["codex", "claude-code"],
            "runner_state": "working_silent_past_soft_deadline",
            "stdout_size": 0,
            "stderr_size": 0,
            "soft_deadline_at": soft_deadline_past,
            "last_chat_action_at": None,
        },
    ]

    tasks = [
        {
            "task_id": task_id,
            "room_id": "openclaw-evolution",
            "quality_gate_status": "running",
            "target_agents": ["codex", "claude-code"],
            "collaboration": {
                "material_points": 0,
                "peer_uptakes": 0,
                "peer_challenges": 0,
                "summary_points": 0,
                "summary_peer_uptakes": 0,
                "integrated_summaries": 0,
                "summary_needs_integration": 0,
                "points_without_peer_uptake": 0,
                "summary_points_without_peer_uptake": 0,
                "summary_needs_integration_ids": [],
                "point_counts_by_agent": {},
                "peer_uptake_counts_by_agent": {},
                "peer_challenge_counts_by_agent": {},
            },
            "per_agent_progress": {
                "codex": {"points_produced": 0, "peer_points_uptaken": 0, "peer_challenges": 0},
                "claude-code": {"points_produced": 0, "peer_points_uptaken": 0, "peer_challenges": 0},
            },
            "updated_at": "2026-05-29T10:33:00+08:00",
        }
    ]

    runner_attention = status_tool.runner_attention_overview(synthetic_runners_pre_soft)
    overview = status_tool.collaboration_overview(tasks, runner_attention)
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    watch_items = [
        item
        for item in action_items
        if isinstance(item, dict) and str(item.get("type") or "") == "active_material_silence_watch"
    ]

    failures: list[str] = []
    check(
        "active material silence is counted",
        int(overview.get("active_material_silence_task_count") or 0) == 1,
        failures,
    )
    check(
        "active material silence task id is reported",
        task_id in (overview.get("active_material_silence_task_ids") or []),
        failures,
    )
    check(
        "active silent agent set includes both local agents",
        overview.get("active_material_silent_agents_by_task", {}).get(task_id) == ["claude-code", "codex"],
        failures,
    )
    check(
        "active material silence does not raise runner attention before soft boundary",
        int(runner_attention.get("runner_attention_task_count") or 0) == 0,
        failures,
    )
    check(
        "active silent task generates watch action item",
        len(watch_items) == 1,
        failures,
    )
    if watch_items:
        item = watch_items[0]
        check(
            "watch item targets missing action agents",
            sorted(item.get("silent_agents") or []) == ["claude-code", "codex"],
            failures,
        )
        check(
            "watch item is owned by openclaw-main",
            item.get("agent_id") == "openclaw-main",
            failures,
        )
        check(
            "watch item contains reason",
            isinstance(item.get("reason"), str) and "live runner has no material point/uptake/challenge" in item.get("reason", ""),
            failures,
        )
        check(
            "watch item carries concrete agent next action",
            isinstance(item.get("next_action"), str) and "material point" in item.get("next_action", "") and "NO_COMMENT" in item.get("next_action", ""),
            failures,
        )
    check(
        "per-agent action item pressure is assigned to silent agents",
        (overview.get("per_agent_action_items") or {}).get("codex", 0) == 1
        and (overview.get("per_agent_action_items") or {}).get("claude-code", 0) == 1,
        failures,
    )
    pre_soft_items = action_items

    runner_attention = status_tool.runner_attention_overview(synthetic_runners_post_soft)
    tasks_post_soft = [dict(task, task_id=task_id_post_soft, per_agent_progress={"codex": {"points_produced": 0, "peer_points_uptaken": 0, "peer_challenges": 0}, "claude-code": {"points_produced": 0, "peer_points_uptaken": 0, "peer_challenges": 0}}) for task in tasks]
    overview_post_soft = status_tool.collaboration_overview(tasks_post_soft, runner_attention)
    action_items_post_soft = overview_post_soft.get("action_items") if isinstance(overview_post_soft.get("action_items"), list) else []
    repair_items = [
        item
        for item in action_items_post_soft
        if isinstance(item, dict) and str(item.get("type") or "") == "collaboration_repair_needed"
    ]
    material_progress_items = [
        item
        for item in action_items_post_soft
        if isinstance(item, dict) and str(item.get("type") or "") == "material_progress_needed"
    ]
    watch_items_post_soft = [
        item
        for item in action_items_post_soft
        if isinstance(item, dict) and str(item.get("type") or "") == "active_material_silence_watch"
    ]
    check(
        "post-soft deadline task is tracked",
        task_id_post_soft in (overview_post_soft.get("active_material_silence_post_soft_deadline_task_ids") or []),
        failures,
    )
    check(
        "post-soft deadline task emits repair action",
        len(repair_items) >= 1,
        failures,
    )
    check(
        "post-soft deadline emits per-agent material progress actions",
        len(material_progress_items) >= 2,
        failures,
    )
    check(
        "post-soft material progress items carry repair next action",
        all("material progress now" in str(item.get("next_action") or "") for item in material_progress_items),
        failures,
    )
    check(
        "post-soft repair item carries degraded quorum next action",
        repair_items and "degraded-quorum" in str(repair_items[0].get("next_action") or ""),
        failures,
    )
    check(
        "post-soft deadline remains watch-only for same silence profile before hard attention",
        len(watch_items_post_soft) == 1,
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.collaboration_status_active_material_silence_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "runner_attention_overview": runner_attention,
        "pre_soft_deadline_collaboration_overview": overview,
        "pre_soft_deadline_action_items": pre_soft_items,
        "post_soft_deadline_collaboration_overview": overview_post_soft,
        "post_soft_deadline_action_items": action_items_post_soft,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
