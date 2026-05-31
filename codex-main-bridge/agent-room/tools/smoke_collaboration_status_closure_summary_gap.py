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


def main() -> int:
    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_closure_summary_gap_smoke")
    task_id = "standing-smoke-closure-summary-gap"
    target_agents = ["codex", "claude-code"]
    collaboration = {
        "schema": "openclaw.agent_room.collaboration.v0",
        "status": "open",
        "participants": target_agents,
        "points": [
            {"id": "pt-codex", "agent_id": "codex", "kind": "evidence", "text": "Codex produced local status evidence."},
            {"id": "pt-claude", "agent_id": "claude-code", "kind": "risk", "text": "Claude Code challenged the missing integration surface."},
        ],
        "uptakes": [
            {
                "id": "uptake-claude",
                "point_id": "pt-codex",
                "point_agent_id": "codex",
                "by_agent": "claude-code",
                "status": "accepted",
                "reason": "Peer accepted the evidence.",
            },
            {
                "id": "uptake-codex",
                "point_id": "pt-claude",
                "point_agent_id": "claude-code",
                "by_agent": "codex",
                "status": "challenged",
                "reason": "Codex found the status surface should route closure to an agent.",
            },
        ],
        "handoffs": [],
        "blockers": [],
    }
    metrics = status_tool.collaboration_metrics(collaboration, target_agents)
    progress = status_tool.per_agent_collaboration_progress(collaboration, target_agents)
    overview = status_tool.collaboration_overview(
        [
            {
                "task_id": task_id,
                "room_id": "openclaw-evolution",
                "quality_gate_status": "",
                "target_agents": target_agents,
                "collaboration": metrics,
                "per_agent_progress": progress,
                "efficiency": status_tool.collaboration_efficiency_score(metrics),
            }
        ],
        {},
    )
    markdown = status_tool.render_markdown_status({
        "generated_at": "2026-05-31T13:20:00+08:00",
        "visibility_state": "smoke",
        "active_runner_count": 0,
        "one_glance": {},
        "per_agent_engagement": {},
        "active_runners": [],
        "collaboration_overview": overview,
        "provider_health": {},
    })
    signature = status_tool.status_watch_signature({
        "generated_at": "2026-05-31T13:20:00+08:00",
        "visibility_state": "smoke",
        "per_agent_engagement": {},
        "collaboration_overview": overview,
        "provider_health": {},
    })
    action_items = overview.get("action_items") if isinstance(overview.get("action_items"), list) else []
    next_actions = overview.get("per_agent_next_actions") if isinstance(overview.get("per_agent_next_actions"), dict) else {}
    sig_health = signature.get("collaboration_health") if isinstance(signature.get("collaboration_health"), dict) else {}

    closed_metrics = status_tool.collaboration_metrics(
        {
            **collaboration,
            "points": [
                *collaboration["points"],
                {
                    "id": "pt-summary",
                    "agent_id": "claude-code",
                    "kind": "summary",
                    "status": "incorporated",
                    "text": "Integrated closure summary.",
                },
            ],
        },
        target_agents,
    )

    failures: list[str] = []
    check("metrics detect closure summary gap", metrics.get("closure_summary_needed") == 1, failures)
    check("latest peer-reviewed point chooses source agent as closure owner", metrics.get("closure_summary_candidate_agent") == "claude-code", failures)
    check("overview aggregates closure summary gap", overview.get("closure_summary_needed_count") == 1, failures)
    check("overview maps closure owner per task", (overview.get("closure_summary_candidate_agents_by_task") or {}).get(task_id) == "claude-code", failures)
    check("closure gap counts as summary integration needed", task_id in (overview.get("tasks_needing_summary_integration_ids") or []), failures)
    check("closure gap has a dedicated task id list", task_id in (overview.get("tasks_needing_closure_summary_ids") or []), failures)
    check("action item routes to participating agent, not main", any((item.get("type") == "summary_integration_needed" and item.get("agent_id") == "claude-code" and item.get("point_id") == "pt-claude") for item in action_items if isinstance(item, dict)), failures)
    check("main is not the manual closure owner", not any((item.get("type") == "summary_integration_needed" and item.get("agent_id") == "openclaw-main") for item in action_items if isinstance(item, dict)), failures)
    check("per-agent queue carries closure action", (next_actions.get("claude-code") or {}).get("action_count") == 1, failures)
    check("markdown surfaces closure summary gap", "closure_summary_needed_count: 1" in markdown and "claude-code/summary_integration_needed" in markdown, failures)
    check("watch signature carries closure summary gap", sig_health.get("tasks_needing_closure_summary_count") == 1, failures)
    check("integrated summary suppresses closure summary gap", closed_metrics.get("closure_summary_needed") == 0, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_closure_summary_gap_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "metrics": metrics,
        "overview_action_items": action_items,
        "signature_closure_summary": {
            "tasks_needing_closure_summary_count": sig_health.get("tasks_needing_closure_summary_count"),
            "tasks_needing_closure_summary_ids": sig_health.get("tasks_needing_closure_summary_ids"),
            "closure_summary_candidate_agents_by_task": sig_health.get("closure_summary_candidate_agents_by_task"),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
