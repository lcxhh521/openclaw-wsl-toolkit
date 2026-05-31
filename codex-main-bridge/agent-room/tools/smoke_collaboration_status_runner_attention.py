#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-runner-attention-smoke"


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


def active_runner_record(
    agent_id: str,
    task_id: str,
    pid: int,
    runner_dir: Path,
    *,
    soft_deadline_at: str = "2026-05-27T13:05:00+08:00",
    hard_deadline_at: str = "2026-05-27T13:30:00+08:00",
) -> dict[str, Any]:
    return {
        "schema": "openclaw.agent_room.active_runner.v0",
        "status": "running",
        "agent_id": agent_id,
        "run_id": task_id,
        "task_id": task_id,
        "room_id": "openclaw-evolution",
        "pid": pid,
        "runner_dir": str(runner_dir),
        "stdout_path": str(runner_dir / "stdout.log"),
        "stderr_path": str(runner_dir / "stderr.log"),
        "started_at": "2026-05-27T13:00:00+08:00",
        "soft_deadline_at": soft_deadline_at,
        "hard_deadline_at": hard_deadline_at,
        "task_budget": {
            "expected_agents": ["codex", "claude-code"],
        },
        "task": {
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "agent-room-standing-mainline"},
        },
    }


def write_runner(
    room: Path,
    task_id: str,
    agent_id: str,
    pid: int,
    *,
    soft_deadline_at: str = "2026-05-27T13:05:00+08:00",
    hard_deadline_at: str = "2026-05-27T13:30:00+08:00",
) -> None:
    runner_dir = room / "dry-run-runners" / task_id / agent_id
    runner_dir.mkdir(parents=True, exist_ok=True)
    (runner_dir / "stdout.log").write_text("", encoding="utf-8")
    (runner_dir / "stderr.log").write_text("", encoding="utf-8")
    write_json(
        room / "active-runners" / f"{agent_id}-{task_id}.json",
        active_runner_record(
            agent_id,
            task_id,
            pid,
            runner_dir,
            soft_deadline_at=soft_deadline_at,
            hard_deadline_at=hard_deadline_at,
        ),
    )


