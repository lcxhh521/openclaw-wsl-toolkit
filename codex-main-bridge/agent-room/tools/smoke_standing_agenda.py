#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agent_room_resident_bridge as resident
import standing_agenda_tick


def check(checks: list[str], name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def configure_temp_room(root: Path) -> None:
    standing_agenda_tick.ROOT = root
    standing_agenda_tick.ROOM = root / "agent-room"
    standing_agenda_tick.CONFIG = standing_agenda_tick.ROOM / "config" / "standing-agenda.json"
    standing_agenda_tick.STATE = standing_agenda_tick.ROOM / "standing-agenda-state.json"
    standing_agenda_tick.TASKS_JSONL = standing_agenda_tick.ROOM / "tasks.jsonl"
    standing_agenda_tick.ACTIVE_RUNNERS = standing_agenda_tick.ROOM / "active-runners"


def write_fixture_config(enabled: bool) -> None:
    standing_agenda_tick.write_json(
        standing_agenda_tick.CONFIG,
        {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": enabled,
            "proactive_tick_interval_seconds": 300,
            "post_completion_idle_rescan_seconds": 0,
            "max_rounds": 1,
            "standing_collaboration_tick": {
                "enabled": True,
                "max_rounds": 2,
                "scope": "standing_mainline_tasks_only",
            },
            "autonomy_improvement_policy": {
                "self_evolution": {
                    "enabled": True,
                    "snapshot_path": "agent-room/artifacts/mainline-autonomy-evolution-ledger.jsonl",
                    "repeat_failure_threshold": 2,
                    "repeat_failure_window_hours": 24,
                }
            },
            "items": [
                {
                    "id": "smoke-proactive-discussion",
                    "mainline_item_id": "smoke-mainline-lane",
                    "title": "Smoke proactive discussion",
                    "description": "Create a quiet-period standing task.",
                    "status": "open",
                    "priority": 10,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                    "standing_artifact_hooks": [
                        {"type": "model_routing_reliability_snapshot", "since_hours": 24},
                    ],
                }
            ],
        },
    )


def write_room_fixture() -> None:
    room_id = "openclaw-evolution"
    standing_agenda_tick.write_json(
        standing_agenda_tick.ROOM / "rooms" / room_id / "room.json",
        {"room_id": room_id, "telegram_chat_id": "-1009000000001"},
    )
    standing_agenda_tick.write_json(
        standing_agenda_tick.ROOM / "rooms" / room_id / "mainline_agenda.json",
        {
            "schema": "openclaw.agent_room.mainline_agenda.v0",
            "room_id": room_id,
            "active_items": [
                {
                    "id": "smoke-mainline-lane",
                    "status": "open",
                    "work_item": "Use linked mainline work item in generated standing brief.",
                    "acceptance_evidence": ["linked mainline acceptance is present"],
                    "must_not_displace": ["existing production workflows"],
                }
            ],
        },
    )
    standing_agenda_tick.write_json(
        standing_agenda_tick.ROOM / "config" / "claude-code-model-policy.json",
        {
            "schema": "openclaw.agent_room.claude_code_model_policy.v0",
            "doubao_family": {"allowed_tails": [], "allowed_route_keys": []},
            "routes": {"workspace_write": {"candidates": ["glm-5.1", "deepseek-v4-pro"]}},
        },
    )
    standing_agenda_tick.append_jsonl(
        standing_agenda_tick.ROOT / "agent-comments" / "claude-code.jsonl",
        [
            {
                "agent_id": "claude-code",
                "run_id": "smoke-routing-success",
                "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "backend": "ark-coding-plan-official-claude-endpoint",
                "model_attempts": [
                    {"model": "glm-5.1", "status": "failed", "ok": False, "reason": "timeout"},
                    {"model": "deepseek-v4-pro", "status": "completed", "ok": True},
                ],
            }
        ],
    )


def tick(*, fresh_task_count: int | None = 0, active_runner_count: int | None = 0, dry_run: bool = False) -> dict[str, Any]:
    return standing_agenda_tick.tick(
        argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=fresh_task_count,
            active_runner_count=active_runner_count,
            dry_run=dry_run,
        )
    )


