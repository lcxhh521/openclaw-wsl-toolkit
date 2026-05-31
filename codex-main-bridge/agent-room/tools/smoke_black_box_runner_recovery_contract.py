#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("agent_room_resident_bridge.py")
    spec = importlib.util.spec_from_file_location("agent_room_resident_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load agent_room_resident_bridge")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    resident = load_module()
    failures: list[str] = []
    now = datetime.now(timezone.utc).astimezone()

    hard_expired = {
        "agent_id": "claude-code",
        "pid": os.getpid(),
        "started_at": (now - timedelta(seconds=2000)).isoformat(timespec="seconds"),
        "soft_deadline_at": (now - timedelta(seconds=1000)).isoformat(timespec="seconds"),
        "hard_deadline_at": (now - timedelta(seconds=10)).isoformat(timespec="seconds"),
    }
    check("hard deadline classifies exceeded", resident.classify_runner_deadline_state(hard_expired) == "hard_deadline_exceeded", failures)
    check("hard deadline marks active runner stale", resident.active_runner_stale(hard_expired), failures)

    task = {
        "task_id": "smoke-black-box-runner-recovery",
        "run_id": "smoke-black-box-runner-recovery",
        "room_id": "openclaw-evolution",
        "target_agents": ["claude-code"],
        "source": {"transport": "telegram", "chat_id": "-1009000000001"},
        "requested_by": "telegram-user",
        "delivery_policy": "targeted_reply",
    }
    comment = resident.fallback_runner_comment(task, "claude-code", {
        "timeout": True,
        "age_seconds": 1900,
        "max_seconds": 1800,
        "deadline_state": "hard_deadline_exceeded",
        "ok": False,
    })
    recovery = comment.get("runner_recovery") if isinstance(comment.get("runner_recovery"), dict) else {}
    check("fallback comment records owner", recovery.get("owner") == "claude-code", failures)
    check("fallback comment records impact", bool(recovery.get("impact")), failures)
    check("fallback comment records recovery action", bool(recovery.get("recovery_action")), failures)
    check("fallback comment has Chinese telegram-safe summary", "恢复动作" in str(recovery.get("telegram_safe_summary") or ""), failures)
    check("runner failure stays local by default", comment.get("telegram_projection_status") == "local_only_runner_failure", failures)

    promoted, promoted_comments = resident.promote_runner_failures_for_visible_silence(task, [comment])
    check("visible liveness task promotes runner blocker", promoted_comments and promoted[0].get("telegram_projection_status") == "user_visible_runner_failure", failures)
    check("promoted comment preserves recovery contract", bool((promoted[0].get("runner_recovery") or {}).get("recovery_action")), failures)

    result = {
        "schema": "openclaw.agent_room.smoke_black_box_runner_recovery_contract.v0",
        "ok": not failures,
        "failures": failures,
        "samples": {
            "deadline_state": resident.classify_runner_deadline_state(hard_expired),
            "comment_projection_status": comment.get("telegram_projection_status"),
            "promoted_projection_status": promoted[0].get("telegram_projection_status") if promoted else None,
            "runner_recovery": recovery,
        },
        "tokens_printed": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