def write_task(room: Path, task_id: str) -> None:
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
            "updated_at": "2026-05-27T13:00:00+08:00",
            "source": {"transport": "agent-room-standing-mainline"},
        },
    )


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    degraded_task = "standing-smoke-degraded-quorum"
    partial_task = "standing-smoke-partial-attention"
    material_stall_task = "standing-smoke-material-stall"
    live_silent_task = "standing-smoke-live-material-silence"
    completed_task = "standing-smoke-ledger-completed-stale-runner"
    completed_live_task = "standing-smoke-ledger-completed-live-runner"
    systemd_no_main_task = "standing-smoke-systemd-no-mainpid"
    exit_marker_task = "standing-smoke-exit-marker"
    impossible_pid = 99999999

    write_task(room, degraded_task)
    write_runner(room, degraded_task, "codex", impossible_pid)
    write_runner(room, degraded_task, "claude-code", impossible_pid)

    write_task(room, partial_task)
    write_runner(room, partial_task, "codex", impossible_pid)
    write_runner(room, partial_task, "claude-code", os.getpid())

    write_task(room, material_stall_task)
    write_runner(room, material_stall_task, "codex", impossible_pid)
    write_runner(room, material_stall_task, "claude-code", impossible_pid)
    write_json(
        room / "collaboration-ledgers" / f"{material_stall_task}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": material_stall_task,
            "run_id": material_stall_task,
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
                    "id": "pt-material-001",
                    "agent_id": "codex",
                    "kind": "evidence",
                    "text": "Codex produced a material point while peer runner has no material point.",
                }
            ],
            "uptakes": [],
        },
    )
    write_task(room, live_silent_task)
    write_runner(
        room,
        live_silent_task,
        "codex",
        os.getpid(),
        soft_deadline_at="2999-01-01T00:00:00+08:00",
        hard_deadline_at="2999-01-01T00:30:00+08:00",
    )

    write_task(room, completed_task)
    write_runner(room, completed_task, "codex", impossible_pid)
    write_json(
        room / "collaboration-ledgers" / f"{completed_task}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": completed_task,
            "run_id": completed_task,
            "room_id": "openclaw-evolution",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {
                    "id": f"{completed_task}_codex",
                    "status": "completed",
                    "assigned_to": "codex",
                    "claimed_by": "codex",
                }
            ],
            "claims": [
                {
                    "work_item_id": f"{completed_task}_codex",
                    "agent_id": "codex",
                    "status": "completed",
                }
            ],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
        },
    )
    write_task(room, completed_live_task)
    write_runner(room, completed_live_task, "codex", os.getpid())
    write_json(
        room / "collaboration-ledgers" / f"{completed_live_task}.json",
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": completed_live_task,
            "run_id": completed_live_task,
            "room_id": "openclaw-evolution",
            "status": "completed",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {
                    "id": f"{completed_live_task}_codex",
                    "status": "completed",
                    "assigned_to": "codex",
                    "claimed_by": "codex",
                }
            ],
            "claims": [
                {
                    "work_item_id": f"{completed_live_task}_codex",
                    "agent_id": "codex",
                    "status": "completed",
                }
            ],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
        },
    )
    write_task(room, exit_marker_task)
    exit_marker_task_dir = room / "dry-run-runners" / exit_marker_task / "codex"
    write_runner(
        room,
        exit_marker_task,
        "codex",
        os.getpid(),
        soft_deadline_at="2999-01-01T00:00:00+08:00",
        hard_deadline_at="2999-01-01T00:30:00+08:00",
    )
    exit_marker_task_dir.mkdir(parents=True, exist_ok=True)
    (exit_marker_task_dir / ".runner-exit-marker").write_text(
        json.dumps({"finished_at": "2026-05-29T00:00:00+08:00", "exit_code": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    write_task(room, systemd_no_main_task)
    systemd_runner_dir = room / "dry-run-runners" / systemd_no_main_task / "codex"
    systemd_runner_dir.mkdir(parents=True, exist_ok=True)
    (systemd_runner_dir / "stdout.log").write_text("", encoding="utf-8")
    (systemd_runner_dir / "stderr.log").write_text("", encoding="utf-8")
    systemd_no_main_record = active_runner_record("codex", systemd_no_main_task, os.getpid(), systemd_runner_dir)
    systemd_no_main_record["systemd_unit"] = "openclaw-agent-runner-smoke-no-mainpid"
    systemd_no_main_record["task_budget"] = {"expected_agents": ["codex"]}
    systemd_no_main_record["task"]["target_agents"] = ["codex"]
    write_json(
        room / "active-runners" / f"codex-{systemd_no_main_task}.json",
        systemd_no_main_record,
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"
    status_tool.systemd_show_unit = lambda unit: {
        "show_exit_code": "0",
        "MainPID": "0",
        "ActiveState": "active",
        "SubState": "running",
    }

    status = status_tool.build_status(include_background=True)
    markdown = status_tool.render_markdown_status(status)
    signature = status_tool.status_watch_signature(status)
    written_task_status_paths = status_tool.write_task_status_snapshots(status)
    collaboration = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    mixed_engagement = status_tool.agent_engagement_rows(
        [
            {
                "agent_id": "codex",
                "task_id": "standing-smoke-current-active",
                "run_id": "standing-smoke-current-active",
                "runner_state": "working_silent_before_soft_deadline",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": False,
                "is_black_box": True,
            }
        ],
        [
            {
                "agent_id": "codex",
                "task_id": "standing-smoke-prior-completed",
                "run_id": "standing-smoke-prior-completed",
                "runner_state": "completed_awaiting_integration",
                "presence_state": "completed",
            }
        ],
    )
    mixed_attention_engagement = status_tool.agent_engagement_rows(
        [
            {
                "agent_id": "codex",
                "task_id": "standing-smoke-current-active",
                "run_id": "standing-smoke-current-active",
                "runner_state": "working_silent_before_soft_deadline",
                "alive": True,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": False,
                "is_black_box": True,
            },
            {
                "agent_id": "codex",
                "task_id": "standing-smoke-stale-runner",
                "run_id": "standing-smoke-stale-runner",
                "runner_state": "dead_without_result",
                "alive": False,
                "result_exists": False,
                "ledger_completed": False,
                "needs_attention": True,
                "is_black_box": False,
            },
        ],
        [],
    )
    mixed_attention_one_glance = status_tool.one_glance_status(mixed_attention_engagement)
    mixed_attention_codex_card = next(
        (
            card for card in (mixed_attention_one_glance.get("cards") or [])
            if isinstance(card, dict) and card.get("agent_id") == "codex"
        ),
        {},
    )
    mixed_attention_compact = status_tool.render_compact_status(
        {
            "generated_at": "2026-05-27T13:00:00+08:00",
            "per_agent_engagement": mixed_attention_engagement,
            "collaboration_overview": {},
        }
    )
    stale_suppressed_visibility = status_tool.classify_visibility_state(
        [
            {
                "agent_id": "codex",
                "task_id": "standing-smoke-completed-ledger-stale-active-file",
                "run_id": "standing-smoke-completed-ledger-stale-active-file",
                "runner_state": "completed_ledger_stale_runner_record",
                "alive": False,
                "result_exists": False,
                "ledger_completed": True,
                "needs_attention": False,
                "stdout_size": 0,
                "stderr_size": 0,
            }
        ],
        {
            "standing_agenda_tick": {
                "result": {
                    "status": "suppressed_active_runner",
                    "active_runner_count": 1,
                }
            }
        },
    )
    degraded_task_status = json.loads(
        (status_dir / f"{degraded_task}.json").read_text(encoding="utf-8")
    )
    partial_task_status = json.loads(
        (status_dir / f"{partial_task}.json").read_text(encoding="utf-8")
    )
    completed_task_status = json.loads(
        (status_dir / f"{completed_task}.json").read_text(encoding="utf-8")
    )
    exit_marker_task_path = status_dir / f"{exit_marker_task}.json"
    systemd_no_main_task_status = json.loads(
        (status_dir / f"{systemd_no_main_task}.json").read_text(encoding="utf-8")
    )
    failures: list[str] = []
    check("runner attention task count includes dead runners", collaboration.get("runner_attention_task_count") == 5, failures)
    check("full dead expected-agent set is degraded quorum", degraded_task in (collaboration.get("runner_degraded_quorum_task_ids") or []), failures)
    check("partial dead expected-agent set is not degraded quorum", partial_task not in (collaboration.get("runner_degraded_quorum_task_ids") or []), failures)
    check("partial runner attention is surfaced separately", partial_task in (collaboration.get("runner_partial_attention_task_ids") or []), failures)
    check("ledger-completed stale runner is not runner attention", completed_task not in (collaboration.get("runner_attention_task_ids") or []), failures)
    check("ledger-completed live runner is not runner attention", completed_live_task not in (collaboration.get("runner_attention_task_ids") or []), failures)
    check("systemd unit without MainPID is runner attention", systemd_no_main_task in (collaboration.get("runner_attention_task_ids") or []), failures)
    check("degraded quorum contributes to collaboration overview", degraded_task in (collaboration.get("degraded_quorum_task_ids") or []), failures)
    check("runner attention contributes to attention task ids", degraded_task in (collaboration.get("needs_collaboration_attention_task_ids") or []), failures)
    check("partial attention contributes to attention task ids", partial_task in (collaboration.get("needs_collaboration_attention_task_ids") or []), failures)
    check(
        "live runner without material point is surfaced before soft deadline",
        live_silent_task in (collaboration.get("active_material_silence_task_ids") or []),
        failures,
    )
    check(
        "active material silence targets the live silent runner only",
        (collaboration.get("active_material_silent_agents_by_task") or {}).get(live_silent_task) == ["codex"],
        failures,
    )
    check(
        "active material silence is not runner attention before soft deadline",
        live_silent_task not in (collaboration.get("runner_attention_task_ids") or []),
        failures,
    )
    check(
        "active material silence does not create material-progress action before soft deadline",
        not any(
            item.get("type") == "material_progress_needed"
            and item.get("task_id") == live_silent_task
            for item in (collaboration.get("action_items") or [])
            if isinstance(item, dict)
        ),
        failures,
    )
    check(
        "active material silence creates watch action before soft deadline",
        any(
            item.get("type") == "active_material_silence_watch"
            and item.get("task_id") == live_silent_task
            and item.get("agent_id") == "openclaw-main"
            and item.get("silent_agents") == ["codex"]
            for item in (collaboration.get("action_items") or [])
            if isinstance(item, dict)
        ),
        failures,
    )
    check(
        "active material silence watch does not mark task as attention-needed",
        live_silent_task not in (collaboration.get("needs_collaboration_attention_task_ids") or []),
        failures,
    )
    check(
        "active material silence watch is skipped once runner attention exists",
        not any(
            item.get("type") == "active_material_silence_watch"
            and item.get("task_id") == partial_task
            for item in (collaboration.get("action_items") or [])
            if isinstance(item, dict)
        ),
        failures,
    )
    check(
        "markdown renders active material silence diagnostic",
        "active_material_silence_task_ids:" in markdown
        and live_silent_task in markdown,
        failures,
    )
    check(
        "markdown renders active material silence watch in per-agent queue",
        "per_agent_next_actions:" in markdown
        and f"active_material_silence_watch: task={live_silent_task}; role=watch_target; primary=openclaw-main; detail=codex" in markdown,
        failures,
    )
    check(
        "watch signature includes active material silence diagnostic",
        ((signature.get("collaboration_health") or {}).get("active_material_silence_task_count") or 0) >= 1
        and (signature.get("collaboration_health") or {}).get("active_material_silent_agents_by_task", {}).get(live_silent_task) == ["codex"],
        failures,
    )
    check(
        "watch signature includes active material silence watch in per-agent queue",
        f"active_material_silence_watch:{live_silent_task}:watch_target:openclaw-main:codex"
        in ((((signature.get("collaboration_health") or {}).get("per_agent_next_actions") or {}).get("codex") or {}).get("actions") or []),
        failures,
    )
    check("material stall task is surfaced", material_stall_task in (collaboration.get("material_stall_task_ids") or []), failures)
    check(
        "material progress reads ledger point counts",
        (collaboration.get("material_progress_agents_by_task") or {}).get(material_stall_task) == ["codex"],
        failures,
    )
    check(
        "material stall is assigned to agent without points",
        (collaboration.get("material_stall_agents_by_task") or {}).get(material_stall_task) == ["claude-code"],
        failures,
    )
    check(
        "material stall action item targets stalled peer",
        any(
            item.get("type") == "material_progress_needed"
            and item.get("task_id") == material_stall_task
            and item.get("agent_id") == "claude-code"
            for item in (collaboration.get("action_items") or [])
            if isinstance(item, dict)
        ),
        failures,
    )
    check("task-specific degraded snapshot is refreshed", str(status_dir / f"{degraded_task}.json") in written_task_status_paths, failures)
    check("task-specific degraded snapshot reports runner attention", degraded_task_status.get("status") == "runner_attention_needed", failures)
    check(
        "task-specific degraded snapshot carries degraded quorum record",
        isinstance(degraded_task_status.get("degraded_quorum"), dict)
        and degraded_task_status.get("degraded_quorum", {}).get("reason") == "all_local_agents_need_attention",
        failures,
    )
    check("task-specific degraded snapshot records dead runner", any(row.get("runner_state") == "dead_without_result" for row in degraded_task_status.get("active_runners") or []), failures)
    check(
        "task-specific degraded snapshot does not report dead PID as live",
        all(row.get("alive") is False for row in degraded_task_status.get("active_runners") or []),
        failures,
    )
    check(
        "task-specific degraded snapshot does not report dead PID as black-box live",
        not any(
            row.get("runner_state") in {"working_silent_before_soft_deadline", "over_soft_deadline_no_output", "hard_deadline_exceeded_no_result"}
            for row in degraded_task_status.get("active_runners") or []
        ),
        failures,
    )
    check("task-specific partial snapshot reports runner attention", partial_task_status.get("status") == "runner_attention_needed", failures)
    check("task-specific partial snapshot does not claim degraded quorum", partial_task_status.get("degraded_quorum") is None, failures)
    check("task-specific completed stale snapshot does not override completed ledger", completed_task_status.get("status") == "completed", failures)
    check("task-specific systemd no-mainpid snapshot reports runner attention", systemd_no_main_task_status.get("status") == "runner_attention_needed", failures)
    check(
        "systemd no-mainpid row is not projected as live record-pid work",
        any(
            row.get("runner_state") == "dead_without_result"
            and row.get("liveness_source") == "systemd_no_main_pid"
            and row.get("alive") is False
            for row in systemd_no_main_task_status.get("active_runners") or []
        ),
        failures,
    )
    check(
        "exit-marker snapshot exists",
        exit_marker_task_path.exists(),
        failures,
    )
    if exit_marker_task_path.exists():
        exit_marker_task_status = json.loads(
            exit_marker_task_path.read_text(encoding="utf-8")
        )
        check(
            "exit-marker row is not treated as live even with live-looking pid",
            exit_marker_task_status.get("status") == "runner_attention_needed",
            failures,
        )
        check(
            "exit-marker row is classified from runner_exit_marker source",
            any(
                row.get("liveness_source") == "runner_exit_marker"
                and row.get("alive") is False
                for row in exit_marker_task_status.get("active_runners") or []
            ),
            failures,
        )
    else:
        failures.append("exit-marker snapshot missing")
    check(
        "current active runner outranks older completed presence in agent engagement",
        mixed_engagement.get("codex", {}).get("engagement_state") == "working_silent_before_soft_deadline"
        and mixed_engagement.get("codex", {}).get("active_runner_count") == 1
        and mixed_engagement.get("codex", {}).get("working_runner_count") == 1
        and mixed_engagement.get("codex", {}).get("completed_presence_count") == 1,
        failures,
    )
    check(
        "one-glance separates current work from stale runner attention",
        mixed_attention_codex_card.get("work_status") == "working_with_attention"
        and mixed_attention_codex_card.get("working_runner_count") == 1
        and mixed_attention_codex_card.get("needs_attention_count") == 1,
        failures,
    )
    check(
        "compact status says agent is still working when stale runner also needs attention",
        "Codex 🟡执行+异常" in mixed_attention_compact
        and "工作中，有异常待排查" in mixed_attention_compact
        and "1工作 / 1异常" in mixed_attention_compact,
        failures,
    )
    check(
        "stale daemon active-runner suppression is not projected as current suppression",
        stale_suppressed_visibility == "standing_agenda_suppressed_active_runner_stale",
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.collaboration_status_runner_attention_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "collaboration_overview": collaboration,
        "mixed_attention_compact": mixed_attention_compact,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
