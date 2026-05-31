#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parent


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_task_runner = load_module(TOOLS / "agent_task_runner.py", "agent_task_runner")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    failures: list[str] = []
    task = {
        "task_id": "smoke-collab-quality-gate",
        "collaboration": {
            "artifacts": [
                {"type": "comment_jsonl", "produced_by": "codex", "path": "agent-comments/codex.jsonl"},
                {"type": "comment_jsonl", "produced_by": "claude-code", "path": "agent-comments/claude-code.jsonl"},
            ],
            "blockers": [],
            "handoffs": [],
        },
    }
    gate = agent_task_runner.collaboration_quality_gate(
        task,
        ["codex", "claude-code"],
        {"codex", "claude-code"},
    )
    check(
        "produced_by artifacts count as agent artifacts",
        gate.get("status") == "needs_collaboration_review"
        and gate.get("reason") == "parallel_artifacts_without_integration"
        and gate.get("artifact_agents") == ["claude-code", "codex"],
        failures,
    )

    task["collaboration"]["handoffs"] = [
        {"from_agent": "codex", "to_agent": "claude-code", "status": "accepted"}
    ]
    gate = agent_task_runner.collaboration_quality_gate(
        task,
        ["codex", "claude-code"],
        {"codex", "claude-code"},
    )
    check(
        "handoff closes quality gate as peer reviewed",
        gate.get("status") == "peer_reviewed" and gate.get("reason") == "handoff_recorded",
        failures,
    )

    result = {
        "schema": "openclaw.agent_room.collaboration_closure_gate_smoke.v0",
        "ok": not failures,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
