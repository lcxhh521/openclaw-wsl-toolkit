#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
RESIDENT = ROOT / "agent-room" / "tools" / "agent_room_resident_bridge.py"
REPLY = ROOT / "agent-room" / "tools" / "telegram_agent_reply.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    resident = load_module(RESIDENT, "agent_room_resident_bridge_under_test")
    reply = load_module(REPLY, "telegram_agent_reply_under_test")
    task = {
        "task_id": "visibility-invariant-task",
        "run_id": "visibility-invariant-run",
        "room_id": "openclaw-evolution",
        "requested_by": "telegram-user",
        "delivery_policy": "broadcast_all_agents_decide",
        "source": {"transport": "telegram"},
    }
    generated = resident.fallback_runner_comment(task, "codex", {"missing_process": True})
    assert_true(generated.get("telegram_projection_status") == "local_only_runner_failure", "fallback runner failures must stay local-only")
    allow, reason = resident.telegram_projection_decision(task, [generated])
    assert_true(not allow and reason == "runner_lifecycle_failure_local_only", f"runner lifecycle failure projected: {allow=} {reason=}")
    promoted, promoted_records = resident.promote_runner_failures_for_visible_silence(task, [generated])
    assert_true(not promoted_records, "runner lifecycle failures must not be promoted for visible silence")
    assert_true(promoted[0].get("telegram_projection_status") == "local_only_runner_failure", "promotion mutated local-only status")
    legacy_visible = dict(generated)
    legacy_visible["telegram_projection_status"] = "user_visible_runner_failure"
    assert_true(resident.is_internal_runner_failure_comment(legacy_visible), "legacy visible runner failure must still classify as internal")
    allow, reason = resident.telegram_projection_decision(task, [legacy_visible])
    assert_true(not allow and reason == "runner_lifecycle_failure_local_only", "legacy visible runner failure must be suppressed centrally")
    assert_true(reply.is_internal_runner_failure_comment(legacy_visible, legacy_visible["title"], legacy_visible["body"], legacy_visible["blockers"]), "telegram reply layer must suppress legacy visible runner failure")
    visible_task = dict(task)
    visible_task["visible_runner_failure_allowed"] = True
    promoted, promoted_records = resident.promote_runner_failures_for_visible_silence(visible_task, [generated])
    assert_true(len(promoted_records) == 1, "explicit liveness task should promote one runner failure")
    assert_true(promoted[0].get("telegram_projection_status") == "user_visible_runner_failure", "explicit liveness promotion must mark visible runner failure")
    assert_true(not resident.is_internal_runner_failure_comment(promoted[0]), "explicit liveness promotion must remain publishable")
    assert_true(not reply.is_internal_runner_failure_comment(promoted[0], promoted[0]["title"], promoted[0]["body"], promoted[0]["blockers"]), "reply layer must accept explicit liveness promotion")
    print(json.dumps({"ok": True, "checked": "runner_failure_visibility_invariant"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
