#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PEOPLE_DAILY_ROOT = Path(os.environ.get("OPENCLAW_PEOPLE_DAILY_ROOT", str(Path.home() / ".openclaw" / "workspace" / "people-daily-deep-read")))
MARKET_DAILY_ROOT = Path(os.environ.get("OPENCLAW_MARKET_DAILY_ROOT", str(Path.home() / ".openclaw" / "workspace" / "market-immersion" / "daily")))
TASKS_ROOT = Path(os.environ.get("OPENCLAW_TASKS_ROOT", str(Path.home() / ".openclaw" / "workspace" / "tasks")))
MARKET_PHASE_DUE_TIME = {
    "morning": (9, 5),
    "midday": (12, 15),
    "close": (15, 20),
    "night": (22, 10),
}


@dataclass
class ScheduledTask:
    task_id: str
    kind: str
    date: str
    phase: str
    state: str
    root_cause: str
    next_action: str
    auto_recoverable: bool
    evidence: list[str]
    details: dict[str, Any]


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc)}


def checkpoint_status(path: Path) -> str:
    payload = read_json(path)
    if not payload:
        return "missing"
    if "_read_error" in payload:
        return "invalid"
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    if payload.get("ok") is True:
        return "done"
    if payload.get("success") is True:
        return "done"
    return "present"


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_minutes(value: Any) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    now = datetime.now().astimezone()
    return max(0.0, (now - parsed.astimezone(now.tzinfo)).total_seconds() / 60.0)


def pid_alive(pid: Any) -> bool | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    proc_path = Path(f"/proc/{value}")
    if proc_path.exists():
        try:
            fields = (proc_path / "stat").read_text(encoding="utf-8", errors="replace").split()
            if len(fields) >= 3 and fields[2] == "Z":
                return False
        except OSError:
            pass
    elif os.name == "posix":
        return False
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def process_matches(needle: str) -> list[dict[str, Any]]:
    if not needle:
        return []
    try:
        result = subprocess.run(
            ["pgrep", "-af", "--", needle],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )
    except Exception:
        return []
    matches: list[dict[str, Any]] = []
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1] if len(parts) > 1 else ""
        if pid == current_pid or "scheduled_task_status.py" in command:
            continue
        matches.append({"pid": pid, "command": command[:240]})
    return matches


def task_process_matches(task_id: str, phase: str) -> list[dict[str, Any]]:
    needles = [task_id]
    if phase:
        needles.append(f"--phase {phase}")
    seen: set[int] = set()
    matches: list[dict[str, Any]] = []
    for needle in needles:
        for match in process_matches(needle):
            pid = match.get("pid")
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid)
                matches.append(match)
    return matches


def people_daily_process_matches() -> list[dict[str, Any]]:
    seen: set[int] = set()
    matches: list[dict[str, Any]] = []
    for needle in ("people_daily_deep_read", "people_daily_workflow.py"):
        for match in process_matches(needle):
            pid = match.get("pid")
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid)
                matches.append(match)
    return matches


