#!/usr/bin/env python3
from __future__ import annotations

import agent_room_resident_bridge as resident


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def task_fixture() -> dict:
    return {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": "smoke-coproducer-projection-guard",
        "run_id": "smoke-coproducer-projection-guard",
        "room_id": "openclaw-evolution",
        "requested_by": "telegram-user",
        "target_agents": ["codex", "claude-code"],
        "delivery_policy": "broadcast_all_agents_decide",
        "problem_statement": "我感觉你们总是在重复讨论",
        "source": {"transport": "telegram", "chat_id": "-1009000000001"},
        "collaboration": {
            "schema": "openclaw.agent_room.collaboration.v0",
            "acceptance": "distinct non-duplicative room contributions",
        },
    }


def comment_fixture(body: str, *, turn_position: str = "co_producer", kind: str = "comment") -> dict:
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": "codex",
        "run_id": "smoke-coproducer-projection-guard",
        "task_id": "smoke-coproducer-projection-guard",
        "room_id": "openclaw-evolution",
        "kind": kind,
        "title": "Codex room response",
        "body": body,
        "blockers": [],
        "collaboration_assignment": {"turn_position": turn_position},
    }


def main() -> int:
    failures: list[str] = []
    task = task_fixture()

    generic_coproducer = comment_fixture("我同意这个方向，后续应该减少重复讨论。")
    may_project, reason = resident.telegram_projection_decision(task, [generic_coproducer])
    check(
        "generic co-producer reply is suppressed",
        may_project is False and reason == "coproducer_no_concrete_delta",
        failures,
    )

    concrete_coproducer = comment_fixture(
        "已落地 Patch: [agent_room_resident_bridge.py](/tmp/agent_room_resident_bridge.py:1)。"
        "Smoke: `smoke_coproducer_projection_guard.py` 退出 0。"
    )
    may_project, mode = resident.telegram_projection_decision(task, [concrete_coproducer])
    check("co-producer with concrete delta remains visible", may_project is True and mode == "normal", failures)

    lead_comment = comment_fixture("我会压缩这轮口径并给出一个收敛结论。", turn_position="lead")
    may_project, mode = resident.telegram_projection_decision(task, [lead_comment])
    check("lead reply remains visible", may_project is True and mode == "normal", failures)

    if failures:
        print("smoke_coproducer_projection_guard: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("smoke_coproducer_projection_guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
