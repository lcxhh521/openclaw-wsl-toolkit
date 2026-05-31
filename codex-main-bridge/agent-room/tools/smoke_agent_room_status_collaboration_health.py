#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "agent-room-status-collaboration-health-smoke"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge = DRY_RUN / "codex-main-bridge"
    room = bridge / "agent-room"
    status_dir = room / "collaboration-status"

    write_json(bridge / "room.json", {"room_id": "openclaw-evolution", "backend": "telegram", "status": "active"})
    write_json(bridge / "participants.json", {"participants": []})
    write_json(bridge / "baselines.json", {"baselines": []})
    write_json(
        status_dir / "latest.json",
        {
            "schema": "openclaw.agent_room.collaboration_status.v0",
            "generated_at": "2026-05-27T20:40:00+08:00",
            "include_background": True,
            "active_task_ids": ["standing-health-smoke"],
            "attention_task_ids": ["standing-needs-uptake"],
            "activity_dashboard": {
                "summary_state": "active_with_attention",
                "live_runner_count": 1,
                "pending_harvest_count": 1,
                "needs_attention_count": 2,
            },
            "participant_presence": [
                {"agent_id": "codex", "task_id": "standing-health-smoke", "presence_state": "working"},
                {"agent_id": "claude-code", "task_id": "standing-health-smoke", "presence_state": "completed"},
            ],
            "per_agent_engagement": {
                "codex": {
                    "engagement_state": "working_with_local_output",
                    "active_runner_count": 1,
                    "working_runner_count": 1,
                    "pending_harvest_count": 0,
                    "completed_presence_count": 0,
                    "needs_attention_count": 1,
                    "black_box_runner_count": 0,
                    "active_task_ids": ["standing-health-smoke"],
                    "next_soft_deadline_at": "2026-05-27T20:45:00+08:00",
                    "next_hard_deadline_at": "2026-05-27T21:00:00+08:00",
                },
                "claude-code": {
                    "engagement_state": "completed_awaiting_integration",
                    "active_runner_count": 0,
                    "working_runner_count": 0,
                    "pending_harvest_count": 1,
                    "completed_presence_count": 1,
                    "needs_attention_count": 0,
                    "black_box_runner_count": 0,
                    "active_task_ids": ["standing-health-smoke"],
                },
            },
            "collaboration_overview": {
                "tracked_tasks": 3,
                "material_point_count": 4,
                "peer_uptake_count": 2,
                "peer_challenge_count": 1,
                "integration_signal_count": 2,
                "summary_point_count": 1,
                "peer_reviewed_task_count": 1,
                "needs_collaboration_review_count": 1,
                "needs_collaboration_repair_count": 0,
                "tasks_missing_peer_uptake_count": 1,
                "tasks_missing_peer_uptake_ids": ["standing-needs-uptake"],
                "degraded_quorum_task_count": 1,
                "degraded_quorum_task_ids": ["standing-degraded"],
                "runner_attention_task_count": 1,
                "runner_attention_task_ids": ["standing-runner-attention"],
                "active_claim_count": 2,
                "expired_claim_count": 1,
                "claim_lease_expired_task_count": 1,
                "claim_lease_expired_task_ids": ["standing-lease-expired"],
                "per_agent_material_points": {"codex": 2, "claude-code": 2},
                "per_agent_peer_uptakes": {"codex": 1, "claude-code": 1},
                "per_agent_peer_challenges": {"codex": 1},
            },
            "fixed_status_card": {"text": "OpenClaw status card smoke"},
        },
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "agent_room_status.py"), "--bridge", str(bridge), "--no-write"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload = json.loads(result.stdout) if result.returncode == 0 else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    health = payload.get("collaboration_health") if isinstance(payload.get("collaboration_health"), dict) else {}

    failures: list[str] = []
    check("agent_room_status exits cleanly", result.returncode == 0, failures)
    check("summary exposes collaboration health", summary.get("collaboration_health_present") is True, failures)
    check("summary exposes material points", summary.get("collaboration_material_point_count") == 4, failures)
    check("summary exposes peer uptake", summary.get("collaboration_peer_uptake_count") == 2, failures)
    check("summary exposes peer challenges", summary.get("collaboration_peer_challenge_count") == 1, failures)
    check("summary exposes integration signals", summary.get("collaboration_integration_signal_count") == 2, failures)
    check("summary exposes missing uptake tasks", summary.get("collaboration_tasks_missing_peer_uptake") == 1, failures)
    check("summary exposes degraded quorum", summary.get("collaboration_degraded_quorum_tasks") == 1, failures)
    check("summary exposes runner attention", summary.get("collaboration_runner_attention_tasks") == 1, failures)
    check("health keeps per-agent engagement", (health.get("per_agent_engagement") or {}).get("codex", {}).get("engagement_state") == "working_with_local_output", failures)
    check("health keeps pending harvest state", (health.get("per_agent_engagement") or {}).get("claude-code", {}).get("pending_harvest_count") == 1, failures)
    check("health records participant presence count", health.get("participant_presence_count") == 2, failures)
    check("health records status surfaces", (health.get("status_surfaces") or {}).get("fixed_status_card_text_present") is True, failures)

    smoke = {
        "schema": "openclaw.agent_room.agent_room_status_collaboration_health_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "summary": summary,
        "collaboration_health": health,
        "stderr": result.stderr[-1000:],
    }
    print(json.dumps(smoke, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
