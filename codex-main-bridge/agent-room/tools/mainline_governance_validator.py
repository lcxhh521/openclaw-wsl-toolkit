#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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
GOVERNANCE_STATES = {"intake", "triage", "plan", "execute", "review", "integrate", "close"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def governance_view(task: dict[str, Any]) -> dict[str, Any]:
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    out: dict[str, Any] = {}
    for field in REQUIRED_FIELDS:
        if field in task:
            out[field] = task.get(field)
        elif field in governance:
            out[field] = governance.get(field)
    out["state"] = task.get("governance_state") or governance.get("state")
    return out


def validate_task(task: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    view = governance_view(task)

    for field in REQUIRED_FIELDS:
        value = view.get(field)
        if field == "participants":
            if not isinstance(value, list) or not any(compact_text(item) for item in value):
                errors.append(f"{field}: missing non-empty participants list")
            continue
        if field == "approval_gate":
            if isinstance(value, dict):
                if "required" not in value:
                    errors.append("approval_gate: object must include required")
                if not compact_text(value.get("reason")):
                    errors.append("approval_gate: object must include reason")
            elif not compact_text(value):
                errors.append("approval_gate: missing approval boundary")
            continue
        if not compact_text(value):
            errors.append(f"{field}: missing non-empty value")

    state = compact_text(view.get("state"))
    if state and state not in GOVERNANCE_STATES:
        errors.append(f"governance_state: expected one of {sorted(GOVERNANCE_STATES)}, got {state!r}")
    if not state:
        warnings.append("governance_state: missing explicit intake/triage/plan/execute/review/integrate/close state")

    if compact_text(task.get("lane")) == "standing_mainline_discussion" and "openclaw-main" not in [
        compact_text(item) for item in (view.get("participants") or [])
    ]:
        warnings.append("participants: standing mainline tasks should include openclaw-main as runtime-context participant")

    return {
        "schema": "openclaw.agent_room.mainline_governance_validation.v0",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "governance": view,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Agent Room mainline governance fields on a task manifest.")
    parser.add_argument("--task-file", required=True, help="Path to a task manifest JSON file.")
    args = parser.parse_args()

    task_path = Path(args.task_file).expanduser().resolve()
    task = load_json(task_path)
    if not isinstance(task, dict):
        print(json.dumps({"ok": False, "errors": ["task root is not object"], "task_file": str(task_path)}, ensure_ascii=False, indent=2))
        return 2
    result = validate_task(task)
    result["task_file"] = str(task_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
