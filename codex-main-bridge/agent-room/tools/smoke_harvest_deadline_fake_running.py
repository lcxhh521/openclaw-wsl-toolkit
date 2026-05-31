#!/usr/bin/env python3
"""Smoke test: deadline-exceeded runners get force-harvested even when
systemd shows a stale alive process (WSL fake-running scenario).

This validates that harvest_active_runners() does NOT treat a deadline-exceeded
runner as "still_running" when systemd/process shows a stale alive PID.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


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
    tmp = Path(tempfile.mkdtemp(prefix="openclaw-harvest-deadline-fake-running-"))
    try:
        module = load_module(ROOT / "agent-room" / "tools" / "agent_room_resident_bridge.py", "agent_room_resident_bridge_smoke")
        bridge = tmp / "codex-main-bridge"
        active = bridge / "agent-room" / "active-runners"
        active.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).astimezone()

        # ---- Deadline-exceeded runner with stale systemd process ----
        # Simulates WSL: systemd shows ActiveState=active with stale MainPID,
        # but hard_deadline_at has passed. Should NOT be "still_running".
        write_json(
            active / "claude-code-fake-running-past-deadline.json",
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "claude-code",
                "run_id": "fake-running-past-deadline",
                "task_id": "fake-running-past-deadline",
                "room_id": "openclaw-evolution",
                "pid": 999999991,
                "systemd_unit": "openclaw-agent-runner-fake-deadline",
                "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "soft_deadline_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "hard_deadline_at": (now - timedelta(seconds=10)).isoformat(timespec="seconds"),
                "max_seconds": 600,  # long max_seconds so NOT stale by age
                "runner_dir": str(active.parent / "fake-runner-dir"),
            },
        )

        # ---- Runner within deadline, systemd alive ----
        # Should NOT be harvested: within deadline, systemd says alive.
        write_json(
            active / "codex-normal-within-deadline.json",
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "normal-within-deadline",
                "task_id": "normal-within-deadline",
                "room_id": "openclaw-evolution",
                "pid": os.getpid(),
                "systemd_unit": "openclaw-agent-runner-normal-within",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "soft_deadline_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
                "hard_deadline_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                "max_seconds": 600,
                "runner_dir": str(active.parent / "normal-runner-dir"),
            },
        )

        saved_show = module.systemd_show_unit
        saved_alive = module.process_alive
        try:
            # Both runners' units are "alive" from systemd's perspective
            module.systemd_show_unit = lambda unit: {
                "MainPID": "424242" if unit and "fake" not in str(unit) else str(os.getpid()),
                "ActiveState": "active",
                "SubState": "running",
            }
            module.process_alive = lambda pid: bool(pid and int(pid) > 0)
            module.ACTIVE_RUNNERS = active
            module.FINISHED_RUNNERS = bridge / "agent-room" / "finished-runners"
            module.FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)
            module.TASK_BUDGET_V0_ENABLED = True

            harvested = module.harvest_active_runners(allow_send=False)

        finally:
            module.systemd_show_unit = saved_show
            module.process_alive = saved_alive

        # Categorize results
        by_run: dict[str, dict[str, Any]] = {}
        for h in harvested:
            rid = h.get("run_id") or h.get("active_runner") or ""
            by_run[rid] = h

        fake = by_run.get("fake-running-past-deadline", {})
        normal = by_run.get("normal-within-deadline", {})

        failures: list[str] = []
        check(
            "deadline-exceeded fake-running runner is NOT still_running",
            fake.get("status") != "still_running",
            failures,
        )
        check(
            "deadline-exceeded fake-running runner is orphan_harvested or finished",
            fake.get("status") in ("finished", "cleaned_stale_before_dispatch", "orphan_harvest") or fake.get("orphan_harvest"),
            failures,
        )
        check(
            "deadline-exceeded active-runner file removed",
            not active.joinpath("claude-code-fake-running-past-deadline.json").exists(),
            failures,
        )
        check(
            "normal-within-deadline runner is still_running",
            normal.get("status") == "still_running",
            failures,
        )
        check(
            "normal-within-deadline active-runner file preserved",
            active.joinpath("codex-normal-within-deadline.json").exists(),
            failures,
        )

        result = {
            "schema": "openclaw.agent_room.harvest_deadline_fake_running_smoke.v0",
            "ok": not failures,
            "failures": failures,
            "harvest_summaries": harvested,
            "active_files_after": sorted(p.name for p in active.glob("*.json")),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())