def classify_people_daily(day: str) -> ScheduledTask:
    root = PEOPLE_DAILY_ROOT / day
    manifest_path = root / "manifest.json"
    checkpoints = root / "checkpoints"
    manifest = read_json(manifest_path)
    collect_path = checkpoints / "collect.json"
    validate_path = checkpoints / "validate.json"
    analyze_path = checkpoints / "analyze.json"
    publish_path = checkpoints / "publish.json"
    notify_path = checkpoints / "notify.json"
    validate = read_json(validate_path)
    analyze = read_json(analyze_path)
    publish = read_json(publish_path)
    notify = read_json(notify_path)

    evidence = [str(path) for path in (manifest_path, collect_path, validate_path, analyze_path, publish_path, notify_path)]
    validate_done = checkpoint_status(validate_path) == "done" or validate.get("status") == "done"
    analyze_done = checkpoint_status(analyze_path) == "done" or analyze.get("status") == "done"
    analyze_failed = checkpoint_status(analyze_path) == "failed" or analyze.get("status") == "failed"
    analyze_errors = json.dumps(analyze.get("failed", []), ensure_ascii=False)
    direct_provider_env_missing = "VOLCANO_ENGINE_API_KEY" in analyze_errors or "missing_api_key" in analyze_errors
    publish_done = checkpoint_status(publish_path) == "done" or publish.get("status") == "done"
    notify_done = checkpoint_status(notify_path) == "done" or notify.get("status") == "done"
    notify_attempted = notify.get("attempted") is True
    live_processes = people_daily_process_matches()

    if publish_done and notify_done and notify_attempted and validate_done and analyze_done:
        state = "complete_on_artifacts"
        root_cause = "none"
        next_action = "none"
        auto_recoverable = False
    elif publish_done and notify_done and not notify_attempted:
        state = "complete_unpushed"
        root_cause = notify.get("reason") or "notify_checkpoint_done_without_attempt"
        next_action = "resume_notify_after_duplicate_check"
        auto_recoverable = True
    elif live_processes:
        state = "running"
        root_cause = "recovery_or_scheduled_run_in_progress"
        next_action = "wait_and_monitor_stage_advance"
        auto_recoverable = False
    elif not manifest:
        state = "missing_artifacts"
        root_cause = "scheduler_or_workflow_not_started"
        next_action = "inspect_timer_and_journal"
        auto_recoverable = True
    elif analyze_failed and direct_provider_env_missing:
        state = "failed_recoverable"
        root_cause = "direct_provider_env_not_loaded"
        next_action = "rerun_after_runner_env_fix_with_duplicate_publish_check"
        auto_recoverable = True
    elif not validate_done or not analyze_done:
        state = "failed_needs_review"
        root_cause = "validate_or_analyze_not_done"
        next_action = "review_validate_analyze_checkpoints_before_publish_notify"
        auto_recoverable = False
    elif not publish_done:
        state = "failed_recoverable"
        root_cause = "publish_checkpoint_missing_or_failed"
        next_action = "resume_from_publish_after_idempotency_check"
        auto_recoverable = True
    elif not notify_done:
        state = "complete_unnotified"
        root_cause = "notify_checkpoint_missing_or_failed"
        next_action = "resume_notify_after_quality_and_duplicate_check"
        auto_recoverable = True
    else:
        state = "unknown"
        root_cause = "status_classifier_needs_more_evidence"
        next_action = "inspect_manifest_and_checkpoints"
        auto_recoverable = False

    return ScheduledTask(
        task_id=f"people-daily:{day}",
        kind="people_daily",
        date=day,
        phase="deep_read",
        state=state,
        root_cause=root_cause,
        next_action=next_action,
        auto_recoverable=auto_recoverable,
        evidence=evidence,
        details={
            "manifest_exists": bool(manifest),
            "validate_status": checkpoint_status(validate_path),
            "analyze_status": checkpoint_status(analyze_path),
            "analyze_completed": analyze.get("completed"),
            "analyze_total": analyze.get("total"),
            "analyze_failed_count": len(analyze.get("failed", [])) if isinstance(analyze.get("failed"), list) else None,
            "publish_status": checkpoint_status(publish_path),
            "notify_status": checkpoint_status(notify_path),
            "live_process_matches": live_processes,
            "notion_url": publish.get("notion_url") or publish.get("url"),
            "telegram_message_id": notify.get("message_id"),
        },
    )


def market_phase_paths(day: str, phase: str) -> tuple[Path, Path, Path]:
    ymd = day.replace("-", "")
    root = MARKET_DAILY_ROOT / day
    run_slug = f"{ymd}_{phase}"
    return (
        root / f"{run_slug}.manifest.json",
        root / f"{run_slug}.md",
        root / "checkpoints" / run_slug,
    )


def market_task_path(day: str, phase: str) -> Path:
    ymd = day.replace("-", "")
    run_slug = f"{ymd}_{phase}"
    return TASKS_ROOT / f"market_immersion_{phase}-{run_slug}" / "task.json"


