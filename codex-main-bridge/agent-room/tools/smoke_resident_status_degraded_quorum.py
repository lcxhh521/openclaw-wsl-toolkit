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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "resident-status-degraded-quorum-smoke"


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


def configure_resident(module: Any, bridge_root: Path, room: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    module.RUNS = room / "resident-runs"
    module.ACTIVE_RUNNERS = room / "active-runners"
    module.FINISHED_RUNNERS = room / "finished-runners"
    module.COLLABORATION_STATUS = room / "collaboration-status"


def task(task_id: str, **extra: Any) -> dict[str, Any]:
    base = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": "openclaw-evolution",
        "target_agents": ["codex", "claude-code"],
        "quality_gate_status": "not_applicable",
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "mode": "standing_mainline_discussion",
            "participants": ["codex", "claude-code"],
            "status": "open",
        },
    }
    base.update(extra)
    return base


def write_runner(module: Any, room: Path, task_id: str, agent_id: str, pid: int) -> None:
    runner_dir = room / "dry-run-runners" / task_id / agent_id
    runner_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = runner_dir / "stdout.log"
    stderr_path = runner_dir / "stderr.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    now = datetime.now(timezone.utc).astimezone()
    started_at = (now - timedelta(seconds=30)).isoformat(timespec="seconds")
    soft_deadline_at = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
    hard_deadline_at = (now + timedelta(minutes=30)).isoformat(timespec="seconds")
    write_json(
        module.active_runner_path(agent_id, task_id),
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": agent_id,
            "run_id": task_id,
            "task_id": task_id,
            "pid": pid,
            "started_at": started_at,
            "soft_deadline_at": soft_deadline_at,
            "hard_deadline_at": hard_deadline_at,
            "runner_dir": str(runner_dir),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        },
    )


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    resident = load_module(TOOLS / "agent_room_resident_bridge.py", "agent_room_resident_bridge_degraded_quorum_smoke")
    configure_resident(resident, bridge_root, room)

    failures: list[str] = []
    missing_pid = 99999999

    full_dead = "standing-smoke-resident-full-dead"
    write_runner(resident, room, full_dead, "codex", missing_pid)
    write_runner(resident, room, full_dead, "claude-code", missing_pid)
    full_snapshot = resident.write_collaboration_status_snapshot(task(full_dead), "smoke_full_dead")
    full_degraded = full_snapshot.get("degraded_quorum") if isinstance(full_snapshot, dict) else {}
    unavailable = {
        item.get("agent_id")
        for item in (full_degraded.get("unavailable_agents") or [])
        if isinstance(item, dict)
    }
    check("full dead snapshot marks runner attention", full_snapshot.get("status") == "runner_attention_needed", failures)
    check("full dead snapshot has degraded quorum record", full_degraded.get("status") == "degraded_quorum_observed", failures)
    check("full dead reason is explicit", full_degraded.get("reason") == "all_local_agents_need_attention", failures)
    check("full dead records both unavailable agents", unavailable == {"codex", "claude-code"}, failures)

    partial_dead = "standing-smoke-resident-partial-dead"
    write_runner(resident, room, partial_dead, "codex", os.getpid())
    write_runner(resident, room, partial_dead, "claude-code", missing_pid)
    partial_snapshot = resident.write_collaboration_status_snapshot(task(partial_dead), "smoke_partial_dead")
    partial_liveness = partial_snapshot.get("agent_liveness") if isinstance(partial_snapshot, dict) else {}
    check("partial dead snapshot still marks runner attention", partial_snapshot.get("status") == "runner_attention_needed", failures)
    check("partial dead does not claim degraded quorum", partial_snapshot.get("degraded_quorum") is None, failures)
    check("partial dead keeps live peer visible", partial_liveness.get("codex", {}).get("state") == "alive_black_box_no_output_yet", failures)
    check("partial dead marks only failed peer attention", partial_liveness.get("claude-code", {}).get("needs_attention") is True, failures)

    gate_task_id = "standing-smoke-resident-quality-gate"
    gate_snapshot = resident.write_collaboration_status_snapshot(
        task(
            gate_task_id,
            quality_gate_status="degraded_quorum",
            runner_summary={
                "completed_agents": ["codex"],
                "targets": ["codex", "claude-code"],
                "degraded_quorum": True,
                "collaboration_quality_gate": {
                    "status": "degraded_quorum",
                    "reason": "missing_local_agent_results",
                    "missing_agents": ["claude-code"],
                },
            },
        ),
        "smoke_quality_gate",
    )
    gate_degraded = gate_snapshot.get("degraded_quorum") if isinstance(gate_snapshot, dict) else {}
    gate_unavailable = {
        item.get("agent_id")
        for item in (gate_degraded.get("unavailable_agents") or [])
        if isinstance(item, dict)
    }
    check("quality gate degraded quorum is mirrored", gate_degraded.get("reason") == "missing_local_agent_results", failures)
    check("quality gate unavailable agent is preserved", gate_unavailable == {"claude-code"}, failures)
    check("quality gate continued_by is preserved", gate_degraded.get("continued_by") == ["codex"], failures)

    result = {
        "schema": "openclaw.agent_room.resident_status_degraded_quorum_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "full_dead_degraded_quorum": full_degraded,
        "partial_dead_degraded_quorum": partial_snapshot.get("degraded_quorum"),
        "quality_gate_degraded_quorum": gate_degraded,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
