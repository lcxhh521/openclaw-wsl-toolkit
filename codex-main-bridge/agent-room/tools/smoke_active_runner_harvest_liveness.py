#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agent_room_resident_bridge as resident
import standing_agenda_tick


WORKSPACE = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok {name}")


def restore_globals(saved: dict[tuple[Any, str], Any]) -> None:
    for (module, name), value in saved.items():
        setattr(module, name, value)


def main() -> int:
    tmp_parent = Path(tempfile.mkdtemp(prefix="openclaw-active-runner-harvest-"))
    bridge_root = WORKSPACE / "codex-main-bridge"
    if str(bridge_root) not in sys.path:
        sys.path.insert(0, str(bridge_root))
    bridge_daemon = load_module("agent_room_bridge_daemon_smoke", WORKSPACE / "codex-main-bridge" / "agent-room" / "tools" / "agent_room_bridge_daemon.py")
    bridge_watchdog = load_module("bridge_continuation_watchdog_smoke", WORKSPACE / "codex-main-bridge" / "continuation_watchdog.py")
    continuation_lib = load_module("runtime_continuation_lib_smoke", WORKSPACE / "runtime-continuations" / "bin" / "continuation_lib.py")
    saved = {
        (resident, "ROOM"): resident.ROOM,
        (resident, "ACTIVE_RUNNERS"): resident.ACTIVE_RUNNERS,
        (resident, "FINISHED_RUNNERS"): resident.FINISHED_RUNNERS,
        (standing_agenda_tick, "ROOT"): standing_agenda_tick.ROOT,
        (standing_agenda_tick, "ROOM"): standing_agenda_tick.ROOM,
        (standing_agenda_tick, "CONFIG"): standing_agenda_tick.CONFIG,
        (standing_agenda_tick, "STATE"): standing_agenda_tick.STATE,
        (standing_agenda_tick, "TASKS_JSONL"): standing_agenda_tick.TASKS_JSONL,
        (standing_agenda_tick, "ACTIVE_RUNNERS"): standing_agenda_tick.ACTIVE_RUNNERS,
    }
    try:
        room = tmp_parent / "agent-room"
        resident.ROOM = room
        resident.ACTIVE_RUNNERS = room / "active-runners"
        resident.FINISHED_RUNNERS = room / "finished-runners"
        resident.ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
        resident.FINISHED_RUNNERS.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).astimezone()
        run_id = "smoke-dead-active-runner"
        active_path = resident.active_runner_path("codex", run_id)
        resident.write_json(
            active_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": run_id,
                "pid": 999999999,
                "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "runner_dir": str(room / "runner-runs" / run_id),
            },
        )

        check("dead active-runner is not counted as live", resident.active_runner_count() == 0)
        check("dead active-runner does not satisfy same-run liveness", not resident.active_runner_exists("codex", run_id))
        harvested = resident.harvest_active_runners(allow_send=False)
        finished = resident.read_json(resident.FINISHED_RUNNERS / active_path.name, {})
        check(
            "harvest-only removes dead active-runner without result",
            not active_path.exists()
            and len(harvested) == 1
            and harvested[0].get("orphan_harvest") is True
            and harvested[0].get("missing_result_json") is True
            and finished.get("status") == "finished"
            and finished.get("missing_process") is True
            and finished.get("missing_result_json") is True,
        )

        exit_marker_run_id = "smoke-dead-active-runner-exit-marker"
        exit_marker_dir = room / "runner-runs" / exit_marker_run_id
        exit_marker_dir.mkdir(parents=True, exist_ok=True)
        exit_marker_path = exit_marker_dir / ".runner-exit-marker"
        exit_marker_path.write_text(json.dumps({"finished_at": (now - timedelta(seconds=10)).isoformat(timespec="seconds"), "ok": True}), encoding="utf-8")

        exit_marker_active_path = resident.active_runner_path("codex", exit_marker_run_id)
        resident.write_json(
            exit_marker_active_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": exit_marker_run_id,
                "pid": 888888888,
                "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "runner_dir": str(exit_marker_dir),
            },
        )
        exit_marker_record = resident.read_json(exit_marker_active_path, {})
        check("exit-marker short-circuits active-runner liveness", not resident.active_runner_alive(exit_marker_record))
        check("dead exit-marker active-runner does not trigger same-run liveness", not resident.active_runner_exists("codex", exit_marker_run_id))
        harvested_with_marker = resident.harvest_active_runners(allow_send=False)
        check(
            "exit-marker file allows dead active-runner to be harvested immediately",
            not exit_marker_active_path.exists()
            and len(harvested_with_marker) == 1,
        )

        stale_run_id = "smoke-stale-active-runner-pre-dispatch"
        stale_path = resident.active_runner_path("codex", stale_run_id)
        resident.write_json(
            stale_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": stale_run_id,
                "pid": 424242,
                "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "hard_deadline_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_budget": {"hard_seconds": 60},
                "runner_dir": str(room / "runner-runs" / stale_run_id),
            },
        )
        saved_process_alive = resident.process_alive
        saved_terminate_runner_record = resident.terminate_runner_record
        try:
            resident.process_alive = lambda pid: int(pid or 0) == 424242
            resident.terminate_runner_record = lambda record: {
                "attempted": True,
                "method": "smoke_stop",
                "pid": record.get("pid"),
                "alive_after": False,
            }
            stale_record = resident.read_json(stale_path, {})
            check("pre-dispatch smoke fixture is stale", resident.active_runner_stale(stale_record))
            cleanup = resident.cleanup_stale_active_runner_before_dispatch(
                stale_path,
                stale_record,
                reason="stale_active_runner_prior_to_dispatch",
            )
        finally:
            resident.process_alive = saved_process_alive
            resident.terminate_runner_record = saved_terminate_runner_record
        cleaned = resident.read_json(resident.FINISHED_RUNNERS / stale_path.name, {})
        check(
            "pre-dispatch stale active-runner lock is archived and released",
            not stale_path.exists()
            and cleanup.get("status") == "cleaned_stale_before_dispatch"
            and cleanup.get("pre_dispatch_cleanup") is True
            and cleanup.get("cleanup_reason") == "stale_active_runner_prior_to_dispatch"
            and cleanup.get("termination_result", {}).get("method") == "smoke_stop"
            and cleaned.get("pre_dispatch_cleanup") is True
            and cleaned.get("stale_runner") is True
            and cleaned.get("missing_result_json") is True,
        )

        systemd_run_id = "smoke-systemd-mainpid-liveness"
        systemd_path = resident.active_runner_path("codex", systemd_run_id)
        resident.write_json(
            systemd_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": systemd_run_id,
                "pid": 999999998,
                "systemd_unit": "openclaw-agent-runner-smoke-mainpid",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_budget": {"hard_seconds": 600},
                "runner_dir": str(room / "runner-runs" / systemd_run_id),
            },
        )
        saved_systemd_show_unit = resident.systemd_show_unit
        saved_process_alive = resident.process_alive
        try:
            resident.systemd_show_unit = lambda unit: {
                "show_exit_code": "0",
                "MainPID": "424242",
                "ActiveState": "active",
                "SubState": "running",
            }
            resident.process_alive = lambda pid: int(pid or 0) == 424242
            systemd_record = resident.read_json(systemd_path, {})
            check("systemd MainPID backs active-runner liveness", resident.active_runner_alive(systemd_record))
            check("stale stored pid does not override live systemd MainPID", not resident.active_runner_stale(systemd_record))
            check("same-run liveness uses systemd MainPID", resident.active_runner_exists("codex", systemd_run_id))

            # --- PID 复用防假活跃回归：systemd MainPID 在运行，但命令并非 runner entrypoint 时不应判活 ---
            stale_pid_record = {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "smoke-systemd-reused-pid-false-positive",
                "pid": 777777,
                "systemd_unit": "openclaw-agent-runner-smoke-stale-pid",
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_budget": {"hard_seconds": 600},
                "runner_dir": str(room / "runner-runs" / "smoke-systemd-reused-pid-false-positive"),
            }
            stale_pid_path = resident.active_runner_path("codex", stale_pid_record["run_id"])
            resident.write_json(stale_pid_path, stale_pid_record)
            saved_systemd_show_unit_pid_reuse = resident.systemd_show_unit
            saved_process_cmdline = resident.process_cmdline
            saved_process_alive_pid = resident.process_alive
            try:
                resident.systemd_show_unit = lambda unit: {
                    "show_exit_code": "0",
                    "MainPID": "424242",
                    "ActiveState": "active",
                    "SubState": "running",
                }
                resident.process_alive = lambda pid: int(pid or 0) == 424242
                resident.process_cmdline = lambda pid: ["/usr/bin/python3", "-c", "print('unrelated-daemon')"]
                stale_pid_data = resident.read_json(stale_pid_path, {})
                check(
                    "systemd reused PID with unrelated cmdline is treated as dead",
                    not resident.active_runner_alive(stale_pid_data),
                )
                check(
                    "stale PID reuse does not produce same-run liveness",
                    not resident.active_runner_exists("codex", stale_pid_record["run_id"]),
                )
            finally:
                resident.systemd_show_unit = saved_systemd_show_unit_pid_reuse
                resident.process_cmdline = saved_process_cmdline
                resident.process_alive = saved_process_alive_pid

            resident.systemd_show_unit = lambda unit: {
                "show_exit_code": "0",
                "MainPID": "0",
                "ActiveState": "active",
                "SubState": "running",
            }
            resident.process_alive = lambda pid: int(pid or 0) == 999999998
            check(
                "resident rejects systemd unit with no MainPID even when record pid exists",
                not resident.active_runner_alive(systemd_record),
            )

            resident.systemd_show_unit = lambda unit: {
                "show_exit_code": "1",
                "stderr": "Failed to connect to bus: Operation not permitted",
            }
            resident.process_alive = lambda pid: int(pid or 0) == 999999998
            check(
                "resident falls back to record pid when systemd visibility fails",
                resident.active_runner_alive(systemd_record),
            )
        finally:
            resident.systemd_show_unit = saved_systemd_show_unit
            resident.process_alive = saved_process_alive

        duplicate_run_id = "smoke-dispatch-lock-live-existing"
        duplicate_runner_dir = room / "runner-runs" / duplicate_run_id / "existing"
        duplicate_runner_dir.mkdir(parents=True, exist_ok=True)
        duplicate_path = resident.active_runner_path("codex", duplicate_run_id)
        resident.write_json(
            duplicate_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": duplicate_run_id,
                "task_id": duplicate_run_id,
                "pid": os.getpid(),
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_budget": {"hard_seconds": 600},
                "runner_dir": str(duplicate_runner_dir),
            },
        )
        saved_start_runner_process_isolated = resident.start_runner_process_isolated
        try:
            def fail_duplicate_launch(*args: Any, **kwargs: Any) -> dict[str, Any]:
                raise AssertionError("duplicate live same-run runner should defer before launch")

            resident.start_runner_process_isolated = fail_duplicate_launch
            duplicate_deferred = resident.start_agent_runner_async(
                {
                    "schema": "openclaw.agent_room.task.v0",
                    "task_id": duplicate_run_id,
                    "run_id": duplicate_run_id,
                    "room_id": "openclaw-evolution",
                    "target_agents": ["codex"],
                    "created_at": now.isoformat(timespec="seconds"),
                },
                "codex",
                room / "runner-runs" / duplicate_run_id / "new" / "local-runtime-task.json",
                room / "runner-runs" / duplicate_run_id / "new",
                ["python3", "-c", "print('should-not-launch')"],
                None,
            )
        finally:
            resident.start_runner_process_isolated = saved_start_runner_process_isolated
        preserved_duplicate = resident.read_json(duplicate_path, {})
        check(
            "start_agent_runner_async lock recheck defers existing live same-run runner",
            duplicate_deferred.get("runner_start_deferred") is True
            and duplicate_deferred.get("defer_reason") == "already_running"
            and preserved_duplicate.get("runner_dir") == str(duplicate_runner_dir),
        )

        busy_run_id = "smoke-dispatch-lock-busy"
        busy_handle, _busy_state = resident.try_acquire_active_runner_dispatch_lock("codex", busy_run_id)
        saved_start_runner_process_isolated = resident.start_runner_process_isolated
        try:
            resident.start_runner_process_isolated = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("busy same-run dispatch lock should defer before launch")
            )
            busy_deferred = resident.start_agent_runner_async(
                {
                    "schema": "openclaw.agent_room.task.v0",
                    "task_id": busy_run_id,
                    "run_id": busy_run_id,
                    "room_id": "openclaw-evolution",
                    "target_agents": ["codex"],
                    "created_at": now.isoformat(timespec="seconds"),
                },
                "codex",
                room / "runner-runs" / busy_run_id / "local-runtime-task.json",
                room / "runner-runs" / busy_run_id,
                ["python3", "-c", "print('should-not-launch')"],
                None,
            )
        finally:
            resident.start_runner_process_isolated = saved_start_runner_process_isolated
            resident.release_active_runner_dispatch_lock(busy_handle)
        check(
            "busy same-run dispatch lock defers duplicate launch",
            busy_deferred.get("runner_start_deferred") is True
            and busy_deferred.get("defer_reason") == "same_run_dispatch_lock_busy",
        )

        saved_subprocess_run = resident.subprocess.run
        saved_systemd_show_unit = resident.systemd_show_unit
        saved_process_alive = resident.process_alive
        saved_process_cmdline = resident.process_cmdline
        try:
            calls: list[list[str]] = []

            class FakeAlreadyLoadedProc:
                returncode = 1
                stdout = ""
                stderr = "Unit openclaw-agent-runner-smoke already loaded or has a fragment file"

            def fake_systemd_run(cmd: list[str], **kwargs: Any) -> FakeAlreadyLoadedProc:
                calls.append([str(part) for part in cmd])
                return FakeAlreadyLoadedProc()

            resident.subprocess.run = fake_systemd_run
            resident.systemd_show_unit = lambda unit: {
                "show_exit_code": "0",
                "MainPID": "424242",
                "ActiveState": "active",
                "SubState": "running",
            }
            resident.process_alive = lambda pid: int(pid or 0) == 424242
            resident.process_cmdline = lambda pid: ["/tmp/runner-systemd-entrypoint.sh"]
            duplicate_unit_dir = room / "runner-runs" / "smoke-duplicate-live-unit"
            duplicate_unit_dir.mkdir(parents=True, exist_ok=True)
            duplicate_unit_launch = resident.start_runner_process_isolated(
                ["python3", "-c", "print('duplicate')"],
                duplicate_unit_dir,
                duplicate_unit_dir / "stdout.log",
                duplicate_unit_dir / "stderr.log",
                "codex",
                "smoke-duplicate-live-unit",
            )
        finally:
            resident.subprocess.run = saved_subprocess_run
            resident.systemd_show_unit = saved_systemd_show_unit
            resident.process_alive = saved_process_alive
            resident.process_cmdline = saved_process_cmdline
        check(
            "systemd already-loaded live runner is not stopped/reset",
            duplicate_unit_launch.get("duplicate_live_unit") is True
            and duplicate_unit_launch.get("existing_pid") == 424242
            and not any("systemctl" in cmd[:1] and "stop" in cmd for cmd in calls),
        )

        standing_root = tmp_parent / "standing-root"
        standing_room = standing_root / "agent-room"
        standing_agenda_tick.ROOT = standing_root
        standing_agenda_tick.ROOM = standing_room
        standing_agenda_tick.CONFIG = standing_room / "config" / "standing-agenda.json"
        standing_agenda_tick.STATE = standing_room / "standing-agenda-state.json"
        standing_agenda_tick.TASKS_JSONL = standing_room / "tasks.jsonl"
        standing_agenda_tick.ACTIVE_RUNNERS = standing_room / "active-runners"
        standing_agenda_tick.write_json(
            standing_agenda_tick.CONFIG,
            {
                "enabled": True,
                "standing_reconcile_limit": 10,
                "standing_dead_runner_grace_seconds": 0,
            },
        )
        saved_process_alive = standing_agenda_tick.process_alive
        saved_standing_systemd_unit_alive = standing_agenda_tick.systemd_unit_alive
        try:
            standing_agenda_tick.process_alive = lambda pid: int(pid) == 424242
            standing_agenda_tick.systemd_unit_alive = lambda unit: False
            check(
                "standing agenda ignores active systemd unit without live MainPID",
                not standing_agenda_tick.systemd_state_process_backed_alive(
                    {"ActiveState": "active", "SubState": "running", "MainPID": "0"}
                ),
            )
            check(
                "standing agenda requires process-backed systemd MainPID",
                standing_agenda_tick.systemd_state_process_backed_alive(
                    {"ActiveState": "inactive", "SubState": "dead", "MainPID": "424242"}
                ),
            )
            standing_agenda_tick.write_json(
                standing_agenda_tick.active_runner_path("codex", "smoke-systemd-no-mainpid"),
                {
                    "schema": "openclaw.agent_room.active_runner.v0",
                    "status": "running",
                    "agent_id": "codex",
                    "run_id": "smoke-systemd-no-mainpid",
                    "pid": 424242,
                    "systemd_unit": "openclaw-agent-runner-smoke-no-mainpid",
                    "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                    "runner_dir": str(standing_room / "runner-runs" / "smoke-systemd-no-mainpid"),
                },
            )
            check(
                "standing active-runner count does not fall back to record pid for systemd records",
                standing_agenda_tick.active_runner_count() == 0
                and not standing_agenda_tick.active_runner_record_is_alive("codex", "smoke-systemd-no-mainpid"),
            )
        finally:
            standing_agenda_tick.process_alive = saved_process_alive
            standing_agenda_tick.systemd_unit_alive = saved_standing_systemd_unit_alive

        terminal_result = "smoke-standing-result"
        terminal_dir = standing_room / "runner-runs" / terminal_result
        standing_agenda_tick.write_json(
            terminal_dir / "result.json",
            {"schema": "openclaw.agent_room.agent_task_runner.v0", "ok": True, "status": "completed"},
        )
        terminal_path = standing_agenda_tick.active_runner_path("codex", terminal_result)
        standing_agenda_tick.write_json(
            terminal_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": terminal_result,
                "pid": 424242,
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_dir": str(terminal_dir),
            },
        )
        check(
            "standing runner with terminal result is not treated as alive",
            not standing_agenda_tick.active_runner_record_is_alive("codex", terminal_result),
        )
        check("standing active-runner count excludes terminal-result locks", standing_agenda_tick.active_runner_count() == 0)

        marker_run_id = "smoke-standing-exit-marker"
        marker_dir = standing_room / "runner-runs" / marker_run_id
        marker_path = standing_agenda_tick.active_runner_path("claude-code", marker_run_id)
        standing_agenda_tick.write_json(
            marker_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "claude-code",
                "run_id": marker_run_id,
                "pid": 424242,
                "started_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds"),
                "runner_dir": str(marker_dir),
            },
        )
        standing_agenda_tick.write_json(
            marker_dir / ".runner-exit-marker",
            {"finished_at": now.isoformat(timespec="seconds"), "ok": False},
        )
        marker_record = standing_agenda_tick.read_json(marker_path, {})
        check(
            "standing exit-marker short-circuits systemd-backed liveness",
            not standing_agenda_tick.active_runner_process_backed_alive(marker_record),
        )
        check("standing liveness ignores exit-marked stale lock in count", standing_agenda_tick.active_runner_count() == 0)

        saved_bridge_run = bridge_watchdog.subprocess.run
        saved_bridge_pid_alive = bridge_watchdog.pid_alive
        saved_lib_run = continuation_lib.subprocess.run
        saved_lib_pid_alive = continuation_lib._pid_alive
        try:
            class FakeProc:
                stdout = "MainPID=0\nActiveState=active\nSubState=running\n"
                stderr = ""
                returncode = 0

            bridge_watchdog.subprocess.run = lambda *args, **kwargs: FakeProc()
            continuation_lib.subprocess.run = lambda *args, **kwargs: FakeProc()
            bridge_watchdog.pid_alive = lambda pid: int(pid or 0) == 424242
            continuation_lib._pid_alive = lambda pid: int(pid or 0) == 424242
            check("bridge watchdog rejects active systemd unit without MainPID", not bridge_watchdog.systemd_unit_alive("openclaw-smoke"))
            check("runtime continuation rejects active systemd unit without MainPID", not continuation_lib._systemd_unit_alive("openclaw-smoke"))

            FakeProc.stdout = "MainPID=424242\nActiveState=inactive\nSubState=dead\n"
            check("bridge watchdog accepts process-backed MainPID", bridge_watchdog.systemd_unit_alive("openclaw-smoke"))
            check("runtime continuation accepts process-backed MainPID", continuation_lib._systemd_unit_alive("openclaw-smoke"))
        finally:
            bridge_watchdog.subprocess.run = saved_bridge_run
            bridge_watchdog.pid_alive = saved_bridge_pid_alive
            continuation_lib.subprocess.run = saved_lib_run
            continuation_lib._pid_alive = saved_lib_pid_alive

        saved_daemon_run = bridge_daemon.subprocess.run
        try:
            daemon_capture: dict[str, Any] = {}

            class FakeHarvestProc:
                returncode = 0
                stderr = ""
                stdout = json.dumps({
                    "ok": True,
                    "mode": "harvest-only",
                    "harvested_runners": [
                        {"agent_id": "codex", "status": "finished"},
                        {"agent_id": "claude-code", "status": "still_running"},
                    ],
                    "harvested_runner_count": 1,
                    "still_running_count": 1,
                    "active_runner_count_after_harvest": 1,
                    "telegram_outbound": False,
                    "tokens_printed": False,
                })

            def fake_daemon_run(cmd: list[str], **kwargs: Any) -> FakeHarvestProc:
                daemon_capture["cmd"] = cmd
                daemon_capture["timeout"] = kwargs.get("timeout")
                return FakeHarvestProc()

            bridge_daemon.subprocess.run = fake_daemon_run
            daemon_harvest = bridge_daemon.run_maintenance_harvest(tmp_parent / "daemon-maintenance", "openclaw-evolution")
            daemon_cmd = [str(part) for part in daemon_capture.get("cmd") or []]
            check(
                "daemon maintenance harvest runs local harvest-only before status surfaces",
                daemon_harvest.get("ok") is True
                and daemon_harvest.get("harvested_runner_count") == 1
                and daemon_harvest.get("observed_runner_count") == 2
                and daemon_harvest.get("still_running_count") == 1
                and "--mode" in daemon_cmd
                and daemon_cmd[daemon_cmd.index("--mode") + 1] == "harvest-only"
                and "--allow-send" not in daemon_cmd
                and (tmp_parent / "daemon-maintenance" / "maintenance-harvest-tick.json").exists(),
            )
        finally:
            bridge_daemon.subprocess.run = saved_daemon_run

        task_id = "standing-openclaw-evolution-smoke-dead-active-runner"
        task = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "target_agents": ["codex", "claude-code"],
            "status": "running",
            "created_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
            "updated_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
            "source": {"transport": "agent-room-standing-mainline"},
            "standing_agenda": {"item_id": "smoke-dead-runner"},
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "work_items": [
                    {"id": "smoke-dead-runner_codex", "status": "open", "assigned_to": "codex"},
                    {"id": "smoke-dead-runner_claude-code", "status": "open", "assigned_to": "claude-code"},
                ],
                "blockers": [],
            },
        }
        manifest = standing_room / "tasks" / task_id / "manifest.json"
        standing_agenda_tick.write_json(manifest, task)
        standing_agenda_tick.append_jsonl(standing_agenda_tick.TASKS_JSONL, [task])
        for agent_id in ("codex", "claude-code"):
            standing_agenda_tick.write_json(
                standing_agenda_tick.active_runner_path(agent_id, task_id),
                {
                    "schema": "openclaw.agent_room.active_runner.v0",
                    "status": "running",
                    "agent_id": agent_id,
                    "run_id": task_id,
                    "pid": 999999999,
                    "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                    "runner_dir": str(standing_room / "missing-runner-result" / agent_id),
                },
            )

        reconciled = standing_agenda_tick.tick(
            argparse.Namespace(
                room_id="openclaw-evolution",
                fresh_task_count=0,
                active_runner_count=0,
                dry_run=False,
                reconcile_only=True,
            )
        )
        after = standing_agenda_tick.read_json(manifest, {})
        summary = after.get("runner_summary") if isinstance(after.get("runner_summary"), dict) else {}
        archived = summary.get("archived_dead_active_runners") if isinstance(summary.get("archived_dead_active_runners"), list) else []
        check(
            "standing agenda closes dead active-runner pending task",
            reconciled.get("status") == "reconciled_only"
            and after.get("status") == "failed"
            and (after.get("standing_closure") or {}).get("reason") == "dead_active_runner_evidence"
            and set(summary.get("failed_agents") or []) == {"codex", "claude-code"},
        )
        check(
            "standing reconcile archives dead active-runner files",
            len(archived) == 2
            and all(item.get("status") == "archived_dead_missing_result" for item in archived)
            and not standing_agenda_tick.active_runner_path("codex", task_id).exists()
            and not standing_agenda_tick.active_runner_path("claude-code", task_id).exists()
            and standing_agenda_tick.read_json(standing_agenda_tick.finished_runner_path("codex", task_id), {}).get("standing_reconcile_archive") is True
            and standing_agenda_tick.read_json(standing_agenda_tick.finished_runner_path("claude-code", task_id), {}).get("missing_result_json") is True,
        )
    finally:
        restore_globals(saved)
        shutil.rmtree(tmp_parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