def market_due_at(day: str, phase: str) -> str | None:
    spec = MARKET_PHASE_DUE_TIME.get(phase)
    if not spec:
        return None
    hour, minute = spec
    base = date.fromisoformat(day)
    now = datetime.now().astimezone()
    due = datetime(base.year, base.month, base.day, hour, minute, tzinfo=now.tzinfo)
    return due.isoformat(timespec="seconds")


def due_in_future(due_at: str | None) -> bool:
    if not due_at:
        return False
    try:
        return datetime.now().astimezone() < datetime.fromisoformat(due_at)
    except ValueError:
        return False


def classify_market(day: str, phase: str) -> ScheduledTask:
    manifest_path, report_path, checkpoint_dir = market_phase_paths(day, phase)
    task_path = market_task_path(day, phase)
    due_at = market_due_at(day, phase)
    manifest = read_json(manifest_path)
    task = read_json(task_path)
    digest_path = checkpoint_dir / "digest.json"
    quality_path = checkpoint_dir / "quality_check.json"
    render_path = checkpoint_dir / "render.json"
    publish_path = checkpoint_dir / "publish.json"
    notify_path = checkpoint_dir / "notify.json"
    checkpoint = str(manifest.get("checkpoint") or "")
    pid = manifest.get("pid") or manifest.get("process_id")
    run_slug = f"{day.replace('-', '')}_{phase}"
    digest_status = checkpoint_status(digest_path)
    notify = read_json(notify_path)
    task_status = str(task.get("status") or "")
    task_updated_age = age_minutes(task.get("updated_at"))
    live_processes = task_process_matches(f"market_immersion_{phase}-{run_slug}", phase)

    evidence = [
        str(task_path),
        str(manifest_path),
        str(report_path),
        str(digest_path),
        str(quality_path),
        str(render_path),
        str(publish_path),
        str(notify_path),
    ]

    if (
        manifest
        and (checkpoint == "notify_done" or checkpoint_status(notify_path) == "done")
        and notify.get("attempted") is True
    ):
        state = "complete_on_artifacts"
        root_cause = "none"
        next_action = "none"
        auto_recoverable = False
    elif manifest and (checkpoint == "notify_done" or checkpoint_status(notify_path) == "done"):
        state = "complete_unpushed"
        root_cause = notify.get("reason") or "notify_checkpoint_done_without_attempt"
        next_action = "resume_notify_after_duplicate_check"
        auto_recoverable = True
    elif task_status == "running" and live_processes:
        state = "running"
        root_cause = "recovery_or_scheduled_run_in_progress"
        next_action = "wait_and_monitor_stage_advance"
        auto_recoverable = False
    elif manifest and (
        checkpoint.endswith("_stop_after")
        or checkpoint in {"render_done_stop_after", "quality_check_done_stop_after"}
        or (report_path.exists() and checkpoint_status(notify_path) == "missing")
    ):
        state = "complete_unnotified"
        root_cause = (
            "intentionally_stopped_before_publish_notify"
            if checkpoint.endswith("_stop_after")
            or checkpoint in {"render_done_stop_after", "quality_check_done_stop_after"}
            else "rendered_without_notify_checkpoint"
        )
        next_action = "review_report_then_resume_publish_notify_if_appropriate"
        auto_recoverable = root_cause == "rendered_without_notify_checkpoint"
    elif not manifest and due_in_future(due_at):
        state = "not_due"
        root_cause = "not_due_yet"
        next_action = "wait_until_due_window"
        auto_recoverable = False
    elif (
        not manifest
        and task_status == "running"
        and digest_status == "done"
        and not live_processes
        and (task_updated_age is None or task_updated_age >= 10)
    ):
        state = "stale_running_after_digest"
        root_cause = "parent_task_left_running_after_digest_without_live_worker"
        next_action = "finalize_or_resume_from_post_digest_stage_after_idempotency_check"
        auto_recoverable = True
    elif not manifest and digest_status == "done":
        state = "digest_done_but_not_finalized"
        root_cause = "digest_checkpoint_done_but_final_manifest_missing"
        next_action = "inspect_parent_task_and_resume_from_post_digest_stage"
        auto_recoverable = True
    elif not manifest and task:
        state = "scheduler_started_no_final_artifacts"
        root_cause = task_status or "task_record_exists_without_final_manifest"
        next_action = "inspect_task_events_and_resume_or_mark_failed"
        auto_recoverable = task_status in {"failed", "running"}
    elif not manifest:
        state = "scheduler_not_fired_or_disabled"
        root_cause = "no_task_record_or_final_artifacts"
        next_action = "inspect_timer_journal_and_scheduler_registration"
        auto_recoverable = True
    elif checkpoint in {"empty_report_blocked", "digest_degraded_blocked"}:
        state = "failed_needs_review"
        root_cause = checkpoint
        next_action = "inspect_source_readiness_before_retry_or_publish"
        auto_recoverable = checkpoint == "digest_degraded_blocked"
    elif checkpoint in {"publish_failed", "notify_failed"}:
        state = "failed_recoverable"
        root_cause = checkpoint
        next_action = "resume_from_failed_delivery_stage_after_duplicate_check"
        auto_recoverable = True
    else:
        state = "unknown"
        root_cause = checkpoint or "status_classifier_needs_more_evidence"
        next_action = "inspect_manifest_checkpoints_and_logs"
        auto_recoverable = False

    return ScheduledTask(
        task_id=f"market:{day}:{phase}",
        kind="market_immersion",
        date=day,
        phase=phase,
        state=state,
        root_cause=root_cause,
        next_action=next_action,
        auto_recoverable=auto_recoverable,
        evidence=evidence,
        details={
            "manifest_exists": bool(manifest),
            "task_exists": bool(task),
            "task_status": task_status or None,
            "task_updated_at": task.get("updated_at"),
            "task_age_minutes": round(task_updated_age, 1) if task_updated_age is not None else None,
            "task_last_stage": (task.get("metadata") or {}).get("last_stage") if isinstance(task.get("metadata"), dict) else None,
            "live_process_matches": live_processes,
            "report_exists": report_path.exists(),
            "due_at": due_at,
            "checkpoint": checkpoint,
            "pid": pid,
            "pid_alive": pid_alive(pid),
            "digest_status": digest_status,
            "quality_status": checkpoint_status(quality_path),
            "render_status": checkpoint_status(render_path),
            "publish_status": checkpoint_status(publish_path),
            "notify_status": checkpoint_status(notify_path),
        },
    )


