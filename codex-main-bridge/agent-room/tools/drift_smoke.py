#!/usr/bin/env python3
"""
Drift-check smoke (mainline-governance-contract-20260528 section 7).
Checks active standing tasks and runner state for governance contract drift.
Outputs a local artifact; no Telegram output unless meaningful drift is found.

Usage:
  python3 drift_smoke.py --json          # machine-readable report
  python3 drift_smoke.py --room-id openclaw-evolution  # specific room
  python3 drift_smoke.py --artifact-dir /path/artifacts # override output dir
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
TASKS_JSONL = ROOM / "tasks.jsonl"
ACTIVE_RUNNERS = ROOM / "active-runners"
AGENDA_CONFIG = ROOM / "config" / "standing-agenda.json"
ARTIFACT_DIR_DEFAULT = ROOM / "artifacts" / "drift-smoke"
MAINLINE_TERMINAL_STATUSES = {"done", "accepted", "closed", "superseded", "cancelled", "wont_do"}
TASK_TERMINAL_STATUSES = {"completed", "blocked", "failed", "partial", "partial_failed", "cancelled", "stale", "merged"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except Exception:
            continue
    return out


def safe_str(value: Any, default: str = "") -> str:
    return str(value).strip() if value else default


def task_manifest_path(task: dict[str, Any]) -> Path | None:
    task_id = safe_str(task.get("task_id")) or safe_str(task.get("run_id"))
    if not task_id:
        return None
    return ROOM / "tasks" / task_id / "manifest.json"


def effective_task(task: dict[str, Any]) -> dict[str, Any]:
    """Prefer the mutable manifest over stale append-only task ledger rows."""
    manifest_path = task_manifest_path(task)
    if manifest_path is None or not manifest_path.exists():
        return task
    manifest = read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        return task
    merged = dict(task)
    merged.update(manifest)
    return merged


def mainline_item_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()

    def add(raw: Any) -> None:
        text = safe_str(raw)
        if text:
            keys.add(text)

    add(item.get("id"))
    add(item.get("mainline_id"))
    add(item.get("mainline_item_id"))
    add(item.get("mainline_agenda_item_id"))
    for field in ("aliases", "standing_aliases", "standing_item_ids", "legacy_ids"):
        raw = item.get(field)
        if isinstance(raw, list):
            for entry in raw:
                add(entry)
        else:
            add(raw)
    return keys


def run_drift_check(room_id: str = "openclaw-evolution") -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"tasks_scanned": 0, "active_runners": 0, "drift_count": 0}

    # --- Check 1: Active runners with no governance fields in their task_budget ---
    runner_dir = ACTIVE_RUNNERS
    if runner_dir.exists():
        runners = sorted(runner_dir.glob("*.json"))
        stats["active_runners"] = len(runners)
        for r_path in runners:
            record = read_json(r_path, {})
            budget = record.get("task_budget") if isinstance(record.get("task_budget"), dict) else {}
            governance = budget.get("governance") if isinstance(budget.get("governance"), dict) else {}
            if not governance.get("mainline_id"):
                findings.append({
                    "kind": "missing_governance",
                    "path": str(r_path.relative_to(ROOT) if r_path.is_relative_to(ROOT) else r_path),
                    "detail": "task_budget.governance missing mainline_id",
                    "severity": "warning",
                })

    # --- Check 2: Open tasks with no governance block ---
    tasks = [effective_task(task) for task in read_jsonl(TASKS_JSONL)]
    stats["tasks_scanned"] = len(tasks)
    for task in tasks:
        task_id = safe_str(task.get("task_id"))
        status = safe_str(task.get("status"))
        if status in TASK_TERMINAL_STATUSES or status in MAINLINE_TERMINAL_STATUSES:
            continue
        governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
        if not governance:
            findings.append({
                "kind": "missing_governance_block",
                "task_id": task_id,
                "detail": "task has no governance block (required by governance contract)",
                "severity": "warning",
            })
        elif not governance.get("mainline_id"):
            findings.append({
                "kind": "missing_mainline_id",
                "task_id": task_id,
                "detail": "governance block missing mainline_id; has keys: " + str(list(governance.keys())),
                "severity": "warning",
            })

    # --- Check 3: Standing agenda items that lack a mainline_item_id ---
    agenda = read_json(AGENDA_CONFIG, {})
    mainline_agenda = read_json(ROOM / "rooms" / room_id / "mainline_agenda.json", {})
    mainline_keys: set[str] = set()
    if isinstance(mainline_agenda, dict):
        for active_item in mainline_agenda.get("active_items") or []:
            if isinstance(active_item, dict):
                mainline_keys.update(mainline_item_keys(active_item))
    if isinstance(agenda, dict):
        for item in (agenda.get("items") or []):
            if isinstance(item, dict) and item.get("status") in MAINLINE_TERMINAL_STATUSES:
                continue
            item_id = safe_str(item.get("mainline_item_id")) or safe_str(item.get("mainline_id"))
            if not item_id:
                findings.append({
                    "kind": "agenda_item_no_mainline",
                    "item_id": safe_str(item.get("id")),
                    "detail": "standing agenda item has no mainline_item_id or mainline_id",
                    "severity": "warning",
                })
            elif mainline_keys and item_id not in mainline_keys:
                findings.append({
                    "kind": "agenda_item_mainline_not_found",
                    "item_id": safe_str(item.get("id")),
                    "mainline_item_id": item_id,
                    "detail": "standing agenda item points at a mainline item id that is absent from room mainline_agenda active_items/aliases",
                    "severity": "warning",
                })

    # --- Check 4: Duplicate open tasks for same dedupe_key ---
    dedupe_map: dict[str, list[str]] = {}
    for task in tasks:
        status = safe_str(task.get("status"))
        if status in TASK_TERMINAL_STATUSES or status in MAINLINE_TERMINAL_STATUSES:
            continue
        g = task.get("governance") if isinstance(task.get("governance"), dict) else {}
        dk = safe_str(g.get("dedupe_key")) or safe_str(task.get("dedupe_key"))
        if dk:
            dedupe_map.setdefault(dk, []).append(safe_str(task.get("task_id")))
    for dk, tids in dedupe_map.items():
        if len(tids) > 1:
            findings.append({
                "kind": "duplicate_standing_tasks",
                "dedupe_key": dk,
                "task_ids": tids,
                "detail": str(len(tids)) + " open tasks share the same dedupe_key; merge or stale-close extras",
                "severity": "warning",
            })

    # --- Check 5: Tasks stuck in queued/running beyond reasonable age ---
    now = datetime.now(timezone.utc).astimezone()
    for task in tasks:
        status = safe_str(task.get("status"))
        if status not in ("queued", "running"):
            continue
        created_raw = task.get("created_at") or task.get("updated_at")
        if not created_raw:
            continue
        try:
            created_dt = datetime.fromisoformat(str(created_raw))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc).astimezone()
        except Exception:
            continue
        age_hours = (now - created_dt).total_seconds() / 3600
        if age_hours > 4:
            findings.append({
                "kind": "stale_open_task",
                "task_id": safe_str(task.get("task_id")),
                "status": status,
                "age_hours": round(age_hours, 1),
                "detail": "task " + status + " for " + str(round(age_hours, 1)) + "h without progress",
                "severity": "info",
            })

    stats["drift_count"] = len(findings)
    result = {
        "schema": "openclaw.agent_room.drift_smoke.v0",
        "created_at": now_iso(),
        "contract_ref": "mainline-governance-contract-20260528",
        "stats": stats,
        "findings": findings,
        "drift_detected": len(findings) > 0,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Drift-check smoke for Agent Room governance contract")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--room-id", default="openclaw-evolution", help="Room ID for artifact path")
    parser.add_argument("--artifact-dir", default=str(ARTIFACT_DIR_DEFAULT), help="Directory for drift artifacts")
    args = parser.parse_args()

    report = run_drift_check(args.room_id)
    artifact_dir = Path(args.artifact_dir) / args.room_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    artifact_path = artifact_dir / ("drift-smoke-" + ts + ".json")
    (artifact_dir / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if artifact_path.is_relative_to(ROOT):
        report["artifact_path"] = str(artifact_path.relative_to(ROOT))
    else:
        report["artifact_path"] = str(artifact_path)

    if report.get("drift_detected"):
        report["has_drift"] = True
        report["telegram_surface"] = False

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        stats = report.get("stats", {})
        findings = report.get("findings", [])
        print("Drift smoke: " + str(stats.get("drift_count", 0)) + " findings from " + str(stats.get("tasks_scanned", 0)) + " tasks / " + str(stats.get("active_runners", 0)) + " runners")
        for f in findings:
            print("  [" + f.get("severity", "info") + "] " + f.get("kind", "?") + ": " + f.get("detail", ""))
        print("Artifact: " + report.get("artifact_path", "unknown"))

    return 1 if report.get("drift_detected") else 0


if __name__ == "__main__":
    raise SystemExit(main())
