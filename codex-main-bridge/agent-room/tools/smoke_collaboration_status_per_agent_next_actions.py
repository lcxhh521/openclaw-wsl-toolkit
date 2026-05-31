#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def first_action(queue: dict[str, Any]) -> dict[str, Any]:
    actions = queue.get("actions") if isinstance(queue.get("actions"), list) else []
    return actions[0] if actions and isinstance(actions[0], dict) else {}


def main() -> int:
    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_per_agent_next_actions_smoke")
    uptake_task = "standing-smoke-next-actions-uptake"
    active_task = "standing-smoke-next-actions-active"
    runner_task = "standing-smoke-next-actions-runner"

    tasks = [
        {
            "task_id": uptake_task,
            "room_id": "openclaw-evolution",
            "quality_gate_status": "needs_collaboration_review",
            "quality_gate_reason": "ledger_points_missing_peer_uptake",
            "target_agents": ["codex", "claude-code"],
            "collaboration": {
                "material_points": 1,
                "peer_uptakes": 0,
                "peer_challenges": 0,
                "summary_points": 0,
                "summary_peer_uptakes": 0,
                "integrated_summaries": 0,
                "summary_needs_integration": 0,
                "integration_signals": 0,
                "blockers": 0,
                "open_blockers": 0,
                "active_claims": 0,
                "expired_claims": 0,
                "missing_claim_leases": 0,
                "points_without_peer_uptake": 1,
                "summary_points_without_peer_uptake": 0,
                "point_counts_by_agent": {"codex": 1},
                "peer_uptake_counts_by_agent": {},
                "peer_challenge_counts_by_agent": {},
                "recent_material_threads": [
                    {
                        "point_id": "pt-codex-001",
                        "agent_id": "codex",
                        "kind": "evidence",
                        "text": "Codex produced a material point that needs peer uptake.",
                        "peer_uptakes": [],
                        "pending_uptake_agents": ["claude-code"],
                    }
                ],
            },
            "per_agent_progress": {},
            "efficiency": {"overall": 0.2, "grade": "low"},
        }
    ]
    runner_attention = {
        "runner_attention_task_count": 1,
        "runner_attention_task_ids": [runner_task],
        "runner_degraded_quorum_task_count": 0,
        "runner_degraded_quorum_task_ids": [],
        "runner_partial_attention_task_count": 1,
        "runner_partial_attention_task_ids": [runner_task],
        "material_stall_task_count": 0,
        "material_stall_task_ids": [],
        "active_material_silence_task_count": 1,
        "active_material_silence_task_ids": [active_task],
        "material_progress_agents_by_task": {},
        "material_stall_agents_by_task": {},
        "active_material_silent_agents_by_task": {active_task: ["codex", "claude-code"]},
    }

    overview = status_tool.collaboration_overview(tasks, runner_attention)
    next_actions = overview.get("per_agent_next_actions") if isinstance(overview.get("per_agent_next_actions"), dict) else {}
    markdown = status_tool.render_markdown_status({
        "generated_at": "2026-05-29T11:20:00+08:00",
        "visibility_state": "smoke",
        "active_runner_count": 0,
        "one_glance": {},
        "per_agent_engagement": {},
        "active_runners": [],
        "collaboration_overview": overview,
        "provider_health": {},
    })
    signature = status_tool.status_watch_signature({
        "generated_at": "2026-05-29T11:20:00+08:00",
        "visibility_state": "smoke",
        "per_agent_engagement": {},
        "collaboration_overview": overview,
        "provider_health": {},
    })
    sig_next = (signature.get("collaboration_health") or {}).get("per_agent_next_actions") or {}

    failures: list[str] = []
    check("per-agent next action queues are generated", sorted(next_actions) == ["claude-code", "codex", "openclaw-main"], failures)
    check("main receives global review/runner/watch actions", (next_actions.get("openclaw-main") or {}).get("action_count") == 3, failures)
    check("claude-code receives peer uptake plus active silence watch", (next_actions.get("claude-code") or {}).get("action_count") == 2, failures)
    check("codex receives active silence watch", (next_actions.get("codex") or {}).get("action_count") == 1, failures)
    check("claude-code queue prioritizes peer uptake before watch", first_action(next_actions.get("claude-code") or {}).get("type") == "peer_uptake_needed", failures)
    check("codex queue marks active silence as watch target", first_action(next_actions.get("codex") or {}).get("role") == "watch_target", failures)
    check("main queue keeps primary role", first_action(next_actions.get("openclaw-main") or {}).get("role") == "primary", failures)
    check("markdown surfaces per-agent next actions", "per_agent_next_actions:" in markdown and "- codex: count=1" in markdown, failures)
    check("markdown surfaces watch target role", "role=watch_target; primary=openclaw-main" in markdown, failures)
    check("markdown surfaces concrete next action", "next=produce a material point" in markdown, failures)
    check("watch signature carries per-agent next actions", (sig_next.get("claude-code") or {}).get("action_count") == 2, failures)
    check(
        "watch signature preserves prioritized first action",
        ((sig_next.get("claude-code") or {}).get("actions") or [""])[0].startswith("peer_uptake_needed:"),
        failures,
    )
    check(
        "watch signature carries active-silence next action",
        any("produce a material point" in value for value in ((sig_next.get("codex") or {}).get("actions") or [])),
        failures,
    )
    check(
        "watch signature carries runner-attention next action",
        any("harvest runner output" in value for value in ((sig_next.get("openclaw-main") or {}).get("actions") or [])),
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.collaboration_status_per_agent_next_actions_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "per_agent_next_actions": next_actions,
        "signature_next_actions": sig_next,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