def date_range(days: int, end_day: str | None) -> list[str]:
    end = date.fromisoformat(end_day) if end_day else datetime.now().astimezone().date()
    return [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def text_summary(tasks: list[ScheduledTask]) -> str:
    lines = ["Scheduled task punctuality status:"]
    for task in tasks:
        lines.append(
            f"- {task.task_id}: {task.state}; root_cause={task.root_cause}; next={task.next_action}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only scheduled task punctuality status.")
    parser.add_argument("--date", help="End date in YYYY-MM-DD. Defaults to local today.")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--write", type=Path, help="Optional JSON output path.")
    args = parser.parse_args()

    tasks: list[ScheduledTask] = []
    for day in date_range(max(1, args.days), args.date):
        tasks.append(classify_people_daily(day))
        for phase in ("morning", "midday", "close", "night"):
            tasks.append(classify_market(day, phase))

    payload = {
        "schema": "codex.openclaw.scheduled_task_status.v0",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tasks": [asdict(task) for task in tasks],
        "summary": {
            "total": len(tasks),
            "attention": sum(1 for task in tasks if task.state not in {"complete_on_artifacts"}),
            "auto_recoverable": sum(1 for task in tasks if task.auto_recoverable),
        },
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.write.with_suffix(args.write.suffix + ".tmp")
        tmp.write_text(output, encoding="utf-8")
        tmp.replace(args.write)
    if args.json:
        print(output, end="")
    else:
        print(text_summary(tasks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
