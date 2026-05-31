#!/usr/bin/env python3
"""Dry-run Agent Room runner idempotency validator.

This validator is local-only: it does not send Telegram messages, does not
advance canonical room tasks, and does not start fresh provider/model calls.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/lcxhh/.openclaw/workspace")
ROOT = WORKSPACE / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TOOLS = ROOM / "tools"
VALIDATOR_RUNS = ROOM / "validator-runs"
EXISTING_COMPLETED_CLAUDE_RUN = "tg-openclaw-evolution-4fe67182fb403c50-glm-5.1"


def now_cst() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def unique_run_id_check(agent_task_runner) -> dict[str, Any]:
    base = "tg-openclaw-evolution-4fe67182fb403c50"
    values = [
        agent_task_runner.claude_ark_attempt_run_id(
            base,
            "glm-5.1",
            Path("/tmp/resident-runs/20260525-190732/runner/task/claude-code"),
        ),
        agent_task_runner.claude_ark_attempt_run_id(
            base,
            "glm-5.1",
            Path("/tmp/resident-runs/20260525-190841/runner/task/claude-code"),
        ),
        agent_task_runner.claude_ark_attempt_run_id(
            base,
            "minimax-m2.7",
            Path("/tmp/resident-runs/20260525-190841/runner/task/claude-code"),
        ),
    ]
    return {
        "name": "claude_ark_attempt_run_id_unique",
        "ok": len(set(values)) == len(values),
        "values": values,
    }


def terminal_result_check(resident_bridge) -> dict[str, Any]:
    cases = [
        ({}, False),
        ({"status": "running"}, False),
        ({"results": []}, True),
        ({"ok": True, "exit_code": 0}, True),
        ({"status": "failed"}, True),
    ]
    observed = []
    ok = True
    for payload, expected in cases:
        actual = bool(resident_bridge.runner_result_is_terminal(payload))
        observed.append({"payload": payload, "expected": expected, "actual": actual})
        ok = ok and actual == expected
    return {"name": "runner_result_terminal_predicate", "ok": ok, "cases": observed}


def duplicate_recovery_check(agent_task_runner, run_dir: Path) -> dict[str, Any]:
    existing = WORKSPACE / "coding-runs" / EXISTING_COMPLETED_CLAUDE_RUN
    if not existing.exists():
        return {
            "name": "claude_duplicate_run_dir_recovery",
            "ok": False,
            "skipped": True,
            "reason": "existing_completed_claude_run_missing",
            "expected_existing_run": str(existing),
        }
    task = {
        "task_id": "duplicate-recovery-local-validator",
        "run_id": "tg-openclaw-evolution-4fe67182fb403c50",
        "ark_run_id": EXISTING_COMPLETED_CLAUDE_RUN,
        "room_id": "openclaw-evolution",
        "claude_code_model": "glm-5.1",
        "permissions": {"source_edit": False, "global_state_change": False},
    }
    result, body, fields = agent_task_runner.run_claude_code_ark_once(
        task,
        "Local duplicate recovery validator. Do not call external services.",
        run_dir,
        task["permissions"],
    )
    return {
        "name": "claude_duplicate_run_dir_recovery",
        "ok": bool(result.get("ok") and result.get("duplicate_recovered") and not fields.get("blockers")),
        "result_ok": bool(result.get("ok")),
        "exit_code": result.get("exit_code"),
        "duplicate_recovered": bool(result.get("duplicate_recovered")),
        "reused_existing_run_dir": bool(result.get("reused_existing_run_dir")),
        "coding_run_dir": fields.get("coding_run_dir"),
        "field_kind": fields.get("kind"),
        "field_title": fields.get("title"),
        "blockers": fields.get("blockers"),
        "body_preview": str(body)[:240],
    }


def main() -> int:
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime("runner-idempotency-%Y%m%d-%H%M%S")
    run_dir = VALIDATOR_RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    agent_task_runner = load_module("agent_task_runner_validator", TOOLS / "agent_task_runner.py")
    resident_bridge = load_module("agent_room_resident_bridge_validator", TOOLS / "agent_room_resident_bridge.py")
    checks = [
        unique_run_id_check(agent_task_runner),
        terminal_result_check(resident_bridge),
        duplicate_recovery_check(agent_task_runner, run_dir),
    ]
    result = {
        "schema": "openclaw.agent_room.runner_idempotency_validator.v0",
        "created_at": now_cst(),
        "run_id": run_id,
        "mode": "dry_run_local_only",
        "telegram_outbound": False,
        "canonical_task_advanced": False,
        "provider_call_started": False,
        "checks": checks,
        "ok": all(bool(item.get("ok")) for item in checks),
    }
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
