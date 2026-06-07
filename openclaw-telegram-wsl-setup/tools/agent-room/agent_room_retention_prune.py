#!/usr/bin/env python3
"""Retention pruning for OpenClaw Agent Room runtime snapshots.

Default mode is dry-run. Use --apply from a timer for cleanup.
Protected canonical state is never deleted: ledgers, tasks, rooms, config, and tools.
Runner result directories are pruned only when terminal evidence exists.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOM = Path.home() / ".openclaw/workspace/codex-main-bridge/agent-room"
BRIDGE_ROOT = ROOM.parent
DAEMON_ROOT = ROOM / "daemon-runs"
RESIDENT_ROOT = ROOM / "resident-runs"
FINISHED_ROOT = ROOM / "finished-runners"
MAINT = ROOM / "maintenance/retention-prune"
TERMINAL_STATUSES = {
    "completed",
    "accepted",
    "accepted_with_runner_followup",
    "review_ready_with_runner_followup",
    "failed",
    "blocked",
    "cancelled",
    "stale",
    "partial_failed",
}
PROTECTED = {
    ROOM / "collaboration-ledgers",
    ROOM / "tasks",
    ROOM / "rooms",
    ROOM / "config",
    ROOM / "tools",
}


@dataclass
class Candidate:
    category: str
    path: Path
    reason: str
    mtime: float


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def is_within(path: Path, base: Path) -> bool:
    try:
        safe_resolve(path).relative_to(safe_resolve(base))
        return True
    except ValueError:
        return False


def ensure_deletable(path: Path, allowed_base: Path) -> Path:
    resolved = safe_resolve(path)
    base = safe_resolve(allowed_base)
    if resolved == base or not is_within(resolved, base):
        raise ValueError(f"refusing path outside allowed base: {resolved}")
    for protected in PROTECTED:
        if resolved == safe_resolve(protected) or is_within(resolved, protected):
            raise ValueError(f"refusing protected path: {resolved}")
    return resolved


def dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def top_dirs(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir()]


def newest_paths(paths: list[Path], keep: int) -> set[Path]:
    if keep <= 0:
        return set()
    return set(sorted(paths, key=dir_mtime, reverse=True)[:keep])


def old_enough(path: Path, older_than_seconds: int, now: float) -> bool:
    return dir_mtime(path) < now - older_than_seconds


def bridge_relative(path: Path) -> str:
    try:
        return str(safe_resolve(path).relative_to(safe_resolve(BRIDGE_ROOT)))
    except ValueError:
        return str(path)


def iter_json_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_json_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_values(item)
    elif isinstance(value, str):
        yield value


def result_json_paths(run_dir: Path) -> list[Path]:
    return [p for p in run_dir.rglob("result.json") if p.is_file()]


def task_id_from_result_path(path: Path) -> str | None:
    parts = path.parts
    if "runner" not in parts:
        return None
    idx = parts.index("runner")
    return parts[idx + 1] if idx + 1 < len(parts) else None


def manifest_for_task(task_id: str | None) -> dict:
    if not task_id:
        return {}
    manifest = ROOM / "tasks" / task_id / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_canonical_reference_text() -> str:
    chunks: list[str] = []
    for path in [ROOM / "tasks.jsonl", *list((ROOM / "rooms").glob("*/tasks.jsonl"))]:
        if path.exists() and path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    for path in (ROOM / "collaboration-ledgers").glob("*.json"):
        if path.exists() and path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def has_manifest_result_reference(result_path: Path) -> tuple[bool, str]:
    rel = bridge_relative(result_path)
    task_id = task_id_from_result_path(result_path)
    manifest = manifest_for_task(task_id)
    if not manifest:
        return False, f"missing_manifest_for_{task_id or 'unknown_task'}"
    status = str(manifest.get("status") or manifest.get("collaboration_status") or "").strip().lower()
    values = set(iter_json_values(manifest))
    if rel not in values:
        return False, f"result_not_referenced_in_manifest:{task_id}"
    if status not in TERMINAL_STATUSES:
        return False, f"manifest_not_terminal:{task_id}:{status or 'missing'}"
    return True, f"manifest_terminal_reference:{task_id}:{status}"


def runner_dir_evidence_gate(run_dir: Path, canonical_reference_text: str) -> tuple[bool, str]:
    results = result_json_paths(run_dir)
    if not results:
        return True, "no_runner_result_poll_or_empty_runtime_snapshot"
    reasons: list[str] = []
    for result in results:
        rel = bridge_relative(result)
        ok, reason = has_manifest_result_reference(result)
        if ok:
            reasons.append(reason)
            continue
        if rel in canonical_reference_text:
            reasons.append(f"canonical_text_reference:{rel}")
            continue
        return False, f"retention_wait_unreferenced_result:{rel}:{reason}"
    return True, "evidence_closed:" + ",".join(reasons[:6])


def collect_daemon_run_dirs(args: argparse.Namespace, now: float) -> list[Candidate]:
    candidates: list[Candidate] = []
    older_than = int(args.daemon_run_retain_hours * 3600)
    for bridge_dir in top_dirs(DAEMON_ROOT):
        run_dirs = top_dirs(bridge_dir)
        keep = newest_paths(run_dirs, max(0, args.daemon_run_keep_latest))
        for run_dir in run_dirs:
            if run_dir not in keep and old_enough(run_dir, older_than, now):
                candidates.append(Candidate("daemon_run_dir", run_dir, f"older_than_{args.daemon_run_retain_hours}h", dir_mtime(run_dir)))
    return candidates


def collect_daemon_tick_dirs(args: argparse.Namespace, now: float) -> list[Candidate]:
    candidates: list[Candidate] = []
    older_than = int(args.daemon_tick_retain_hours * 3600)
    for bridge_dir in top_dirs(DAEMON_ROOT):
        for run_dir in top_dirs(bridge_dir):
            tick_dirs = [p for p in top_dirs(run_dir) if p.name.startswith("tick-")]
            keep = newest_paths(tick_dirs, max(0, args.daemon_tick_keep_latest_per_run))
            for tick_dir in tick_dirs:
                if tick_dir not in keep and old_enough(tick_dir, older_than, now):
                    candidates.append(Candidate("daemon_tick_dir", tick_dir, f"older_than_{args.daemon_tick_retain_hours}h", dir_mtime(tick_dir)))
    return candidates


def collect_runner_dirs(base: Path, category: str, retain_hours: float, now: float, reference_text: str, evidence_gate: bool):
    candidates: list[Candidate] = []
    blocked: list[dict[str, str]] = []
    older_than = int(retain_hours * 3600)
    for item in top_dirs(base):
        if not old_enough(item, older_than, now):
            continue
        reason = f"older_than_{retain_hours}h"
        if evidence_gate:
            ok, gate_reason = runner_dir_evidence_gate(item, reference_text)
            if not ok:
                blocked.append({"category": category, "path": str(item), "decision": "retention_wait", "reason": gate_reason})
                continue
            reason += ";" + gate_reason
        candidates.append(Candidate(category, item, reason, dir_mtime(item)))
    return candidates, blocked


def du_bytes(path: Path) -> int | None:
    try:
        out = subprocess.check_output(["du", "-sb", str(path)], text=True, stderr=subprocess.DEVNULL, timeout=20)
        return int(out.split()[0])
    except Exception:
        return None


def summarize_sizes() -> dict[str, int | None]:
    return {
        "agent_room": du_bytes(ROOM),
        "daemon_runs": du_bytes(DAEMON_ROOT),
        "resident_runs": du_bytes(RESIDENT_ROOT),
        "finished_runners": du_bytes(FINISHED_ROOT),
        "collaboration_ledgers": du_bytes(ROOM / "collaboration-ledgers"),
        "tasks_dir": du_bytes(ROOM / "tasks"),
        "tasks_jsonl": du_bytes(ROOM / "tasks.jsonl"),
    }


def delete_candidate(candidate: Candidate) -> dict[str, object]:
    base_by_category = {
        "daemon_run_dir": DAEMON_ROOT,
        "daemon_tick_dir": DAEMON_ROOT,
        "resident_run_dir": RESIDENT_ROOT,
        "finished_runner_dir": FINISHED_ROOT,
    }
    resolved = ensure_deletable(candidate.path, base_by_category[candidate.category])
    shutil.rmtree(resolved)
    return {"category": candidate.category, "path": str(resolved), "reason": candidate.reason, "status": "deleted"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--daemon-run-retain-hours", type=float, default=6.0)
    parser.add_argument("--daemon-run-keep-latest", type=int, default=2)
    parser.add_argument("--daemon-tick-retain-hours", type=float, default=0.5)
    parser.add_argument("--daemon-tick-keep-latest-per-run", type=int, default=20)
    parser.add_argument("--resident-retain-hours", type=float, default=48.0)
    parser.add_argument("--finished-retain-hours", type=float, default=168.0)
    parser.add_argument("--no-evidence-gate", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=80)
    args = parser.parse_args()

    now = time.time()
    MAINT.mkdir(parents=True, exist_ok=True)
    sizes_before = summarize_sizes()
    evidence_gate = not args.no_evidence_gate
    reference_text = build_canonical_reference_text() if evidence_gate else ""

    candidates: list[Candidate] = []
    blocked: list[dict[str, str]] = []
    candidates.extend(collect_daemon_tick_dirs(args, now))
    candidates.extend(collect_daemon_run_dirs(args, now))
    resident, resident_blocked = collect_runner_dirs(RESIDENT_ROOT, "resident_run_dir", args.resident_retain_hours, now, reference_text, evidence_gate)
    finished, finished_blocked = collect_runner_dirs(FINISHED_ROOT, "finished_runner_dir", args.finished_retain_hours, now, reference_text, evidence_gate)
    candidates.extend(resident)
    candidates.extend(finished)
    blocked.extend(resident_blocked)
    blocked.extend(finished_blocked)

    deleted: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    if args.apply:
        for candidate in sorted(candidates, key=lambda c: str(c.path)):
            try:
                deleted.append(delete_candidate(candidate))
            except Exception as exc:
                errors.append({"category": candidate.category, "path": str(candidate.path), "reason": candidate.reason, "error": str(exc)})

    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.category] = counts.get(candidate.category, 0) + 1

    summary = {
        "schema": "openclaw.agent_room.retention_prune.v1",
        "generated_at": now_iso(),
        "mode": "apply" if args.apply else "dry_run",
        "root": str(ROOM),
        "policy": {
            "daemon_run_retain_hours": args.daemon_run_retain_hours,
            "daemon_run_keep_latest": args.daemon_run_keep_latest,
            "daemon_tick_retain_hours": args.daemon_tick_retain_hours,
            "daemon_tick_keep_latest_per_run": args.daemon_tick_keep_latest_per_run,
            "resident_retain_hours": args.resident_retain_hours,
            "finished_retain_hours": args.finished_retain_hours,
            "evidence_gate_enabled": evidence_gate,
            "protected": [str(p) for p in sorted(PROTECTED)],
        },
        "candidate_count": len(candidates),
        "candidate_counts": counts,
        "blocked_count": len(blocked),
        "deleted_count": len(deleted),
        "error_count": len(errors),
        "sizes_before_bytes": sizes_before,
        "sizes_after_bytes": summarize_sizes() if args.apply else sizes_before,
        "sample_candidates": [
            {"category": c.category, "path": str(c.path), "reason": c.reason, "mtime": datetime.fromtimestamp(c.mtime).astimezone().isoformat(timespec="seconds")}
            for c in candidates[: max(0, args.sample_limit)]
        ],
        "sample_blocked": blocked[: max(0, args.sample_limit)],
        "sample_deleted": deleted[: max(0, args.sample_limit)],
        "errors": errors[: max(0, args.sample_limit)],
    }
    (MAINT / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
