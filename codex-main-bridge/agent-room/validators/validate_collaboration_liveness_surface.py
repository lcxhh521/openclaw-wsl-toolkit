#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
RESIDENT = ROOT / "agent-room" / "tools" / "agent_room_resident_bridge.py"
STATUS = ROOT / "agent-room" / "tools" / "collaboration_status.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    resident = load_module(RESIDENT, "agent_room_resident_bridge_liveness_under_test")
    status = load_module(STATUS, "collaboration_status_liveness_under_test")
    now = datetime.now(timezone.utc).astimezone()

    attention_rows = [{
        "agent_id": "codex",
        "run_id": "smoke-run",
        "task_id": "smoke-run",
        "alive": False,
        "result_exists": False,
        "stdout_size": 0,
        "stderr_size": 0,
        "needs_attention": True,
        "runner_state": "dead_without_result",
    }]
    assert_true(
        status.classify_visibility_state(attention_rows, {}) == "runner_attention_needed",
        "dead active-runner records must win over idle/standing agenda states",
    )
    engagement = status.agent_engagement_rows(attention_rows)
    assert_true(
        engagement["codex"]["engagement_state"] == "needs_attention",
        "per-agent status must expose stuck runner attention",
    )

    with tempfile.TemporaryDirectory(prefix="openclaw-liveness-smoke-") as tmp:
        tmp_root = Path(tmp)
        old_active = resident.ACTIVE_RUNNERS
        resident.ACTIVE_RUNNERS = tmp_root / "active-runners"
        resident.ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
        try:
            alive_dir = tmp_root / "alive"
            dead_dir = tmp_root / "dead"
            alive_dir.mkdir()
            dead_dir.mkdir()
            (alive_dir / "stdout.log").write_text("", encoding="utf-8")
            (alive_dir / "stderr.log").write_text("", encoding="utf-8")
            (dead_dir / "stdout.log").write_text("", encoding="utf-8")
            (dead_dir / "stderr.log").write_text("", encoding="utf-8")
            started = (now - timedelta(seconds=5)).isoformat(timespec="seconds")
            resident.write_json(
                resident.active_runner_path("codex", "smoke-run"),
                {
                    "agent_id": "codex",
                    "run_id": "smoke-run",
                    "task_id": "smoke-run",
                    "pid": os.getpid(),
                    "started_at": started,
                    "soft_deadline_at": (now + timedelta(seconds=60)).isoformat(timespec="seconds"),
                    "hard_deadline_at": (now + timedelta(seconds=600)).isoformat(timespec="seconds"),
                    "runner_dir": str(alive_dir),
                    "stdout_path": str(alive_dir / "stdout.log"),
                    "stderr_path": str(alive_dir / "stderr.log"),
                },
            )
            resident.write_json(
                resident.active_runner_path("claude-code", "smoke-run"),
                {
                    "agent_id": "claude-code",
                    "run_id": "smoke-run",
                    "task_id": "smoke-run",
                    "pid": 999999999,
                    "started_at": started,
                    "soft_deadline_at": (now + timedelta(seconds=60)).isoformat(timespec="seconds"),
                    "hard_deadline_at": (now + timedelta(seconds=600)).isoformat(timespec="seconds"),
                    "runner_dir": str(dead_dir),
                    "stdout_path": str(dead_dir / "stdout.log"),
                    "stderr_path": str(dead_dir / "stderr.log"),
                },
            )
            rows = resident.active_runner_status_records("smoke-run")
            by_agent = {row["agent_id"]: row for row in rows}
            assert_true(
                by_agent["codex"]["liveness_state"] == "alive_black_box_no_output_yet",
                "alive no-output runner must be distinguishable from completed work",
            )
            assert_true(
                by_agent["claude-code"]["liveness_state"] == "dead_missing_result",
                "dead no-result runner must be distinguishable from silent work",
            )
            liveness = resident.build_agent_liveness_snapshot(
                ["codex", "claude-code"],
                rows,
                {"work_items": [], "claims": []},
                [],
            )
            assert_true(
                liveness["codex"]["state"] == "alive_black_box_no_output_yet",
                "agent_liveness must show live black-box work per agent",
            )
            assert_true(
                liveness["claude-code"]["state"] == "dead_missing_result" and liveness["claude-code"]["needs_attention"],
                "agent_liveness must show stuck agents per agent",
            )
        finally:
            resident.ACTIVE_RUNNERS = old_active

    print(json.dumps({"ok": True, "checked": "collaboration_liveness_surface"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
