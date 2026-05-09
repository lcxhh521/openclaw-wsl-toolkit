#!/usr/bin/env python3
"""Small file-backed background task records for OpenClaw workflows.

This is intentionally local and boring: it does not change Gateway, Telegram,
models, secrets, or OpenClaw core.  Workflows write durable task records under
workspace/tasks so the Telegram/main session is not the source of truth for
long-running work.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
TASKS_ROOT = WORKSPACE / "tasks"
VALID_STATUSES = {
    "pending",
    "running",
    "awaiting_main_review",
    "succeeded",
    "failed",
    "needs_review",
    "cancelled",
    "rejected",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip())
    return value.strip("-")[:120] or "task"


def make_task_id(task_type: str, key: str | None = None) -> str:
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    suffix = safe_slug(key or uuid.uuid4().hex[:8])
    return f"{safe_slug(task_type)}-{stamp}-{suffix}"


def task_dir(task_id: str) -> Path:
    return TASKS_ROOT / safe_slug(task_id)


def task_path(task_id: str) -> Path:
    return task_dir(task_id) / "task.json"


def load_task(task_id: str) -> dict[str, Any]:
    path = task_path(task_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["task_id"])
    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = now_iso()
    tmp = d / "task.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "task.json")
    return payload


def create_or_resume_task(
    *,
    task_type: str,
    task_id: str | None = None,
    key: str | None = None,
    requested_by: str = "system",
    input_summary: str = "",
    success_criteria: list[str] | None = None,
    retry_policy: dict[str, Any] | None = None,
    review_required: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = task_id or os.environ.get("OPENCLAW_TASK_ID") or make_task_id(task_type, key)
    existing = load_task(task_id)
    if existing:
        existing.setdefault("attempts", 0)
        existing["attempts"] += 1
        existing["status"] = "running"
        existing["last_started_at"] = now_iso()
        return write_task(existing)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "task_type": task_type,
        "requested_by": requested_by,
        "created_at": now_iso(),
        "last_started_at": now_iso(),
        "status": "running",
        "input_summary": input_summary,
        "artifact_paths": [],
        "checkpoint_path": "",
        "error_kind": "",
        "error_summary": "",
        "retry_policy": retry_policy or {"mode": "supervisor", "max_immediate_retries": 0},
        "review_required": review_required,
        "attempts": 1,
        "metadata": metadata or {},
        "events": [{"at": now_iso(), "kind": "created"}],
    }
    return write_task(payload)


def update_task(task_id: str, **updates: Any) -> dict[str, Any]:
    payload = load_task(task_id) or {"task_id": task_id, "created_at": now_iso(), "status": "pending"}
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid task status: {updates['status']}")
    events = payload.setdefault("events", [])
    event = {"at": now_iso(), "kind": updates.pop("event", "update")}
    if "status" in updates:
        event["status"] = updates["status"]
    events.append(event)
    payload.update(updates)
    return write_task(payload)


def add_artifacts(task_id: str, paths: list[str | Path]) -> dict[str, Any]:
    payload = load_task(task_id)
    existing = [str(p) for p in payload.get("artifact_paths") or []]
    for path in paths:
        text = str(path)
        if text and text not in existing:
            existing.append(text)
    return update_task(task_id, artifact_paths=existing, event="artifacts")


def finish_task(
    task_id: str,
    *,
    artifacts: list[str | Path] | None = None,
    summary: str = "",
    main_review_required: bool = False,
) -> dict[str, Any]:
    if artifacts:
        add_artifacts(task_id, artifacts)
    if main_review_required:
        payload = load_task(task_id)
        metadata = payload.get("metadata") or {}
        metadata["next_action"] = "main_review"
        return update_task(
            task_id,
            status="awaiting_main_review",
            completed_at=now_iso(),
            error_kind="",
            error_summary="",
            result_summary=summary,
            review_required=True,
            metadata=metadata,
            event="awaiting_main_review",
        )
    return update_task(task_id, status="succeeded", completed_at=now_iso(), error_kind="", error_summary="", result_summary=summary, review_required=False, event="succeeded")


def approve_task(task_id: str, *, reviewer: str = "main", summary: str = "") -> dict[str, Any]:
    payload = load_task(task_id)
    metadata = payload.get("metadata") or {}
    metadata["reviewed_by"] = reviewer
    metadata["review_decision"] = "approved"
    metadata.pop("next_action", None)
    return update_task(
        task_id,
        status="succeeded",
        review_required=False,
        reviewed_at=now_iso(),
        review_summary=summary,
        metadata=metadata,
        event="main_review_approved",
    )


def reject_task(task_id: str, *, reviewer: str = "main", summary: str = "") -> dict[str, Any]:
    payload = load_task(task_id)
    metadata = payload.get("metadata") or {}
    metadata["reviewed_by"] = reviewer
    metadata["review_decision"] = "rejected"
    metadata["next_action"] = "revise_or_rerun"
    return update_task(
        task_id,
        status="rejected",
        review_required=True,
        reviewed_at=now_iso(),
        review_summary=summary,
        metadata=metadata,
        event="main_review_rejected",
    )


def fail_task(
    task_id: str,
    *,
    error_kind: str,
    error_summary: str,
    checkpoint_path: str | Path | None = None,
    artifacts: list[str | Path] | None = None,
    needs_review: bool = False,
) -> dict[str, Any]:
    if artifacts:
        add_artifacts(task_id, artifacts)
    return update_task(
        task_id,
        status="needs_review" if needs_review else "failed",
        failed_at=now_iso(),
        error_kind=error_kind,
        error_summary=error_summary[:1000],
        checkpoint_path=str(checkpoint_path or ""),
        review_required=needs_review,
        event="failed",
    )


def write_error(task_id: str, *, error_kind: str, error_summary: str, details: dict[str, Any] | None = None) -> Path:
    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "error.json"
    p.write_text(json.dumps({"at": now_iso(), "error_kind": error_kind, "error_summary": error_summary, "details": details or {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
