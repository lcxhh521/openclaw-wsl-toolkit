#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-expired-claim-reconcile-smoke"


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
    shutil.rmtree(DRY_RUN, ignore_errors=True)
    room = DRY_RUN / "codex-main-bridge" / "agent-room"
    ledger_path = room / "collaboration-ledgers" / "smoke-expired-claim.json"
    archive_path = room / "collaboration-ledgers" / "smoke-expired-claim.jsonl"
    task_id = "smoke-expired-claim"
    write_json(
        ledger_path,
        {
            "schema": "openclaw.agent_room.collaboration_ledger.v0",
            "room_id": "openclaw-evolution",
            "task_id": task_id,
            "run_id": task_id,
            "status": "open",
            "participants": ["codex", "claude-code"],
            "work_items": [
                {
                    "id": "wi-codex",
                    "assigned_to": "codex",
                    "claimed_by": "codex",
                    "status": "claimed",
                    "claimed_at": "2026-05-29T01:00:00+08:00",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "id": "wi-claude",
                    "assigned_to": "claude-code",
                    "claimed_by": "claude-code",
                    "status": "completed",
                    "claimed_at": "2026-05-29T01:00:00+08:00",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "claims": [
                {
                    "work_item_id": "wi-codex",
                    "agent_id": "codex",
                    "status": "active",
                    "claimed_at": "2026-05-29T01:00:00+08:00",
                    "lease_expiry": "2000-01-01T00:00:00+00:00",
                },
                {
                    "work_item_id": "wi-claude",
                    "agent_id": "claude-code",
                    "status": "completed",
                    "claimed_at": "2026-05-29T01:00:00+08:00",
                    "lease_expiry": "2999-01-01T00:00:00+00:00",
                },
            ],
            "artifacts": [
                {"id": "art-001", "work_item_id": "wi-claude", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"}
            ],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
            "created_at": "2026-05-29T01:00:00+08:00",
            "updated_at": "2026-05-29T01:01:00+08:00",
        },
    )

    resident = load_module(TOOLS / "agent_room_resident_bridge.py", "resident_expired_claim_smoke")
    resident.ROOT = DRY_RUN / "codex-main-bridge"
    resident.ROOM = room
    resident.TOOLS = TOOLS
    resident.ACTIVE_RUNNERS = room / "active-runners"
    resident.FINISHED_RUNNERS = room / "finished-runners"
    resident.COLLABORATION_STATUS = room / "collaboration-status"

    reconciled = resident.reconcile_expired_collaboration_claims(limit=5)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    codex_item = next(item for item in ledger["work_items"] if item["id"] == "wi-codex")
    codex_claim = next(claim for claim in ledger["claims"] if claim["work_item_id"] == "wi-codex")
    failures: list[str] = []
    check("expired claim reconciled", len(reconciled) == 1 and reconciled[0]["status"] == "blocked_expired_claim", failures)
    check("ledger becomes blocked", ledger.get("status") == "blocked", failures)
    check("work item blocked", codex_item.get("status") == "blocked", failures)
    check("active claim no longer active", codex_claim.get("status") == "blocked", failures)
    check(
        "blocker recorded",
        any(
            item.get("work_item_id") == "wi-codex" and item.get("reason") == "claim_lease_expired_no_live_runner"
            for item in ledger.get("blockers", [])
            if isinstance(item, dict)
        ),
        failures,
    )
    check("archive records release", archive_path.exists() and "release_expired" in archive_path.read_text(encoding="utf-8"), failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_expired_claim_reconcile_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "reconciled": reconciled,
        "dry_run": str(DRY_RUN),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
