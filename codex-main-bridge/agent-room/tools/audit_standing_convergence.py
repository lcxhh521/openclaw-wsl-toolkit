#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"

TERMINAL_MANIFEST_STATUSES = {
    "blocked",
    "cancelled",
    "completed",
    "failed",
    "partial",
    "partial_failed",
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        return {"_read_error": type(exc).__name__ + ": " + str(exc)}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return False
    try:
        stat_text = stat_path.read_text(encoding="utf-8", errors="replace")
        right_paren = stat_text.rfind(")")
        after_name = stat_text[right_paren + 2 :].split() if right_paren != -1 else stat_text.split()[2:]
        if after_name and after_name[0] == "Z":
            return False
    except Exception:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def standing_item_id(task: dict[str, Any]) -> str:
    standing = task.get("standing_agenda") if isinstance(task.get("standing_agenda"), dict) else {}
    mainline = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
    return str(standing.get("item_id") or mainline.get("item_id") or "")


def source_transport(task: dict[str, Any]) -> str:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return str(source.get("transport") or task.get("transport") or "")


def load_standing_manifests(room: Path) -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    for path in sorted((room / "tasks").glob("standing-*/manifest.json")):
        task = read_json(path, {})
        if not isinstance(task, dict):
            continue
        if source_transport(task) != "agent-room-standing-mainline" and not standing_item_id(task):
            continue
        task_id = str(task.get("task_id") or path.parent.name)
        manifests[task_id] = {
            "task_id": task_id,
            "item_id": standing_item_id(task),
            "status": str(task.get("status") or "queued"),
            "updated_at": task.get("updated_at"),
            "path": str(path),
        }
    return manifests


def load_standing_ledgers(room: Path) -> dict[str, dict[str, Any]]:
    ledgers: dict[str, dict[str, Any]] = {}
    for path in sorted((room / "collaboration-ledgers").glob("standing-*.json")):
        ledger = read_json(path, {})
        if not isinstance(ledger, dict):
            continue
        task_id = str(ledger.get("task_id") or path.stem)
        work_items = ledger.get("work_items") if isinstance(ledger.get("work_items"), list) else []
        ledgers[task_id] = {
            "task_id": task_id,
            "status": str(ledger.get("status") or "unknown"),
            "updated_at": ledger.get("updated_at"),
            "path": str(path),
            "work_item_statuses": [
                str(item.get("status") or "unknown")
                for item in work_items
                if isinstance(item, dict)
            ],
            "work_items_without_acceptance": sum(
                1
                for item in work_items
                if isinstance(item, dict)
                and str(item.get("status") or "") == "completed"
                and not item.get("acceptance")
            ),
        }
    return ledgers


def active_runner_summary(room: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted((room / "active-runners").glob("*.json")):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        try:
            pid = int(record.get("pid") or 0)
        except Exception:
            pid = 0
        alive = process_alive(pid)
        records.append(
            {
                "file": path.name,
                "agent_id": record.get("agent_id"),
                "run_id": record.get("run_id"),
                "status": record.get("status"),
                "pid_alive": alive,
            }
        )
    return {
        "records": len(records),
        "alive": sum(1 for record in records if record.get("pid_alive")),
        "needing_harvest": [
            record
            for record in records
            if str(record.get("status") or "") == "running" and not record.get("pid_alive")
        ],
    }


def audit(room: Path) -> dict[str, Any]:
    manifests = load_standing_manifests(room)
    ledgers = load_standing_ledgers(room)
    manifest_status_counts = Counter(item["status"] for item in manifests.values())
    ledger_status_counts = Counter(item["status"] for item in ledgers.values())

    manifest_terminal_ledger_open: list[dict[str, Any]] = []
    ledger_completed_manifest_not_completed: list[dict[str, Any]] = []
    completed_work_items_without_acceptance: list[dict[str, Any]] = []

    for task_id, manifest in manifests.items():
        ledger = ledgers.get(task_id)
        if not ledger:
            continue
        manifest_status = str(manifest.get("status") or "")
        ledger_status = str(ledger.get("status") or "")
        if manifest_status in TERMINAL_MANIFEST_STATUSES and ledger_status == "open":
            manifest_terminal_ledger_open.append(
                {
                    "task_id": task_id,
                    "manifest_status": manifest_status,
                    "ledger_status": ledger_status,
                    "item_id": manifest.get("item_id"),
                }
            )
        if ledger_status == "completed" and manifest_status != "completed":
            ledger_completed_manifest_not_completed.append(
                {
                    "task_id": task_id,
                    "manifest_status": manifest_status,
                    "ledger_status": ledger_status,
                    "item_id": manifest.get("item_id"),
                }
            )
        if int(ledger.get("work_items_without_acceptance") or 0) > 0:
            completed_work_items_without_acceptance.append(
                {
                    "task_id": task_id,
                    "count": ledger.get("work_items_without_acceptance"),
                    "item_id": manifest.get("item_id"),
                }
            )

    runners = active_runner_summary(room)
    critical = {
        "manifest_terminal_ledger_open": manifest_terminal_ledger_open,
        "ledger_completed_manifest_not_completed": ledger_completed_manifest_not_completed,
        "active_runners_needing_harvest": runners["needing_harvest"],
    }
    warnings = {
        "completed_work_items_without_acceptance": completed_work_items_without_acceptance,
    }
    critical_count = sum(len(values) for values in critical.values())
    warning_count = sum(len(values) for values in warnings.values())
    return {
        "schema": "openclaw.agent_room.standing_convergence_audit.v0",
        "ok": critical_count == 0,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "room_path": str(room),
        "standing_manifests": {
            "total": len(manifests),
            "status_counts": dict(sorted(manifest_status_counts.items())),
        },
        "standing_ledgers": {
            "total": len(ledgers),
            "status_counts": dict(sorted(ledger_status_counts.items())),
        },
        "active_runners": {
            "records": runners["records"],
            "alive": runners["alive"],
            "needing_harvest_count": len(runners["needing_harvest"]),
        },
        "critical": critical,
        "warnings": warnings,
        "tokens_printed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Agent Room standing task convergence audit.")
    parser.add_argument("--room-path", default=str(ROOM))
    parser.add_argument("--fail-on-critical", action="store_true")
    args = parser.parse_args()

    result = audit(Path(args.room_path).expanduser())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_critical and not result.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
