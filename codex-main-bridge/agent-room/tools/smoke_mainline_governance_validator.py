#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
TASK_TERMINAL_STATUSES = {"completed", "blocked", "failed", "partial", "partial_failed", "cancelled", "stale", "merged"}
GOVERNANCE_TERMINAL_STATES = {"close", "needs_alex", "blocked", "stale", "failed", "merged"}


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


def configure_standing_module(module: Any, bridge_root: Path, room: Path) -> None:
    module.ROOT = bridge_root
    module.ROOM = room
    module.CONFIG = room / "config" / "standing-agenda.json"
    module.STATE = room / "standing-agenda-state.json"
    module.TASKS_JSONL = room / "tasks.jsonl"
    module.ACTIVE_RUNNERS = room / "active-runners"


def task_label(task: dict[str, Any], fallback: str) -> str:
    return str(task.get("task_id") or task.get("run_id") or fallback)[:80]


def task_governance(task: dict[str, Any]) -> dict[str, Any]:
    governance = task.get("governance")
    return governance if isinstance(governance, dict) else {}


def task_dedupe_key(task: dict[str, Any]) -> str:
    governance = task_governance(task)
    return str(governance.get("dedupe_key") or task.get("dedupe_key") or "").strip()


def task_governance_state(task: dict[str, Any]) -> str:
    governance = task_governance(task)
    return str(task.get("governance_state") or governance.get("state") or "").strip().lower()


def task_is_unresolved(task: dict[str, Any]) -> bool:
    status = str(task.get("status") or "").strip().lower()
    return status not in TASK_TERMINAL_STATUSES and task_governance_state(task) not in GOVERNANCE_TERMINAL_STATES


def find_unresolved_duplicate_dedupe_keys(tasks: list[tuple[str, dict[str, Any]]]) -> list[dict[str, str]]:
    """Find open tasks that violate the standing agenda duplicate policy."""
    seen: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    for fallback, task in tasks:
        if not task_is_unresolved(task):
            continue
        dedupe_key = task_dedupe_key(task)
        if not dedupe_key:
            continue
        label = task_label(task, fallback)
        first = seen.get(dedupe_key)
        if first:
            duplicates.append({"dedupe_key": dedupe_key, "first_task": first, "duplicate_task": label})
            continue
        seen[dedupe_key] = label
    return duplicates


