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
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "standing-mainline-uptake-closure-smoke"


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


def configure_runner_module(module: Any, bridge_root: Path, room: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    module.COMMENT_ROOT = bridge_root / "agent-comments"
    module.RUN_ROOT = room / "runner-runs"
    module.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    module.AGENT_PRESENCE_DIR = room / "agent-presence"
    module.PROJECTION_EVENTS_FILE = room / "projection_events.jsonl"
    module.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = room / "projections" / "openclaw-main" / "projection_events.jsonl"


def configure_status_module(module: Any, bridge_root: Path, room: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    module.ACTIVE_RUNNERS = room / "active-runners"
    module.TASKS = room / "tasks"
    module.STATUS_DIR = room / "collaboration-status"
    module.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    module.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    module.AGENT_PRESENCE_DIR = room / "agent-presence"


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    task_id = "standing-smoke-uptake-closure"
    task_path = room / "tasks" / task_id / "manifest.json"
    task = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": "openclaw-evolution",
        "lane": "standing_mainline_discussion",
        "status": "open",
        "review_status": "requested",
        "quality_gate_status": "not_applicable",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "agent-room-standing-mainline"},
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "status": "open",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {"id": "wi-codex", "assigned_to": "codex", "status": "open"},
                {"id": "wi-claude-code", "assigned_to": "claude-code", "status": "open"},
            ],
            "claims": [],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
        },
        "runner_summary": {"completed_agents": []},
        "updated_at": "2026-05-27T14:20:00+08:00",
    }
    write_json(task_path, task)

    runner = load_module(TOOLS / "agent_task_runner.py", "agent_task_runner_standing_uptake_smoke")
    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_standing_uptake_smoke")
    configure_runner_module(runner, bridge_root, room)
    configure_status_module(status_tool, bridge_root, room)

    failures: list[str] = []
    codex_comment = {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": "codex",
        "task_id": task_id,
        "run_id": task_id,
        "kind": "response",
        "title": "Codex standing point",
        "body": "状态面必须证明 peer uptake，而不是只证明 runner started。",
        "blockers": [],
    }
    codex_begin = runner.collaboration_begin(task, "codex", task_path)
    runner.append_jsonl(runner.comment_path("codex"), codex_comment)
    codex_finish = runner.collaboration_finish(task, "codex", codex_begin.get("work_item_id"), codex_comment, {"ok": True})

    claude_comment = {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": "claude-code",
        "task_id": task_id,
        "run_id": task_id,
        "kind": "response",
        "title": "Claude Code uptake",
        "body": "把 Codex 的状态面判断纳入执行：用本地 smoke 锁住 closure 行为。",
        "blockers": [],
    }
    claude_begin = runner.collaboration_begin(task, "claude-code", task_path)
    runner.append_jsonl(runner.comment_path("claude-code"), claude_comment)
    claude_finish = runner.collaboration_finish(task, "claude-code", claude_begin.get("work_item_id"), claude_comment, {"ok": True})
    standing_uptake = runner.record_standing_mainline_peer_uptake(task, "claude-code", claude_comment)

    state_file, _archive_file = runner.collaboration_ledger_paths(task)
    ledger = json.loads(state_file.read_text(encoding="utf-8"))
    refreshed_task = json.loads(task_path.read_text(encoding="utf-8"))
    status = status_tool.build_status()
    compact = status_tool.render_compact_status(status)
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}

    points = ledger.get("points") if isinstance(ledger.get("points"), list) else []
    uptakes = ledger.get("uptakes") if isinstance(ledger.get("uptakes"), list) else []
    artifacts = ledger.get("artifacts") if isinstance(ledger.get("artifacts"), list) else []
    quality_gate = refreshed_task.get("runner_summary", {}).get("collaboration_quality_gate", {})

    check("codex claim succeeds", bool(codex_begin.get("claim", {}).get("ok")), failures)
    check("codex finish records point", bool(codex_finish and codex_finish.get("point_recorded")), failures)
    check("claude claim succeeds", bool(claude_begin.get("claim", {}).get("ok")), failures)
    check("claude finish skips response point", bool(claude_finish and claude_finish.get("point_recording") == "skipped_standing_uptake_response"), failures)
    check("standing uptake is recorded", standing_uptake.get("status") == "recorded", failures)
    check("only opener point remains", len(points) == 1 and points[0].get("agent_id") == "codex", failures)
    check("peer uptake recorded once", len(uptakes) == 1 and uptakes[0].get("by_agent") == "claude-code", failures)
    check("both artifacts recorded", {item.get("agent_id") for item in artifacts} == {"codex", "claude-code"}, failures)
    check("manifest quality gate closes", quality_gate.get("status") == "peer_reviewed", failures)
    check("overview has no missing peer uptake", overview.get("tasks_missing_peer_uptake_count") == 0, failures)
    check("compact status does not show pending uptake", "任务待接收" not in compact, failures)

    result = {
        "schema": "openclaw.agent_room.standing_mainline_uptake_closure_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "ledger_path": str(state_file),
        "codex_finish": codex_finish,
        "claude_finish": claude_finish,
        "standing_uptake": standing_uptake,
        "quality_gate": quality_gate,
        "compact_status": compact,
        "collaboration_overview": overview,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
