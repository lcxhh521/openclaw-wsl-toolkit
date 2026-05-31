#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
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


def install_roots(status, bridge_root: Path) -> None:
    room = bridge_root / "agent-room"
    status.ROOT = bridge_root
    status.ROOM = room
    status.ACTIVE_RUNNERS = room / "active-runners"
    status.TASKS = room / "tasks"
    status.STATUS_DIR = room / "collaboration-status"
    status.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status.AGENT_PRESENCE_DIR = room / "agent-presence"
    status.MODEL_QUOTA_SIGNAL = room / "model_quota_signal.json"
    status.AGENT_QUOTA_STATE = room / "agent_quota_state.json"
    status.COLLAB_LEDGER_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    status = load_module(STATUS, "collaboration_status_preflight_scope_conflicts_under_test")
    task_id = "smoke-preflight-scope-conflict"
    shared_path = "codex-main-bridge/agent-room/tools/collaboration_status.py"

    with tempfile.TemporaryDirectory(prefix="openclaw-preflight-scope-") as tmp:
        bridge_root = Path(tmp) / "codex-main-bridge"
        install_roots(status, bridge_root)
        ledger_path = status.collaboration_ledger_path_for_task(task_id)
        ledger_path.write_text(
            json.dumps(
                {
                    "schema": "openclaw.agent_room.collaboration_ledger.v0",
                    "room_id": "openclaw-evolution",
                    "task_id": task_id,
                    "run_id": task_id,
                    "status": "open",
                    "mode": "standing_mainline_discussion",
                    "participants": ["codex", "claude-code"],
                    "work_items": [
                        {
                            "id": "wi-codex",
                            "status": "claimed",
                            "assigned_to": "codex",
                            "claimed_by": "codex",
                            "declared_scope": {
                                "scope_type": "file_edit",
                                "paths": [shared_path],
                            },
                        },
                        {
                            "id": "wi-claude",
                            "status": "claimed",
                            "assigned_to": "claude-code",
                            "claimed_by": "claude-code",
                            "declared_scope": {
                                "scope_type": "file_edit",
                                "paths": [shared_path],
                            },
                        },
                    ],
                    "claims": [],
                    "artifacts": [],
                    "blockers": [],
                    "handoffs": [],
                    "points": [],
                    "uptakes": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        conflicts = status.detect_scope_conflicts(task_id)
        assert_true(conflicts["ok"], "scope conflict detector should read the smoke ledger")
        assert_true(
            conflicts["conflicts"] == [{"path": shared_path, "agents": ["claude-code", "codex"]}],
            "scope conflict detector must retain the conflicted path and agents",
        )

        advisory = status.turn_preflight_advisory(task_id, "codex")
        assert_true(advisory["ok"], "preflight advisory should read the smoke ledger")
        body = advisory["advisory"]
        assert_true(
            body["conflict_paths"] == [shared_path],
            "preflight advisory must not drop detect_scope_conflicts().path entries",
        )
        assert_true(
            body["scope_conflicts"] == [{"path": shared_path, "agents": ["claude-code", "codex"]}],
            "preflight advisory should expose conflict ownership for handoff decisions",
        )

    print(json.dumps({"ok": True, "checked": "collaboration_preflight_scope_conflicts"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
