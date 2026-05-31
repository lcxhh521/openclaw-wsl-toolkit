#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(name: str, file_name: str):
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {file_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    resident = load("agent_room_resident_bridge", "agent_room_resident_bridge.py")
    status = load("collaboration_status", "collaboration_status.py")
    failures: list[str] = []

    task_budget = {
        "enabled": True,
        "task_id": "smoke-peer-followup",
        "interaction_class": "design_discussion",
        "soft_deadline_at": (datetime.now(timezone.utc).astimezone() - timedelta(minutes=10)).isoformat(timespec="seconds"),
        "soft_seconds": 180,
        "hard_seconds": 1800,
    }
    runner_budget = resident.runner_budget_for_agent(task_budget, "codex")
    check("runner records original task soft separately", runner_budget.get("task_soft_deadline_at") == task_budget["soft_deadline_at"], failures)
    check("runner soft deadline is not inherited stale task soft", runner_budget.get("soft_deadline_at") != task_budget["soft_deadline_at"], failures)

    started = datetime.now(timezone.utc).astimezone()
    old_record = {
        "started_at": started.isoformat(timespec="seconds"),
        "soft_deadline_at": (started - timedelta(minutes=5)).isoformat(timespec="seconds"),
        "hard_deadline_at": (started + timedelta(minutes=30)).isoformat(timespec="seconds"),
        "runner_budget": {"soft_seconds": 180},
    }
    check("resident normalizes stale soft deadline for diagnostics", resident.classify_runner_deadline_state(old_record) == "running_within_budget", failures)
    effective = status.effective_runner_soft_deadline(old_record)
    check("status normalizes stale soft deadline for cards", status.seconds_until(effective, started) > 0, failures)
    runner_state = status.classify_runner_state(
        alive=True,
        result_exists=False,
        stdout_size=0,
        stderr_size=0,
        soft_deadline_at=effective,
        hard_deadline_at=old_record["hard_deadline_at"],
        now=started,
    )
    check("fresh active runner is not rendered over_soft_deadline", runner_state == "working_silent_before_soft_deadline", failures)

    result = {
        "schema": "openclaw.agent_room.smoke_runner_attempt_soft_deadline.v0",
        "ok": not failures,
        "failures": failures,
        "runner_budget": runner_budget,
        "effective_soft_deadline_at": effective,
        "runner_state": runner_state,
        "tokens_printed": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
