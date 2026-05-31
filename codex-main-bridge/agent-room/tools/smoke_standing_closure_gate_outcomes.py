#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("standing_agenda_tick.py")
    spec = importlib.util.spec_from_file_location("standing_agenda_tick", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load standing_agenda_tick")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    mod = load_module()
    at = "2026-05-29T00:00:00+08:00"
    failures: list[str] = []

    completed = mod.build_standing_closure(
        {"evidence_paths": ["agent-room/artifacts/result.md"]},
        terminal_status="completed",
        reason="finished_runner_evidence",
        at=at,
        targets=["codex", "claude-code"],
        agent_statuses={"codex": "completed", "claude-code": "completed"},
    )
    check("completed task records completed_with_evidence", completed.get("outcome") == "completed_with_evidence", failures)
    check("completed task keeps evidence path", "agent-room/artifacts/result.md" in completed.get("evidence_paths", []), failures)

    degraded = mod.build_standing_closure(
        {},
        terminal_status="completed",
        reason="manifest_completed",
        at=at,
        targets=["codex"],
        agent_statuses={},
    )
    check("completed without evidence is degraded_no_progress", degraded.get("outcome") == "degraded_no_progress", failures)
    check("degraded closure belongs to main", degraded.get("owner") == "openclaw-main", failures)

    failed = mod.build_standing_closure(
        {},
        terminal_status="blocked",
        reason="dead_active_runner_evidence",
        at=at,
        targets=["claude-code", "codex"],
        agent_statuses={"claude-code": "failed", "codex": "completed"},
    )
    check("dead runner maps to failed_with_rca", failed.get("outcome") == "failed_with_rca", failures)
    check("failed closure owner is failed agent", failed.get("owner") == "claude-code", failures)

    blocked = mod.build_standing_closure(
        {},
        terminal_status="blocked",
        reason="manifest_blocked",
        at=at,
        targets=["codex"],
        agent_statuses={"codex": "blocked"},
    )
    check("blocked maps to blocked_with_owner", blocked.get("outcome") == "blocked_with_owner", failures)
    check("blocked closure has telegram-safe summary", bool(blocked.get("telegram_safe_summary")), failures)

    result = {
        "schema": "openclaw.agent_room.smoke_standing_closure_gate_outcomes.v0",
        "ok": not failures,
        "failures": failures,
        "samples": {
            "completed": completed,
            "degraded": degraded,
            "failed": failed,
            "blocked": blocked,
        },
        "tokens_printed": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
