#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-ledger-release-expired-smoke"


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def run_ledger(state: Path, archive: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOLS / "collaboration_ledger.py"),
            "--state-file",
            str(state),
            "--archive-file",
            str(archive),
            *args,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    task_id = "standing-smoke-release-expired"
    state = room / "collaboration-ledgers" / f"{task_id}.json"
    archive = room / "collaboration-ledgers" / f"{task_id}.jsonl"

    manifest = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": task_id,
        "room_id": "openclaw-evolution",
        "status": "running",
        "review_status": "requested",
        "quality_gate_status": "not_applicable",
        "target_agents": ["codex", "claude-code"],
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "status": "open",
            "participants": ["codex", "claude-code"],
            "work_items": [],
            "claims": [],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
        },
        "created_at": "2026-05-29T02:00:00+08:00",
        "updated_at": "2026-05-29T02:00:00+08:00",
        "source": {"transport": "agent-room-standing-mainline"},
    }
    write_json(room / "tasks" / task_id / "manifest.json", manifest)
    write_json(
        state,
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "task_id": task_id,
            "run_id": task_id,
            "room_id": "openclaw-evolution",
            "status": "open",
            "mode": "standing_mainline_discussion",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {
                    "id": "wi-codex",
                    "assigned_to": "codex",
                    "claimed_by": "codex",
                    "status": "claimed",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "id": "wi-claude-code",
                    "assigned_to": "claude-code",
                    "claimed_by": "claude-code",
                    "status": "claimed",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "claims": [
                {
                    "work_item_id": "wi-codex",
                    "agent_id": "codex",
                    "status": "active",
                    "claimed_at": "2026-05-29T02:00:00+08:00",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "work_item_id": "wi-claude-code",
                    "agent_id": "claude-code",
                    "status": "active",
                    "claimed_at": "2026-05-29T02:00:00+08:00",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
            "created_at": "2026-05-29T02:00:00+08:00",
            "updated_at": "2026-05-29T02:00:00+08:00",
        },
    )

    proc = run_ledger(state, archive, "release-expired")
    proc_output = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    ledger = read_json(state)
    codex_item = next(item for item in ledger["work_items"] if item["id"] == "wi-codex")
    claude_item = next(item for item in ledger["work_items"] if item["id"] == "wi-claude-code")
    codex_claim = next(claim for claim in ledger["claims"] if claim["work_item_id"] == "wi-codex")
    claude_claim = next(claim for claim in ledger["claims"] if claim["work_item_id"] == "wi-claude-code")

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_release_expired_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = room / "collaboration-status"
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"
    status_tool.MODEL_QUOTA_SIGNAL = room / "model_quota_signal.json"
    status_tool.AGENT_QUOTA_STATE = room / "agent_quota_state.json"
    status = status_tool.build_status(include_background=True)
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    recent_tasks = status.get("recent_tasks") if isinstance(status.get("recent_tasks"), list) else []
    task_collaboration = recent_tasks[0].get("collaboration") if recent_tasks and isinstance(recent_tasks[0], dict) else {}
    markdown = status_tool.render_markdown_status(status)
    archive_lines = archive.read_text(encoding="utf-8").splitlines() if archive.exists() else []

    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    failures: list[str] = []
    check("release-expired command exits cleanly", proc.returncode == 0, failures)
    check("release-expired reports one released item", proc_output.get("released_count") == 1, failures)
    check("expired work item is open", codex_item.get("status") == "open", failures)
    check("expired work item owner is cleared", codex_item.get("claimed_by") is None, failures)
    check("expired work item lease is removed", "lease_expiry" not in codex_item, failures)
    check("active peer work item remains claimed", claude_item.get("status") == "claimed" and claude_item.get("claimed_by") == "claude-code", failures)
    check("expired claim is no longer active", codex_claim.get("status") == "expired_released", failures)
    check("expired claim keeps audit reason", codex_claim.get("release_reason") == "claim_lease_expired", failures)
    check("active peer claim remains active", claude_claim.get("status") == "active", failures)
    check("release event is archived", any('"event_type": "release_expired"' in line for line in archive_lines), failures)
    check("status surface clears expired claims", task_collaboration.get("expired_claims") == 0, failures)
    check("status surface retains active claim", task_collaboration.get("active_claims") == 1, failures)
    check("overview clears expired claim count", overview.get("expired_claim_count") == 0, failures)
    check(
        "overview has no stale claim lease action",
        not any(item.get("type") == "claim_lease_expired" for item in action_items if isinstance(item, dict)),
        failures,
    )
    check("markdown surfaces zero expired claims", "expired_claim_count: 0" in markdown, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_ledger_release_expired_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "release_stdout": proc_output,
        "release_stderr": proc.stderr,
        "collaboration_overview": overview,
        "task_collaboration": task_collaboration,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
