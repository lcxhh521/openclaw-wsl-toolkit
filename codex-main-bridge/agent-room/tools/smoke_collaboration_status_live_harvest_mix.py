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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-live-harvest-mix-smoke"
ROOM_ID = "openclaw-evolution"
TASK_ID = "tg-openclaw-evolution-smoke-live-harvest-mix"


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


def write_task(room: Path) -> None:
    write_json(
        room / "tasks" / TASK_ID / "manifest.json",
        {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": TASK_ID,
            "run_id": TASK_ID,
            "room_id": ROOM_ID,
            "status": "running",
            "review_status": "requested",
            "quality_gate_status": "not_applicable",
            "target_agents": ["codex", "claude-code"],
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        },
    )


def write_runner(room: Path, agent_id: str, *, result_ready: bool) -> None:
    now = datetime.now(timezone.utc).astimezone()
    runner_dir = room / "dry-run-runners" / TASK_ID / agent_id
    runner_dir.mkdir(parents=True, exist_ok=True)
    (runner_dir / "stdout.log").write_text(f"{agent_id} smoke output\n", encoding="utf-8")
    (runner_dir / "stderr.log").write_text("", encoding="utf-8")
    if result_ready:
        write_json(
            runner_dir / "result.json",
            {
                "ok": True,
                "agent_id": agent_id,
                "task_id": TASK_ID,
                "finished_at": now.isoformat(timespec="seconds"),
            },
        )
    write_json(
        room / "active-runners" / f"{agent_id}-{TASK_ID}.json",
        {
            "schema": "openclaw.agent_room.active_runner.v0",
            "status": "running",
            "agent_id": agent_id,
            "run_id": TASK_ID,
            "task_id": TASK_ID,
            "room_id": ROOM_ID,
            "pid": os.getpid(),
            "runner_dir": str(runner_dir),
            "stdout_path": str(runner_dir / "stdout.log"),
            "stderr_path": str(runner_dir / "stderr.log"),
            "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
            "soft_deadline_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
            "hard_deadline_at": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
            "task_budget": {"expected_agents": ["codex", "claude-code"]},
            "task": {
                "task_id": TASK_ID,
                "run_id": TASK_ID,
                "room_id": ROOM_ID,
                "target_agents": ["codex", "claude-code"],
            },
        },
    )


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    write_task(room)
    write_runner(room, "codex", result_ready=False)
    write_runner(room, "claude-code", result_ready=True)

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_live_harvest_mix_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"

    status = status_tool.build_status(room_id=ROOM_ID)
    signature = status_tool.status_watch_signature(status)
    per_agent = status.get("per_agent_engagement") if isinstance(status.get("per_agent_engagement"), dict) else {}
    codex = per_agent.get("codex") if isinstance(per_agent.get("codex"), dict) else {}
    claude = per_agent.get("claude-code") if isinstance(per_agent.get("claude-code"), dict) else {}
    runner_states = {
        str(row.get("agent_id")): row.get("runner_state")
        for row in (status.get("active_runners") or [])
        if isinstance(row, dict)
    }

    failures: list[str] = []
    check("two runner records are visible in room scope", status.get("runner_record_count") == 2, failures)
    check("only non-harvested live runner counts as active", status.get("active_runner_count") == 1, failures)
    check("codex runner remains working", codex.get("working_runner_count") == 1 and codex.get("pending_harvest_count") == 0, failures)
    check("claude-code result is pending harvest, not working", claude.get("working_runner_count") == 0 and claude.get("pending_harvest_count") == 1, failures)
    check("live task id survives status projection", status.get("active_task_ids") == [TASK_ID], failures)
    check("working runner state is preserved", runner_states.get("codex") == "working_with_local_output", failures)
    check("pending harvest state is preserved", runner_states.get("claude-code") == "result_pending_harvest_process_alive", failures)
    check("watch signature records active task id", signature.get("active_task_ids") == [TASK_ID], failures)
    check("watch signature records per-agent task ids", signature.get("agents", {}).get("codex", {}).get("task_ids") == [TASK_ID], failures)
    check("watch signature records pending harvest count", signature.get("agents", {}).get("claude-code", {}).get("pending_harvest_count") == 1, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_live_harvest_mix_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "active_runner_count": status.get("active_runner_count"),
        "per_agent_engagement": per_agent,
        "runner_states": runner_states,
        "watch_signature": signature,
    }
    write_json(DRY_RUN / "smoke-result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
