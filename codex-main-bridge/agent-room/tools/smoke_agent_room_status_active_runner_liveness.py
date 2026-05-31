#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load_status_module() -> Any:
    path = ROOT / "agent_room_status.py"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("agent_room_status_liveness_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="openclaw-agent-room-status-liveness-"))
    try:
        module = load_status_module()
        bridge = tmp / "codex-main-bridge"
        active = bridge / "agent-room" / "active-runners"
        active.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).astimezone()
        write_json(
            active / "codex-live-systemd-mainpid.json",
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "live-systemd-mainpid",
                "pid": 999999998,
                "systemd_unit": "openclaw-agent-runner-live-mainpid",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "max_seconds": 600,
            },
        )
        write_json(
            active / "claude-dead-systemd-no-mainpid.json",
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "claude-code",
                "run_id": "dead-systemd-no-mainpid",
                "pid": 999999997,
                "systemd_unit": "openclaw-agent-runner-dead-no-mainpid",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "max_seconds": 600,
            },
        )
        write_json(
            active / "codex-wrapper-systemd-no-mainpid.json",
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "wrapper-systemd-no-mainpid",
                "pid": 424242,
                "systemd_unit": "openclaw-agent-runner-wrapper-no-mainpid",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "max_seconds": 600,
            },
        )

        saved_show = module.systemd_show_unit
        saved_alive = module.process_alive
        try:
            module.systemd_show_unit = lambda unit: (
                {"MainPID": "424242", "ActiveState": "active", "SubState": "running"}
                if str(unit).endswith("live-mainpid")
                else {"show_exit_code": "0", "MainPID": "0", "ActiveState": "active", "SubState": "running"}
            )
            module.process_alive = lambda pid: int(pid or 0) == 424242
            rows = module.active_runner_summary(bridge)
        finally:
            module.systemd_show_unit = saved_show
            module.process_alive = saved_alive

        by_run = {row.get("run_id"): row for row in rows}
        live = by_run.get("live-systemd-mainpid") or {}
        dead = by_run.get("dead-systemd-no-mainpid") or {}
        wrapper = by_run.get("wrapper-systemd-no-mainpid") or {}
        failures: list[str] = []
        check("live systemd MainPID counts as alive", live.get("alive") is True, failures)
        check("live systemd MainPID is not pending harvest", live.get("needs_harvest") is False, failures)
        check("live row records systemd liveness source", live.get("liveness_source") == "systemd_main_pid", failures)
        check("dead systemd unit without MainPID is not alive", dead.get("alive") is False, failures)
        check("dead systemd unit without MainPID needs harvest", dead.get("needs_harvest") is True, failures)
        check("systemd record with no MainPID does not fall back to record pid", wrapper.get("alive") is False, failures)
        check("systemd no-MainPID row records liveness source", wrapper.get("liveness_source") == "systemd_no_main_pid", failures)
        check("systemd no-MainPID row needs harvest", wrapper.get("needs_harvest") is True, failures)

        smoke = {
            "schema": "openclaw.agent_room.agent_room_status_active_runner_liveness_smoke.v0",
            "ok": not failures,
            "failures": failures,
            "rows": rows,
        }
        print(json.dumps(smoke, ensure_ascii=False, indent=2))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