def validate_real_tasks(tasks_jsonl: Path) -> int:
    """Validate real task manifests from a tasks.jsonl file for governance completeness."""
    validator = load_module(TOOLS / "mainline_governance_validator.py", "mainline_governance_validator_real")
    if not tasks_jsonl.exists():
        print(json.dumps({"schema": "openclaw.agent_room.mainline_governance_real_check.v0", "ok": False, "error": f"file not found: {tasks_jsonl}"}, ensure_ascii=False, indent=2))
        return 1
    raw = tasks_jsonl.read_text(encoding="utf-8").strip()
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        print(json.dumps({"schema": "openclaw.agent_room.mainline_governance_real_check.v0", "ok": True, "total_tasks": 0, "valid_tasks": 0, "invalid_tasks": 0, "warnings": [], "failures": []}, ensure_ascii=False, indent=2))
        return 0
    failures: list[str] = []
    warnings: list[str] = []
    parsed_tasks: list[tuple[str, dict[str, Any]]] = []
    valid_count = 0
    invalid_count = 0
    for i, line in enumerate(lines):
        try:
            task = json.loads(line)
        except json.JSONDecodeError as e:
            failures.append(f"line {i + 1}: JSON decode error: {e}")
            invalid_count += 1
            continue
        parsed_tasks.append((f"line {i + 1}", task))
        result = validator.validate_task(task)
        if result.get("ok") is True:
            valid_count += 1
        else:
            invalid_count += 1
            errors = result.get("errors") or []
            warnings_list = result.get("warnings") or []
            task_id = task.get("task_id", str(i))[:24]
            if errors:
                failures.append(f"task {task_id}: {'; '.join(str(e) for e in errors[:3])}")
            if warnings_list:
                for w in warnings_list[:3]:
                    warnings.append(f"task {task_id}: {w}")
    duplicate_dedupe_keys = find_unresolved_duplicate_dedupe_keys(parsed_tasks)
    for duplicate in duplicate_dedupe_keys[:20]:
        failures.append(
            "dedupe_key "
            f"{duplicate['dedupe_key']}: unresolved duplicate tasks "
            f"{duplicate['first_task']} and {duplicate['duplicate_task']}"
        )
    report = {
        "schema": "openclaw.agent_room.mainline_governance_real_check.v0",
        "ok": not failures,
        "total_tasks": len(lines),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "duplicate_unresolved_dedupe_key_count": len(duplicate_dedupe_keys),
        "duplicate_unresolved_dedupe_keys": duplicate_dedupe_keys[:20],
        "failures": failures[:20],
        "warnings": warnings[:20],
        "task_count_with_failures_hint": f"{invalid_count}/{len(lines)} tasks fail governance validation" if failures else "all tasks pass",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate task governance fields")
    parser.add_argument("--tasks-jsonl", type=Path, default=None, help="Path to a real tasks.jsonl file for live validation")
    args = parser.parse_args()
    if args.tasks_jsonl:
        return validate_real_tasks(args.tasks_jsonl)

    failures: list[str] = []
    dry_root = Path(tempfile.mkdtemp(prefix="openclaw-mainline-governance-smoke-"))
    try:
        bridge_root = dry_root / "codex-main-bridge"
        room = bridge_root / "agent-room"
        standing = load_module(TOOLS / "standing_agenda_tick.py", "standing_agenda_tick_governance_smoke")
        validator = load_module(TOOLS / "mainline_governance_validator.py", "mainline_governance_validator_smoke")
        configure_standing_module(standing, bridge_root, room)

        invalid = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": "missing-governance",
            "run_id": "missing-governance",
            "room_id": "openclaw-evolution",
            "lane": "standing_mainline_discussion",
            "target_agents": ["codex"],
        }
        invalid_result = validator.validate_task(invalid)
        check("missing governance is rejected", invalid_result.get("ok") is False and len(invalid_result.get("errors") or []) >= 8, failures)

        room_id = "openclaw-evolution"
        standing.write_json(room / "rooms" / room_id / "room.json", {"room_id": room_id, "telegram_chat_id": "-1009000000001"})
        standing.write_json(
            room / "rooms" / room_id / "mainline_agenda.json",
            {
                "schema": "openclaw.agent_room.mainline_agenda.v0",
                "room_id": room_id,
                "active_items": [
                    {
                        "id": "smoke_mainline_governance",
                        "status": "open",
                        "owner": "openclaw-main",
                        "user_value": "Alex sees fewer duplicated standing tasks and clearer recovery evidence.",
                        "work_item": "Create a bounded standing task with explicit governance fields.",
                        "acceptance_evidence": [
                            "manifest validates against mainline_governance_validator.py",
                            "dedupe_key is recorded on the task",
                        ],
                    }
                ],
            },
        )
        item = {
            "id": "smoke-governance-item",
            "mainline_id": "smoke_mainline_governance",
            "mainline_item_id": "smoke_mainline_governance",
            "title": "Smoke governance item",
            "description": "Standing agenda task creation must bind to a mainline and a concrete user-visible problem.",
            "target_agents": ["codex", "claude-code"],
            "acceptance_evidence": ["validator accepts generated manifest"],
            "next_action": "Run the governance validator against the generated manifest.",
        }
        created = standing.create_task(room_id, item, {"standing_collaboration_tick": {"enabled": True, "max_rounds": 2}}, {})
        manifest_path = Path(str(created.get("manifest_path") or ""))
        task = standing.read_json(manifest_path, {})
        valid_result = validator.validate_task(task)
        governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}

        check("standing task validates", valid_result.get("ok") is True, failures)
        check("top-level mainline mirrors governance", task.get("mainline_id") == governance.get("mainline_id") == "smoke_mainline_governance", failures)
        check("dedupe_key is recorded", task.get("dedupe_key") == governance.get("dedupe_key") == "smoke_mainline_governance", failures)
        check("participants include tri-agent context", task.get("participants") == ["openclaw-main", "codex", "claude-code"], failures)
        check("approval gate records local boundary", isinstance(task.get("approval_gate"), dict) and task["approval_gate"].get("required") is False, failures)
        check("governance state enters execute", task.get("governance_state") == governance.get("state") == "execute", failures)
        duplicate_task = dict(task)
        duplicate_task["task_id"] = "duplicate-smoke-governance-item"
        duplicate_task["run_id"] = "duplicate-smoke-governance-item"
        duplicate_result = find_unresolved_duplicate_dedupe_keys([("original", task), ("duplicate", duplicate_task)])
        check("unresolved duplicate dedupe keys are rejected", len(duplicate_result) == 1, failures)

        result = {
            "schema": "openclaw.agent_room.mainline_governance_validator_smoke.v0",
            "ok": not failures,
            "failures": failures,
            "invalid_errors": invalid_result.get("errors"),
            "valid_warnings": valid_result.get("warnings"),
            "manifest_path": str(manifest_path),
            "dry_run_root": str(dry_root),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(dry_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
