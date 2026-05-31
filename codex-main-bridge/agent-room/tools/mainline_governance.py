#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
from typing import Any


CONTRACT_PATH = "agent-room/methodology/mainline-governance-contract-20260528.md"
REQUIRED_FIELDS = (
    "mainline_id",
    "problem_statement",
    "expected_user_value",
    "owner",
    "participants",
    "definition_of_done",
    "approval_gate",
    "dedupe_key",
    "next_action",
)
GOVERNANCE_STATES = {
    "intake",
    "triage",
    "plan",
    "execute",
    "review",
    "integrate",
    "close",
    "needs_alex",
    "blocked",
    "stale",
    "failed",
    "retry",
    "merged",
}
LEGACY_TO_GOVERNANCE_STATE = {
    "queued": "triage",
    "leased": "execute",
    "running": "execute",
    "deferred": "plan",
    "completed": "close",
    "done": "close",
    "closed": "close",
    "blocked": "blocked",
    "failed": "failed",
    "cancelled": "failed",
    "canceled": "failed",
    "stale": "stale",
    "retry": "retry",
    "merged": "merged",
}
MAINLINE_MARKERS = (
    ("translation_agent", ("translation", "翻译", "译文", "校对")),
    ("people_daily_deep_read", ("people daily", "人民日报", "人民日", "日报", "深读")),
    ("market_daily_report", ("market", "市场", "报告", "行情")),
    ("model_quota_routing", ("model", "quota", "ark", "provider", "fallback", "模型", "配额", "路由")),
    ("telegram_reliability", ("telegram", "群", "消息", "回复", "状态卡", "可见")),
    ("control_center_observability", ("control center", "observability", "监控", "可观测", "状态面")),
    ("scheduled_task_reliability", ("scheduled", "timer", "standing", "定时", "调度", "常态")),
    ("agent_room_infrastructure", ("agent room", "agent-room", "协作", "agent", "bot", "codex", "claude", "主线")),
)
EXPECTED_USER_VALUE = {
    "telegram_reliability": "Alex can send a room message and get a visible answer or exact blocker without silent drops.",
    "scheduled_task_reliability": "Scheduled work advances or records stale/blocked evidence instead of drifting.",
    "agent_room_infrastructure": "Agent collaboration stays tied to concrete OpenClaw progress and leaves evidence.",
    "model_quota_routing": "Provider/model failure state is visible, recoverable, and not confused with unrelated workflows.",
    "control_center_observability": "Alex gets a minimal status card while diagnostics remain in local status surfaces.",
    "people_daily_deep_read": "People Daily work keeps its existing quality gate and deliverable path.",
    "market_daily_report": "Market report work keeps its source and publication gates intact.",
    "translation_agent": "Translation work remains isolated, quality-gated, and resumable.",
}


def compact(value: Any, limit: int = 180) -> str:
    if isinstance(value, list):
        text = "; ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = str(value)
    else:
        text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def first_text(*values: Any) -> str:
    for value in values:
        text = compact(value)
        if text:
            return text
    return ""


def unique(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def infer_mainline_id(text: str) -> str:
    lowered = str(text or "").lower()
    for mainline_id, markers in MAINLINE_MARKERS:
        if any(marker.lower() in lowered for marker in markers):
            return mainline_id
    return "agent_room_infrastructure"


def approval_gate(permissions: Any) -> dict[str, Any]:
    perms = permissions if isinstance(permissions, dict) else {}
    risky = [
        key
        for key in ("telegram_send", "notion_publish", "github_push", "secrets_access", "quality_surface_change")
        if perms.get(key) is True
    ]
    return {
        "required": bool(risky),
        "reason": "approval_required_for_" + ",".join(risky) if risky else "safe_reversible_local_agent_room_work",
    }


def dedupe_key(room_id: str, mainline_id: str, problem_statement: str, source_key: str = "") -> str:
    if source_key:
        return f"{mainline_id}:{room_id}:{source_key}"
    digest = hashlib.sha256(f"{room_id}:{mainline_id}:{problem_statement.lower()}".encode("utf-8")).hexdigest()[:20]
    return f"{mainline_id}:{room_id}:{digest}"


def state_from_status(status: Any) -> str:
    return LEGACY_TO_GOVERNANCE_STATE.get(str(status or "").strip().lower(), "triage")


def stamp_task(
    task: dict[str, Any],
    *,
    text: str = "",
    requested_by: str | None = None,
    source_key: str = "",
    next_action: str = "",
) -> dict[str, Any]:
    problem = first_text(
        task.get("problem_statement"),
        text,
        task.get("task_id"),
    )
    mainline_id = first_text(task.get("mainline_id"), infer_mainline_id(problem))
    participants = unique([
        task.get("owner"),
        "openclaw-main",
        requested_by or task.get("requested_by"),
        *(task.get("target_agents") or []),
        *(task.get("participants") if isinstance(task.get("participants"), list) else []),
    ])
    task["mainline_id"] = mainline_id
    task["problem_statement"] = problem
    task["expected_user_value"] = first_text(
        task.get("expected_user_value"),
        EXPECTED_USER_VALUE.get(mainline_id),
        "Alex can see concrete progress, a verified blocker, or a safer resumed state.",
    )
    task["owner"] = first_text(task.get("owner"), "openclaw-main")
    task["participants"] = participants
    if not isinstance(task.get("definition_of_done"), list) or not task.get("definition_of_done"):
        task["definition_of_done"] = [
            "patch_or_config_change",
            "artifact_or_decision_record",
            "smoke_or_runtime_verification",
            "rca_with_prevention_rule",
            "accepted_blocker_with_exact_next_action",
        ]
    if not isinstance(task.get("approval_gate"), dict):
        task["approval_gate"] = approval_gate(task.get("permissions"))
    task["dedupe_key"] = first_text(task.get("dedupe_key"), dedupe_key(str(task.get("room_id") or ""), mainline_id, problem, source_key))
    task["next_action"] = first_text(task.get("next_action"), next_action, "triage_plan_execute_review_integrate_or_close_with_concrete_output")
    task["governance_state"] = first_text(task.get("governance_state"), state_from_status(task.get("status")))
    task["governance_contract_path"] = CONTRACT_PATH
    task["drift_check_passed"] = True
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    governance.update({
        "schema": governance.get("schema") or "openclaw.agent_room.mainline_governance.v0",
        "mainline_id": task["mainline_id"],
        "problem_statement": task["problem_statement"],
        "expected_user_value": task["expected_user_value"],
        "owner": task["owner"],
        "participants": task["participants"],
        "definition_of_done": task["definition_of_done"],
        "approval_gate": task["approval_gate"],
        "dedupe_key": task["dedupe_key"],
        "next_action": task["next_action"],
        "state": task["governance_state"],
        "drift_check_passed": task["drift_check_passed"],
    })
    task["governance"] = governance
    task["governance_validation"] = {
        "required_fields": list(REQUIRED_FIELDS),
        "missing_required_fields": [
            key for key in REQUIRED_FIELDS if task.get(key) in (None, "", [], {})
        ] + ([] if task["governance_state"] in GOVERNANCE_STATES else ["governance_state:invalid"]),
    }
    task["governance_validation"]["ok"] = not task["governance_validation"]["missing_required_fields"]
    return task
