#!/usr/bin/env python3
"""Read-only agent room status aggregator.

Reads room metadata, participant registry, bridge status, and adapter probe
snapshots. It does not wake agents, send Telegram, or change runtime behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from mailbox_paths import CODE_ROOT, MAILBOX_ROOT

BRIDGE = CODE_ROOT
MAILBOX = MAILBOX_ROOT
PROBES = {
    "codex": BRIDGE / "adapter-probes" / "codex" / "latest.json",
    "claude-code": BRIDGE / "adapter-probes" / "claude-code" / "latest.json",
    "antigravity": BRIDGE / "adapter-probes" / "antigravity" / "latest.json",
}
RUNTIME_STATUS_FILE = BRIDGE / "agent-room" / "agent_room_status.json"
MAIN_WATCHER_STATE_FILE = MAILBOX / ".openclaw_main_watcher_state.json"
MODEL_QUOTA_SIGNAL_FILE = BRIDGE / "agent-room" / "model_quota_signal.json"
MAIN_AGENT_ID = "openclaw-main"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_read_error": str(exc)}


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return False
    try:
        stat_parts = stat_path.read_text(encoding="utf-8", errors="replace").split()
        if len(stat_parts) > 2 and stat_parts[2] == "Z":
            return False
    except Exception:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def systemd_show_unit(unit: str | None) -> dict[str, str]:
    unit_name = str(unit or "").strip()
    if not unit_name:
        return {}
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit_name,
                "-p",
                "MainPID",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "--no-pager",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return {"show_error": str(exc)[:240]}
    state: dict[str, str] = {"show_exit_code": str(proc.returncode)}
    if proc.stderr:
        state["stderr"] = proc.stderr[-500:]
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key] = value
    return state


def _active_runner_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def active_runner_liveness(record: dict[str, Any]) -> dict[str, Any]:
    record_pid = _active_runner_int(record.get("pid"))
    unit = str(record.get("systemd_unit") or "").strip()
    state = systemd_show_unit(unit) if unit else {}
    main_pid = _active_runner_int(state.get("MainPID"))
    show_ok = state.get("show_exit_code") == "0"
    base = {
        "record_pid": record_pid or None,
        "systemd_main_pid": main_pid or None,
        "systemd_unit": unit or None,
        "systemd_active_state": state.get("ActiveState"),
        "systemd_sub_state": state.get("SubState"),
    }
    if main_pid and process_alive(main_pid):
        return {
            **base,
            "alive": True,
            "pid": main_pid,
            "liveness_source": "systemd_main_pid",
        }
    if unit and show_ok and main_pid <= 0:
        return {
            **base,
            "alive": False,
            "pid": record_pid or None,
            "liveness_source": "systemd_no_main_pid",
        }
    if process_alive(record_pid):
        return {
            **base,
            "alive": True,
            "pid": record_pid,
            "liveness_source": "record_pid",
        }
    return {
        **base,
        "alive": False,
        "pid": record_pid or main_pid or None,
        "liveness_source": "none",
    }


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def seconds_since(value: str | None) -> int | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    return max(0, int((datetime.now().astimezone() - parsed).total_seconds()))


def quota_signal_lookup_keys(model: str | None) -> list[str]:
    value = str(model or "").strip() or "unknown-model"
    keys = [value]
    if "/" in value:
        keys.append(value.rsplit("/", 1)[-1])
    else:
        keys.append(f"openai-codex/{value}")
    out: list[str] = []
    for key in [*keys, *[key.lower() for key in keys]]:
        if key and key not in out:
            out.append(key)
    return out


def normalize_trusted_quota_signal(record: dict[str, Any], default_source: str, signal_file: Path) -> dict[str, Any] | None:
    expires_at = parse_iso(str(record.get("expires_at") or ""))
    if expires_at and expires_at <= datetime.now().astimezone():
        return None
    fields = ("remaining_percent", "remaining_units", "remaining_requests", "remaining_tokens", "remaining_messages", "per_model_remaining_units")
    if not (bool(record.get("remaining_known")) or any(record.get(key) is not None for key in fields)):
        return None
    per_model_units = record.get("per_model_remaining_units")
    if not isinstance(per_model_units, dict):
        per_model_units = {}
    if record.get("remaining_requests") is not None:
        per_model_units.setdefault("requests", record.get("remaining_requests"))
    if record.get("remaining_tokens") is not None:
        per_model_units.setdefault("tokens", record.get("remaining_tokens"))
    if record.get("remaining_messages") is not None:
        per_model_units.setdefault("messages", record.get("remaining_messages"))
    source = str(record.get("trusted_remaining_source") or record.get("source") or default_source or "model_quota_signal").strip()
    signal: dict[str, Any] = {
        "mode": "trusted_remaining_quota_signal",
        "remaining_known": True,
        "remaining_percent": record.get("remaining_percent"),
        "remaining_units": record.get("remaining_units"),
        "source": source,
        "trusted_remaining_source": source,
        "proactive_switching_ready": bool(record.get("proactive_switching_ready") or record.get("routing_ready")),
        "signal_file": str(signal_file),
        "quota_signal_contract_version": str(record.get("quota_signal_contract_version") or "1-trusted-remaining"),
        "blocking_missing": list(record.get("blocking_missing") or []),
        "limitation": str(record.get("limitation") or "trusted remaining quota is wired; routing thresholds remain a separate policy"),
    }
    if per_model_units:
        signal["per_model_remaining_units"] = per_model_units
    for key in ("remaining_requests", "remaining_tokens", "remaining_messages", "limit_requests", "limit_tokens", "used_percent", "reset_at", "estimated_reset_at", "observed_at", "updated_at", "expires_at", "quota_scope", "per_model_remaining_known", "per_model_remaining_units", "provider_quota_window_known", "blocking_missing", "provider", "provider_display_name", "plan", "windows", "primary_window_label", "primary_window_remaining_percent", "canonical_model"):
        if record.get(key) is not None:
            signal[key] = record.get(key)
    return signal


def trusted_remaining_quota_signal(bridge: Path, agent_id: str, model: str | None = None) -> dict[str, Any] | None:
    path = Path(os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_FILE") or bridge / "agent-room" / "model_quota_signal.json")
    data = read_json(path) or {}
    root = data.get("signals") if isinstance(data.get("signals"), dict) else data.get("agents")
    if not isinstance(root, dict):
        root = data if isinstance(data, dict) else {}
    agent_signal = root.get(agent_id) if isinstance(root.get(agent_id), dict) else None
    if agent_signal is None:
        alt = agent_id.replace("-", "_")
        agent_signal = root.get(alt) if isinstance(root.get(alt), dict) else None
    if not isinstance(agent_signal, dict):
        return None
    models = agent_signal.get("models")
    record = agent_signal
    if isinstance(models, dict):
        record = {}
        for key in quota_signal_lookup_keys(model):
            value = models.get(key)
            if isinstance(value, dict):
                record = value
                break
        if not record:
            lower = {str(key).lower(): value for key, value in models.items() if isinstance(value, dict)}
            for key in quota_signal_lookup_keys(model):
                value = lower.get(key.lower())
                if isinstance(value, dict):
                    record = value
                    break
    return normalize_trusted_quota_signal(record if isinstance(record, dict) else {}, str(data.get("source") or "model_quota_signal"), path)


def main_runtime_summary(bridge: Path) -> dict[str, Any]:
    watcher = read_json(bridge / ".openclaw_main_watcher_state.json") or {}
    runtime = read_json(bridge / "agent-room" / "agent_room_status.json") or {}
    runtime_agent = (((runtime.get("agents") or {}) if isinstance(runtime, dict) else {}).get(MAIN_AGENT_ID) or {})
    models = runtime_agent.get("models") if isinstance(runtime_agent, dict) else {}
    if not isinstance(models, dict):
        models = {}
    quota_state = runtime_agent.get("quota_state") if isinstance(runtime_agent, dict) else None
    if not quota_state:
        quota_state = watcher.get("main_quota_state") or "unknown"
    fallback_attempts = watcher.get("main_ark_fallback_model_attempts") if isinstance(watcher.get("main_ark_fallback_model_attempts"), list) else []
    fallback_skipped = watcher.get("main_ark_fallback_skipped_models") if isinstance(watcher.get("main_ark_fallback_skipped_models"), list) else []
    fallback_active = bool(
        quota_state in {"fallback_active", "depleted_ark_active"}
        or watcher.get("main_quota_state") == "depleted_ark_active"
        or (runtime_agent.get("fallback_active") if isinstance(runtime_agent, dict) else False)
    )
    active_model = (runtime_agent.get("active_model") if isinstance(runtime_agent, dict) else None) or watcher.get("main_ark_fallback_last_model")
    quota_signal = {
        "mode": "reactive_failure_cooldown_and_success_recovery",
        "remaining_known": False,
        "remaining_percent": None,
        "remaining_units": None,
        "source": "main watcher state + agent_room_status model records",
        "proactive_switching_ready": False,
        "trusted_remaining_source": "session_status quota signal not wired into this aggregator",
        "quota_signal_contract_version": "0-no-numeric",
        "blocking_missing": ["per_model_remaining_units"],
        "limitation": "quota_state=available means no active recorded depletion/cooldown; it is not a numeric remaining-quota measurement",
    }
    trusted_signal = trusted_remaining_quota_signal(bridge, MAIN_AGENT_ID, active_model or "openai-codex/gpt-5.5")
    if trusted_signal:
        quota_signal.update(trusted_signal)
        quota_signal["reactive_fallback_source"] = "main watcher state + agent_room_status model records"
    return {
        "agent_id": MAIN_AGENT_ID,
        "quota_state": quota_state,
        "quota_signal": quota_signal,
        "fallback_active": fallback_active,
        "primary_model": "openai-codex/gpt-5.5",
        "active_model": active_model,
        "fallback_candidate_models": watcher.get("main_ark_fallback_candidate_models") or [],
        "fallback_model_attempts": fallback_attempts,
        "fallback_skipped_models": fallback_skipped,
        "fallback_last_model": watcher.get("main_ark_fallback_last_model"),
        "fallback_last_used_at": watcher.get("main_ark_fallback_last_used_at"),
        "fallback_last_detail": watcher.get("main_ark_fallback_last_detail"),
        "model_quota": models,
    }


def active_runner_summary(bridge: Path) -> list[dict[str, Any]]:
    active_dir = bridge / "agent-room" / "active-runners"
    if not active_dir.exists():
        return []
    runners: list[dict[str, Any]] = []
    for path in sorted(active_dir.glob("*.json")):
        record = read_json(path) or {}
        if not isinstance(record, dict):
            continue
        liveness = active_runner_liveness(record)
        pid = liveness.get("pid")
        started_at = str(record.get("started_at") or "")
        max_seconds = record.get("max_seconds")
        try:
            max_seconds = int(max_seconds) if max_seconds is not None else None
        except (TypeError, ValueError):
            max_seconds = None
        age_seconds = seconds_since(started_at)
        alive = bool(liveness.get("alive"))
        stale = bool(
            alive
            and age_seconds is not None
            and max_seconds is not None
            and age_seconds > max_seconds
        )
        needs_harvest = bool(record.get("status") == "running" and not alive)
        if stale:
            effective_status = "stale_running"
        elif needs_harvest:
            effective_status = "finished_or_missing_process_needs_harvest"
        else:
            effective_status = str(record.get("status") or "unknown")
        runners.append({
            "agent_id": record.get("agent_id"),
            "run_id": record.get("run_id"),
            "task_id": record.get("task_id"),
            "room_id": record.get("room_id"),
            "status": record.get("status"),
            "effective_status": effective_status,
            "pid": pid or None,
            "record_pid": liveness.get("record_pid"),
            "systemd_main_pid": liveness.get("systemd_main_pid"),
            "liveness_source": liveness.get("liveness_source"),
            "systemd_unit": liveness.get("systemd_unit"),
            "systemd_active_state": liveness.get("systemd_active_state"),
            "systemd_sub_state": liveness.get("systemd_sub_state"),
            "alive": alive,
            "stale": stale,
            "needs_harvest": needs_harvest,
            "started_at": started_at or None,
            "age_seconds": age_seconds,
            "expires_at": record.get("expires_at"),
            "max_seconds": max_seconds,
            "active_runner_path": str(path),
        })
    return runners


def collaboration_ledger_summary(bridge: Path) -> dict[str, Any]:
    path = bridge / "collaboration_ledger.json"
    archive = bridge / "archive" / "collaboration_ledger.jsonl"
    ledger = read_json(path)
    if not isinstance(ledger, dict):
        return {
            "present": False,
            "path": str(path),
            "archive_path": str(archive),
            "read_error": ledger.get("_read_error") if isinstance(ledger, dict) else None,
        }

    work_items = []
    for item in ledger.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        work_items.append({
            "id": item.get("id"),
            "status": item.get("status"),
            "assigned_to": item.get("assigned_to"),
            "claimed_by": item.get("claimed_by"),
            "role": item.get("role"),
            "handoff_to": item.get("handoff_to"),
            "updated_at": item.get("updated_at"),
        })
    claims = [
        {
            "work_item_id": claim.get("work_item_id"),
            "agent_id": claim.get("agent_id"),
            "status": claim.get("status"),
            "claimed_at": claim.get("claimed_at"),
        }
        for claim in ledger.get("claims") or []
        if isinstance(claim, dict)
    ]
    event_count = 0
    if archive.exists():
        try:
            event_count = sum(1 for line in archive.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        except Exception:
            event_count = 0
    return {
        "present": True,
        "path": str(path),
        "archive_path": str(archive),
        "archive_event_count": event_count,
        "task_id": ledger.get("task_id"),
        "run_id": ledger.get("run_id"),
        "room_id": ledger.get("room_id"),
        "mode": ledger.get("mode"),
        "status": ledger.get("status"),
        "participants": ledger.get("participants") if isinstance(ledger.get("participants"), list) else [],
        "work_items": work_items,
        "claims": claims,
        "artifacts_count": len(ledger.get("artifacts") or []),
        "blockers_count": len(ledger.get("blockers") or []),
        "handoffs_count": len(ledger.get("handoffs") or []),
        "updated_at": ledger.get("updated_at"),
    }


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def compact_id_list(value: Any, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if item is not None]


def collaboration_health_summary(bridge: Path) -> dict[str, Any]:
    """Summarize the richer collaboration status surface for room readiness.

    `agent-room/tools/collaboration_status.py` owns the detailed material-point,
    peer-uptake, challenge, presence, and degraded-quorum calculations.  This
    top-level status command should expose those signals without duplicating the
    detailed artifact or relying on Alex/main to manually cross-reference files.
    """
    status_dir = bridge / "agent-room" / "collaboration-status"
    latest_path = status_dir / "latest.json"
    latest = read_json(latest_path)
    if not isinstance(latest, dict):
        return {
            "present": False,
            "path": str(latest_path),
            "read_error": latest.get("_read_error") if isinstance(latest, dict) else None,
        }

    overview = latest.get("collaboration_overview")
    if not isinstance(overview, dict):
        overview = {}
    dashboard = latest.get("activity_dashboard")
    if not isinstance(dashboard, dict):
        dashboard = {}
    fixed_card = latest.get("fixed_status_card")
    if not isinstance(fixed_card, dict):
        fixed_card = {}
    per_agent = latest.get("per_agent_engagement")
    if not isinstance(per_agent, dict):
        per_agent = latest.get("agent_liveness") if isinstance(latest.get("agent_liveness"), dict) else {}

    per_agent_engagement: dict[str, dict[str, Any]] = {}
    for agent_id, data in per_agent.items():
        if not isinstance(data, dict):
            continue
        per_agent_engagement[str(agent_id)] = {
            "engagement_state": data.get("engagement_state"),
            "active_runner_count": int_value(data.get("active_runner_count")),
            "working_runner_count": int_value(data.get("working_runner_count")),
            "pending_harvest_count": int_value(data.get("pending_harvest_count")),
            "completed_presence_count": int_value(data.get("completed_presence_count")),
            "needs_attention_count": int_value(data.get("needs_attention_count")),
            "black_box_runner_count": int_value(data.get("black_box_runner_count")),
            "active_task_ids": compact_id_list(data.get("active_task_ids")),
            "next_soft_deadline_at": data.get("next_soft_deadline_at"),
            "next_hard_deadline_at": data.get("next_hard_deadline_at"),
        }

    participant_presence = latest.get("participant_presence")
    participant_presence_count = len(participant_presence) if isinstance(participant_presence, list) else 0
    card_text = fixed_card.get("text") if isinstance(fixed_card.get("text"), str) else ""
    watch_path = status_dir / "watch.txt"
    compact_path = status_dir / "compact.txt"
    markdown_path = status_dir / "latest.md"

    return {
        "present": True,
        "path": str(latest_path),
        "generated_at": latest.get("generated_at"),
        "include_background": latest.get("include_background"),
        "active_task_ids": compact_id_list(latest.get("active_task_ids")),
        "attention_task_ids": compact_id_list(latest.get("attention_task_ids")),
        "activity_dashboard_summary_state": dashboard.get("summary_state"),
        "live_runner_count": int_value(dashboard.get("live_runner_count")),
        "pending_harvest_count": int_value(dashboard.get("pending_harvest_count")),
        "needs_attention_count": int_value(dashboard.get("needs_attention_count")),
        "tracked_tasks": int_value(overview.get("tracked_tasks")),
        "material_point_count": int_value(overview.get("material_point_count")),
        "peer_uptake_count": int_value(overview.get("peer_uptake_count")),
        "peer_challenge_count": int_value(overview.get("peer_challenge_count")),
        "integration_signal_count": int_value(overview.get("integration_signal_count")),
        "summary_point_count": int_value(overview.get("summary_point_count")),
        "peer_reviewed_task_count": int_value(overview.get("peer_reviewed_task_count")),
        "needs_collaboration_review_count": int_value(overview.get("needs_collaboration_review_count")),
        "needs_collaboration_repair_count": int_value(overview.get("needs_collaboration_repair_count")),
        "tasks_missing_peer_uptake_count": int_value(overview.get("tasks_missing_peer_uptake_count")),
        "tasks_missing_peer_uptake_ids": compact_id_list(overview.get("tasks_missing_peer_uptake_ids")),
        "degraded_quorum_task_count": int_value(overview.get("degraded_quorum_task_count")),
        "degraded_quorum_task_ids": compact_id_list(overview.get("degraded_quorum_task_ids")),
        "runner_attention_task_count": int_value(overview.get("runner_attention_task_count")),
        "runner_attention_task_ids": compact_id_list(overview.get("runner_attention_task_ids")),
        "active_claim_count": int_value(overview.get("active_claim_count")),
        "expired_claim_count": int_value(overview.get("expired_claim_count")),
        "claim_lease_expired_task_count": int_value(overview.get("claim_lease_expired_task_count")),
        "claim_lease_expired_task_ids": compact_id_list(overview.get("claim_lease_expired_task_ids")),
        "per_agent_material_points": overview.get("per_agent_material_points") if isinstance(overview.get("per_agent_material_points"), dict) else {},
        "per_agent_peer_uptakes": overview.get("per_agent_peer_uptakes") if isinstance(overview.get("per_agent_peer_uptakes"), dict) else {},
        "per_agent_peer_challenges": overview.get("per_agent_peer_challenges") if isinstance(overview.get("per_agent_peer_challenges"), dict) else {},
        "per_agent_engagement": per_agent_engagement,
        "participant_presence_count": participant_presence_count,
        "status_surfaces": {
            "latest_json": str(latest_path),
            "latest_markdown": str(markdown_path),
            "compact_text": str(compact_path),
            "watch_text": str(watch_path),
            "fixed_status_card_text_present": bool(card_text),
            "fixed_status_card_text_length": len(card_text),
        },
    }


def recent_jsonl_records(path: Path, limit: int = 400) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line)
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def compact_trigger(trigger: Any) -> dict[str, Any] | None:
    if not isinstance(trigger, dict):
        return None
    return {
        "trigger": trigger.get("trigger"),
        "intent": trigger.get("intent"),
        "mentioned_usernames": trigger.get("mentioned_usernames") if isinstance(trigger.get("mentioned_usernames"), list) else [],
        "main_agent_id": trigger.get("main_agent_id"),
        "route_to": trigger.get("route_to") if isinstance(trigger.get("route_to"), list) else [],
        "delivery_policy": trigger.get("delivery_policy"),
        "native_telegram_bot_to_bot_required": bool(trigger.get("native_telegram_bot_to_bot_required")),
    }


def source_comment_id(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    update_id = str(source.get("update_id") or source.get("stable_message_id") or "")
    if update_id.startswith("agent-comment:") or update_id.startswith("bot-message:"):
        return update_id
    return None


def agent_to_agent_activity_summary(bridge: Path) -> dict[str, Any]:
    room_root = bridge / "agent-room"
    recent_tasks: list[dict[str, Any]] = []
    for task in recent_jsonl_records(room_root / "tasks.jsonl"):
        source = task.get("source") if isinstance(task.get("source"), dict) else {}
        trigger = task.get("bot_to_bot_trigger") or source.get("bot_to_bot_trigger")
        lane = str(task.get("lane") or "")
        routing_intent = str(task.get("routing_intent") or "")
        is_peer_followup = lane == "peer_collaboration_followup"
        is_bot_to_bot = bool(trigger) or lane == "agent_to_agent_mention" or routing_intent == "bot_to_bot_coordination"
        if not (is_peer_followup or is_bot_to_bot):
            continue
        activity_type = "peer_collaboration_followup"
        if trigger:
            activity_type = "bot_to_bot_trigger"
        elif lane == "agent_to_agent_mention":
            activity_type = "agent_to_agent_mention"
        recent_tasks.append({
            "activity_type": activity_type,
            "task_id": task.get("task_id"),
            "run_id": task.get("run_id"),
            "room_id": task.get("room_id"),
            "created_at": task.get("created_at"),
            "requested_by": task.get("requested_by"),
            "lane": lane,
            "routing_intent": routing_intent or None,
            "source_comment_id": source_comment_id(source),
            "source_agent_id": source.get("source_agent_id") or task.get("collab_parent_agent_id"),
            "source_run_id": source.get("source_run_id") or task.get("collab_parent_task_id"),
            "source_telegram_message_id": source.get("source_telegram_message_id"),
            "target_agents": task.get("target_agents") if isinstance(task.get("target_agents"), list) else [],
            "delivery_policy": task.get("delivery_policy"),
            "bot_to_bot_trigger": compact_trigger(trigger),
        })

    recent_events: list[dict[str, Any]] = []
    for event in recent_jsonl_records(room_root / "events.jsonl"):
        event_type = str(event.get("event_type") or "")
        trigger = event.get("bot_to_bot_trigger")
        if event_type not in {"bot_to_bot_trigger", "agent_originated_mentions"} and not trigger:
            continue
        recent_events.append({
            "event_id": event.get("event_id"),
            "event_type": event_type,
            "room_id": event.get("room_id"),
            "created_at": event.get("created_at"),
            "actor_agent_id": event.get("actor_agent_id"),
            "telegram_message_id": event.get("telegram_message_id"),
            "target_agents": [
                candidate.get("agent_id")
                for candidate in event.get("agent_candidates") or []
                if isinstance(candidate, dict) and candidate.get("agent_id")
            ],
            "bot_to_bot_trigger": compact_trigger(trigger),
        })

    recent_messages: list[dict[str, Any]] = []
    for message in recent_jsonl_records(room_root / "messages.jsonl"):
        trigger = message.get("bot_to_bot_trigger")
        bot_to_bot_targets = message.get("bot_to_bot_targets") if isinstance(message.get("bot_to_bot_targets"), list) else []
        if not trigger and not bot_to_bot_targets:
            continue
        recent_messages.append({
            "message_event_id": message.get("message_event_id"),
            "room_id": message.get("room_id"),
            "created_at": message.get("created_at"),
            "actor_agent_id": message.get("actor_agent_id"),
            "telegram_message_id": message.get("telegram_message_id"),
            "target_agents": message.get("target_agents") if isinstance(message.get("target_agents"), list) else [],
            "bot_to_bot_targets": bot_to_bot_targets,
            "bot_to_bot_trigger": compact_trigger(trigger),
        })

    recent_tasks = recent_tasks[-12:]
    recent_events = recent_events[-12:]
    recent_messages = recent_messages[-12:]
    return {
        "present": bool(recent_tasks or recent_events or recent_messages),
        "scan_limit_per_jsonl": 400,
        "recent_tasks": recent_tasks,
        "recent_events": recent_events,
        "recent_messages": recent_messages,
        "recent_task_count": len(recent_tasks),
        "bot_to_bot_task_count": sum(1 for task in recent_tasks if task.get("activity_type") == "bot_to_bot_trigger"),
        "peer_followup_task_count": sum(1 for task in recent_tasks if task.get("activity_type") == "peer_collaboration_followup"),
    }


def agent_to_agent_human_summary(activity: dict[str, Any]) -> list[str]:
    """Generate human-readable short summary lines for chat visibility."""
    summaries = []
    recent_tasks = activity.get("recent_tasks", [])
    if not recent_tasks:
        return ["无最近 agent 间协作活动"]

    for task in reversed(recent_tasks[-3:]):  # 取最近3条
        activity_type = task.get("activity_type")
        source_agent = task.get("source_agent_id") or "unknown"
        target_agents = task.get("target_agents", [])

        # 尝试从 source_comment_id 或 source_telegram_message_id 中提取消息 ID
        msg_id = task.get("source_telegram_message_id")
        if not msg_id:
            source_comment_id_val = task.get("source_comment_id")
            if source_comment_id_val and ":" in source_comment_id_val:
                parts = source_comment_id_val.split(":")
                if len(parts) >= 2 and parts[-1].isdigit():
                    msg_id = parts[-1]

        age_sec = seconds_since(task.get("created_at"))

        target_str = ",".join(target_agents) if target_agents else "others"
        age_str = f"{age_sec}秒前" if age_sec is not None else "之前"

        if activity_type == "bot_to_bot_trigger":
            if msg_id:
                summaries.append(f"{age_str} {source_agent} 通过消息 {msg_id} @ {target_str}")
            else:
                summaries.append(f"{age_str} {source_agent} 路由协作到 {target_str}")
        elif activity_type == "peer_collaboration_followup":
            summaries.append(f"{age_str} {source_agent} 接力协作到 {target_str}")
        elif activity_type == "agent_to_agent_mention":
            if msg_id:
                summaries.append(f"{age_str} {source_agent} 在消息 {msg_id} 中提到 {target_str}")
            else:
                summaries.append(f"{age_str} {source_agent} 提到 {target_str}")

    return summaries if summaries else ["无最近 agent 间协作活动"]


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_bridge_status() -> dict[str, Any] | None:
    script = BRIDGE / "bridge_status.py"
    if not script.exists():
        return None
    try:
        result = subprocess.run(
            ["python3", str(script)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except Exception as exc:
        return {"_read_error": f"bridge_status invocation failed: {exc}"}
    if result.returncode != 0:
        return {"_read_error": result.stderr[-2000:] or f"bridge_status returned {result.returncode}"}
    try:
        return json.loads(result.stdout)
    except Exception as exc:
        return {"_read_error": f"bridge_status JSON parse failed: {exc}", "stdout_tail": result.stdout[-2000:]}


def participant_summary(participant: dict[str, Any], probe: dict[str, Any] | None, probe_paths: dict[str, Path]) -> dict[str, Any]:
    pid = participant.get("id")
    declared_status = participant.get("adapter_status")
    probe_status = probe.get("adapter_status") if isinstance(probe, dict) else None
    blockers = []
    for source in [participant.get("blockers"), probe.get("blockers") if isinstance(probe, dict) else None]:
        if isinstance(source, list):
            blockers.extend(str(x) for x in source if x)
    blockers = list(dict.fromkeys(blockers))

    effective_status = probe_status or declared_status or participant.get("status") or "unknown"
    if participant.get("status") == "active" and not blockers and effective_status in {"active", "active_mailbox_baseline"}:
        severity = "ok"
    elif "missing" in str(effective_status) or "blocked" in str(effective_status) or blockers:
        severity = "blocked" if "blocked" in str(effective_status) else "warning"
    else:
        severity = "info"

    return {
        "id": pid,
        "declared_status": participant.get("status"),
        "declared_adapter_status": declared_status,
        "probe_status": probe_status,
        "effective_status": effective_status,
        "severity": severity,
        "host": participant.get("host"),
        "adapter": participant.get("adapter"),
        "capabilities_declared": participant.get("capabilities"),
        "capabilities_observed": probe.get("capabilities_observed") if isinstance(probe, dict) else None,
        "blockers": blockers,
        "safe_next_actions": probe.get("safe_next_actions") if isinstance(probe, dict) else None,
        "requires_alex_action": probe.get("requires_alex_action") if isinstance(probe, dict) else None,
        "probe_checked_at": probe.get("checked_at") if isinstance(probe, dict) else None,
        "probe_path": str(probe_paths.get(str(pid))) if str(pid) in probe_paths else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, default=BRIDGE)
    parser.add_argument("--write", type=Path, default=BRIDGE / "agent_room_status.latest.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    bridge = args.bridge
    probe_paths = {
        "codex": bridge / "adapter-probes" / "codex" / "latest.json",
        "claude-code": bridge / "adapter-probes" / "claude-code" / "latest.json",
        "antigravity": bridge / "adapter-probes" / "antigravity" / "latest.json",
    }

    room = read_json(bridge / "room.json")
    participants_doc = read_json(bridge / "participants.json") or {}
    baselines_doc = read_json(bridge / "baselines.json") or {}
    turn = read_json(MAILBOX / "turn.json")
    bridge_status = run_bridge_status()
    active_runners = active_runner_summary(bridge)
    collaboration_ledger = collaboration_ledger_summary(bridge)
    collaboration_health = collaboration_health_summary(bridge)
    agent_to_agent_activity = agent_to_agent_activity_summary(bridge)
    main_runtime = main_runtime_summary(bridge)

    probes = {pid: read_json(path) for pid, path in probe_paths.items()}
    participants = participants_doc.get("participants") if isinstance(participants_doc, dict) else []
    if not isinstance(participants, list):
        participants = []

    summaries = [participant_summary(p, probes.get(str(p.get("id")), None), probe_paths) for p in participants if isinstance(p, dict)]
    blocked = [p for p in summaries if p["severity"] in {"blocked", "warning"}]
    active = [p for p in summaries if p["declared_status"] == "active"]
    baselines = baselines_doc.get("baselines") if isinstance(baselines_doc, dict) else []
    if not isinstance(baselines, list):
        baselines = []
    unstable_baselines = [
        b
        for b in baselines
        if isinstance(b, dict)
        and str(b.get("status") or "") not in {"active_baseline", "stable", "keep"}
    ]
    why_not_ready: list[str] = []
    for participant in blocked:
        why_not_ready.append(
            f"{participant.get('id')}: {participant.get('effective_status')}"
        )
    for baseline in unstable_baselines:
        why_not_ready.append(
            f"baseline {baseline.get('id')}: {baseline.get('status')}"
        )

    payload: dict[str, Any] = {
        "schema": "openclaw.agent_room_status.v0",
        "checked_at": now_iso(),
        "room": {
            "room_id": room.get("room_id") if isinstance(room, dict) else None,
            "backend": room.get("backend") if isinstance(room, dict) else None,
            "status": room.get("status") if isinstance(room, dict) else None,
            "telegram_foreground_policy": room.get("telegram_foreground_policy") if isinstance(room, dict) else None,
        },
        "turn": {
            "active_epoch": bridge_status.get("active_epoch") if isinstance(bridge_status, dict) else None,
            "mailbox": bridge_status.get("mailbox") if isinstance(bridge_status, dict) else str(MAILBOX),
            "seq": turn.get("seq") if isinstance(turn, dict) else None,
            "needs_reply": turn.get("needs_reply") if isinstance(turn, dict) else None,
            "waiting_on": bridge_status.get("waiting_on") if isinstance(bridge_status, dict) else (turn.get("needs_reply") if isinstance(turn, dict) else None),
            "last_writer": turn.get("last_writer") if isinstance(turn, dict) else None,
            "updated_at": turn.get("updated_at") if isinstance(turn, dict) else None,
        },
        "bridge_status": bridge_status,
        "summary": {
            "participants_total": len(summaries),
            "participants_active_declared": len(active),
            "participants_with_blockers_or_warnings": len(blocked),
            "baselines_total": len(baselines),
            "baselines_active": len(baselines) - len(unstable_baselines),
            "baselines_unstable": len(unstable_baselines),
            "active_runners_total": len(active_runners),
            "active_runners_alive": sum(1 for runner in active_runners if runner.get("alive")),
            "active_runners_stale": sum(1 for runner in active_runners if runner.get("stale")),
            "active_runners_needing_harvest": sum(1 for runner in active_runners if runner.get("needs_harvest")),
            "collaboration_ledger_present": bool(collaboration_ledger.get("present")),
            "collaboration_ledger_task_id": collaboration_ledger.get("task_id"),
            "collaboration_health_present": bool(collaboration_health.get("present")),
            "collaboration_material_point_count": collaboration_health.get("material_point_count"),
            "collaboration_peer_uptake_count": collaboration_health.get("peer_uptake_count"),
            "collaboration_peer_challenge_count": collaboration_health.get("peer_challenge_count"),
            "collaboration_integration_signal_count": collaboration_health.get("integration_signal_count"),
            "collaboration_tasks_missing_peer_uptake": collaboration_health.get("tasks_missing_peer_uptake_count"),
            "collaboration_degraded_quorum_tasks": collaboration_health.get("degraded_quorum_task_count"),
            "collaboration_runner_attention_tasks": collaboration_health.get("runner_attention_task_count"),
            "agent_to_agent_activity_present": bool(agent_to_agent_activity.get("present")),
            "agent_to_agent_recent_tasks": agent_to_agent_activity.get("recent_task_count"),
            "bot_to_bot_recent_tasks": agent_to_agent_activity.get("bot_to_bot_task_count"),
            "peer_followup_recent_tasks": agent_to_agent_activity.get("peer_followup_task_count"),
            "agent_to_agent_human_summary": agent_to_agent_human_summary(agent_to_agent_activity),
            "main_quota_state": main_runtime.get("quota_state"),
            "main_quota_remaining_known": ((main_runtime.get("quota_signal") or {}).get("remaining_known") if isinstance(main_runtime.get("quota_signal"), dict) else False),
            "main_quota_proactive_switching_ready": ((main_runtime.get("quota_signal") or {}).get("proactive_switching_ready") if isinstance(main_runtime.get("quota_signal"), dict) else False),
            "main_fallback_active": main_runtime.get("fallback_active"),
            "main_active_model": main_runtime.get("active_model"),
            "agent_room_ready": not blocked and not unstable_baselines,
            "why_not_ready": why_not_ready,
        },
        "main_runtime": main_runtime,
        "participants": summaries,
        "baselines": baselines,
        "active_runners": active_runners,
        "collaboration_ledger": collaboration_ledger,
        "collaboration_health": collaboration_health,
        "agent_to_agent_activity": agent_to_agent_activity,
    }
    if not args.no_write and args.write:
        write_json_atomic(args.write, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