def main() -> int:
    checks: list[str] = []
    saved_env = {
        "AGENT_ROOM_STANDING_AGENDA_ENABLED": os.environ.get("AGENT_ROOM_STANDING_AGENDA_ENABLED"),
        "AGENT_ROOM_STANDING_MAINLINE_DISCUSSION": os.environ.get("AGENT_ROOM_STANDING_MAINLINE_DISCUSSION"),
    }
    saved_globals = {
        "ROOT": standing_agenda_tick.ROOT,
        "ROOM": standing_agenda_tick.ROOM,
        "CONFIG": standing_agenda_tick.CONFIG,
        "STATE": standing_agenda_tick.STATE,
        "TASKS_JSONL": standing_agenda_tick.TASKS_JSONL,
        "ACTIVE_RUNNERS": standing_agenda_tick.ACTIVE_RUNNERS,
    }
    root = Path(tempfile.mkdtemp(prefix="openclaw-standing-agenda-smoke-"))
    try:
        for name in saved_env:
            os.environ.pop(name, None)
        configure_temp_room(root)
        write_room_fixture()
        write_fixture_config(enabled=False)

        expired_task_id = "standing-smoke-expired-claim"
        expired_ledger_path, expired_archive_path = standing_agenda_tick.collaboration_ledger_paths(expired_task_id)
        standing_agenda_tick.write_json(
            expired_ledger_path,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": expired_task_id,
                "run_id": expired_task_id,
                "status": "open",
                "participants": ["codex", "claude-code"],
                "work_items": [
                    {
                        "id": "wi-codex",
                        "assigned_to": "codex",
                        "claimed_by": "codex",
                        "status": "claimed",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2000-01-01T00:00:00+00:00",
                    },
                    {
                        "id": "wi-claude",
                        "assigned_to": "claude-code",
                        "claimed_by": "claude-code",
                        "status": "completed",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2999-01-01T00:00:00+00:00",
                    },
                ],
                "claims": [
                    {
                        "work_item_id": "wi-codex",
                        "agent_id": "codex",
                        "status": "active",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2000-01-01T00:00:00+00:00",
                    },
                    {
                        "work_item_id": "wi-claude",
                        "agent_id": "claude-code",
                        "status": "completed",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2999-01-01T00:00:00+00:00",
                    },
                ],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
                "points": [],
                "uptakes": [],
                "created_at": "2026-05-29T01:00:00+08:00",
                "updated_at": "2026-05-29T01:01:00+08:00",
            },
        )
        protected_task_id = "standing-smoke-expired-claim-live-runner"
        protected_ledger_path, _protected_archive_path = standing_agenda_tick.collaboration_ledger_paths(protected_task_id)
        standing_agenda_tick.write_json(
            protected_ledger_path,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": protected_task_id,
                "run_id": protected_task_id,
                "status": "open",
                "participants": ["codex"],
                "work_items": [
                    {
                        "id": "wi-codex-live",
                        "assigned_to": "codex",
                        "claimed_by": "codex",
                        "status": "claimed",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2000-01-01T00:00:00+00:00",
                    },
                ],
                "claims": [
                    {
                        "work_item_id": "wi-codex-live",
                        "agent_id": "codex",
                        "status": "active",
                        "claimed_at": "2026-05-29T01:00:00+08:00",
                        "lease_expiry": "2000-01-01T00:00:00+00:00",
                    },
                ],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
                "points": [
                    {
                        "id": "pt-live-material-progress",
                        "agent_id": "codex",
                        "kind": "evidence",
                        "status": "open",
                        "text": "live runner has recorded material progress",
                    }
                ],
                "uptakes": [],
                "created_at": "2026-05-29T01:00:00+08:00",
                "updated_at": "2026-05-29T01:01:00+08:00",
            },
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.active_runner_path("codex", protected_task_id),
            {
                "agent_id": "codex",
                "run_id": protected_task_id,
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "runner_dir": str(root / "live-runner"),
            },
        )
        disabled = tick(fresh_task_count=0, active_runner_count=0)
        expired_ledger = standing_agenda_tick.read_json(expired_ledger_path, {})
        protected_ledger = standing_agenda_tick.read_json(protected_ledger_path, {})
        expired_codex_item = next(item for item in expired_ledger.get("work_items", []) if item.get("id") == "wi-codex")
        expired_codex_claim = next(
            claim for claim in expired_ledger.get("claims", []) if claim.get("work_item_id") == "wi-codex"
        )
        protected_codex_item = next(
            item for item in protected_ledger.get("work_items", []) if item.get("id") == "wi-codex-live"
        )
        protected_codex_claim = next(
            claim for claim in protected_ledger.get("claims", []) if claim.get("work_item_id") == "wi-codex-live"
        )
        check(
            checks,
            "disabled flag leaves daemon-side injection unchanged",
            disabled.get("status") == "disabled"
            and disabled.get("created") is False
            and not standing_agenda_tick.TASKS_JSONL.exists(),
        )
        check(
            checks,
            "standing tick reconciles expired collaboration claim before suppression",
            len(disabled.get("expired_collaboration_claims") or []) == 1
            and expired_ledger.get("status") == "blocked"
            and expired_codex_item.get("status") == "blocked"
            and expired_codex_claim.get("status") == "blocked"
            and expired_archive_path.exists()
            and "release_expired" in expired_archive_path.read_text(encoding="utf-8"),
        )
        check(
            checks,
            "standing tick leaves expired claim alone while live runner exists",
            protected_codex_item.get("status") == "claimed"
            and protected_codex_claim.get("status") == "active",
        )

        write_fixture_config(enabled=True)
        active_runner = tick(fresh_task_count=0, active_runner_count=1)
        active_runner_material_state = active_runner.get("active_runner_material_state") or {}
        check(
            checks,
            "active runner suppresses standing agenda injection",
            active_runner.get("status") == "suppressed_active_runner" and active_runner.get("created") is False,
        )
        check(
            checks,
            "active runner suppression reads material progress from collaboration-ledgers path",
            active_runner_material_state.get("progress_count") == 1
            and active_runner_material_state.get("stall_count") == 0
            and ["codex", protected_task_id] in (active_runner_material_state.get("progress_runners") or []),
        )

        for path in standing_agenda_tick.ACTIVE_RUNNERS.glob("*.json"):
            path.unlink(missing_ok=True)
        now = datetime.now(timezone.utc).astimezone()
        stalled_task_id = "standing-openclaw-evolution-smoke-stalled-no-progress"
        stalled_old = (now - timedelta(seconds=600)).isoformat(timespec="seconds")
        standing_agenda_tick.write_json(
            standing_agenda_tick.active_runner_path("codex", stalled_task_id),
            {
                "agent_id": "codex",
                "run_id": stalled_task_id,
                "pid": os.getpid(),
                "started_at": stalled_old,
                "runner_dir": str(root / "stalled-runner"),
            },
        )
        stalled_bypass = tick(fresh_task_count=0, active_runner_count=1, dry_run=True)
        stalled_material = standing_agenda_tick.material_stall_info_for_output(standing_agenda_tick.material_stall_active_runner_info())
        check(
            checks,
            "active runner without material progress is bypassed after stall threshold so tick can create the next due item",
            stalled_bypass.get("status") == "would_create"
            and stalled_material.get("progress_count") == 0
            and stalled_material.get("stall_count") == 1
            and ["codex", stalled_task_id] in (stalled_material.get("stall_runners") or [])
            and stalled_bypass.get("item_id") == "smoke-proactive-discussion",
        )
        pending_dir = standing_agenda_tick.ROOM / "pending-tasks"
        standing_agenda_tick.write_json(
            pending_dir / "stale.json",
            {
                "task_id": "stale-user-task",
                "run_id": "stale-user-task",
                "created_at": (now - timedelta(seconds=600)).isoformat(timespec="seconds"),
                "source": {"transport": "telegram"},
                "target_agents": ["codex"],
            },
        )
        check(checks, "stale Telegram task is ignored", standing_agenda_tick.fresh_user_task_count(300) == 0)
        standing_agenda_tick.write_json(
            pending_dir / "recent.json",
            {
                "task_id": "recent-user-task",
                "run_id": "recent-user-task",
                "created_at": now.isoformat(timespec="seconds"),
                "source": {"transport": "telegram"},
                "target_agents": ["codex"],
            },
        )
        fresh_suppressed = tick(fresh_task_count=None, active_runner_count=0)
        check(
            checks,
            "fresh Telegram user task suppresses standing agenda injection",
            fresh_suppressed.get("status") == "suppressed_fresh_user_task"
            and fresh_suppressed.get("created") is False,
        )
        for path in pending_dir.glob("*.json"):
            path.unlink()

        dry = tick(fresh_task_count=0, active_runner_count=0, dry_run=True)
        check(
            checks,
            "quiet-period eligibility would create exactly one task",
            dry.get("status") == "would_create"
            and dry.get("item_id") == "smoke-proactive-discussion"
            and dry.get("created") is False,
        )
        dry_selection = dry.get("selection") if isinstance(dry.get("selection"), dict) else {}
        check(
            checks,
            "quiet-period dry run records why the highest-priority due item was selected",
            dry_selection.get("selected_item_id") == "smoke-proactive-discussion"
            and dry_selection.get("selected_reason") in {"never_discussed", "max_silence_elapsed"}
            and "smoke-proactive-discussion" in (dry_selection.get("due_item_ids") or []),
        )
        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        dry_evolution_snapshot_path = standing_agenda_tick.autonomy_self_evolution_snapshot_path(
            standing_agenda_tick.autonomy_self_evolution_policy(config)
        )
        check(
            checks,
            "autonomy self-evolution snapshot path is root-relative, not double agent-room nested",
            dry_evolution_snapshot_path
            == root / "agent-room" / "artifacts" / "mainline-autonomy-evolution-ledger.jsonl",
        )
        dry_evolution_rows = standing_agenda_tick.read_jsonl(dry_evolution_snapshot_path)
        check(
            checks,
            "autonomy self-evolution emits snapshot row for each dry-run selection",
            any(
                (row.get("schema") or "") == "openclaw.agent_room.mainline_autonomy_evolution_snapshot.v0"
                and row.get("selected_item_id") == "smoke-proactive-discussion"
                and bool(row.get("selected_due")) is True
                for row in dry_evolution_rows
            ),
        )
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).extend(
            [
                {
                    "id": "smoke-blocked-mainline-lane",
                    "status": "blocked",
                    "work_item": "Recover explicit blocker before starting ordinary idle work.",
                },
                {
                    "id": "smoke-normal-open-lane",
                    "status": "open",
                    "work_item": "Ordinary idle work remains selectable after blockers clear.",
                },
            ]
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        blocked_mainline_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "items": [
                {
                    "id": "smoke-high-priority-open-mainline",
                    "mainline_item_id": "smoke-normal-open-lane",
                    "title": "Smoke high priority open mainline",
                    "status": "open",
                    "priority": 100,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
                {
                    "id": "smoke-low-priority-blocked-mainline",
                    "mainline_item_id": "smoke-blocked-mainline-lane",
                    "title": "Smoke lower priority blocked mainline",
                    "status": "open",
                    "priority": 1,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
            ],
        }
        blocked_mainline_state = {"schema": "openclaw.agent_room.standing_agenda_state.v0"}
        blocked_mainline_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            blocked_mainline_config,
            blocked_mainline_state,
            bypass_cooldown=True,
        )
        blocked_mainline_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            blocked_mainline_config,
            blocked_mainline_state,
            bypass_cooldown=True,
        )
        blocked_mainline_record = next(
            record
            for record in (blocked_mainline_selection.get("considered_items") or [])
            if record.get("item_id") == "smoke-low-priority-blocked-mainline"
        )
        check(
            checks,
            "blocked mainline item outranks a higher-priority ordinary idle item",
            (
                blocked_mainline_selection.get("selected_item_id") == "smoke-low-priority-blocked-mainline"
                and blocked_mainline_selection.get("selected_selection_class") == "mainline_blocker"
                and blocked_mainline_due_items
                and blocked_mainline_due_items[0].get("id") == "smoke-low-priority-blocked-mainline"
                and blocked_mainline_record.get("mainline_status") == "blocked"
                and blocked_mainline_record.get("mainline_attention") == 1
                and blocked_mainline_record.get("mainline_attention_reason") == "mainline_blocked"
                and ((blocked_mainline_record.get("selection_rank") or {}).get("mainline_attention") == 1)
            ),
        )
        recovery_priority_task_id = "standing-openclaw-evolution-smoke-low-priority-recovery-priority"
        recovery_old = (now - timedelta(seconds=901)).isoformat(timespec="seconds")
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / recovery_priority_task_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": recovery_priority_task_id,
                "run_id": recovery_priority_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "blocked",
                "created_at": recovery_old,
                "updated_at": recovery_old,
                "terminal_state_at": recovery_old,
                "standing_agenda": {"item_id": "smoke-low-priority-recovery"},
                "standing_closure": {
                    "status": "blocked",
                    "reason": "manifest_blocked",
                    "reconciled_at": recovery_old,
                    "source": "smoke_standing_agenda.py",
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        recovery_first_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "failure_recovery": {"enabled": True, "retry_after_seconds": 900},
            "items": [
                {
                    "id": "smoke-high-priority-normal",
                    "title": "Smoke high priority normal due item",
                    "status": "open",
                    "priority": 100,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
                {
                    "id": "smoke-low-priority-recovery",
                    "title": "Smoke lower priority recovery item",
                    "status": "open",
                    "priority": 1,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 300,
                    "max_rounds": 1,
                },
            ],
        }
        recovery_first_state = {"schema": "openclaw.agent_room.standing_agenda_state.v0"}
        recovery_first_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            recovery_first_config,
            {"schema": "openclaw.agent_room.standing_agenda_state.v0"},
            bypass_cooldown=True,
        )
        recovery_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            recovery_first_config,
            recovery_first_state,
            bypass_cooldown=True,
        )
        recovery_record = next(
            record
            for record in (recovery_first_selection.get("considered_items") or [])
            if record.get("item_id") == "smoke-low-priority-recovery"
        )
        check(
            checks,
            "failure recovery outranks a higher-priority ordinary idle item",
            (
                recovery_first_selection.get("selected_item_id") == "smoke-low-priority-recovery"
                and recovery_first_selection.get("selected_reason") == "failure_recovery_due"
                and recovery_first_selection.get("selected_selection_class") == "failure_recovery"
                and recovery_due_items
                and recovery_due_items[0].get("id") == "smoke-low-priority-recovery"
                and ((recovery_record.get("selection_rank") or {}).get("failure_recovery_first") == 1)
                and (recovery_record.get("failure_recovery_state") or {}).get("due") is True
                and ((recovery_record.get("failure_recovery_state") or {}).get("enabled") is True)
                and (recovery_record.get("failure_recovery_state") or {}).get("latest_status") == "blocked"
                and ((recovery_record.get("failure_recovery_state") or {}).get("remaining_seconds") == 0)
            ),
        )

        recovery_and_mainline_blocked_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "failure_recovery": {"enabled": True, "retry_after_seconds": 900},
            "items": [
                {
                    "id": "smoke-low-priority-recovery-mainline-blocked",
                    "mainline_item_id": "smoke-blocked-mainline-lane",
                    "title": "Smoke recovery item also blocked in mainline",
                    "status": "open",
                    "priority": 1,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 300,
                    "max_rounds": 1,
                },
                {
                    "id": "smoke-high-priority-open-mainline",
                    "mainline_item_id": "smoke-normal-open-lane",
                    "title": "Smoke high priority idle mainline",
                    "status": "open",
                    "priority": 100,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
            ],
        }
        recovery_and_blocked_item = "smoke-low-priority-recovery-mainline-blocked"
        recovery_and_blocked_old = (now - timedelta(seconds=901)).isoformat(timespec="seconds")
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / f"{recovery_and_blocked_item}-manifest" / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": recovery_and_blocked_item,
                "run_id": recovery_and_blocked_item,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "failed",
                "created_at": recovery_and_blocked_old,
                "updated_at": recovery_and_blocked_old,
                "standing_agenda": {"item_id": recovery_and_blocked_item},
                "standing_closure": {
                    "status": "blocked",
                    "reason": "smoke_recovery_and_mainline_blocked",
                    "reconciled_at": recovery_and_blocked_old,
                    "source": "smoke_standing_agenda.py",
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        recovery_and_blocked_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            recovery_and_mainline_blocked_config,
            {"schema": "openclaw.agent_room.standing_agenda_state.v0"},
            bypass_cooldown=True,
        )
        recovery_and_blocked_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            recovery_and_mainline_blocked_config,
            {"schema": "openclaw.agent_room.standing_agenda_state.v0"},
            bypass_cooldown=True,
        )
        recovery_and_blocked_record = next(
            record
            for record in (recovery_and_blocked_selection.get("considered_items") or [])
            if record.get("item_id") == recovery_and_blocked_item
        )
        check(
            checks,
            "failure recovery class wins when same item is also mainline blocked",
            (
                recovery_and_blocked_selection.get("selected_item_id") == recovery_and_blocked_item
                and recovery_and_blocked_selection.get("selected_selection_class") == "failure_recovery"
                and (recovery_and_blocked_record.get("selection_rank") or {}).get("failure_recovery_first") == 1
                and recovery_and_blocked_record.get("mainline_attention") == 1
                and recovery_and_blocked_record.get("mainline_attention_reason") == "mainline_blocked"
                and recovery_and_blocked_record.get("failure_recovery_state")
                and (recovery_and_blocked_record.get("failure_recovery_state") or {}).get("due") is True
                and recovery_and_blocked_due_items
                and recovery_and_blocked_due_items[0].get("id") == recovery_and_blocked_item
            ),
        )

        cooldown_state = {
            "schema": "openclaw.agent_room.standing_agenda_state.v0",
            "last_injected_at": (now - timedelta(seconds=180)).isoformat(timespec="seconds"),
            "items": {},
        }
        cooldown_mix_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "failure_recovery": {"enabled": True, "retry_after_seconds": 900},
            "items": [
                {
                    "id": "smoke-high-priority-blocked-mainline",
                    "mainline_item_id": "smoke-blocked-mainline-lane",
                    "title": "Smoke high priority blocked mainline",
                    "status": "open",
                    "priority": 100,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
                {
                    "id": "smoke-low-priority-cooldown-normal",
                    "title": "Smoke lower priority cooldown-normal item",
                    "status": "open",
                    "priority": 1,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 300,
                    "max_rounds": 1,
                },
            ],
        }
        cooldown_mix_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            cooldown_mix_config,
            cooldown_state,
            bypass_cooldown=False,
        )
        cooldown_mix_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            cooldown_mix_config,
            cooldown_state,
            bypass_cooldown=False,
        )
        cooldown_mix_recovery_record = next(
            record
            for record in (cooldown_mix_selection.get("considered_items") or [])
            if record.get("item_id") == "smoke-low-priority-cooldown-normal"
        )
        cooldown_mix_blocked_record = next(
            record
            for record in (cooldown_mix_selection.get("considered_items") or [])
            if record.get("item_id") == "smoke-high-priority-blocked-mainline"
        )
        check(
            checks,
            "blocked mainline attention outranks global cooldown when not in failure recovery",
            (
                cooldown_mix_selection.get("selected_item_id") == "smoke-high-priority-blocked-mainline"
                and cooldown_mix_selection.get("selected_selection_class") == "mainline_blocker"
                and cooldown_mix_selection.get("selected_reason") == "never_discussed"
                and cooldown_mix_due_items
                and cooldown_mix_due_items[0].get("id") == "smoke-high-priority-blocked-mainline"
                and cooldown_mix_recovery_record.get("due") is False
                and cooldown_mix_blocked_record.get("reason") == "never_discussed"
                and cooldown_mix_blocked_record.get("mainline_attention") == 1
                and cooldown_mix_blocked_record.get("mainline_attention_reason") == "mainline_blocked"
                and cooldown_mix_blocked_record.get("due") is True
            ),
        )

        accepted_blocker_task_id = "smoke-accepted-blocked-mainline"
        accepted_blocked_ledger_path, _accepted_finished = standing_agenda_tick.collaboration_ledger_paths(accepted_blocker_task_id)
        standing_agenda_tick.write_json(
            accepted_blocked_ledger_path,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": accepted_blocker_task_id,
                "run_id": accepted_blocker_task_id,
                "status": "completed",
                "participants": ["codex", "claude-code"],
                "work_items": [
                    {
                        "id": "smoke-accepted-marker",
                        "agent_id": "codex",
                        "status": "completed",
                        "acceptance": "accepted",
                    },
                ],
                "claims": [],
                "artifacts": [],
                "handoffs": [],
                "blockers": [],
                "points": [],
                "uptakes": [],
                "created_at": now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
            },
        )
        accepted_blocker_state = {
            "schema": "openclaw.agent_room.standing_agenda_state.v0",
            "items": {
                "smoke-accepted-blocked-mainline": {
                    "last_discussed_task_id": accepted_blocker_task_id,
                }
            },
        }
        accepted_blocker_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "items": [
                {
                    "id": "smoke-high-priority-accepted-closed",
                    "title": "Smoke accepted normal item should not hide blocked mainline",
                    "status": "open",
                    "priority": 100,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
                {
                    "id": "smoke-accepted-blocked-mainline",
                    "mainline_item_id": "smoke-blocked-mainline-lane",
                    "title": "Smoke blocked mainline item with prior accepted outcome",
                    "status": "open",
                    "priority": 1,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                    "max_rounds": 1,
                },
            ],
        }
        accepted_blocker_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            accepted_blocker_config,
            accepted_blocker_state,
            bypass_cooldown=True,
        )
        accepted_blocker_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            accepted_blocker_config,
            accepted_blocker_state,
            bypass_cooldown=True,
        )
        accepted_blocker_record = next(
            record
            for record in (accepted_blocker_selection.get("considered_items") or [])
            if record.get("item_id") == "smoke-accepted-blocked-mainline"
        )
        check(
            checks,
            "accepted normal completion should not suppress blocked mainline attention",
            (
                accepted_blocker_selection.get("selected_item_id") == "smoke-accepted-blocked-mainline"
                and accepted_blocker_selection.get("selected_selection_class") == "mainline_blocker"
                and accepted_blocker_record.get("mainline_attention") == 1
                and accepted_blocker_record.get("reason") == "never_discussed"
                and accepted_blocker_record.get("due") is True
                and accepted_blocker_due_items
                and accepted_blocker_due_items[0].get("id") == "smoke-accepted-blocked-mainline"
            ),
        )

        big_list_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 300,
            "failure_recovery": {"enabled": True, "retry_after_seconds": 900},
            "items": [],
        }
        for i in range(13):
            big_list_config["items"].append(
                {
                    "id": f"smoke-trimmed-healthy-{i:02d}",
                    "title": f"Smoke trimmed healthy item {i:02d}",
                    "status": "open",
                    "priority": 200 - i,
                    "target_agents": ["codex", "claude-code"],
                    "max_silence_seconds": 0,
                }
            )
        recovery_tail_task_id = "smoke-trimmed-recovery-tail"
        recovery_tail_old = (now - timedelta(seconds=901)).isoformat(timespec="seconds")
        big_list_config["items"].append(
            {
                "id": recovery_tail_task_id,
                "title": "Smoke recovery item beyond preview window",
                "status": "open",
                "priority": 1,
                "target_agents": ["codex", "claude-code"],
                "max_silence_seconds": 900,
            },
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / recovery_tail_task_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": recovery_tail_task_id,
                "run_id": recovery_tail_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "failed",
                "created_at": recovery_tail_old,
                "updated_at": recovery_tail_old,
                "standing_agenda": {"item_id": recovery_tail_task_id},
                "standing_closure": {
                    "status": "failed",
                    "reason": "smoke_tail_recovery",
                    "reconciled_at": recovery_tail_old,
                    "source": "smoke_standing_agenda.py",
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        trimmed_state = {"schema": "openclaw.agent_room.standing_agenda_state.v0"}
        trimmed_selection = standing_agenda_tick.standing_item_selection_snapshot(
            "openclaw-evolution",
            big_list_config,
            trimmed_state,
            bypass_cooldown=True,
        )
        trimmed_due_items = standing_agenda_tick.due_items(
            "openclaw-evolution",
            big_list_config,
            trimmed_state,
            bypass_cooldown=True,
        )
        trimmed_recovery_record = next(
            record
            for record in (trimmed_selection.get("considered_items") or [])
            if record.get("item_id") == recovery_tail_task_id
        )
        check(
            checks,
            "selected record is preserved even when selected item is outside top 12 preview window",
            (
                trimmed_selection.get("selected_item_id") == recovery_tail_task_id
                and trimmed_selection.get("selected_reason") == "failure_recovery_due"
                and trimmed_selection.get("selected_selection_class") == "failure_recovery"
                and ((trimmed_recovery_record.get("selection_rank") or {}).get("failure_recovery_first") == 1)
                and (trimmed_recovery_record.get("failure_recovery_state") or {}).get("due") is True
                and ((trimmed_recovery_record.get("failure_recovery_state") or {}).get("enabled") is True)
                and trimmed_due_items
                and trimmed_due_items[0].get("id") == recovery_tail_task_id
            ),
        )

        created = tick(fresh_task_count=0, active_runner_count=0)
        manifest_path = Path(str(created.get("manifest_path") or ""))
        task = standing_agenda_tick.read_json(manifest_path, {})
        brief = manifest_path.with_name("brief.md").read_text(encoding="utf-8") if manifest_path.exists() else ""
        rows = standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)
        check(
            checks,
            "enabled fixture creates one internal standing-mainline task",
            created.get("created") is True
            and len(rows) == 1
            and rows[0].get("task_id") == created.get("task_id")
            and ((task.get("source") or {}).get("transport") == "agent-room-standing-mainline"),
        )
        check(
            checks,
            "generated brief links mainline work item and acceptance",
            "Use linked mainline work item in generated standing brief." in brief
            and "linked mainline acceptance is present" in brief,
        )
        config_only_brief = standing_agenda_tick.build_brief(
            "openclaw-evolution",
            {
                "id": "smoke-config-only-mainline",
                "title": "Smoke config-only mainline",
                "description": "Use standing config when no room mainline item exists.",
                "work_item": "Config-only work item survives brief generation.",
                "acceptance_evidence": ["config-only acceptance evidence is present"],
                "must_not_displace": ["config-only boundary is present"],
            },
            {},
        )
        check(
            checks,
            "config-only standing item keeps work item, acceptance, and boundary in generated brief",
            "Config-only work item survives brief generation." in config_only_brief
            and "config-only acceptance evidence is present" in config_only_brief
            and "config-only boundary is present" in config_only_brief,
        )
        alias_agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        alias_agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-canonical-mainline",
                "aliases": ["smoke-standing-alias"],
                "status": "open",
                "work_item": "Resolve standing agenda aliases to canonical mainline items.",
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            alias_agenda,
        )
        alias_match = standing_agenda_tick.matching_mainline_item("openclaw-evolution", "smoke-standing-alias")
        alias_advance = standing_agenda_tick.advance_mainline_item(
            "openclaw-evolution",
            "smoke-standing-alias",
            status="in_progress",
            evidence_paths=["agent-room/tasks/smoke-standing-alias/manifest.json"],
            note="alias resolution smoke",
            source={"tool": "smoke_standing_agenda.py"},
        )
        alias_after = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        alias_item_after = next(
            (entry for entry in alias_after.get("active_items") or [] if entry.get("id") == "smoke-canonical-mainline"),
            {},
        )
        check(
            checks,
            "standing agenda resolves mainline aliases before sync",
            alias_match.get("id") == "smoke-canonical-mainline"
            and alias_advance.get("ok") is True
            and alias_item_after.get("status") == "in_progress"
            and "agent-room/tasks/smoke-standing-alias/manifest.json" in (alias_item_after.get("evidence_paths") or []),
        )
        agenda_after_create = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        mainline_after_create = (agenda_after_create.get("active_items") or [{}])[0]
        standing_artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
        routing_artifact = next(
            (artifact for artifact in standing_artifacts if artifact.get("type") == "model_routing_reliability_snapshot"),
            {},
        )
        routing_artifact_path = str(routing_artifact.get("path") or "")
        check(
            checks,
            "created standing task advances linked mainline item with evidence",
            mainline_after_create.get("status") == "in_progress"
            and isinstance(mainline_after_create.get("updated_at"), str)
            and f"agent-room/tasks/{created.get('task_id')}/manifest.json" in (mainline_after_create.get("evidence_paths") or [])
            and f"agent-room/tasks/{created.get('task_id')}/brief.md" in (mainline_after_create.get("evidence_paths") or [])
            and isinstance(mainline_after_create.get("status_history"), list),
        )
        check(
            checks,
            "standing artifact hook writes model-routing reliability snapshot into manifest and mainline evidence",
            bool(routing_artifact_path)
            and (standing_agenda_tick.ROOT / routing_artifact_path).exists()
            and routing_artifact_path in (task.get("result_paths") or [])
            and routing_artifact_path in (mainline_after_create.get("evidence_paths") or [])
            and not task.get("standing_artifact_hook_errors"),
        )
        check(
            checks,
            "standing manifest carries mainline governance and drift-check fields",
            task.get("mainline_id") == "smoke-mainline-lane"
            and task.get("problem_statement")
            and task.get("expected_user_value")
            and task.get("owner") == "openclaw-main"
            and "openclaw-main" in (task.get("participants") or [])
            and task.get("definition_of_done")
            and isinstance(task.get("approval_gate"), dict)
            and task.get("dedupe_key") == "smoke-mainline-lane"
            and task.get("governance_state") == "execute"
            and task.get("governance_contract_path") == "agent-room/methodology/mainline-governance-contract-20260528.md"
            and task.get("drift_check_passed") is True
            and "drift_check" not in task,
        )
        manifest_selection = ((task.get("standing_mainline") or {}).get("selection") or {})
        check(
            checks,
            "standing manifest persists item selection evidence for autonomous execution loop",
            manifest_selection.get("item_id") == "smoke-proactive-discussion"
            and manifest_selection.get("due") is True
            and ((task.get("standing_agenda") or {}).get("selection_reason") == manifest_selection.get("reason")),
        )
        duplicate_direct = standing_agenda_tick.create_task(
            "openclaw-evolution",
            (standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {}).get("items") or [{}])[0],
            standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {}),
            standing_agenda_tick.read_json(standing_agenda_tick.STATE, {}),
        )
        check(
            checks,
            "standing duplicate is merged by dedupe_key before creating another task",
            duplicate_direct.get("dedupe_merged") is True
            and duplicate_direct.get("status") == "merged"
            and duplicate_direct.get("dedupe_key") == "smoke-mainline-lane"
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 1,
        )
        advanced = standing_agenda_tick.advance_mainline_item(
            "openclaw-evolution",
            "smoke-mainline-lane",
            status="in_review",
            evidence_paths=["agent-room/artifacts/smoke-review.md"],
            note="smoke review complete",
            source={"agent_id": "codex", "run_id": str(created.get("run_id") or "")},
        )
        agenda_after_manual = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        mainline_after_manual = (agenda_after_manual.get("active_items") or [{}])[0]
        check(
            checks,
            "manual mainline advancement records status, evidence, and history",
            advanced.get("ok") is True
            and mainline_after_manual.get("status") == "in_review"
            and "agent-room/artifacts/smoke-review.md" in (mainline_after_manual.get("evidence_paths") or [])
            and (mainline_after_manual.get("status_history") or [])[-1].get("note") == "smoke review complete",
        )
        check(
            checks,
            "manifest has max_rounds, standing-local collaboration tick, lease, and one-attempt bounds",
            ((task.get("standing_agenda") or {}).get("max_rounds") == 1)
            and ((task.get("standing_agenda") or {}).get("round") == 1)
            and ((task.get("collaboration_tick") or {}).get("enabled") is True)
            and ((task.get("collaboration_tick") or {}).get("max_rounds") == 2)
            and task.get("collab_tick_enabled") is True
            and task.get("collab_tick_max_rounds") == 2
            and ((task.get("collaboration") or {}).get("max_rounds") == 2)
            and ((task.get("retry_budget") or {}).get("max_attempts") == 1)
            and isinstance(task.get("lease"), dict),
        )
        governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
        check(
            checks,
            "manifest binds standing task to mainline governance contract",
            governance.get("mainline_id") == "smoke-mainline-lane"
            and task.get("mainline_id") == governance.get("mainline_id")
            and task.get("dedupe_key") == governance.get("dedupe_key") == "smoke-mainline-lane"
            and task.get("participants") == ["openclaw-main", "codex", "claude-code"]
            and isinstance(task.get("approval_gate"), dict)
            and task["approval_gate"].get("required") is False
            and task.get("governance_state") == "execute",
        )
        cooldown_remaining = standing_agenda_tick.cooldown_remaining_seconds(
            standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {}),
            standing_agenda_tick.read_json(standing_agenda_tick.STATE, {}),
        )
        check(
            checks,
            "configured proactive interval controls cooldown window",
            0 < cooldown_remaining <= 300,
        )

        material_comment = {
            "agent_id": "codex",
            "body": "smoke material progress with a bounded verification result",
            "telegram_projection_status": "ready",
        }
        may_project, projection_mode = resident.telegram_projection_decision(task, [material_comment])
        gated_task = dict(task)
        gated_task["standing_visible_allowed"] = False
        gated_project, gated_reason = resident.telegram_projection_decision(gated_task, [material_comment])
        check(
            checks,
            "Telegram projection is concise internal summary when explicitly allowed",
            may_project and projection_mode == "internal-summary",
        )
        check(
            checks,
            "Telegram projection is gated without explicit standing visibility",
            (not gated_project) and gated_reason == "standing_mainline_projection_not_explicit",
        )

        for agent_id in ("codex", "claude-code"):
            standing_agenda_tick.write_json(
                standing_agenda_tick.ROOM / "telegram-agent-reply" / f"{agent_id}-{created.get('run_id')}.json",
                {"agent_id": agent_id, "run_id": created.get("run_id"), "suppressed_reason": "smoke_local_only"},
            )
        duplicate = tick(fresh_task_count=0, active_runner_count=0)
        check(
            checks,
            "resolved pending task bypasses cooldown but max_rounds prevents self-amplifying loops",
            duplicate.get("status") == "suppressed_max_rounds"
            and duplicate.get("created") is False
            and duplicate.get("resolved_pending_task_id") == created.get("task_id")
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 1,
        )

        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        config.setdefault("items", []).append(
            {
                "id": "smoke-continuation-next",
                "mainline_item_id": "smoke-continuation-lane",
                "title": "Smoke continuation next",
                "description": "Create the next standing item after a previous pending task was already resolved.",
                "status": "open",
                "priority": 20,
                "target_agents": ["codex", "claude-code"],
                "max_silence_seconds": 0,
                "max_rounds": 1,
            }
        )
        standing_agenda_tick.write_json(standing_agenda_tick.CONFIG, config)
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-continuation-lane",
                "status": "open",
                "work_item": "Verify post-completion rescan survives into the next scheduler tick.",
                "acceptance_evidence": ["post-completion rescan creates the next due standing item"],
                "must_not_displace": ["fresh user tasks", "active runners", "pending standing tasks"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        continuation = tick(fresh_task_count=0, active_runner_count=0, dry_run=True)
        check(
            checks,
            "post-completion rescan survives resolved-pending state and would create next due item",
            continuation.get("status") == "would_create"
            and continuation.get("created") is False
            and continuation.get("item_id") == "smoke-continuation-next"
            and (continuation.get("post_completion_idle_rescan") or {}).get("due") is True
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 1,
        )
        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        config["items"] = [
            item
            for item in (config.get("items") or [])
            if not (isinstance(item, dict) and item.get("id") == "smoke-continuation-next")
        ]
        standing_agenda_tick.write_json(standing_agenda_tick.CONFIG, config)

        state = standing_agenda_tick.read_json(standing_agenda_tick.STATE, {})
        old = (now - timedelta(seconds=900)).isoformat(timespec="seconds")
        state["last_injected_at"] = old
        if isinstance(state.get("items"), dict) and isinstance(state["items"].get("smoke-proactive-discussion"), dict):
            state["items"]["smoke-proactive-discussion"]["last_discussed_at"] = old
        standing_agenda_tick.write_json(standing_agenda_tick.STATE, state)
        capped = tick(fresh_task_count=0, active_runner_count=0)
        check(
            checks,
            "max_rounds suppresses self-amplifying loops after cooldown",
            capped.get("status") == "suppressed_max_rounds"
            and capped.get("created") is False
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 1,
        )

        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        config["proactive_tick_interval_seconds"] = 1800
        config["failure_recovery"] = {"enabled": True, "retry_after_seconds": 900}
        config.setdefault("items", []).append(
            {
                "id": "smoke-failure-recovery",
                "mainline_item_id": "smoke-failure-lane",
                "title": "Smoke failure recovery",
                "description": "Retry a blocked standing task before the quiet-period cooldown expires.",
                "status": "open",
                "priority": 20,
                "target_agents": ["codex", "claude-code"],
                "max_silence_seconds": 1800,
                "max_rounds": 1,
            }
        )
        standing_agenda_tick.write_json(standing_agenda_tick.CONFIG, config)
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-failure-lane",
                "status": "open",
                "work_item": "Retry failure recovery smoke item.",
                "acceptance_evidence": ["failure recovery bypasses quiet-period cooldown"],
                "must_not_displace": ["fresh user tasks", "active runners"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        failure_old = (now - timedelta(seconds=901)).isoformat(timespec="seconds")
        failure_task_id = "standing-openclaw-evolution-smoke-failure-recovery"
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / failure_task_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": failure_task_id,
                "run_id": failure_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "blocked",
                "created_at": failure_old,
                "updated_at": failure_old,
                "standing_agenda": {"item_id": "smoke-failure-recovery"},
                "standing_closure": {
                    "status": "blocked",
                    "reason": "manifest_blocked",
                    "reconciled_at": failure_old,
                    "source": "smoke_standing_agenda.py",
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        state = standing_agenda_tick.read_json(standing_agenda_tick.STATE, {})
        state["last_injected_at"] = failure_old
        state.setdefault("items", {})["smoke-failure-recovery"] = {
            "last_discussed_at": failure_old,
            "last_discussed_task_id": failure_task_id,
        }
        state["pending_task"] = None
        standing_agenda_tick.write_json(standing_agenda_tick.STATE, state)
        # A completed task without material evidence should not count toward the
        # max-round cap.  Failure recovery must still bypass the quiet-period
        # cooldown, otherwise a failed/blocked mainline can silently turn into
        # idle instead of a concrete RCA or retry work item.
        completed_recent = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
        completed_task_id = "standing-openclaw-evolution-smoke-failure-recovery-completed-recent"
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / completed_task_id / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": completed_task_id,
                "run_id": completed_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "completed",
                "created_at": completed_recent,
                "updated_at": completed_recent,
                "standing_agenda": {"item_id": "smoke-failure-recovery"},
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        recovery_record = standing_agenda_tick.standing_item_selection_record(
            "openclaw-evolution",
            standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {}),
            standing_agenda_tick.read_json(standing_agenda_tick.STATE, {}),
            next(
                item for item in standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {}).get("items", [])
                if item.get("id") == "smoke-failure-recovery"
            ),
            now,
        )
        recovery = tick(fresh_task_count=0, active_runner_count=0)
        recovery_selection = recovery.get("selection") if isinstance(recovery.get("selection"), dict) else {}
        check(
            checks,
            "failed standing task creates retry before quiet-period cooldown when recent completion has no evidence",
            recovery.get("status") == "created"
            and recovery.get("created") is True
            and recovery.get("item_id") == "smoke-failure-recovery"
            and recovery_selection.get("selected_reason") == "failure_recovery_due"
            and recovery_record.get("global_cooldown_active") is True
            and recovery_record.get("max_rounds_reached") is False
            and recovery_record.get("max_rounds_bypassed_for_recovery") is False
            and ((recovery_record.get("failure_recovery_state") or {}).get("degraded_rounds") == 1),
        )

        resolved_recovery_old = (now - timedelta(seconds=1200)).isoformat(timespec="seconds")
        resolved_recovery_recent = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
        resolved_item = {
            "id": "smoke-resolved-recovery-history",
            "title": "Smoke resolved recovery history",
            "status": "open",
            "priority": 10,
            "target_agents": ["codex", "claude-code"],
            "max_silence_seconds": 1800,
            "max_rounds": 3,
        }
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / "standing-smoke-resolved-recovery-old-failed" / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": "standing-smoke-resolved-recovery-old-failed",
                "run_id": "standing-smoke-resolved-recovery-old-failed",
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "failed",
                "created_at": resolved_recovery_old,
                "updated_at": resolved_recovery_old,
                "terminal_state_at": resolved_recovery_old,
                "standing_agenda": {"item_id": "smoke-resolved-recovery-history"},
                "standing_closure": {
                    "status": "blocked",
                    "outcome": "failed_with_rca",
                    "reason": "smoke_old_failure",
                    "reconciled_at": resolved_recovery_old,
                    "source": "smoke_standing_agenda.py",
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "tasks" / "standing-smoke-resolved-recovery-new-completed" / "manifest.json",
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": "standing-smoke-resolved-recovery-new-completed",
                "run_id": "standing-smoke-resolved-recovery-new-completed",
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "completed",
                "created_at": resolved_recovery_recent,
                "updated_at": resolved_recovery_recent,
                "terminal_state_at": resolved_recovery_recent,
                "standing_agenda": {"item_id": "smoke-resolved-recovery-history"},
                "standing_closure": {
                    "status": "completed",
                    "outcome": "completed_with_evidence",
                    "reason": "smoke_completed_with_evidence",
                    "reconciled_at": resolved_recovery_recent,
                    "source": "smoke_standing_agenda.py",
                },
                "artifacts": [{"path": "agent-room/artifacts/smoke-resolved-recovery-history.md"}],
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        resolved_state = {
            "schema": "openclaw.agent_room.standing_agenda_state.v0",
            "items": {
                "smoke-resolved-recovery-history": {
                    "last_discussed_at": resolved_recovery_recent,
                    "last_discussed_task_id": "standing-smoke-resolved-recovery-new-completed",
                }
            },
        }
        resolved_config = {
            "schema": "openclaw.agent_room.standing_agenda.v0",
            "enabled": True,
            "proactive_tick_interval_seconds": 1800,
            "failure_recovery": {"enabled": True, "retry_after_seconds": 900},
            "items": [resolved_item],
        }
        resolved_record = standing_agenda_tick.standing_item_selection_record(
            "openclaw-evolution",
            resolved_config,
            resolved_state,
            resolved_item,
            now,
            bypass_cooldown=True,
        )
        resolved_failure_state = resolved_record.get("failure_recovery_state") or {}
        check(
            checks,
            "material completion after retryable history suppresses stale failure recovery",
            resolved_record.get("due") is False
            and resolved_record.get("reason") == "silence_window_not_elapsed"
            and resolved_record.get("recovery_due") is False
            and resolved_failure_state.get("due") is False
            and resolved_failure_state.get("reason") == "latest_material_completed_after_retryable_history"
            and resolved_failure_state.get("latest_status") == "completed",
        )

        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        config.setdefault("items", []).append(
            {
                "id": "smoke-blocked-closure",
                "mainline_item_id": "smoke-blocker-lane",
                "title": "Smoke blocked closure propagation",
                "description": "Propagate blocked standing closure to the linked mainline agenda item.",
                "status": "open",
                "priority": 10,
                "target_agents": ["codex", "claude-code"],
                "max_silence_seconds": 1800,
                "max_rounds": 1,
            }
        )
        standing_agenda_tick.write_json(standing_agenda_tick.CONFIG, config)
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-blocker-lane",
                "status": "in_review",
                "work_item": "Verify standing closure propagation for blocked tasks.",
                "acceptance_evidence": ["blocked closure advances mainline agenda to blocked"],
                "must_not_displace": ["standing task creation and recovery selection"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )

        blocked_task_id = "standing-openclaw-evolution-smoke-blocked"
        blocked_manifest = standing_agenda_tick.ROOM / "tasks" / blocked_task_id / "manifest.json"
        blocked_task = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": blocked_task_id,
            "run_id": blocked_task_id,
            "room_id": "openclaw-evolution",
            "target_agents": ["codex", "claude-code"],
            "status": "blocked",
            "created_at": old,
            "updated_at": old,
            "standing_agenda": {"item_id": "smoke-blocked-closure"},
            "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "mode": "standing_mainline_discussion",
                "status": "open",
                "participants": ["codex", "claude-code"],
                "work_items": [
                    {"id": "smoke_codex", "status": "open", "assigned_to": "codex"},
                    {"id": "smoke_claude", "status": "open", "assigned_to": "claude-code"},
                ],
                "claims": [],
                "handoffs": [],
                "artifacts": [],
                "blockers": [],
                "created_at": old,
            },
        }
        standing_agenda_tick.write_json(blocked_manifest, blocked_task)
        ledger_path, archive_path = standing_agenda_tick.collaboration_ledger_paths(blocked_task_id)
        ledger = dict(blocked_task["collaboration"])
        ledger.update(
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": blocked_task_id,
                "run_id": blocked_task_id,
                "turn_seq": None,
                "updated_at": old,
            }
        )
        standing_agenda_tick.write_json(ledger_path, ledger)
        dry_reconciled = standing_agenda_tick.reconcile_standing_task_statuses(limit=10, dry_run=True)
        dry_manifest_after = standing_agenda_tick.read_json(blocked_manifest, {})
        dry_agenda_after = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        dry_mainline_after = next(
            item
            for item in dry_agenda_after.get("active_items") or []
            if item.get("id") == "smoke-blocker-lane"
        )
        check(
            checks,
            "standing closure dry-run reports without mutating manifest",
            any(item.get("task_id") == blocked_task_id for item in dry_reconciled)
            and ((dry_manifest_after.get("collaboration") or {}).get("status") == "open")
            and dry_mainline_after.get("status") == "in_review"
            and any(
                item.get("task_id") == blocked_task_id
                and ((item.get("mainline_sync") or {}).get("status") == "would_advance_mainline_item")
                for item in dry_reconciled
            ),
        )
        reconciled = standing_agenda_tick.reconcile_standing_task_statuses(limit=10, dry_run=False)
        closed_manifest = standing_agenda_tick.read_json(blocked_manifest, {})
        closed_ledger = standing_agenda_tick.read_json(ledger_path, {})
        agenda_after_closure = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        mainline_after_closure = next(
            item
            for item in agenda_after_closure.get("active_items") or []
            if item.get("id") == "smoke-blocker-lane"
        )
        archive_rows = standing_agenda_tick.read_jsonl(archive_path)
        check(
            checks,
            "blocked standing task reconciliation closes manifest and collaboration ledger",
            any(item.get("task_id") == blocked_task_id and item.get("ledger_updated") for item in reconciled)
            and ((closed_manifest.get("collaboration") or {}).get("status") == "blocked")
            and ((closed_ledger.get("status") or "") == "blocked")
            and any(row.get("event_type") == "standing_closure_reconcile" for row in archive_rows),
        )
        check(
            checks,
            "standing closure propagates blocker status and evidence to mainline agenda",
            any(
                item.get("task_id") == blocked_task_id
                and ((item.get("mainline_sync") or {}).get("status") == "mainline_item_advanced")
                for item in reconciled
            )
            and mainline_after_closure.get("status") == "blocked"
            and "agent-room/tasks/standing-openclaw-evolution-smoke-blocked/manifest.json" in (mainline_after_closure.get("evidence_paths") or [])
            and "agent-room/collaboration-ledgers/standing-openclaw-evolution-smoke-blocked.json" in (mainline_after_closure.get("evidence_paths") or [])
            and str(mainline_after_closure.get("status_note") or "").startswith("standing closure blocked_with_owner:"),
        )

        config = standing_agenda_tick.read_json(standing_agenda_tick.CONFIG, {})
        config.setdefault("items", []).append(
            {
                "id": "smoke-standing-config-blocked-completed",
                "mainline_item_id": "smoke-standing-config-blocked-lane",
                "title": "Smoke blocked standing config beats old completion",
                "description": "A paused standing item must not reopen its linked mainline item from old completed manifests.",
                "status": "blocked",
                "blocked_reason": "smoke standing item paused",
                "pause_evidence": ["agent-room/artifacts/smoke-standing-config-paused.md"],
                "priority": 10,
                "target_agents": ["codex", "claude-code"],
                "max_silence_seconds": 1800,
            }
        )
        standing_agenda_tick.write_json(standing_agenda_tick.CONFIG, config)
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-standing-config-blocked-lane",
                "status": "blocked",
                "work_item": "Verify blocked standing config cannot reopen linked mainline.",
                "acceptance_evidence": ["old completed standing manifests keep mainline blocked"],
                "must_not_displace": ["standing task closure evidence gate"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        config_blocked_task_id = "standing-openclaw-evolution-smoke-config-blocked-completed"
        config_blocked_manifest = standing_agenda_tick.ROOM / "tasks" / config_blocked_task_id / "manifest.json"
        standing_agenda_tick.write_json(
            config_blocked_manifest,
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": config_blocked_task_id,
                "run_id": config_blocked_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "completed",
                "created_at": old,
                "updated_at": old,
                "terminal_state_at": old,
                "standing_agenda": {"item_id": "smoke-standing-config-blocked-completed"},
                "artifacts": [{"path": "agent-room/artifacts/smoke-config-blocked-completion.md"}],
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        config_blocked_reconciled = standing_agenda_tick.reconcile_standing_task_statuses(limit=10, dry_run=False)
        config_blocked_agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        config_blocked_mainline = next(
            item
            for item in config_blocked_agenda.get("active_items") or []
            if item.get("id") == "smoke-standing-config-blocked-lane"
        )
        check(
            checks,
            "blocked standing config prevents old completed closure from reopening mainline",
            any(
                item.get("task_id") == config_blocked_task_id
                and ((item.get("mainline_sync") or {}).get("status") == "mainline_item_advanced")
                for item in config_blocked_reconciled
            )
            and config_blocked_mainline.get("status") == "blocked"
            and str(config_blocked_mainline.get("status_note") or "").startswith("standing agenda item blocked:")
            and "agent-room/artifacts/smoke-standing-config-paused.md" in (config_blocked_mainline.get("evidence_paths") or []),
        )

        empty_completed_task_id = "standing-openclaw-evolution-smoke-empty-completed"
        empty_completed_manifest = standing_agenda_tick.ROOM / "tasks" / empty_completed_task_id / "manifest.json"
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-empty-completed-lane",
                "status": "in_progress",
                "work_item": "Verify empty completion cannot satisfy material closure.",
                "acceptance_evidence": ["actual patch, artifact, smoke, RCA, blocker, or material ledger point"],
                "must_not_displace": ["standing task closure evidence gate"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        standing_agenda_tick.write_json(
            empty_completed_manifest,
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": empty_completed_task_id,
                "run_id": empty_completed_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "status": "completed",
                "created_at": old,
                "updated_at": old,
                "terminal_state_at": old,
                "mainline_id": "smoke-empty-completed-lane",
                "acceptance_evidence": ["planned smoke output is not produced evidence"],
                "governance": {
                    "definition_of_done": [
                        "Produce a patch, artifact, smoke result, RCA, blocker, or verified state transition."
                    ],
                },
                "collaboration": {
                    "schema": "openclaw.agent_room.collaboration.v0",
                    "mode": "standing_mainline_discussion",
                    "status": "open",
                    "participants": ["codex", "claude-code"],
                    "work_items": [
                        {"id": "empty_codex", "status": "open", "assigned_to": "codex"},
                        {"id": "empty_claude", "status": "open", "assigned_to": "claude-code"},
                    ],
                    "claims": [],
                    "handoffs": [],
                    "artifacts": [],
                    "blockers": [],
                    "created_at": old,
                },
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        empty_reconciled = standing_agenda_tick.reconcile_standing_task_statuses(limit=20, dry_run=False)
        empty_manifest_after = standing_agenda_tick.read_json(empty_completed_manifest, {})
        empty_closure = empty_manifest_after.get("standing_closure") or {}
        agenda_after_empty = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        empty_mainline_after = next(
            item
            for item in agenda_after_empty.get("active_items") or []
            if item.get("id") == "smoke-empty-completed-lane"
        )
        check(
            checks,
            "completed standing task with only planned acceptance text is degraded no-progress",
            any(
                item.get("task_id") == empty_completed_task_id
                and ((item.get("mainline_sync") or {}).get("status") == "mainline_item_advanced")
                for item in empty_reconciled
            )
            and empty_manifest_after.get("status") == "completed"
            and empty_closure.get("outcome") == "degraded_no_progress"
            and empty_closure.get("evidence_paths") == []
            and empty_closure.get("material_marker_count") == 0
            and standing_agenda_tick.standing_closure_evidence_paths(empty_manifest_after) == []
            and standing_agenda_tick.standing_closure_material_markers(empty_manifest_after) == 0
            and empty_mainline_after.get("status") == "blocked"
            and str(empty_mainline_after.get("status_note") or "").startswith("standing closure degraded_no_progress:"),
        )

        dead_runner_task_id = "standing-openclaw-evolution-smoke-dead-active-runner"
        dead_runner_manifest = standing_agenda_tick.ROOM / "tasks" / dead_runner_task_id / "manifest.json"
        dead_runner_dir = root / "dead-active-runner"
        dead_runner_dir.mkdir(parents=True, exist_ok=True)
        standing_agenda_tick.write_json(
            dead_runner_manifest,
            {
                "schema": "openclaw.agent_room.task.v0",
                "task_id": dead_runner_task_id,
                "run_id": dead_runner_task_id,
                "room_id": "openclaw-evolution",
                "target_agents": ["codex"],
                "status": "running",
                "created_at": old,
                "updated_at": old,
                "mainline_id": "smoke-dead-runner-lane",
                "standing_agenda": {"item_id": "smoke-dead-runner-closure"},
                "source": {"transport": "agent-room-standing-mainline", "chat_id": "-1009000000001"},
            },
        )
        agenda = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        agenda.setdefault("active_items", []).append(
            {
                "id": "smoke-dead-runner-lane",
                "status": "in_progress",
                "work_item": "Verify dead active-runner files cannot keep standing work fake-running.",
                "acceptance_evidence": ["dead active-runner is archived and linked mainline item becomes blocked"],
                "must_not_displace": ["resident harvest remains canonical for terminal result.json"],
            }
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            agenda,
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.active_runner_path("codex", dead_runner_task_id),
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "agent_id": "codex",
                "task_id": dead_runner_task_id,
                "run_id": dead_runner_task_id,
                "pid": 0,
                "started_at": old,
                "runner_dir": str(dead_runner_dir),
            },
        )
        dead_reconciled = standing_agenda_tick.reconcile_standing_task_statuses(
            limit=20,
            dry_run=False,
            dead_runner_grace_seconds=0,
        )
        dead_manifest_after = standing_agenda_tick.read_json(dead_runner_manifest, {})
        dead_finished = standing_agenda_tick.read_json(
            standing_agenda_tick.finished_runner_path("codex", dead_runner_task_id),
            {},
        )
        agenda_after_dead = standing_agenda_tick.read_json(
            standing_agenda_tick.ROOM / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {},
        )
        dead_mainline_after = next(
            item
            for item in agenda_after_dead.get("active_items") or []
            if item.get("id") == "smoke-dead-runner-lane"
        )
        dead_runner_summary = dead_manifest_after.get("runner_summary") or {}
        check(
            checks,
            "dead active-runner without result is archived and closes standing task with RCA",
            any(
                item.get("task_id") == dead_runner_task_id
                and item.get("status") == "failed"
                and item.get("reason") == "dead_active_runner_evidence"
                for item in dead_reconciled
            )
            and dead_manifest_after.get("status") == "failed"
            and ((dead_manifest_after.get("standing_closure") or {}).get("outcome") == "failed_with_rca")
            and ((dead_manifest_after.get("standing_closure") or {}).get("owner") == "codex")
            and (dead_runner_summary.get("agent_status_sources") or {}).get("codex") == "dead_active_runner_projection"
            and dead_finished.get("standing_reconcile_archive") is True
            and dead_finished.get("missing_result_json") is True
            and not standing_agenda_tick.active_runner_path("codex", dead_runner_task_id).exists(),
        )
        check(
            checks,
            "dead active-runner closure propagates blocker to linked mainline agenda",
            dead_mainline_after.get("status") == "blocked"
            and "agent-room/tasks/standing-openclaw-evolution-smoke-dead-active-runner/manifest.json" in (dead_mainline_after.get("evidence_paths") or [])
            and str(dead_mainline_after.get("status_note") or "").startswith("standing closure failed_with_rca:"),
        )
        missing_manifest_state = {
            "pending_task": {
                "task_id": "standing-openclaw-evolution-missing-manifest",
                "run_id": "standing-openclaw-evolution-missing-manifest",
                "target_agents": ["codex", "claude-code"],
                "created_at": old,
            }
        }
        pending_result = standing_agenda_tick.pending_standing_task(missing_manifest_state)
        check(
            checks,
            "orphan pending state is cleared when its manifest is missing and no runner is alive",
            pending_result == (False, "standing-openclaw-evolution-missing-manifest"),
        )

        print(json.dumps({"ok": True, "checks": checks, "tokens_printed": False}, ensure_ascii=False, indent=2))
        return 0
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in saved_globals.items():
            setattr(standing_agenda_tick, name, value)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
