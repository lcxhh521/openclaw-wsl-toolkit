#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    runner = (TOOLS / "agent_task_runner.py").read_text(encoding="utf-8")
    bridge = (TOOLS / "telegram_agent_bridge.py").read_text(encoding="utf-8")
    standing = (TOOLS / "standing_agenda_tick.py").read_text(encoding="utf-8")

    failures: list[str] = []

    require("First look for a safe, scoped way to advance the OpenClaw mainline" in runner,
            "runner requires idle agents to seek scoped mainline work", failures)
    require("先在权限内寻找不重复的主线推进点" in runner,
            "runner work-item brief requires non-duplicative mainline contribution", failures)
    require("A visible contribution must carry at least one concrete unit of value" in runner,
            "runner rejects pure intent/status chatter", failures)
    require("Use parallel production only when the task explicitly opts into a new Agent Room collaboration flow" in runner,
            "runner limits parallel production to explicit opt-in collaboration flow", failures)
    require("Do not reinterpret, replace, or modify existing production/task workflows" in runner,
            "runner protects existing production workflow entrypoints", failures)

    require("actively look for a safe, non-duplicative mainline contribution" in bridge,
            "telegram bridge boundary carries idle-agent contribution rule", failures)
    require("Do not reinterpret, replace, or modify existing production/task workflows" in bridge,
            "telegram bridge protects Translation/People Daily/market/provider gates", failures)
    require('"mode": "dynamic_claims"' in bridge,
            "telegram bridge creates dynamic collaboration claims", failures)
    require('"max_rounds": max_rounds' in bridge,
            "telegram bridge bounds follow-up rounds", failures)

    require('"lease": {"owner": None, "heartbeat_at": None, "expires_at": None}' in standing,
            "standing agenda tasks include a lease field", failures)
    require('"claims": []' in standing and '"handoffs": []' in standing and '"blockers": []' in standing,
            "standing agenda tasks include collaboration bookkeeping fields", failures)
    require("Keep Translation Agent, People Daily, market/report workflows, provider gates, and publication gates intact." in standing,
            "standing agenda brief preserves existing workflow gates", failures)

    if failures:
        print("smoke_idle_agent_contract: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("smoke_idle_agent_contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
