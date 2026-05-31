#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="openclaw-runner-entrypoint-marker-"))
    try:
        module = load_module(ROOT / "agent-room" / "tools" / "agent_room_resident_bridge.py", "resident_bridge_entrypoint_smoke")
        runner_dir = tmp / "runner"
        runner_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = runner_dir / "stdout.log"
        stderr_path = runner_dir / "stderr.log"
        script_path = runner_dir / "runner-systemd-entrypoint.sh"

        saved_run = module.subprocess.run
        saved_show = module.systemd_show_unit
        saved_main_pid = module.systemd_unit_main_pid
        try:
            class FakeSystemdRun:
                returncode = 0
                stdout = ""
                stderr = ""

            module.subprocess.run = lambda *args, **kwargs: FakeSystemdRun()
            module.systemd_show_unit = lambda unit: {"MainPID": "0", "ActiveState": "inactive", "SubState": "dead"}
            module.systemd_unit_main_pid = lambda unit: 0
            launch = module.start_runner_process_isolated(
                [sys.executable, "-c", "import sys; print('before-exit'); sys.exit(7)"],
                runner_dir,
                stdout_path,
                stderr_path,
                "codex",
                "smoke-entrypoint-marker",
            )
        finally:
            module.subprocess.run = saved_run
            module.systemd_show_unit = saved_show
            module.systemd_unit_main_pid = saved_main_pid

        proc = subprocess.run([str(script_path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        marker_path = runner_dir / ".runner-exit-marker"
        marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
        record = {
            "runner_dir": str(runner_dir),
            "pid": os.getpid(),
            "status": "running",
        }

        failures: list[str] = []
        check("systemd launch path generated entrypoint", launch.get("launch_mode") == "systemd_service" and script_path.exists(), failures)
        check("entrypoint propagates runner exit code", proc.returncode == 7, failures)
        check("entrypoint writes exit marker on failed runner", marker.get("exit_code") == 7 and marker.get("source") == "runner-systemd-entrypoint", failures)
        check("runner stdout is still redirected", "before-exit" in stdout_path.read_text(encoding="utf-8", errors="replace"), failures)
        check("exit marker short-circuits active liveness", module.active_runner_alive(record) is False, failures)

        result = {
            "schema": "openclaw.agent_room.runner_exit_marker_entrypoint_smoke.v0",
            "ok": not failures,
            "failures": failures,
            "launch_mode": launch.get("launch_mode"),
            "script_path": str(script_path),
            "marker": marker,
            "entrypoint_exit_code": proc.returncode,
            "tokens_printed": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not failures else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
