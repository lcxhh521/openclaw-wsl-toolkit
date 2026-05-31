#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
ACTIVE_RUNNERS = ROOM / "active-runners"
TASKS = ROOM / "tasks"
STATUS_DIR = ROOM / "collaboration-status"
COLLAB_LEDGER_DIR = ROOM / "collaboration-ledgers"
DAEMON_STATUS = ROOM / "agent_room_bridge_daemon.status.json"
AGENT_PRESENCE_DIR = ROOM / "agent-presence"
MODEL_QUOTA_SIGNAL = ROOM / "model_quota_signal.json"
AGENT_QUOTA_STATE = ROOM / "agent_quota_state.json"
DEFAULT_ROOM_ID = os.environ.get("OPENCLAW_STATUS_ROOM_ID", "openclaw-evolution")
LOCAL_AGENTS = {"codex", "claude-code"}
ROOM_RUNTIME_AGENT = "openclaw-main"
ROOM_STATUS_AGENT_ORDER = (ROOM_RUNTIME_AGENT, "codex", "claude-code")
MATERIAL_UPTAKE_STATUSES = {"accepted", "challenged", "incorporated", "rejected", "superseded"}
CHALLENGE_UPTAKE_STATUSES = {"challenged", "rejected"}
INTEGRATED_SUMMARY_STATUSES = {"accepted", "incorporated", "superseded"}
SUMMARY_POINT_KINDS = {"summary", "integrated_summary", "synthesis", "closure_summary"}
ACTIVE_CLAIM_STATUSES = {"active", "claimed", "running", "handoff"}
BACKGROUND_TRANSPORTS = {"agent-room-standing-mainline", "agent-room-proactive-mainline"}
BACKGROUND_LANES = {"standing_mainline_discussion", "proactive_mainline_discussion", "long_background"}
MAX_FIXED_STATUS_CARD_CHARS = int(os.environ.get("OPENCLAW_FIXED_STATUS_CARD_MAX_CHARS", "3900"))
ACTION_ITEM_SURFACE_LIMIT = int(os.environ.get("OPENCLAW_COLLABORATION_ACTION_ITEM_SURFACE_LIMIT", "12"))
ACTION_ITEM_PRIORITY = {
    "peer_uptake_needed": 10,
    "material_progress_needed": 20,
    "summary_integration_needed": 30,
    "scope_conflict_review_needed": 40,
    "collaboration_repair_needed": 50,
    "collaboration_review_needed": 60,
    "runner_attention_needed": 70,
    "claim_lease_expired": 80,
    "active_material_silence_watch": 85,
    "blocker_review_needed": 90,
}
ACTIVE_MATERIAL_SILENCE_NEXT_ACTION = (
    "produce a material point, peer uptake/challenge, scoped artifact/smoke, "
    "or explicit blocker/NO_COMMENT evidence before the soft deadline"
)
POST_SOFT_MATERIAL_PROGRESS_NEXT_ACTION = (
    "produce material progress now or record degraded-quorum/blocker evidence"
)
RUNNER_ATTENTION_NEXT_ACTION = (
    "harvest runner output if available, otherwise record dead-runner blocker or degraded-quorum evidence"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.now(timezone.utc).astimezone().tzinfo)
        return dt.astimezone()
    except Exception:
        return None


def seconds_until(value: Any, now: datetime) -> int | None:
    dt = parse_iso_datetime(value)
    if dt is None:
        return None
    return int((dt - now).total_seconds())


def seconds_since(value: Any, now: datetime) -> int | None:
    dt = parse_iso_datetime(value)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def sustained_mailbox_lane_snapshot() -> dict[str, Any]:
    """Read the Codex/Main sustained mailbox lane without mutating it."""
    helper = ROOT / "sustained_mailbox_lane.py"
    if not helper.exists():
        return {"available": False, "reason": "helper_missing"}
    try:
        import importlib.util
        import sys

        root_text = str(ROOT)
        inserted = False
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
            inserted = True
        try:
            spec = importlib.util.spec_from_file_location("openclaw_sustained_mailbox_lane", helper)
            if spec is None or spec.loader is None:
                return {"available": False, "reason": "import_spec_missing"}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            data = module.inspect()
        finally:
            if inserted:
                try:
                    sys.path.remove(root_text)
                except ValueError:
                    pass
    except Exception as exc:
        return {"available": False, "reason": "inspect_failed", "error": f"{type(exc).__name__}: {exc}"[:240]}
    if not isinstance(data, dict):
        return {"available": False, "reason": "inspect_returned_non_dict"}
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    gate = data.get("last_gate") if isinstance(data.get("last_gate"), dict) else {}
    return {
        "available": True,
        "schema": data.get("schema"),
        "state": data.get("state"),
        "owner": data.get("owner"),
        "recommended_action": data.get("recommended_action"),
        "seq": turn.get("seq"),
        "needs_reply": turn.get("needs_reply"),
        "age_seconds": turn.get("age_seconds"),
        "context_turns_since_rollover": turn.get("context_turns_since_rollover"),
        "gate_kind": gate.get("gate_kind"),
        "gate_class": gate.get("class"),
        "gate_threshold_reached": gate.get("threshold_reached"),
        "transport_blocks": data.get("p0_boundaries", {}).get("writes_mailbox_turns") is False and data.get("state") == "HARD_BLOCKED",
    }


def active_mailbox_brief() -> dict[str, Any]:
    pointer = read_json(ROOT / "active_mailbox.json", {})
    if not isinstance(pointer, dict):
        pointer = {}
    active_root = Path(str(pointer.get("active_data_root") or ROOT))
    turn = read_json(active_root / "turn.json", {})
    if not isinstance(turn, dict):
        turn = {}
    epoch = str(pointer.get("active_epoch") or turn.get("mailbox_epoch") or "legacy-root")
    seq = turn.get("seq")
    waiting_on = str(turn.get("needs_reply") or "none")
    lane = sustained_mailbox_lane_snapshot()
    lane_state = lane.get("state") if lane.get("available") else None
    if lane_state == "SILENT_WAIT_NOOP":
        waiting_label = "idle"
    elif waiting_on == "none":
        waiting_label = "idle"
    else:
        waiting_label = f"→{waiting_on}"
    lane_label = f" · {lane_state}" if lane_state else ""
    degraded = not epoch or seq is None or (lane.get("state") == "HARD_BLOCKED")
    label = "mailbox degraded" if degraded else f"{epoch}#{seq}{waiting_label}{lane_label}"
    return {
        "active_epoch": epoch or None,
        "active_data_root": str(active_root),
        "seq": seq,
        "waiting_on": waiting_on,
        "label": label,
        "degraded": degraded,
        "sustained_lane": lane,
    }


def safe_slug(value: Any) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug[:120] or "unknown"


def read_presence(agent_id: Any, run_id: Any) -> dict[str, Any] | None:
    if not agent_id or not run_id:
        return None
    path = AGENT_PRESENCE_DIR / "runs" / safe_slug(run_id) / f"{safe_slug(agent_id)}.json"
    value = read_json(path, None)
    return value if isinstance(value, dict) else None


def proc_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return False
    try:
        stat_parts = (proc / "stat").read_text(encoding="utf-8", errors="replace").split()
        if len(stat_parts) > 2 and stat_parts[2] == "Z":
            return False
    except Exception:
        pass
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


def active_runner_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def active_runner_liveness(record: dict[str, Any]) -> dict[str, Any]:
    record_pid = active_runner_int(record.get("pid"))
    runner_dir = Path(str(record.get("runner_dir") or ""))
    unit = str(record.get("systemd_unit") or "").strip()
    if runner_dir and (runner_dir / ".runner-exit-marker").exists():
        return {
            "record_pid": record_pid or None,
            "systemd_main_pid": None,
            "systemd_unit": unit or None,
            "systemd_active_state": None,
            "systemd_sub_state": None,
            "alive": False,
            "pid": record_pid,
            "liveness_source": "runner_exit_marker",
        }
    state = systemd_show_unit(unit) if unit else {}
    main_pid = active_runner_int(state.get("MainPID"))
    show_ok = state.get("show_exit_code") == "0"
    base = {
        "record_pid": record_pid or None,
        "systemd_main_pid": main_pid or None,
        "systemd_unit": unit or None,
        "systemd_active_state": state.get("ActiveState"),
        "systemd_sub_state": state.get("SubState"),
    }
    if main_pid and proc_alive(main_pid):
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
    if proc_alive(record_pid):
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


def file_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return None


def task_manifest_paths() -> list[Path]:
    if not TASKS.exists():
        return []
    return sorted(TASKS.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def task_source(task: dict[str, Any]) -> dict[str, Any]:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return source if isinstance(source, dict) else {}


def truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def task_room_id(task: dict[str, Any], fallback: Any = None) -> str:
    context = task.get("context_snapshot") if isinstance(task.get("context_snapshot"), dict) else {}
    return str(task.get("room_id") or context.get("room_id") or fallback or "").strip()


def record_room_id(record: dict[str, Any]) -> str:
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    context = record.get("context_snapshot") if isinstance(record.get("context_snapshot"), dict) else {}
    return str(record.get("room_id") or task_room_id(task) or context.get("room_id") or "").strip()


def room_matches(value: dict[str, Any], room_id: str | None) -> bool:
    target = str(room_id or "").strip()
    if not target:
        return True
    if "task" in value:
        return record_room_id(value) == target
    return task_room_id(value) == target


def task_explicitly_room_visible(task: dict[str, Any]) -> bool:
    source = task_source(task)
    standing = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
    for container in (task, source, standing):
        if truthy(container.get("room_visible")) or truthy(container.get("status_card_room_visible")):
            return True
    return False


def task_is_background(task: dict[str, Any]) -> bool:
    source = task_source(task)
    return (
        str(task.get("requested_by") or "") in BACKGROUND_TRANSPORTS
        or str(source.get("transport") or "") in BACKGROUND_TRANSPORTS
        or str(task.get("lane") or "") in BACKGROUND_LANES
        or bool(task.get("standing_mainline") or task.get("standing_agenda"))
    )


def task_visible_in_room_status(task: dict[str, Any], room_id: str | None, *, include_background: bool = False) -> bool:
    if not room_matches(task, room_id):
        return False
    if include_background:
        return True
    if task_is_background(task) and not task_explicitly_room_visible(task):
        return False
    return True


def collaboration_ledger_path_for_task(task_id: Any) -> Path:
    return COLLAB_LEDGER_DIR / f"{safe_slug(task_id)}.json"


def task_manifest_path_for_task(task_id: Any) -> Path:
    return TASKS / safe_slug(task_id) / "manifest.json"


def lease_state(value: Any) -> str:
    if not value:
        return "missing"
    seconds_left = seconds_until(value, datetime.now(timezone.utc).astimezone())
    if seconds_left is None:
        return "invalid"
    return "expired" if seconds_left <= 0 else "active"


def ledger_agent_completed(task_id: Any, agent_id: Any) -> bool:
    task_key = str(task_id or "").strip()
    agent = str(agent_id or "").strip()
    if not task_key or not agent:
        return False
    ledger = read_json(collaboration_ledger_path_for_task(task_key), None)
    if not isinstance(ledger, dict):
        return False
    if ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return False
    ledger_task_id = str(ledger.get("task_id") or ledger.get("run_id") or "").strip()
    if ledger_task_id and ledger_task_id != task_key:
        return False
    for item in ledger.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("assigned_to") or item.get("claimed_by") or "").strip()
        if owner == agent and str(item.get("status") or "") == "completed":
            return True
    for claim in ledger.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if str(claim.get("agent_id") or "").strip() == agent and str(claim.get("status") or "") == "completed":
            return True
    return False


def collaboration_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    """Return the freshest collaboration state available for a task.

    Manifests are periodically refreshed from the collaboration ledger, but the
    status surface should not wait for that sync before showing material points,
    peer uptake, or challenges.  Overlaying the per-task ledger keeps Alex and
    peer agents from seeing a stale "parallel output only" view.
    """
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    snapshot = dict(collaboration) if isinstance(collaboration, dict) else {}
    task_key = str(task.get("task_id") or task.get("run_id") or "").strip()
    if not task_key:
        return snapshot
    ledger = read_json(collaboration_ledger_path_for_task(task_key), None)
    if not isinstance(ledger, dict):
        return snapshot
    if ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return snapshot
    ledger_task_id = str(ledger.get("task_id") or ledger.get("run_id") or "").strip()
    if ledger_task_id and ledger_task_id != task_key:
        return snapshot
    for key in (
        "status",
        "mode",
        "participants",
        "role_policy",
        "roles",
        "work_items",
        "claims",
        "artifacts",
        "blockers",
        "handoffs",
        "points",
        "uptakes",
        "created_at",
        "updated_at",
    ):
        if key in ledger:
            snapshot[key] = ledger[key]
    return snapshot


def increment_count(counts: dict[str, int], key: Any) -> None:
    text = str(key or "").strip()
    if not text:
        return
    counts[text] = counts.get(text, 0) + 1


def merge_counts(target: dict[str, int], source: dict[str, Any]) -> None:
    for key, value in source.items():
        try:
            amount = int(value)
        except Exception:
            continue
        if amount:
            target[str(key)] = target.get(str(key), 0) + amount


def text_preview(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def declared_scope_summary(collaboration: dict[str, Any]) -> dict[str, Any]:
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    path_to_agents: dict[str, set[str]] = {}
    paths_by_agent: dict[str, set[str]] = {}
    scoped_work_item_count = 0
    path_entry_count = 0
    for item in work_items:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("assigned_to") or item.get("claimed_by") or "").strip()
        scope = item.get("declared_scope") if isinstance(item.get("declared_scope"), dict) else None
        if not agent_id or scope is None:
            continue
        clean_paths = sorted({str(path).strip() for path in (scope.get("paths") or []) if str(path).strip()})
        if not clean_paths:
            continue
        scoped_work_item_count += 1
        path_entry_count += len(clean_paths)
        agent_paths = paths_by_agent.setdefault(agent_id, set())
        for path in clean_paths:
            agent_paths.add(path)
            path_to_agents.setdefault(path, set()).add(agent_id)
    conflicts = [
        {"path": path, "agents": sorted(agents)}
        for path, agents in sorted(path_to_agents.items())
        if len(agents) > 1
    ]
    return {
        "declared_scope_work_items": scoped_work_item_count,
        "declared_scope_path_count": path_entry_count,
        "declared_scope_unique_path_count": len(path_to_agents),
        "declared_scope_path_counts_by_agent": {
            agent_id: len(paths)
            for agent_id, paths in sorted(paths_by_agent.items())
        },
        "declared_scope_paths_by_agent": {
            agent_id: sorted(paths)[:8]
            for agent_id, paths in sorted(paths_by_agent.items())
        },
        "scope_conflict_count": len(conflicts),
        "scope_conflicts": conflicts[:8],
    }


def int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def provider_window_summary(window: Any) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    out: dict[str, Any] = {}
    for key in (
        "label",
        "remaining_known",
        "used_known",
        "observed_only",
        "display_in_quota_card",
        "remaining_percent",
        "observed_used_requests",
        "source",
        "limitation",
    ):
        if key in window:
            out[key] = window.get(key)
    return out


def token_channel_summary(channel: Any) -> dict[str, Any] | None:
    if not isinstance(channel, dict):
        return None
    available_models = int_or_none(channel.get("available_models"))
    total_models = int_or_none(channel.get("total_models"))
    raw_windows = channel.get("windows") if isinstance(channel.get("windows"), list) else []
    windows = [
        item for item in (provider_window_summary(window) for window in raw_windows)
        if item is not None
    ]
    return {
        "id": channel.get("id"),
        "display_name": channel.get("display_name"),
        "status": channel.get("status"),
        "availability_known": channel.get("availability_known"),
        "remaining_known": channel.get("remaining_known"),
        "live_usage_known": channel.get("live_usage_known"),
        "available_models": available_models,
        "total_models": total_models,
        "usage_api_status": channel.get("usage_api_status"),
        "usage_api_credential_accepted": channel.get("usage_api_credential_accepted"),
        "current_quota_basis": channel.get("current_quota_basis"),
        "updated_at": channel.get("updated_at"),
        "expires_at": channel.get("expires_at"),
        "source": channel.get("source"),
        "limitation": channel.get("limitation"),
        "windows": windows,
    }


def agent_quota_summary(agent_state: Any, now: datetime) -> dict[str, Any]:
    if not isinstance(agent_state, dict):
        return {
            "status": "unknown",
            "active_cooldown_count": 0,
            "stale_depleted_model_count": 0,
            "available_model_count": 0,
        }
    models = agent_state.get("models") if isinstance(agent_state.get("models"), dict) else {}
    active_cooldowns: list[dict[str, Any]] = []
    stale_depleted_models: list[str] = []
    available_models: list[str] = []
    for model, state in sorted(models.items()):
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "")
        if status == "available":
            available_models.append(str(model))
            continue
        if status == "depleted":
            cooldown_until = state.get("cooldown_until")
            cooldown_dt = parse_iso_datetime(cooldown_until)
            if cooldown_dt and cooldown_dt > now:
                active_cooldowns.append({
                    "model": str(model),
                    "reason": state.get("reason"),
                    "cooldown_until": cooldown_until,
                })
            else:
                stale_depleted_models.append(str(model))
    return {
        "status": agent_state.get("status") or "unknown",
        "updated_at": agent_state.get("updated_at"),
        "recovered_at": agent_state.get("recovered_at"),
        "active_cooldown_count": len(active_cooldowns),
        "active_cooldowns": active_cooldowns,
        "stale_depleted_model_count": len(stale_depleted_models),
        "stale_depleted_models": stale_depleted_models[:8],
        "available_model_count": len(available_models),
        "available_models": available_models[:8],
    }


def classify_provider_availability(channels: list[dict[str, Any]], quota: dict[str, Any]) -> str:
    if int(quota.get("active_cooldown_count") or 0) > 0:
        return "degraded"
    known_channels = [channel for channel in channels if isinstance(channel, dict)]
    if not known_channels:
        return "unknown"
    if any(str(channel.get("status") or "") == "available" for channel in known_channels):
        return "available"
    for channel in known_channels:
        available = int_or_none(channel.get("available_models"))
        total = int_or_none(channel.get("total_models"))
        if available is not None and available > 0:
            return "degraded" if total is not None and available < total else "available"
    if any(channel.get("availability_known") is True for channel in known_channels):
        return "unknown"
    return "unknown"


def provider_health_snapshot() -> dict[str, Any]:
    """Read-only provider/token health for collaboration availability.

    This is deliberately diagnostic.  It does not select a model, trigger a
    provider call, or change any workflow quality gate; it only lets the Agent
    Room status surface explain whether peer collaboration is running with full,
    degraded, or unknown provider capacity.
    """
    signal = read_json(MODEL_QUOTA_SIGNAL, {}) or {}
    quota_state = read_json(AGENT_QUOTA_STATE, {}) or {}
    now = datetime.now(timezone.utc).astimezone()
    signal_expires_at = signal.get("expires_at") if isinstance(signal, dict) else None
    expires_dt = parse_iso_datetime(signal_expires_at)
    signal_fresh = bool(expires_dt and expires_dt > now)
    token_channels_raw = signal.get("token_channels") if isinstance(signal, dict) else {}
    token_channels_raw = token_channels_raw if isinstance(token_channels_raw, dict) else {}
    channel_ids = ("codex", "ark-coding-plan")
    token_channels = {
        channel_id: summary
        for channel_id in channel_ids
        for summary in [token_channel_summary(token_channels_raw.get(channel_id))]
        if summary is not None
    }
    quota_agents_raw = quota_state.get("agents") if isinstance(quota_state, dict) else {}
    quota_agents_raw = quota_agents_raw if isinstance(quota_agents_raw, dict) else {}
    agent_channels = {
        ROOM_RUNTIME_AGENT: ["codex", "ark-coding-plan"],
        "codex": ["codex"],
        "claude-code": ["ark-coding-plan"],
    }
    per_agent: dict[str, Any] = {}
    degraded_agents: list[str] = []
    unknown_agents: list[str] = []
    for agent_id, ids in agent_channels.items():
        channels = [token_channels[channel_id] for channel_id in ids if channel_id in token_channels]
        quota_agent = agent_quota_summary(quota_agents_raw.get(agent_id), now)
        if agent_id == ROOM_RUNTIME_AGENT:
            main_quota = quota_agents_raw.get("openclaw_main") or quota_agents_raw.get("main")
            if not quota_agent.get("available_model_count") and isinstance(main_quota, dict):
                quota_agent = agent_quota_summary(main_quota, now)
        availability = classify_provider_availability(channels, quota_agent)
        if availability == "degraded":
            degraded_agents.append(agent_id)
        elif availability == "unknown":
            unknown_agents.append(agent_id)
        per_agent[agent_id] = {
            "agent_id": agent_id,
            "availability": availability,
            "token_channels": ids,
            "active_cooldown_count": quota_agent.get("active_cooldown_count"),
            "available_model_count": quota_agent.get("available_model_count"),
            "stale_depleted_model_count": quota_agent.get("stale_depleted_model_count"),
            "quota_state": quota_agent,
        }
    limitations = [
        str(channel.get("limitation"))
        for channel in token_channels.values()
        if isinstance(channel, dict) and channel.get("limitation")
    ]
    return {
        "schema": "openclaw.agent_room.provider_health.v0",
        "purpose": "read-only provider/token-channel availability for collaboration quorum interpretation",
        "generated_at": now_iso(),
        "signal_generated_at": signal.get("generated_at") if isinstance(signal, dict) else None,
        "signal_updated_at": signal.get("updated_at") if isinstance(signal, dict) else None,
        "signal_expires_at": signal_expires_at,
        "signal_fresh": signal_fresh,
        "token_channels": token_channels,
        "per_agent": per_agent,
        "degraded_agents": degraded_agents,
        "unknown_agents": unknown_agents,
        "remaining_unknown_channels": [
            channel_id
            for channel_id, channel in token_channels.items()
            if isinstance(channel, dict) and channel.get("remaining_known") is False
        ],
        "limitations": limitations[:8],
        "source_paths": {
            "model_quota_signal": str(MODEL_QUOTA_SIGNAL),
            "agent_quota_state": str(AGENT_QUOTA_STATE),
        },
        "safety": {
            "read_only": True,
            "does_not_route_models": True,
            "does_not_change_quality_gates": True,
            "does_not_read_secrets": True,
        },
    }


def register_work_scope(task_id: str, agent_id: str, scope_paths: list[str], *, scope_type: str = "file_edit") -> dict[str, Any]:
    """Register an agent's intended work scope for duplicate-work prevention.

    Before starting implementation, an agent declares which file paths or
    artifact paths it intends to touch.  Peer agents can then call
    detect_scope_conflicts() to avoid overlapping work.

    The scope is recorded in the collaboration ledger's work_items entry
    for the claiming agent, under a ``declared_scope`` key.
    """
    task_key = str(task_id or "").strip()
    agent = str(agent_id or "").strip()
    if not task_key or not agent:
        return {"ok": False, "error": "task_id and agent_id required"}
    ledger_path = collaboration_ledger_path_for_task(task_key)
    ledger = read_json(ledger_path, None)
    if not isinstance(ledger, dict):
        return {"ok": False, "error": "ledger_not_found"}
    if ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return {"ok": False, "error": "invalid_ledger_schema"}
    clean_paths = sorted({str(p).strip() for p in scope_paths if str(p).strip()})
    if not clean_paths:
        return {"ok": False, "error": "empty_scope_paths"}
    updated = False
    for item in ledger.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("assigned_to") or item.get("claimed_by") or "").strip()
        if owner != agent:
            continue
        existing = item.get("declared_scope") if isinstance(item.get("declared_scope"), dict) else {}
        existing_type = existing.get("scope_type") or scope_type
        existing_paths = set(existing.get("paths") or [])
        merged = sorted(existing_paths.union(clean_paths))
        item["declared_scope"] = {
            "scope_type": existing_type,
            "paths": merged,
            "updated_at": now_iso(),
        }
        updated = True
        break
    if not updated:
        return {"ok": False, "error": "no_work_item_found_for_agent"}
    ledger["updated_at"] = now_iso()
    write_json(ledger_path, ledger)
    return {
        "ok": True,
        "task_id": task_key,
        "agent_id": agent,
        "scope_type": scope_type,
        "paths": clean_paths,
        "registered_at": now_iso(),
    }


def detect_scope_conflicts(task_id: str) -> dict[str, Any]:
    """Detect overlapping declared scopes across agents for a task.

    Returns a list of conflict entries, each identifying a shared path and
    the agents that declared it.  Agents can use this to decide whether to
    narrow their scope, hand off, or coordinate.
    """
    task_key = str(task_id or "").strip()
    if not task_key:
        return {"ok": False, "error": "task_id required", "conflicts": []}
    ledger_path = collaboration_ledger_path_for_task(task_key)
    ledger = read_json(ledger_path, None)
    if not isinstance(ledger, dict):
        return {"ok": False, "error": "ledger_not_found", "conflicts": []}
    agent_scopes: dict[str, set[str]] = {}
    for item in ledger.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("assigned_to") or item.get("claimed_by") or "").strip()
        if not owner:
            continue
        scope = item.get("declared_scope") if isinstance(item.get("declared_scope"), dict) else None
        if scope is None:
            continue
        paths = scope.get("paths") or []
        agent_scopes[owner] = set(str(p) for p in paths if str(p).strip())
    if len(agent_scopes) < 2:
        return {"ok": True, "task_id": task_key, "conflicts": [], "agent_scopes": {k: sorted(v) for k, v in agent_scopes.items()}}
    path_to_agents: dict[str, list[str]] = {}
    for agent_id, paths in sorted(agent_scopes.items()):
        for path in sorted(paths):
            path_to_agents.setdefault(path, []).append(agent_id)
    conflicts = [
        {"path": path, "agents": agents}
        for path, agents in sorted(path_to_agents.items())
        if len(agents) > 1
    ]
    return {
        "ok": True,
        "task_id": task_key,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "agent_scopes": {k: sorted(v) for k, v in agent_scopes.items()},
    }


def collaboration_metrics(collaboration: dict[str, Any], target_agents: list[str] | None = None) -> dict[str, Any]:
    points = collaboration.get("points") if isinstance(collaboration.get("points"), list) else []
    uptakes = collaboration.get("uptakes") if isinstance(collaboration.get("uptakes"), list) else []
    handoffs = collaboration.get("handoffs") if isinstance(collaboration.get("handoffs"), list) else []
    blockers = collaboration.get("blockers") if isinstance(collaboration.get("blockers"), list) else []
    claims = collaboration.get("claims") if isinstance(collaboration.get("claims"), list) else []
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    participants = [
        str(agent_id)
        for agent_id in (collaboration.get("participants") or target_agents or [])
        if str(agent_id) in LOCAL_AGENTS
    ]
    if target_agents:
        for agent_id in target_agents:
            text = str(agent_id)
            if text in LOCAL_AGENTS and text not in participants:
                participants.append(text)
    expected_agents = set(participants) or set(LOCAL_AGENTS)

    point_counts_by_agent: dict[str, int] = {}
    uptake_counts_by_agent: dict[str, int] = {}
    challenge_counts_by_agent: dict[str, int] = {}
    blocker_counts_by_agent: dict[str, int] = {}
    expired_claim_counts_by_agent: dict[str, int] = {}
    material_uptake_agents_by_point: dict[str, set[str]] = {}
    uptakes_by_point: dict[str, list[dict[str, Any]]] = {}
    point_ids = {
        str(point.get("id") or "")
        for point in points
        if isinstance(point, dict) and point.get("id")
    }

    peer_uptake_count = 0
    peer_challenge_count = 0
    incorporated_uptake_count = 0
    for point in points:
        if isinstance(point, dict):
            increment_count(point_counts_by_agent, point.get("agent_id"))
    for uptake in uptakes:
        if not isinstance(uptake, dict):
            continue
        status = str(uptake.get("status") or "")
        by_agent = str(uptake.get("by_agent") or "")
        point_agent = str(uptake.get("point_agent_id") or "")
        point_id = str(uptake.get("point_id") or "")
        if point_id:
            uptakes_by_point.setdefault(point_id, []).append(uptake)
        if status in MATERIAL_UPTAKE_STATUSES and by_agent and by_agent != point_agent:
            peer_uptake_count += 1
            increment_count(uptake_counts_by_agent, by_agent)
            material_uptake_agents_by_point.setdefault(point_id, set()).add(by_agent)
            if status in CHALLENGE_UPTAKE_STATUSES:
                peer_challenge_count += 1
                increment_count(challenge_counts_by_agent, by_agent)
            if status == "incorporated":
                incorporated_uptake_count += 1

    points_without_peer_uptake_ids: list[str] = []
    pending_uptake_agents: set[str] = set()
    pending_uptake_by_point: dict[str, list[str]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("id") or "")
        if point_id not in point_ids:
            continue
        point_agent = str(point.get("agent_id") or "")
        expected_peer_agents = {agent_id for agent_id in expected_agents if agent_id != point_agent}
        if not expected_peer_agents:
            continue
        received_agents = material_uptake_agents_by_point.get(point_id, set())
        missing_agents = expected_peer_agents.difference(received_agents)
        if missing_agents:
            points_without_peer_uptake_ids.append(point_id)
            pending_uptake_agents.update(missing_agents)
            pending_uptake_by_point[point_id] = sorted(missing_agents)

    recent_material_threads: list[dict[str, Any]] = []
    for point in list(points)[-8:]:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("id") or "")
        point_agent = str(point.get("agent_id") or "")
        peer_uptakes = []
        for uptake in (uptakes_by_point.get(point_id) or [])[-4:]:
            if not isinstance(uptake, dict):
                continue
            by_agent = str(uptake.get("by_agent") or "")
            if by_agent and by_agent == point_agent:
                continue
            peer_uptakes.append({
                "uptake_id": uptake.get("id"),
                "by_agent": by_agent,
                "status": uptake.get("status"),
                "reason": text_preview(uptake.get("reason"), 120),
                "behavior_impact": text_preview(uptake.get("behavior_impact"), 120),
            })
        recent_material_threads.append({
            "point_id": point_id,
            "agent_id": point_agent,
            "kind": point.get("kind"),
            "status": point.get("status"),
            "text": text_preview(point.get("text")),
            "peer_uptakes": peer_uptakes,
            "pending_uptake_agents": pending_uptake_by_point.get(point_id, []),
        })

    summary_point_count = 0
    summary_peer_uptake_count = 0
    integrated_summary_ids: list[str] = []
    summary_without_peer_uptake_ids: list[str] = []
    summary_needs_integration_ids: list[str] = []
    closure_summary_candidate_point_ids: list[str] = []
    closure_summary_candidate_agent = ""
    for point in points:
        if not isinstance(point, dict):
            continue
        kind = str(point.get("kind") or "").strip()
        if kind not in SUMMARY_POINT_KINDS:
            continue
        summary_point_count += 1
        point_id = str(point.get("id") or "")
        point_agent = str(point.get("agent_id") or "")
        expected_peer_agents = {agent_id for agent_id in expected_agents if agent_id != point_agent}
        peer_uptakes = [
            uptake for uptake in (uptakes_by_point.get(point_id) or [])
            if isinstance(uptake, dict)
            and str(uptake.get("status") or "") in MATERIAL_UPTAKE_STATUSES
            and str(uptake.get("by_agent") or "")
            and str(uptake.get("by_agent") or "") != point_agent
        ]
        summary_peer_uptake_count += len(peer_uptakes)
        integrated_uptakes = [
            uptake for uptake in peer_uptakes
            if str(uptake.get("status") or "") in INTEGRATED_SUMMARY_STATUSES
        ]
        point_status = str(point.get("status") or "")
        integrated = (
            bool(integrated_uptakes)
            or point_status in INTEGRATED_SUMMARY_STATUSES
            or truthy(point.get("integrated"))
            or truthy(point.get("integrated_summary"))
        )
        if integrated:
            integrated_summary_ids.append(point_id)
        if expected_peer_agents:
            received_agents = {
                str(uptake.get("by_agent") or "")
                for uptake in peer_uptakes
                if str(uptake.get("by_agent") or "")
            }
            missing_agents = expected_peer_agents.difference(received_agents)
            if missing_agents:
                summary_without_peer_uptake_ids.append(point_id)
        if not integrated and (expected_peer_agents or point_status in CHALLENGE_UPTAKE_STATUSES):
            summary_needs_integration_ids.append(point_id)
    if summary_point_count == 0 and peer_uptake_count > 0:
        for point in reversed(points):
            if not isinstance(point, dict):
                continue
            point_id = str(point.get("id") or "")
            point_agent = str(point.get("agent_id") or "")
            if not point_id or point_agent not in LOCAL_AGENTS:
                continue
            peer_uptakes = [
                uptake for uptake in (uptakes_by_point.get(point_id) or [])
                if isinstance(uptake, dict)
                and str(uptake.get("status") or "") in MATERIAL_UPTAKE_STATUSES
                and str(uptake.get("by_agent") or "")
                and str(uptake.get("by_agent") or "") != point_agent
            ]
            if not peer_uptakes:
                continue
            closure_summary_candidate_point_ids.append(point_id)
            closure_summary_candidate_agent = point_agent
            break
    open_blocker_ids: list[str] = []
    recent_blockers: list[dict[str, Any]] = []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        blocker_id = str(blocker.get("id") or f"blk-{len(open_blocker_ids) + len(recent_blockers) + 1:03d}")
        agent_id = str(blocker.get("agent_id") or "").strip()
        status = str(blocker.get("status") or "open").strip() or "open"
        increment_count(blocker_counts_by_agent, agent_id)
        if status not in {"resolved", "closed", "superseded"}:
            open_blocker_ids.append(blocker_id)
        recent_blockers.append({
            "blocker_id": blocker_id,
            "agent_id": agent_id,
            "work_item_id": blocker.get("work_item_id"),
            "status": status,
            "reason": text_preview(blocker.get("reason"), 160),
            "detail": text_preview(blocker.get("detail"), 220),
        })
    active_claim_count = 0
    expired_claim_ids: list[str] = []
    missing_claim_lease_count = 0
    claim_rows = claims if claims else work_items
    now = datetime.now(timezone.utc).astimezone()
    for row in claim_rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip()
        if status not in ACTIVE_CLAIM_STATUSES:
            continue
        active_claim_count += 1
        lease_expiry = row.get("lease_expiry")
        if not lease_expiry:
            missing_claim_lease_count += 1
            continue
        seconds_left = seconds_until(lease_expiry, now)
        if seconds_left is None:
            missing_claim_lease_count += 1
            continue
        if seconds_left <= 0:
            claim_id = str(row.get("work_item_id") or row.get("id") or f"claim-{active_claim_count}")
            expired_claim_ids.append(claim_id)
            increment_count(
                expired_claim_counts_by_agent,
                row.get("agent_id") or row.get("claimed_by") or row.get("assigned_to"),
            )
    declared_scope = declared_scope_summary(collaboration)
    return {
        "status": collaboration.get("status"),
        "points": len(points),
        "material_points": len(points),
        "uptakes": len(uptakes),
        "peer_uptakes": peer_uptake_count,
        "peer_challenges": peer_challenge_count,
        "incorporated_uptakes": incorporated_uptake_count,
        "summary_points": summary_point_count,
        "summary_peer_uptakes": summary_peer_uptake_count,
        "integrated_summaries": len(integrated_summary_ids),
        "integrated_summary_ids": integrated_summary_ids[:8],
        "summary_points_without_peer_uptake": len(summary_without_peer_uptake_ids),
        "summary_points_without_peer_uptake_ids": summary_without_peer_uptake_ids[:8],
        "summary_needs_integration": len(summary_needs_integration_ids),
        "summary_needs_integration_ids": summary_needs_integration_ids[:8],
        "closure_summary_needed": 1 if closure_summary_candidate_point_ids else 0,
        "closure_summary_candidate_point_ids": closure_summary_candidate_point_ids[:8],
        "closure_summary_candidate_agent": closure_summary_candidate_agent,
        "integration_signals": summary_point_count + incorporated_uptake_count + len(handoffs),
        "handoffs": len(handoffs),
        "blockers": len(blockers),
        "open_blockers": len(open_blocker_ids),
        "open_blocker_ids": open_blocker_ids[:8],
        "active_claims": active_claim_count,
        "expired_claims": len(expired_claim_ids),
        "expired_claim_ids": expired_claim_ids[:8],
        "missing_claim_leases": missing_claim_lease_count,
        "points_without_peer_uptake": len(points_without_peer_uptake_ids),
        "points_without_peer_uptake_ids": points_without_peer_uptake_ids[:8],
        "pending_uptake_agents": sorted(pending_uptake_agents),
        "point_counts_by_agent": point_counts_by_agent,
        "peer_uptake_counts_by_agent": uptake_counts_by_agent,
        "peer_challenge_counts_by_agent": challenge_counts_by_agent,
        "blocker_counts_by_agent": blocker_counts_by_agent,
        "expired_claim_counts_by_agent": expired_claim_counts_by_agent,
        "recent_material_threads": recent_material_threads,
        "recent_blockers": recent_blockers[-8:],
        **declared_scope,
    }


def collaboration_efficiency_score(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compute a collaboration efficiency score from collaboration_metrics() output.

    The score quantifies how efficiently agents collaborated on a task:
    - peer_uptake_ratio: fraction of material points that received peer uptake
    - integration_ratio: fraction of points that reached integrated status
    - claim_efficiency: fraction of claims that didn't expire unused
    - stall_indicator: whether more than half of points lack peer uptake
    - overall: weighted combination (0..1, higher is better)

    This is a diagnostic metric; it does not change any workflow or quality gate.
    It exists so the standing collaboration-efficiency loop can measure itself
    across iterations and detect regressions.
    """
    points = max(int(metrics.get("points") or 0), 1)
    peer_uptakes = int(metrics.get("peer_uptakes") or 0)
    incorporated = int(metrics.get("incorporated_uptakes") or 0)
    summary_points = int(metrics.get("summary_points") or 0)
    integrated_summaries = int(metrics.get("integrated_summaries") or 0)
    handoffs = int(metrics.get("handoffs") or 0)
    active_claims = max(int(metrics.get("active_claims") or 0), 0)
    expired_claims = int(metrics.get("expired_claims") or 0)
    blockers = int(metrics.get("blockers") or 0)
    points_without_peer = int(metrics.get("points_without_peer_uptake") or 0)

    uptake_ratio = min(peer_uptakes / points, 1.0)
    integration_total = incorporated + integrated_summaries + handoffs
    integration_ratio = min(integration_total / points, 1.0)
    claim_total = active_claims + expired_claims
    claim_efficiency = max(active_claims, 1) / max(claim_total, 1) if claim_total > 0 else 1.0
    stall = 1.0 if points_without_peer > points / 2 else 0.0

    overall = round(
        0.40 * uptake_ratio +
        0.30 * integration_ratio +
        0.20 * claim_efficiency -
        0.10 * stall,
        3,
    )
    overall = max(min(overall, 1.0), 0.0)

    return {
        "schema": "openclaw.agent_room.efficiency_score.v0",
        "peer_uptake_ratio": round(uptake_ratio, 3),
        "integration_ratio": round(integration_ratio, 3),
        "claim_efficiency": round(claim_efficiency, 3),
        "stall_indicator": stall > 0,
        "blocker_count": blockers,
        "overall": overall,
        "grade": (
            "high" if overall >= 0.7 else
            "medium" if overall >= 0.4 else
            "low"
        ),
    }


def per_agent_collaboration_progress(
    collaboration: dict[str, Any],
    target_agents: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Decompose collaboration progress per-agent for the status surface.

    Distinguishes 'alive runner' from 'materially contributing to the discussion':
    - points_produced: how many material points this agent created
    - points_with_peer_uptake: of those, how many received peer uptake
    - peer_points_uptaken: how many *other* agents' points this agent uptake'd
    - peer_challenges: how many of those uptakes were challenges/rejections
    - integration_contributed: whether this agent produced or integrated a summary
    - liveness_vs_progress: 'producing', 'reviewing_only', 'silent', or 'absent'

    This directly addresses the standing item goal: make agent collaboration
    visible at per-agent granularity, not just as an aggregate score.
    """
    points = collaboration.get("points") if isinstance(collaboration.get("points"), list) else []
    uptakes = collaboration.get("uptakes") if isinstance(collaboration.get("uptakes"), list) else []
    handoffs = collaboration.get("handoffs") if isinstance(collaboration.get("handoffs"), list) else []

    participants = [
        str(a) for a in (collaboration.get("participants") or target_agents or [])
        if str(a) in LOCAL_AGENTS or str(a) == ROOM_RUNTIME_AGENT
    ]
    if target_agents:
        for a in target_agents:
            t = str(a)
            if t not in participants and (t in LOCAL_AGENTS or t == ROOM_RUNTIME_AGENT):
                participants.append(t)
    all_agents = sorted(set(participants) or LOCAL_AGENTS.union({ROOM_RUNTIME_AGENT}))

    # Index points and uptakes
    point_by_id: dict[str, dict[str, Any]] = {}
    for pt in points:
        if isinstance(pt, dict) and pt.get("id"):
            point_by_id[str(pt["id"])] = pt
    uptakes_by_point: dict[str, list[dict[str, Any]]] = {}
    for up in uptakes:
        if not isinstance(up, dict):
            continue
        pid = str(up.get("point_id") or "")
        if pid:
            uptakes_by_point.setdefault(pid, []).append(up)

    result: dict[str, dict[str, Any]] = {}
    for agent_id in all_agents:
        my_points = [pt for pt in points if isinstance(pt, dict) and str(pt.get("agent_id") or "") == agent_id]
        my_point_ids = {str(pt.get("id") or "") for pt in my_points}
        my_points_with_peer_uptake = 0
        for pid in my_point_ids:
            peer_ups = [
                up for up in uptakes_by_point.get(pid, [])
                if isinstance(up, dict)
                and str(up.get("by_agent") or "") != agent_id
                and str(up.get("status") or "") in MATERIAL_UPTAKE_STATUSES
            ]
            if peer_ups:
                my_points_with_peer_uptake += 1

        my_uptakes = [
            up for up in uptakes
            if isinstance(up, dict)
            and str(up.get("by_agent") or "") == agent_id
            and str(up.get("status") or "") in MATERIAL_UPTAKE_STATUSES
        ]
        my_challenges = [
            up for up in my_uptakes
            if str(up.get("status") or "") in CHALLENGE_UPTAKE_STATUSES
        ]
        my_integration = [
            up for up in my_uptakes
            if str(up.get("status") or "") in INTEGRATED_SUMMARY_STATUSES
        ]

        my_summaries = [
            pt for pt in my_points
            if isinstance(pt, dict) and str(pt.get("kind") or "") in SUMMARY_POINT_KINDS
        ]
        my_integrated_summaries = [
            pt for pt in my_summaries
            if str(pt.get("status") or "") in INTEGRATED_SUMMARY_STATUSES
            or pt.get("integrated")
        ]

        produced = len(my_points) > 0
        reviewed = len(my_uptakes) > 0
        if produced and reviewed:
            liveness = "producing_and_reviewing"
        elif produced:
            liveness = "producing_not_yet_reviewed"
        elif reviewed:
            liveness = "reviewing_only"
        else:
            liveness = "silent"

        result[agent_id] = {
            "agent_id": agent_id,
            "points_produced": len(my_points),
            "points_with_peer_uptake": my_points_with_peer_uptake,
            "peer_points_uptaken": len(my_uptakes),
            "peer_challenges": len(my_challenges),
            "peer_integrations": len(my_integration),
            "summaries_produced": len(my_summaries),
            "summaries_integrated": len(my_integrated_summaries),
            "liveness_vs_progress": liveness,
        }

    # Add absent agents (known LOCAL_AGENTS + ROOM_RUNTIME_AGENT not in participants)
    for agent_id in sorted(LOCAL_AGENTS.union({ROOM_RUNTIME_AGENT})):
        if agent_id not in result:
            result[agent_id] = {
                "agent_id": agent_id,
                "points_produced": 0,
                "points_with_peer_uptake": 0,
                "peer_points_uptaken": 0,
                "peer_challenges": 0,
                "peer_integrations": 0,
                "summaries_produced": 0,
                "summaries_integrated": 0,
                "liveness_vs_progress": "absent",
            }

    return result


def merge_per_agent_discussion_progress(
    target: dict[str, Any],
    progress: dict[str, Any],
) -> None:
    numeric_keys = (
        "points_produced",
        "points_with_peer_uptake",
        "peer_points_uptaken",
        "peer_challenges",
        "peer_integrations",
        "summaries_produced",
        "summaries_integrated",
    )
    for agent_id, data in sorted(progress.items()):
        agent = str(agent_id or "").strip()
        if not agent or not isinstance(data, dict):
            continue
        counts = {
            key: int_or_none(data.get(key)) or 0
            for key in numeric_keys
        }
        state = str(data.get("liveness_vs_progress") or "unknown").strip() or "unknown"
        has_material_signal = any(counts.values())
        if not has_material_signal and state in {"absent", "silent"}:
            continue
        row = target.setdefault(
            agent,
            {
                **{key: 0 for key in numeric_keys},
                "liveness_vs_progress_counts": {},
            },
        )
        for key, value in counts.items():
            row[key] = int(row.get(key) or 0) + value
        states = row.setdefault("liveness_vs_progress_counts", {})
        states[state] = int(states.get(state) or 0) + 1


def turn_preflight_advisory(task_id: str, requesting_agent_id: str) -> dict[str, Any]:
    """Pre-turn advisory for an agent about to start work.

    Synthesizes ledger state, peer presence, provider health, and declared
    scopes into actionable guidance: what the peer already covered, what
    needs uptake, whether to expand scope due to peer degradation, and
    which file paths to avoid duplicating.

    This is read-only and advisory; it does not change workflow or quality gates.
    """
    agent_id = str(requesting_agent_id or "").strip()
    if agent_id not in LOCAL_AGENTS:
        return {"ok": False, "error": "unknown_agent", "advisory": None}
    peer_agents = [a for a in LOCAL_AGENTS if a != agent_id]

    ledger_path = collaboration_ledger_path_for_task(task_id)
    ledger = read_json(ledger_path, None)
    if not isinstance(ledger, dict):
        return {"ok": False, "error": "ledger_not_found", "advisory": None}

    collaboration = collaboration_snapshot({"task_id": task_id})
    metrics = collaboration_metrics(collaboration, target_agents=list(LOCAL_AGENTS))
    health = provider_health_snapshot()
    efficiency = collaboration_efficiency_score(metrics)

    # Peer's declared scope (paths to avoid)
    peer_scopes: dict[str, list[str]] = {}
    peer_work_items: list[str] = []
    for item in ledger.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("assigned_to") or item.get("claimed_by") or "")
        if owner in peer_agents:
            scope = item.get("declared_scope") if isinstance(item.get("declared_scope"), dict) else None
            if scope and scope.get("paths"):
                peer_scopes[owner] = scope.get("paths", [])
            if item.get("id"):
                peer_work_items.append(str(item.get("id")))

    # Points needing uptake from this agent
    pending_points: list[dict[str, Any]] = []
    for thread in metrics.get("recent_material_threads") or []:
        if not isinstance(thread, dict):
            continue
        thread_agent = str(thread.get("agent_id") or "")
        if thread_agent == agent_id:
            continue
        pending = thread.get("pending_uptake_agents") or []
        if agent_id in pending:
            pending_points.append({
                "point_id": thread.get("point_id"),
                "by_agent": thread_agent,
                "kind": thread.get("kind"),
                "text": thread.get("text"),
            })

    # Provider degradation → scope expansion signal
    degraded = health.get("degraded_agents") or []
    peer_degraded = [a for a in degraded if a in peer_agents]
    should_expand_scope = len(peer_degraded) > 0

    # Scope conflict check
    conflicts = detect_scope_conflicts(task_id)
    conflict_paths = []
    for entry in conflicts.get("conflicts") or []:
        if isinstance(entry, dict):
            path = entry.get("path")
            if path:
                conflict_paths.append(str(path))
            conflict_paths.extend(str(value) for value in (entry.get("paths") or []) if str(value).strip())

    advisory = {
        "task_id": task_id,
        "agent_id": agent_id,
        "peer_agents": peer_agents,
        "peer_degraded_agents": peer_degraded,
        "should_expand_scope": should_expand_scope,
        "peer_declared_scopes": peer_scopes,
        "peer_work_items": peer_work_items,
        "scope_conflicts": conflicts.get("conflicts") or [],
        "conflict_paths": sorted(set(conflict_paths)),
        "points_needing_uptake": pending_points,
        "efficiency": efficiency,
        "collaboration_status": collaboration.get("status"),
        "pending_uptake_agents": metrics.get("pending_uptake_agents") or [],
    }
    return {"ok": True, "advisory": advisory}


def status_quality_gate_from_collaboration(
    task: dict[str, Any],
    collaboration: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any] | None:
    """Derive the status-surface quality gate from the freshest ledger snapshot.

    Task manifests can lag behind the collaboration ledger when a peer records a
    late uptake or artifact. The status surface should reflect that durable
    ledger evidence instead of keeping a stale degraded-quorum line alive.
    """
    target_agents = [
        str(agent_id)
        for agent_id in (task.get("target_agents") or collaboration.get("participants") or [])
        if str(agent_id) in LOCAL_AGENTS
    ]
    local_targets = sorted(set(target_agents))
    if len(local_targets) <= 1:
        return None

    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    claims = collaboration.get("claims") if isinstance(collaboration.get("claims"), list) else []
    artifacts = collaboration.get("artifacts") if isinstance(collaboration.get("artifacts"), list) else []
    blockers = collaboration.get("blockers") if isinstance(collaboration.get("blockers"), list) else []
    completed_agents: set[str] = set()
    artifact_agents: set[str] = set()
    blocker_agents: set[str] = set()
    for item in work_items:
        if not isinstance(item, dict) or str(item.get("status") or "") != "completed":
            continue
        agent_id = str(item.get("assigned_to") or item.get("claimed_by") or "").strip()
        if agent_id in LOCAL_AGENTS:
            completed_agents.add(agent_id)
    for claim in claims:
        if not isinstance(claim, dict) or str(claim.get("status") or "") != "completed":
            continue
        agent_id = str(claim.get("agent_id") or "").strip()
        if agent_id in LOCAL_AGENTS:
            completed_agents.add(agent_id)
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        agent_id = str(artifact.get("agent_id") or artifact.get("produced_by") or "").strip()
        if agent_id in LOCAL_AGENTS:
            artifact_agents.add(agent_id)
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        agent_id = str(blocker.get("agent_id") or "").strip()
        if agent_id in LOCAL_AGENTS:
            blocker_agents.add(agent_id)

    missing = sorted(set(local_targets).difference(completed_agents))
    if missing:
        return None
    if blocker_agents:
        return {"status": "degraded_quorum", "reason": "ledger_blockers_recorded", "blocker_agents": sorted(blocker_agents)}
    if int(metrics.get("handoffs") or 0) > 0:
        return {"status": "peer_reviewed", "reason": "ledger_handoff_recorded"}
    if int(metrics.get("points_without_peer_uptake") or 0) > 0:
        return {
            "status": "needs_collaboration_review",
            "reason": "ledger_points_missing_peer_uptake",
            "points_without_peer_uptake": int(metrics.get("points_without_peer_uptake") or 0),
            "points_without_peer_uptake_ids": metrics.get("points_without_peer_uptake_ids") or [],
            "pending_uptake_agents": metrics.get("pending_uptake_agents") or [],
        }
    if int(metrics.get("summary_needs_integration") or 0) > 0:
        return {
            "status": "needs_collaboration_review",
            "reason": "ledger_summary_needs_integration",
            "summary_needs_integration": int(metrics.get("summary_needs_integration") or 0),
            "summary_needs_integration_ids": metrics.get("summary_needs_integration_ids") or [],
        }
    if int(metrics.get("peer_uptakes") or 0) > 0:
        return {
            "status": "peer_reviewed",
            "reason": "ledger_point_uptake_recorded",
            "uptakes": int(metrics.get("peer_uptakes") or 0),
        }
    if set(local_targets).issubset(artifact_agents):
        return {
            "status": "needs_collaboration_review",
            "reason": "ledger_parallel_artifacts_without_integration",
            "artifact_agents": sorted(artifact_agents),
        }
    return None


def active_runner_rows(room_id: str | None = DEFAULT_ROOM_ID, *, include_background: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ACTIVE_RUNNERS.mkdir(parents=True, exist_ok=True)
    for path in sorted(ACTIVE_RUNNERS.glob("*.json")):
        record = read_json(path, {}) or {}
        if not isinstance(record, dict):
            continue
        task = record.get("task") if isinstance(record.get("task"), dict) else {}
        if room_id and record_room_id(record) != str(room_id):
            continue
        if task and not task_visible_in_room_status(task, room_id, include_background=include_background):
            continue
        liveness = active_runner_liveness(record)
        pid = int(liveness.get("pid") or record.get("pid") or 0)
        runner_dir = Path(str(record.get("runner_dir") or ""))
        stdout_path = Path(str(record.get("stdout_path") or runner_dir / "stdout.log"))
        stderr_path = Path(str(record.get("stderr_path") or runner_dir / "stderr.log"))
        result_path = runner_dir / "result.json" if str(runner_dir) else None
        agent_id = record.get("agent_id")
        run_id = record.get("run_id")
        task_budget = record.get("task_budget") if isinstance(record.get("task_budget"), dict) else {}
        expected_agents = [
            str(value)
            for value in (
                task_budget.get("expected_agents")
                or task.get("target_agents")
                or []
            )
            if str(value) in LOCAL_AGENTS
        ]
        presence = read_presence(agent_id, run_id) or {}
        now = datetime.now(timezone.utc).astimezone()
        result_exists = bool(result_path and result_path.exists())
        stdout_size = stdout_path.stat().st_size if stdout_path.exists() else None
        stderr_size = stderr_path.stat().st_size if stderr_path.exists() else None
        stdout_mtime = file_mtime_iso(stdout_path)
        stderr_mtime = file_mtime_iso(stderr_path)
        result_mtime = file_mtime_iso(result_path) if result_path else None
        alive = bool(liveness.get("alive"))
        effective_soft_deadline_at = effective_runner_soft_deadline(record)
        runner_state = classify_runner_state(
            alive=alive,
            result_exists=result_exists,
            stdout_size=stdout_size,
            stderr_size=stderr_size,
            soft_deadline_at=effective_soft_deadline_at,
            hard_deadline_at=record.get("hard_deadline_at"),
            now=now,
        )
        task_id = record.get("task_id") or record.get("run_id")
        ledger_completed = ledger_agent_completed(task_id, agent_id)
        if ledger_completed and not result_exists:
            runner_state = "completed_ledger_runner_still_alive" if alive else "completed_ledger_stale_runner_record"
        chat_action_result = record.get("last_chat_action_result") if isinstance(record.get("last_chat_action_result"), dict) else {}
        last_chat_action_at = record.get("last_chat_action_at")
        rows.append({
            "agent_id": record.get("agent_id"),
            "run_id": record.get("run_id"),
            "task_id": record.get("task_id"),
            "room_id": record_room_id(record),
            "pid": pid,
            "record_pid": liveness.get("record_pid"),
            "systemd_main_pid": liveness.get("systemd_main_pid"),
            "liveness_source": liveness.get("liveness_source"),
            "systemd_unit": liveness.get("systemd_unit"),
            "systemd_active_state": liveness.get("systemd_active_state"),
            "systemd_sub_state": liveness.get("systemd_sub_state"),
            "alive": alive,
            "started_at": record.get("started_at"),
            "soft_deadline_at": effective_soft_deadline_at,
            "task_soft_deadline_at": (record.get("runner_budget") or {}).get("task_soft_deadline_at") if isinstance(record.get("runner_budget"), dict) else None,
            "raw_soft_deadline_at": record.get("soft_deadline_at"),
            "hard_deadline_at": record.get("hard_deadline_at"),
            "seconds_until_soft_deadline": seconds_until(effective_soft_deadline_at, now),
            "seconds_until_hard_deadline": seconds_until(record.get("hard_deadline_at"), now),
            "result_exists": result_exists,
            "stdout_size": stdout_size,
            "stderr_size": stderr_size,
            "stdout_mtime": stdout_mtime,
            "stderr_mtime": stderr_mtime,
            "result_mtime": result_mtime,
            "last_chat_action_at": last_chat_action_at,
            "last_chat_action_age_seconds": seconds_since(last_chat_action_at, now),
            "last_chat_action_reason": record.get("last_chat_action_reason"),
            "last_chat_action_sent": chat_action_result.get("sent") if chat_action_result else None,
            "last_chat_action_suppressed_reason": chat_action_result.get("suppressed_reason") if chat_action_result else None,
            "runner_state": runner_state,
            "ledger_completed": ledger_completed,
            "presence_state": presence.get("state"),
            "presence_updated_at": presence.get("updated_at"),
            "presence_detail": presence.get("detail"),
            "expected_agents": expected_agents,
            "is_black_box": runner_state in {"working_silent_before_soft_deadline", "over_soft_deadline_no_output", "hard_deadline_exceeded_no_result"},
            "needs_attention": runner_state in {"over_soft_deadline_no_output", "dead_without_result", "hard_deadline_exceeded_no_result"},
            "background_task": bool(task and task_is_background(task)),
            "room_visible": bool(task and task_explicitly_room_visible(task)),
            "active_runner_path": str(path),
        })
    return rows


def effective_runner_soft_deadline(record: dict[str, Any]) -> Any:
    soft = parse_iso_datetime(str(record.get("soft_deadline_at") or ""))
    started = parse_iso_datetime(str(record.get("started_at") or ""))
    runner_budget = record.get("runner_budget") if isinstance(record.get("runner_budget"), dict) else {}
    try:
        soft_seconds = int(runner_budget.get("soft_seconds") or 0)
    except Exception:
        soft_seconds = 0
    if started and soft and soft < started and soft_seconds > 0:
        return (started + timedelta(seconds=soft_seconds)).isoformat(timespec="seconds")
    return record.get("soft_deadline_at")


def classify_runner_state(*, alive: bool, result_exists: bool, stdout_size: int | None, stderr_size: int | None, soft_deadline_at: Any, hard_deadline_at: Any, now: datetime) -> str:
    has_output = bool((stdout_size or 0) > 0 or (stderr_size or 0) > 0)
    soft_delta = seconds_until(soft_deadline_at, now)
    hard_delta = seconds_until(hard_deadline_at, now)
    if result_exists:
        return "result_ready_for_harvest" if not alive else "result_pending_harvest_process_alive"
    if not alive:
        return "dead_without_result"
    if hard_delta is not None and hard_delta <= 0:
        return "hard_deadline_exceeded_no_result"
    if soft_delta is not None and soft_delta <= 0:
        return "over_soft_deadline_with_output" if has_output else "over_soft_deadline_no_output"
    return "working_with_local_output" if has_output else "working_silent_before_soft_deadline"


def participant_presence_rows(task_ids: list[str], active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface per-agent presence for current tasks even after the runner file is harvested.

    Active-runner files are intentionally short-lived. Without this bridge, an
    agent that already produced a comment can disappear from the status surface
    while another agent in the same task is still running, which reads to Alex as
    "the agent is silent" instead of "completed and waiting for integration".
    """
    active_pairs = {
        (str(row.get("agent_id") or ""), str(row.get("run_id") or row.get("task_id") or ""))
        for row in active
        if row.get("agent_id") and (row.get("run_id") or row.get("task_id"))
    }
    rows: list[dict[str, Any]] = []
    for task_id in task_ids:
        if not task_id:
            continue
        for agent_id in sorted(LOCAL_AGENTS):
            if (agent_id, task_id) in active_pairs:
                continue
            presence = read_presence(agent_id, task_id)
            if not isinstance(presence, dict):
                continue
            state = str(presence.get("state") or "unknown")
            if state == "completed":
                runner_state = "completed_awaiting_integration"
            elif state.startswith("blocked"):
                runner_state = "blocked_or_failed_after_presence"
            elif state in {"claimed_or_attempting_work_item", "invoking_agent_backend"}:
                runner_state = "presence_without_active_runner_needs_attention"
            else:
                runner_state = "presence_observed"
            rows.append({
                "agent_id": agent_id,
                "run_id": task_id,
                "task_id": task_id,
                "runner_state": runner_state,
                "presence_state": state,
                "presence_updated_at": presence.get("updated_at"),
                "presence_detail": presence.get("detail"),
                "presence_comment_title": presence.get("comment_title"),
                "presence_ok": presence.get("ok"),
                "work_item_id": presence.get("work_item_id"),
                "backend": presence.get("backend"),
            })
    return rows


def agent_engagement_rows(active: list[dict[str, Any]], participant_presence: list[dict[str, Any]] | None = None, *, room_id_filter: str | None = None) -> dict[str, dict[str, Any]]:
    participant_presence = participant_presence or []
    if room_id_filter:
        active = [row for row in active if str(row.get("room_id") or "") == room_id_filter]
        participant_presence = [row for row in participant_presence if str(row.get("room_id") or "") == room_id_filter]
    agents = sorted(LOCAL_AGENTS.union(
        {str(row.get("agent_id") or "") for row in active if row.get("agent_id")},
        {str(row.get("agent_id") or "") for row in participant_presence if row.get("agent_id")},
    ))
    by_agent: dict[str, dict[str, Any]] = {}
    for agent_id in agents:
        rows = [row for row in active if row.get("agent_id") == agent_id]
        presence_rows = [row for row in participant_presence if row.get("agent_id") == agent_id]
        state_counts: dict[str, int] = {}
        for row in [*rows, *presence_rows]:
            state = str(row.get("runner_state") or "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
        active_no_result = [
            row for row in rows
            if row.get("alive") and not row.get("result_exists") and not row.get("ledger_completed")
        ]
        working_rows = [
            row for row in active_no_result
            if not row.get("needs_attention")
        ]
        pending_harvest = [row for row in rows if row.get("result_exists")]
        attention = [row for row in rows if row.get("needs_attention")]
        black_box = [row for row in rows if row.get("is_black_box")]
        ledger_completed = [row for row in rows if str(row.get("runner_state") or "").startswith("completed_ledger_")]
        completed_presence = [row for row in presence_rows if row.get("runner_state") == "completed_awaiting_integration"]
        blocked_presence = [row for row in presence_rows if row.get("runner_state") == "blocked_or_failed_after_presence"]
        missing_active_presence = [row for row in presence_rows if row.get("runner_state") == "presence_without_active_runner_needs_attention"]
        chat_ages = [
            int(row["last_chat_action_age_seconds"])
            for row in rows
            if row.get("last_chat_action_age_seconds") is not None
        ]
        if attention or missing_active_presence:
            engagement_state = "needs_attention"
        elif blocked_presence:
            engagement_state = "blocked_or_failed_after_presence"
        elif any(row.get("runner_state") == "over_soft_deadline_no_output" for row in rows):
            engagement_state = "over_soft_deadline_no_output"
        elif pending_harvest and active_no_result:
            engagement_state = "mixed_pending_harvest_and_active"
        elif pending_harvest:
            engagement_state = "result_ready_for_harvest"
        elif any(row.get("runner_state") == "working_with_local_output" for row in rows):
            engagement_state = "working_with_local_output"
        elif active_no_result:
            engagement_state = "working_silent_before_soft_deadline"
        elif completed_presence or ledger_completed:
            engagement_state = "completed_awaiting_integration"
        else:
            engagement_state = "not_currently_participating"
        by_agent[agent_id] = {
            "agent_id": agent_id,
            "engagement_state": engagement_state,
            "active_runner_count": len(active_no_result),
            "working_runner_count": len(working_rows),
            "pending_harvest_count": len(pending_harvest),
            "black_box_runner_count": len(black_box),
            "completed_presence_count": len(completed_presence) + len(ledger_completed),
            "needs_attention_count": len(attention) + len(missing_active_presence),
            "state_counts": state_counts,
            "active_task_ids": sorted({
                str(row.get("task_id") or row.get("run_id") or "")
                for row in [*rows, *presence_rows]
                if row.get("task_id") or row.get("run_id")
            }),
            "oldest_started_at": min([str(row.get("started_at")) for row in rows if row.get("started_at")] or [None]),
            "next_soft_deadline_at": min([str(row.get("soft_deadline_at")) for row in rows if row.get("soft_deadline_at")] or [None]),
            "next_hard_deadline_at": min([str(row.get("hard_deadline_at")) for row in rows if row.get("hard_deadline_at")] or [None]),
            "last_chat_action_age_seconds": min(chat_ages) if chat_ages else None,
            "participant_presence": presence_rows,
        }
    return by_agent


def classify_visibility_state(active: list[dict[str, Any]], daemon: dict[str, Any]) -> str:
    attention = [row for row in active if row.get("needs_attention")]
    live = [row for row in active if row.get("alive") and not row.get("result_exists") and not row.get("ledger_completed")]
    if attention and live:
        return "mixed_live_and_runner_attention_needed"
    if attention:
        return "runner_attention_needed"
    if live:
        if all((row.get("stdout_size") or 0) == 0 and (row.get("stderr_size") or 0) == 0 for row in live):
            if any(
                row.get("last_chat_action_age_seconds") is not None
                and int(row.get("last_chat_action_age_seconds") or 0) <= 90
                for row in live
            ):
                return "active_typing_signal_no_output_yet"
            return "active_black_box_no_output_yet"
        return "active_with_local_output"
    pending_results = [row for row in active if row.get("result_exists")]
    if pending_results:
        return "result_pending_harvest"
    standing = daemon.get("standing_agenda_tick") if isinstance(daemon.get("standing_agenda_tick"), dict) else {}
    standing_result = standing.get("result") if isinstance(standing.get("result"), dict) else {}
    if standing_result.get("status") == "suppressed_fresh_user_task":
        return "standing_agenda_suppressed_by_fresh_user_task"
    if standing_result.get("status") == "suppressed_active_runner":
        return "standing_agenda_suppressed_active_runner_stale"
    if standing_result.get("status") and str(standing_result.get("status")) != "completed":
        return f"standing_agenda_{standing_result.get('status')}"
    return "idle_or_waiting_for_next_tick"


def activity_dashboard(active: list[dict[str, Any]], per_agent: dict[str, dict[str, Any]]) -> dict[str, Any]:
    recent = sorted(active, key=lambda row: str(row.get("started_at") or ""), reverse=True)[:8]
    attention = [row for row in active if row.get("needs_attention")]
    live = [row for row in active if row.get("alive") and not row.get("result_exists") and not row.get("ledger_completed")]
    pending = [row for row in active if row.get("result_exists")]
    return {
        "purpose": "human-visible answer to whether each agent is working, waiting for harvest, or stuck",
        "summary_state": "needs_attention" if attention else ("working" if live else ("pending_harvest" if pending else "idle")),
        "live_runner_count": len(live),
        "pending_harvest_count": len(pending),
        "needs_attention_count": len(attention),
        "per_agent": per_agent,
        "recent_runner_signals": [
            {
                "agent_id": row.get("agent_id"),
                "task_id": row.get("task_id"),
                "run_id": row.get("run_id"),
                "runner_state": row.get("runner_state"),
                "pid": row.get("pid"),
                "alive": row.get("alive"),
                "result_exists": row.get("result_exists"),
                "stdout_size": row.get("stdout_size"),
                "stderr_size": row.get("stderr_size"),
                "last_chat_action_at": row.get("last_chat_action_at"),
                "last_chat_action_age_seconds": row.get("last_chat_action_age_seconds"),
                "last_chat_action_sent": row.get("last_chat_action_sent"),
                "presence_state": row.get("presence_state"),
                "presence_updated_at": row.get("presence_updated_at"),
                "needs_attention": row.get("needs_attention"),
            }
            for row in recent
        ],
    }


def one_glance_badge(engagement_state: str) -> str:
    if engagement_state in {"needs_attention", "over_soft_deadline_no_output"}:
        return "ATTENTION"
    if engagement_state in {"blocked_or_failed_after_presence"}:
        return "BLOCKED"
    if engagement_state in {"result_ready_for_harvest", "mixed_pending_harvest_and_active"}:
        return "HARVEST"
    if engagement_state in {"completed_awaiting_integration"}:
        return "DONE_WAITING"
    if engagement_state in {"working_with_local_output", "working_silent_before_soft_deadline"}:
        return "WORKING"
    return "IDLE"


def one_glance_action(data: dict[str, Any]) -> str:
    state = str(data.get("engagement_state") or "")
    if data.get("needs_attention_count") and data.get("working_runner_count"):
        return "current work is still running; inspect stale/no-output runners separately"
    if data.get("needs_attention_count"):
        return "inspect stalled/no-output runner or wait for hard-deadline handoff"
    if data.get("pending_harvest_count"):
        return "harvest result and integrate visible answer/artifact"
    if state == "completed_awaiting_integration":
        return "await integration/peer uptake"
    if data.get("active_runner_count"):
        return "keep running; no manual interruption unless a newer task is classified interrupting"
    return "available for next work item"


def one_glance_work_status(data: dict[str, Any]) -> str:
    state = str(data.get("engagement_state") or "")
    working = int(data.get("working_runner_count") or 0)
    attention = int(data.get("needs_attention_count") or 0)
    if working and attention:
        return "working_with_attention"
    if working:
        return "working"
    if attention:
        return "needs_attention"
    if int(data.get("pending_harvest_count") or 0):
        return "pending_harvest"
    if state == "completed_awaiting_integration":
        return "completed_waiting"
    return "idle"


def one_glance_status(per_agent: dict[str, dict[str, Any]], room_id: str | None = None) -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    for agent_id, data in sorted(per_agent.items()):
        if not isinstance(data, dict):
            continue
        tasks = ordered_task_ids([str(task_id) for task_id in (data.get("active_task_ids") or []) if task_id], room_id)
        state = str(data.get("engagement_state") or "unknown")
        work_status = one_glance_work_status(data)
        badge = one_glance_badge(state)
        if work_status == "working_with_attention":
            badge = "WORKING+ATTENTION"
        cards.append({
            "agent_id": agent_id,
            "badge": badge,
            "work_status": work_status,
            "engagement_state": state,
            "active_runner_count": data.get("active_runner_count"),
            "working_runner_count": data.get("working_runner_count"),
            "pending_harvest_count": data.get("pending_harvest_count"),
            "completed_presence_count": data.get("completed_presence_count"),
            "needs_attention_count": data.get("needs_attention_count"),
            "current_task_ids": tasks[:4],
            "hidden_task_count": max(0, len(tasks) - 4),
            "next_soft_deadline_at": data.get("next_soft_deadline_at"),
            "next_hard_deadline_at": data.get("next_hard_deadline_at"),
            "last_chat_action_age_seconds": data.get("last_chat_action_age_seconds"),
            "action": one_glance_action(data),
        })
    return {
        "purpose": "one-screen per-agent state for Alex",
        "cards": cards,
        "summary_line": " | ".join(
            f"{card.get('agent_id')}={card.get('badge')}:{card.get('work_status')}"
            for card in cards
        ),
    }


def short_time(value: Any) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "unknown"
    return parsed.strftime("%H:%M:%S")


def task_display_priority(task_id: str, room_id: str | None = None) -> tuple[int, str]:
    text = str(task_id or "").strip()
    room_slug = safe_slug(room_id) if room_id else ""
    if room_slug and text.startswith(f"tg-{room_slug}-"):
        return (0, text)
    if text.startswith("tg-"):
        return (1, text)
    if text.startswith("collab-"):
        return (2, text)
    if text.startswith("agentmsg-"):
        return (3, text)
    if text.startswith("standing-"):
        return (4, text)
    return (5, text)


def ordered_task_ids(task_ids: list[str], room_id: str | None = None) -> list[str]:
    clean = [str(task_id) for task_id in task_ids if str(task_id or "").strip()]
    return sorted(dict.fromkeys(clean), key=lambda task_id: task_display_priority(task_id, room_id))


def short_task_list(task_ids: list[str], limit: int = 2, room_id: str | None = None) -> str:
    clean = ordered_task_ids(task_ids, room_id)
    if not clean:
        return "无当前任务"
    shown = [task_id if len(task_id) <= 42 else task_id[:39] + "..." for task_id in clean[:limit]]
    hidden = len(clean) - len(shown)
    if hidden > 0:
        shown.append(f"+{hidden}")
    return ", ".join(shown)


def main_runtime_row(status: dict[str, Any]) -> dict[str, Any]:
    daemon = status.get("daemon") if isinstance(status.get("daemon"), dict) else {}
    now = datetime.now(timezone.utc).astimezone()
    last_tick_age = seconds_since(daemon.get("last_tick_finished_at"), now)
    daemon_status = str(daemon.get("status") or "unknown")
    last_tick_ok = daemon.get("last_tick_ok")
    if daemon_status == "running" and last_tick_ok is True and (last_tick_age is None or last_tick_age <= 180):
        state = "online"
        badge = "🟢在线"
        next_step = "下个 daemon tick 刷新状态卡"
    elif daemon_status == "running" and last_tick_ok is False:
        state = "stuck"
        badge = "🔴卡住"
        next_step = "检查 bridge daemon tick 错误"
    elif daemon_status == "running":
        state = "stale"
        badge = "🟠滞后"
        next_step = "检查 daemon 状态文件是否继续刷新"
    else:
        state = "offline"
        badge = "⚪离线"
        next_step = "恢复 bridge daemon"

    outbound = daemon.get("telegram_outbound_enabled")
    outbound_text = "enabled" if outbound is True else ("disabled" if outbound is False else "unknown")
    standing = daemon.get("standing_agenda_tick") if isinstance(daemon.get("standing_agenda_tick"), dict) else {}
    standing_result = standing.get("result") if isinstance(standing.get("result"), dict) else {}
    standing_status = standing_result.get("status") or standing.get("status") or "unknown"
    tick = daemon.get("tick")
    recent = f"tick {tick}; last_ok={last_tick_ok}; last_tick={short_time(daemon.get('last_tick_finished_at'))}"
    return {
        "agent_id": ROOM_RUNTIME_AGENT,
        "badge": badge,
        "state": state,
        "current_task": "room runtime / Telegram ingress / status refresh",
        "recent_output": recent,
        "model_channel": f"bridge daemon; Telegram outbound {outbound_text}; standing={standing_status}",
        "next_step_or_deadline": next_step,
        "last_tick_age_seconds": last_tick_age,
        "tick": tick,
    }


def local_agent_runtime_row(agent_id: str, data: dict[str, Any], *, room_id: str | None = None) -> dict[str, Any]:
    work_status = one_glance_work_status(data)
    state = str(data.get("engagement_state") or "unknown")
    badge = WORK_BADGE.get(work_status, ENGAGEMENT_EMOJI.get(state, "⚪"))
    tasks = ordered_task_ids([str(task_id) for task_id in (data.get("active_task_ids") or []) if str(task_id)], room_id)
    working = int(data.get("working_runner_count") or 0)
    active = int(data.get("active_runner_count") or 0)
    pending = int(data.get("pending_harvest_count") or 0)
    completed = int(data.get("completed_presence_count") or 0)
    attention = int(data.get("needs_attention_count") or 0)
    parts: list[str] = []
    if working:
        parts.append(f"{working}工作中")
    if active:
        parts.append(f"{active}活跃")
    if pending:
        parts.append(f"{pending}待收")
    if completed:
        parts.append(f"{completed}完成")
    if attention:
        parts.append(f"{attention}异常")
    next_deadline = data.get("next_soft_deadline_at") or data.get("next_hard_deadline_at")
    return {
        "agent_id": agent_id,
        "badge": badge,
        "state": work_status,
        "engagement_state": state,
        "current_task": short_task_list(tasks, room_id=room_id),
        "recent_output": ", ".join(parts) if parts else ENGAGEMENT_CHINESE.get(state, state),
        "model_channel": f"{agent_id} runner; model not recorded in active-runner",
        "next_step_or_deadline": f"{one_glance_action(data)}; next={short_time(next_deadline)}",
        "current_task_ids": tasks,
        "working_runner_count": working,
        "needs_attention_count": attention,
        "pending_harvest_count": pending,
    }


def fixed_card_agent_label(agent_id: Any) -> str:
    labels = {
        ROOM_RUNTIME_AGENT: "main",
        "codex": "Codex",
        "claude-code": "Claude Code",
    }
    return labels.get(str(agent_id or ""), str(agent_id or "unknown"))


def fixed_card_state_text(row: dict[str, Any]) -> str:
    state = str(row.get("state") or "")
    agent_id = str(row.get("agent_id") or "")
    if agent_id == ROOM_RUNTIME_AGENT:
        if state == "online":
            return "入口/状态卡正常"
        if state == "stuck":
            return "bridge tick 出错"
        if state == "stale":
            return "bridge 状态滞后"
        return "bridge 离线"
    if state == "working_with_attention":
        return "工作中，有异常待排查"
    if state == "working":
        return "工作中"
    if state == "needs_attention":
        return "异常，需排查"
    if state == "pending_harvest":
        return "结果待收集"
    if state == "completed_waiting":
        return "已完成待整合"
    return "空闲"


def fixed_card_count_text(row: dict[str, Any]) -> str:
    if str(row.get("agent_id") or "") == ROOM_RUNTIME_AGENT:
        tick = row.get("tick")
        return f"tick {tick}" if tick is not None else ""
    parts: list[str] = []
    working = int(row.get("working_runner_count") or 0)
    pending = int(row.get("pending_harvest_count") or 0)
    attention = int(row.get("needs_attention_count") or 0)
    if working:
        parts.append(f"{working}工作")
    if pending:
        parts.append(f"{pending}待收")
    if attention:
        parts.append(f"{attention}异常")
    return " / ".join(parts)


def collaboration_summary_line(status: dict[str, Any]) -> str:
    collaboration = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    material_points = int(collaboration.get("material_point_count") or 0)
    peer_uptakes = int(collaboration.get("peer_uptake_count") or 0)
    peer_challenges = int(collaboration.get("peer_challenge_count") or 0)
    integration_signals = int(collaboration.get("integration_signal_count") or 0)
    missing_uptake_tasks = int(collaboration.get("tasks_missing_peer_uptake_count") or 0)
    summary_integration_tasks = int(collaboration.get("tasks_needing_summary_integration_count") or 0)
    expired_claims = int(collaboration.get("expired_claim_count") or 0)
    degraded_tasks = int(collaboration.get("degraded_quorum_task_count") or 0)
    runner_attention = int(collaboration.get("runner_attention_task_count") or 0)
    parts: list[str] = []
    if material_points or peer_uptakes or peer_challenges:
        parts.append(f"{material_points}点/{peer_uptakes}接收/{peer_challenges}挑战")
    if integration_signals:
        parts.append(f"{integration_signals}整合")
    if missing_uptake_tasks:
        parts.append(f"{missing_uptake_tasks}任务待接收")
    if summary_integration_tasks:
        parts.append(f"{summary_integration_tasks}总结待整合")
    if expired_claims:
        parts.append(f"{expired_claims}租约过期")
    if degraded_tasks:
        parts.append(f"{degraded_tasks}降级")
    if runner_attention:
        parts.append(f"{runner_attention}runner异常")
    return f"协作: {' | '.join(parts)}" if parts else ""


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def task_brief_path(task: dict[str, Any]) -> Path | None:
    raw = str(task.get("brief_path") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        return path
    task_id = str(task.get("task_id") or task.get("run_id") or "").strip()
    if task_id:
        return TASKS / safe_slug(task_id) / "brief.md"
    return None


def read_task_brief(task: dict[str, Any]) -> tuple[str, Path | None]:
    path = task_brief_path(task)
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="replace"), path
    return "", path


def task_sort_timestamp(task: dict[str, Any], manifest_path: Path | None = None) -> float:
    # For the pinned room brief, "current" means the latest user turn, not the
    # task whose harvest/status metadata was touched most recently.
    for key in ("created_at",):
        parsed = parse_iso_datetime(task.get(key))
        if parsed is not None:
            return parsed.timestamp()
    if manifest_path and manifest_path.exists():
        try:
            return manifest_path.stat().st_mtime
        except Exception:
            pass
    return 0.0


def telegram_room_task(task: dict[str, Any]) -> bool:
    source = task_source(task)
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    return str(source.get("transport") or "") == "telegram" or task_id.startswith("tg-")


def current_task_state_rank(task: dict[str, Any]) -> int:
    status = str(task.get("status") or "").strip().lower()
    review_status = str(task.get("review_status") or "").strip().lower()
    quality_gate = str(task.get("quality_gate_status") or "").strip().lower()
    if status in {"running", "queued", "open"} or review_status in {"requested", "in_review"}:
        return 0
    if status in {"partial", "blocked"} or review_status == "degraded_quorum" or quality_gate == "degraded_quorum":
        return 1
    if status in {"completed", "done", "closed"}:
        return 2
    return 1


def latest_visible_room_task(status: dict[str, Any], room_id: str) -> tuple[dict[str, Any] | None, Path | None]:
    candidates: list[tuple[int, int, float, dict[str, Any], Path | None]] = []
    seen: set[str] = set()

    def add_candidate(task: dict[str, Any], manifest_path: Path | None = None) -> None:
        if not isinstance(task, dict) or not task_visible_in_room_status(task, room_id):
            return
        task_id = str(task.get("task_id") or task.get("run_id") or manifest_path or "")
        if task_id in seen:
            return
        seen.add(task_id)
        priority = 0 if telegram_room_task(task) else 1
        candidates.append((priority, current_task_state_rank(task), -task_sort_timestamp(task, manifest_path), task, manifest_path))

    for row in status.get("recent_tasks") or []:
        if not isinstance(row, dict):
            continue
        manifest_path = Path(str(row.get("manifest_path") or ""))
        task = read_json(manifest_path, {}) if manifest_path.exists() else {}
        if isinstance(task, dict):
            add_candidate(task, manifest_path)

    for path in task_manifest_paths():
        task = read_json(path, {}) or {}
        if isinstance(task, dict):
            add_candidate(task, path)

    for row in status.get("active_runners") or []:
        if not isinstance(row, dict):
            continue
        task = row.get("task") if isinstance(row.get("task"), dict) else {}
        if task:
            add_candidate(task, None)

    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, _, task, manifest_path = candidates[0]
    return task, manifest_path


def current_task_brief_card(status: dict[str, Any], *, room_id: str, chat_id: str | None) -> dict[str, Any]:
    """Task briefs are internal runner input, never a status-card surface.

    This intentionally returns no visible text.  The Telegram pinned card must be
    generated only from structured runtime rows via fixed_status_card().
    """
    return {}


def runtime_status_card_text(rows: list[dict[str, Any]], generated: Any, collaboration_line: str = "", mailbox_line: str = "") -> str:
    suffix = f" · {mailbox_line}" if mailbox_line else ""
    lines = [f"📌 OpenClaw 状态 {short_time(generated)}{suffix}"]
    for row in rows:
        count_text = fixed_card_count_text(row)
        suffix = f" · {count_text}" if count_text else ""
        lines.append(
            "{agent} {badge} · {state}{suffix}".format(
                agent=fixed_card_agent_label(row.get("agent_id")),
                badge=row.get("badge"),
                state=fixed_card_state_text(row),
                suffix=suffix,
            )
        )
    if collaboration_line:
        lines.append(f"🤝 {collaboration_line}")
    else:
        lines.append("细节在本地 status；这里只编辑这一条，不刷屏。")
    return "\n".join(lines)


def room_scoped_status(status: dict[str, Any], room_id: str) -> dict[str, Any]:
    """Return a status filtered to only include tasks/runners from the given room.

    Main runtime row (daemon-based) is inherently room-scoped and does not need
    filtering; per-agent engagement and active runners are recomputed from the
    room-scoped subset.
    """
    if "active_runners" not in status and "recent_tasks" not in status:
        filtered = dict(status)
        filtered["room_id"] = room_id
        return filtered

    all_active = status.get("active_runners") or []
    room_active = [
        row for row in all_active
        if isinstance(row, dict) and str(row.get("room_id") or "") == room_id
    ]
    # Fallback: runners without room_id field may belong to this room via manifest
    for row in all_active:
        if isinstance(row, dict) and not row.get("room_id"):
            task_id = str(row.get("task_id") or row.get("run_id") or "")
            if task_id:
                manifest = read_json(task_manifest_path_for_task(task_id), {}) or {}
                if str(manifest.get("room_id") or "") == room_id:
                    if row not in room_active:
                        room_active.append(row)

    current_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in room_active
        if row.get("task_id") or row.get("run_id")
    ], room_id)
    participant_presence = participant_presence_rows(current_task_ids, room_active)
    per_agent = agent_engagement_rows(room_active, participant_presence)

    filtered = dict(status)
    filtered["active_runners"] = room_active
    filtered["per_agent_engagement"] = per_agent
    filtered["agent_liveness"] = per_agent
    filtered["active_task_ids"] = current_task_ids
    filtered["active_runner_count"] = sum(
        1 for row in room_active
        if row.get("alive") and not row.get("result_exists") and not row.get("ledger_completed")
    )
    live_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in room_active
        if row.get("alive") and not row.get("ledger_completed") and (row.get("task_id") or row.get("run_id"))
    ], room_id)
    attention_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in room_active
        if row.get("needs_attention") and (row.get("task_id") or row.get("run_id"))
    ], room_id)
    filtered["live_task_ids"] = live_task_ids
    filtered["attention_task_ids"] = attention_task_ids
    filtered["visibility_state"] = classify_visibility_state(room_active, status.get("daemon") if isinstance(status.get("daemon"), dict) else {})

    room_tasks = [
        task for task in (status.get("recent_tasks") or [])
        if isinstance(task, dict) and str(task.get("room_id") or "") == room_id
    ]
    filtered["recent_tasks"] = room_tasks
    runner_attention = runner_attention_overview(room_active)
    filtered["collaboration_overview"] = collaboration_overview(room_tasks, runner_attention)
    filtered["one_glance"] = one_glance_status(per_agent, room_id=room_id)
    filtered["activity_dashboard"] = activity_dashboard(room_active, per_agent)

    return filtered


def fixed_status_card(status: dict[str, Any], *, room_id: str = "openclaw-evolution", chat_id: str | None = None) -> dict[str, Any]:
    room_status = room_scoped_status(status, room_id)
    per_agent = room_status.get("per_agent_engagement") if isinstance(room_status.get("per_agent_engagement"), dict) else {}
    rows: list[dict[str, Any]] = [main_runtime_row(room_status)]
    for agent_id in ("codex", "claude-code"):
        data = per_agent.get(agent_id)
        if isinstance(data, dict):
            rows.append(local_agent_runtime_row(agent_id, data, room_id=room_id))
        else:
            rows.append({
                "agent_id": agent_id,
                "badge": "⚪空闲",
                "state": "idle",
                "current_task": "无当前任务",
                "recent_output": "无 runner/presence 记录",
                "model_channel": f"{agent_id} runner; model not recorded in active-runner",
                "next_step_or_deadline": "available for next work item",
            })
    generated = status.get("generated_at") or now_iso()
    # The Telegram pinned card is a one-glance runtime surface, not a diagnostics
    # panel. Collaboration metrics stay in local status artifacts so the card cannot
    # become noisy or expose internal runner/accounting language.
    mailbox = active_mailbox_brief()
    card_text = runtime_status_card_text(rows, generated, mailbox_line=str(mailbox.get("label") or ""))
    return {
        "schema": "openclaw.agent_room.fixed_status_card.v0",
        "room_id": room_id,
        "chat_id": chat_id,
        "generated_at": generated,
        "rows": rows,
        "active_mailbox": mailbox,
        "text": card_text,
        "display_profile": "minimal_one_glance",
        "telegram_projection": "pin_once_then_edit_same_message",
        "governance_projection_policy": "mainline_drift_diagnostics_stay_local_status_or_status_command_not_pinned_card",
    }


def recent_task_rows(
    limit: int = 8,
    room_id: str | None = DEFAULT_ROOM_ID,
    *,
    include_background: bool = False,
    pinned_task_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pinned_paths = [
        task_manifest_path_for_task(task_id)
        for task_id in (pinned_task_ids or [])
        if str(task_id or "").strip()
    ]
    manifest_paths = []
    seen_paths: set[Path] = set()
    for path in [*pinned_paths, *task_manifest_paths()]:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        manifest_paths.append(path)
    effective_limit = max(limit, len(pinned_paths))
    for path in manifest_paths:
        task = read_json(path, {}) or {}
        if not isinstance(task, dict):
            continue
        if not task_visible_in_room_status(task, room_id, include_background=include_background):
            continue
        target_agents = [str(agent_id) for agent_id in (task.get("target_agents") or []) if str(agent_id)]
        collaboration = collaboration_snapshot(task)
        metrics = collaboration_metrics(collaboration, target_agents)
        per_agent_progress = per_agent_collaboration_progress(collaboration, target_agents)
        manifest_quality_gate_status = str(task.get("quality_gate_status") or "")
        manifest_quality_gate_reason = (
            task.get("quality_gate", {}).get("reason")
            if isinstance(task.get("quality_gate"), dict)
            else task.get("quality_gate_status")
        )
        ledger_quality_gate = status_quality_gate_from_collaboration(task, collaboration, metrics)
        quality_gate_status = str((ledger_quality_gate or {}).get("status") or manifest_quality_gate_status)
        quality_gate_reason = str((ledger_quality_gate or {}).get("reason") or manifest_quality_gate_reason or quality_gate_status)
        runner_summary = task.get("runner_summary") if isinstance(task.get("runner_summary"), dict) else {}
        summary_degraded = bool(runner_summary.get("degraded_quorum")) if isinstance(runner_summary, dict) else False
        degraded_quorum = quality_gate_status == "degraded_quorum" or (
            summary_degraded and ledger_quality_gate is None and quality_gate_status != "peer_reviewed"
        )
        rows.append({
            "task_id": task.get("task_id"),
            "run_id": task.get("run_id"),
            "status": task.get("status"),
            "governance_state": task.get("governance_state"),
            "mainline_id": task.get("mainline_id"),
            "dedupe_key": task.get("dedupe_key"),
            "drift_check_passed": task.get("drift_check_passed"),
            "review_status": task.get("review_status"),
            "quality_gate_status": quality_gate_status,
            "quality_gate_reason": quality_gate_reason,
            "manifest_quality_gate_status": manifest_quality_gate_status,
            "manifest_quality_gate_reason": manifest_quality_gate_reason,
            "ledger_quality_gate": ledger_quality_gate,
            "degraded_quorum": degraded_quorum,
            "room_id": task.get("room_id"),
            "collaboration": metrics,
            "per_agent_progress": per_agent_progress,
            "efficiency": collaboration_efficiency_score(metrics),
            "updated_at": task.get("updated_at"),
            "target_agents": target_agents,
            "source_transport": (task.get("source") or {}).get("transport") if isinstance(task.get("source"), dict) else None,
            "background_task": task_is_background(task),
            "room_visible": task_explicitly_room_visible(task),
            "runner_summary": runner_summary,
            "manifest_path": str(path),
        })
        if len(rows) >= effective_limit:
            break
    return rows


def runner_attention_overview(active: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize runner attention across active tasks.

    Extends liveness-based quorum with a material-progress dimension:
    an agent counts as ``material_progress_agents`` only if its
    collaboration ledger shows at least one material point from that agent.
    Agents that have crossed an attention boundary with zero material output
    are flagged as ``material_stall_agents``. Live runners that have not yet
    crossed a soft/hard boundary are tracked separately as
    ``active_material_silent_agents`` so "runner started" is visible without
    being misclassified as a blocker.
    """
    now = datetime.now(timezone.utc).astimezone()
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in active:
        task_id = str(row.get("task_id") or row.get("run_id") or "").strip()
        if task_id:
            by_task.setdefault(task_id, []).append(row)

    attention_task_ids: list[str] = []
    degraded_task_ids: list[str] = []
    partial_task_ids: list[str] = []
    material_stall_task_ids: list[str] = []
    active_material_silence_task_ids: list[str] = []
    active_material_silence_post_soft_deadline_task_ids: list[str] = []
    material_progress_agents_by_task: dict[str, list[str]] = {}
    material_stall_agents_by_task: dict[str, list[str]] = {}
    active_material_silent_agents_by_task: dict[str, list[str]] = {}
    active_material_silence_post_soft_deadline_agents_by_task: dict[str, list[str]] = {}
    for task_id, rows in sorted(by_task.items()):
        attention_rows = [row for row in rows if row.get("needs_attention")]
        if attention_rows:
            attention_task_ids.append(task_id)
        live_or_pending_rows = [
            row
            for row in rows
            if (row.get("alive") and not row.get("result_exists")) or row.get("result_exists")
        ]
        expected_agents = {
            str(agent_id)
            for row in rows
            for agent_id in (row.get("expected_agents") or [])
            if str(agent_id) in LOCAL_AGENTS
        }
        observed_agents = {
            str(row.get("agent_id"))
            for row in rows
            if str(row.get("agent_id") or "") in LOCAL_AGENTS
        }
        quorum_agents = expected_agents or observed_agents
        attention_agents = {
            str(row.get("agent_id"))
            for row in attention_rows
            if str(row.get("agent_id") or "") in LOCAL_AGENTS
        }
        # Material-progress dimension: which agents have produced at least
        # one material point in the collaboration ledger?
        present_agents = quorum_agents or observed_agents
        live_agents = {
            str(row.get("agent_id"))
            for row in rows
            if row.get("alive")
            and not row.get("result_exists")
            and not row.get("ledger_completed")
            and str(row.get("agent_id") or "") in LOCAL_AGENTS
        }
        material_progress_agents: set[str] = set()
        for row in rows:
            collaboration = row.get("collaboration")
            if not isinstance(collaboration, dict):
                continue
            row_agent = str(row.get("agent_id") or "")
            if row_agent not in LOCAL_AGENTS:
                continue
            per_agent_points = collaboration.get("point_counts_by_agent") or {}
            if int(per_agent_points.get(row_agent, 0)) > 0:
                material_progress_agents.add(row_agent)
            per_agent_uptakes = collaboration.get("peer_uptake_counts_by_agent") or {}
            per_agent_challenges = collaboration.get("peer_challenge_counts_by_agent") or {}
            if int(per_agent_uptakes.get(row_agent, 0)) > 0 or int(per_agent_challenges.get(row_agent, 0)) > 0:
                material_progress_agents.add(row_agent)
        ledger_collaboration = collaboration_snapshot({
            "task_id": task_id,
            "run_id": task_id,
            "target_agents": sorted(present_agents),
        })
        ledger_metrics = collaboration_metrics(ledger_collaboration, sorted(present_agents))
        ledger_point_counts = ledger_metrics.get("point_counts_by_agent") or {}
        ledger_uptake_counts = ledger_metrics.get("peer_uptake_counts_by_agent") or {}
        ledger_challenge_counts = ledger_metrics.get("peer_challenge_counts_by_agent") or {}
        for agent_id in present_agents:
            point_count = int_or_none(ledger_point_counts.get(agent_id)) or 0
            if point_count > 0:
                material_progress_agents.add(agent_id)
            uptake_count = int_or_none(ledger_uptake_counts.get(agent_id)) or 0
            challenge_count = int_or_none(ledger_challenge_counts.get(agent_id)) or 0
            if uptake_count > 0 or challenge_count > 0:
                material_progress_agents.add(agent_id)
        active_material_silent_agents = live_agents.difference(material_progress_agents)
        if active_material_silent_agents:
            active_material_silence_task_ids.append(task_id)
            active_material_silent_agents_by_task[task_id] = sorted(active_material_silent_agents)
            post_soft_deadline_agents = set[str]()
            for row in rows:
                row_agent = str(row.get("agent_id") or "")
                if row_agent not in active_material_silent_agents:
                    continue
                deadline = parse_iso_datetime(row.get("soft_deadline_at"))
                if deadline is None:
                    continue
                if deadline <= now:
                    post_soft_deadline_agents.add(row_agent)
            if post_soft_deadline_agents:
                active_material_silence_post_soft_deadline_task_ids.append(task_id)
                active_material_silence_post_soft_deadline_agents_by_task[task_id] = sorted(post_soft_deadline_agents)
        material_stall_agents = present_agents.difference(material_progress_agents)
        if material_progress_agents and (attention_rows or live_agents):
            material_progress_agents_by_task[task_id] = sorted(material_progress_agents)
        if not attention_rows:
            continue
        if material_stall_agents:
            material_stall_task_ids.append(task_id)
            material_stall_agents_by_task[task_id] = sorted(material_stall_agents)
        if quorum_agents and quorum_agents.issubset(attention_agents) and not live_or_pending_rows:
            degraded_task_ids.append(task_id)
        else:
            partial_task_ids.append(task_id)

    return {
        "runner_attention_task_ids": attention_task_ids,
        "runner_attention_task_count": len(attention_task_ids),
        "runner_degraded_quorum_task_ids": degraded_task_ids,
        "runner_degraded_quorum_task_count": len(degraded_task_ids),
        "runner_partial_attention_task_ids": partial_task_ids,
        "runner_partial_attention_task_count": len(partial_task_ids),
        "material_stall_task_ids": material_stall_task_ids,
        "material_stall_task_count": len(material_stall_task_ids),
        "active_material_silence_task_ids": active_material_silence_task_ids,
        "active_material_silence_task_count": len(active_material_silence_task_ids),
        "active_material_silence_post_soft_deadline_task_ids": active_material_silence_post_soft_deadline_task_ids,
        "active_material_silence_post_soft_deadline_task_count": len(active_material_silence_post_soft_deadline_task_ids),
        "active_material_silence_post_soft_deadline_agents_by_task": active_material_silence_post_soft_deadline_agents_by_task,
        "material_progress_agents_by_task": material_progress_agents_by_task,
        "material_stall_agents_by_task": material_stall_agents_by_task,
        "active_material_silent_agents_by_task": active_material_silent_agents_by_task,
    }


def append_collaboration_action_item(overview: dict[str, Any], item: dict[str, Any]) -> None:
    action_type = str(item.get("type") or "").strip()
    task_id = str(item.get("task_id") or "").strip()
    if not action_type or not task_id:
        return
    cleaned = {
        key: value
        for key, value in item.items()
        if value not in (None, "", [], {})
    }
    overview["action_item_count"] = int(overview.get("action_item_count") or 0) + 1
    agent_id = str(cleaned.get("agent_id") or "").strip()
    if agent_id:
        increment_count(overview["per_agent_action_items"], agent_id)
    overview["action_items"].append(cleaned)


def prioritized_action_items(action_items: Any, limit: int | None = None) -> list[dict[str, Any]]:
    rows = [
        item
        for item in (action_items or [])
        if isinstance(item, dict)
    ]
    prioritized = sorted(
        enumerate(rows),
        key=lambda pair: (
            ACTION_ITEM_PRIORITY.get(str(pair[1].get("type") or ""), 100),
            pair[0],
        ),
    )
    ordered = [item for _idx, item in prioritized]
    if limit is None:
        return ordered
    return ordered[: max(0, limit)]


def action_item_signature_detail(item: dict[str, Any]) -> str:
    for key in ("point_id", "blocker_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    claim_ids = item.get("claim_ids")
    if isinstance(claim_ids, list):
        value = ",".join(str(v) for v in claim_ids if str(v))
        if value:
            return value
    silent_agents = item.get("silent_agents")
    if isinstance(silent_agents, list):
        value = ",".join(str(v) for v in silent_agents if str(v))
        if value:
            return value
    return ""


def action_item_assignees(item: dict[str, Any]) -> list[str]:
    agents: list[str] = []
    primary = str(item.get("agent_id") or "").strip()
    if primary:
        agents.append(primary)
    if str(item.get("type") or "") == "active_material_silence_watch":
        for value in item.get("silent_agents") or []:
            agent_id = str(value or "").strip()
            if agent_id and agent_id not in agents:
                agents.append(agent_id)
    return agents


def per_agent_next_action_queue(action_items: Any, *, limit_per_agent: int = 5) -> dict[str, Any]:
    queues: dict[str, dict[str, Any]] = {}
    for item in prioritized_action_items(action_items):
        if not isinstance(item, dict):
            continue
        primary = str(item.get("agent_id") or "").strip()
        for agent_id in action_item_assignees(item):
            queue = queues.setdefault(agent_id, {"action_count": 0, "hidden_count": 0, "actions": []})
            queue["action_count"] = int(queue.get("action_count") or 0) + 1
            actions = queue.setdefault("actions", [])
            if len(actions) >= limit_per_agent:
                queue["hidden_count"] = int(queue.get("hidden_count") or 0) + 1
                continue
            role = "primary" if agent_id == primary else "watch_target"
            actions.append({
                "type": item.get("type"),
                "task_id": item.get("task_id"),
                "role": role,
                "primary_agent_id": primary or None,
                "detail": action_item_signature_detail(item),
                "source_agent_id": item.get("source_agent_id"),
                "reason": text_preview(item.get("reason"), 140),
                "next_action": text_preview(item.get("next_action"), 180),
            })
    return {
        agent_id: queues[agent_id]
        for agent_id in sorted(queues)
    }


def collaboration_overview(tasks: list[dict[str, Any]], runner_attention: dict[str, Any] | None = None) -> dict[str, Any]:
    runner_attention = runner_attention or {}
    overview: dict[str, Any] = {
        "tracked_tasks": len(tasks),
        "degraded_quorum_task_count": 0,
        "peer_reviewed_task_count": 0,
        "needs_collaboration_review_count": 0,
        "needs_collaboration_repair_count": 0,
        "needs_collaboration_attention_task_ids": [],
        "degraded_quorum_task_ids": [],
        "runner_attention_task_count": int(runner_attention.get("runner_attention_task_count") or 0),
        "runner_attention_task_ids": list(runner_attention.get("runner_attention_task_ids") or []),
        "runner_degraded_quorum_task_count": int(runner_attention.get("runner_degraded_quorum_task_count") or 0),
        "runner_degraded_quorum_task_ids": list(runner_attention.get("runner_degraded_quorum_task_ids") or []),
        "runner_partial_attention_task_count": int(runner_attention.get("runner_partial_attention_task_count") or 0),
        "runner_partial_attention_task_ids": list(runner_attention.get("runner_partial_attention_task_ids") or []),
        "material_stall_task_count": int(runner_attention.get("material_stall_task_count") or 0),
        "material_stall_task_ids": list(runner_attention.get("material_stall_task_ids") or []),
        "active_material_silence_task_count": int(runner_attention.get("active_material_silence_task_count") or 0),
        "active_material_silence_task_ids": list(runner_attention.get("active_material_silence_task_ids") or []),
        "active_material_silence_post_soft_deadline_task_count": int(runner_attention.get("active_material_silence_post_soft_deadline_task_count") or 0),
        "active_material_silence_post_soft_deadline_task_ids": list(runner_attention.get("active_material_silence_post_soft_deadline_task_ids") or []),
        "active_material_silence_post_soft_deadline_agents_by_task": dict(runner_attention.get("active_material_silence_post_soft_deadline_agents_by_task") or {}),
        "material_progress_agents_by_task": dict(runner_attention.get("material_progress_agents_by_task") or {}),
        "material_stall_agents_by_task": dict(runner_attention.get("material_stall_agents_by_task") or {}),
        "active_material_silent_agents_by_task": dict(runner_attention.get("active_material_silent_agents_by_task") or {}),
        "material_point_count": 0,
        "peer_uptake_count": 0,
        "peer_challenge_count": 0,
        "summary_point_count": 0,
        "summary_peer_uptake_count": 0,
        "integrated_summary_count": 0,
        "summary_needs_integration_count": 0,
        "closure_summary_needed_count": 0,
        "integration_signal_count": 0,
        "blocker_count": 0,
        "open_blocker_count": 0,
        "blocker_task_count": 0,
        "blocker_task_ids": [],
        "tasks_missing_peer_uptake_count": 0,
        "tasks_missing_peer_uptake_ids": [],
        "tasks_missing_summary_uptake_count": 0,
        "tasks_missing_summary_uptake_ids": [],
        "tasks_needing_summary_integration_count": 0,
        "tasks_needing_summary_integration_ids": [],
        "tasks_needing_closure_summary_count": 0,
        "tasks_needing_closure_summary_ids": [],
        "closure_summary_candidate_agents_by_task": {},
        "active_claim_count": 0,
        "expired_claim_count": 0,
        "claim_lease_expired_task_count": 0,
        "claim_lease_expired_task_ids": [],
        "missing_claim_lease_count": 0,
        "declared_scope_work_item_count": 0,
        "declared_scope_path_count": 0,
        "declared_scope_unique_path_count": 0,
        "scope_conflict_count": 0,
        "scope_conflict_task_count": 0,
        "scope_conflict_task_ids": [],
        "per_agent_material_points": {},
        "per_agent_peer_uptakes": {},
        "per_agent_peer_challenges": {},
        "per_agent_blockers": {},
        "per_agent_expired_claims": {},
        "per_agent_declared_scope_paths": {},
        "per_agent_material_stalls": {},
        "per_agent_discussion_progress": {},
        "per_agent_action_items": {},
        "per_agent_next_actions": {},
        "action_item_count": 0,
        "action_items": [],
        "recent_material_threads": [],
        "room_task_counts": {},
        "efficiency_score_avg": None,
        "efficiency_low_task_ids": [],
        "per_task_efficiency_breakdown": {},
    }
    efficiency_overalls: list[float] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "")
        if not task_id:
            continue
        quality_gate = str(task.get("quality_gate_status") or "")
        review_status = str(task.get("review_status") or "").strip().lower()
        room_id = str(task.get("room_id") or "")
        if room_id:
            overview["room_task_counts"][room_id] = overview["room_task_counts"].get(room_id, 0) + 1
        needs_repair = (
            bool(task.get("degraded_quorum"))
            or quality_gate == "needs_collaboration_repair"
            or quality_gate == "degraded_quorum"
            or review_status == "degraded_quorum"
        )
        is_degraded = needs_repair
        if is_degraded:
            overview["degraded_quorum_task_count"] += 1
            overview["degraded_quorum_task_ids"].append(task_id)
        if quality_gate == "peer_reviewed":
            overview["peer_reviewed_task_count"] += 1
        elif quality_gate == "needs_collaboration_review":
            overview["needs_collaboration_review_count"] += 1
            overview["needs_collaboration_attention_task_ids"].append(task_id)
            append_collaboration_action_item(overview, {
                "type": "collaboration_review_needed",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "reason": task.get("quality_gate_reason") or "parallel artifacts need peer integration",
            })
        elif needs_repair:
            overview["needs_collaboration_repair_count"] += 1
            overview["needs_collaboration_attention_task_ids"].append(task_id)
            append_collaboration_action_item(overview, {
                "type": "collaboration_repair_needed",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "reason": task.get("quality_gate_reason") or "missing peer interaction evidence",
            })
        collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
        overview["material_point_count"] += int(collaboration.get("material_points") or 0)
        overview["peer_uptake_count"] += int(collaboration.get("peer_uptakes") or 0)
        overview["peer_challenge_count"] += int(collaboration.get("peer_challenges") or 0)
        overview["summary_point_count"] += int(collaboration.get("summary_points") or 0)
        overview["summary_peer_uptake_count"] += int(collaboration.get("summary_peer_uptakes") or 0)
        overview["integrated_summary_count"] += int(collaboration.get("integrated_summaries") or 0)
        summary_needs_integration = int(collaboration.get("summary_needs_integration") or 0)
        overview["summary_needs_integration_count"] += summary_needs_integration
        closure_summary_needed = int(collaboration.get("closure_summary_needed") or 0)
        overview["closure_summary_needed_count"] += closure_summary_needed
        overview["integration_signal_count"] += int(collaboration.get("integration_signals") or 0)
        blocker_count = int(collaboration.get("blockers") or 0)
        open_blocker_count = int(collaboration.get("open_blockers") or 0)
        overview["blocker_count"] += blocker_count
        overview["open_blocker_count"] += open_blocker_count
        if blocker_count > 0:
            overview["blocker_task_count"] += 1
            overview["blocker_task_ids"].append(task_id)
        recent_blockers = collaboration.get("recent_blockers") if isinstance(collaboration.get("recent_blockers"), list) else []
        for blocker in recent_blockers:
            if not isinstance(blocker, dict):
                continue
            if str(blocker.get("status") or "open") in {"resolved", "closed", "superseded"}:
                continue
            append_collaboration_action_item(overview, {
                "type": "blocker_review_needed",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "source_agent_id": blocker.get("agent_id"),
                "blocker_id": blocker.get("blocker_id"),
                "work_item_id": blocker.get("work_item_id"),
                "reason": blocker.get("reason") or "collaboration blocker is open and needs integrated closure",
            })
        overview["active_claim_count"] += int(collaboration.get("active_claims") or 0)
        expired_claims = int(collaboration.get("expired_claims") or 0)
        overview["expired_claim_count"] += expired_claims
        overview["missing_claim_lease_count"] += int(collaboration.get("missing_claim_leases") or 0)
        overview["declared_scope_work_item_count"] += int(collaboration.get("declared_scope_work_items") or 0)
        overview["declared_scope_path_count"] += int(collaboration.get("declared_scope_path_count") or 0)
        overview["declared_scope_unique_path_count"] += int(collaboration.get("declared_scope_unique_path_count") or 0)
        scope_conflicts = collaboration.get("scope_conflicts") if isinstance(collaboration.get("scope_conflicts"), list) else []
        scope_conflict_count = int(collaboration.get("scope_conflict_count") or 0)
        overview["scope_conflict_count"] += scope_conflict_count
        if scope_conflict_count > 0:
            overview["scope_conflict_task_count"] += 1
            overview["scope_conflict_task_ids"].append(task_id)
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
            for conflict in scope_conflicts:
                if not isinstance(conflict, dict):
                    continue
                for agent_id in conflict.get("agents") or []:
                    append_collaboration_action_item(overview, {
                        "type": "scope_conflict_review_needed",
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "path": conflict.get("path"),
                        "reason": "multiple agents declared the same work scope path",
                    })
        if expired_claims > 0:
            overview["claim_lease_expired_task_count"] += 1
            overview["claim_lease_expired_task_ids"].append(task_id)
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
            expired_claim_ids = [
                str(value)
                for value in (collaboration.get("expired_claim_ids") or [])
                if str(value)
            ]
            expired_by_agent = collaboration.get("expired_claim_counts_by_agent") or {}
            if isinstance(expired_by_agent, dict) and expired_by_agent:
                for agent_id, count in sorted(expired_by_agent.items()):
                    append_collaboration_action_item(overview, {
                        "type": "claim_lease_expired",
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "count": int_or_none(count) or 1,
                        "claim_ids": expired_claim_ids[:4],
                        "reason": "claimed work item lease expired before completion",
                    })
            else:
                append_collaboration_action_item(overview, {
                    "type": "claim_lease_expired",
                    "task_id": task_id,
                    "agent_id": ROOM_RUNTIME_AGENT,
                    "count": expired_claims,
                    "claim_ids": expired_claim_ids[:4],
                    "reason": "claimed work item lease expired before completion",
                })
        if int(collaboration.get("points_without_peer_uptake") or 0) > 0:
            overview["tasks_missing_peer_uptake_count"] += 1
            overview["tasks_missing_peer_uptake_ids"].append(task_id)
        if int(collaboration.get("summary_points_without_peer_uptake") or 0) > 0:
            overview["tasks_missing_summary_uptake_count"] += 1
            overview["tasks_missing_summary_uptake_ids"].append(task_id)
        if summary_needs_integration > 0:
            overview["tasks_needing_summary_integration_count"] += 1
            overview["tasks_needing_summary_integration_ids"].append(task_id)
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
        if closure_summary_needed > 0:
            overview["tasks_needing_closure_summary_count"] += 1
            overview["tasks_needing_closure_summary_ids"].append(task_id)
            if task_id not in overview["tasks_needing_summary_integration_ids"]:
                overview["tasks_needing_summary_integration_count"] += 1
                overview["tasks_needing_summary_integration_ids"].append(task_id)
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
        recent_threads = collaboration.get("recent_material_threads") or []
        thread_by_point: dict[str, dict[str, Any]] = {}
        for thread in recent_threads:
            if not isinstance(thread, dict):
                continue
            item = dict(thread)
            item["task_id"] = task_id
            overview["recent_material_threads"].append(item)
            point_id = str(thread.get("point_id") or "").strip()
            if point_id:
                thread_by_point[point_id] = thread
            for pending_agent in thread.get("pending_uptake_agents") or []:
                pending_agent_id = str(pending_agent or "").strip()
                if not pending_agent_id:
                    continue
                append_collaboration_action_item(overview, {
                    "type": "peer_uptake_needed",
                    "task_id": task_id,
                    "agent_id": pending_agent_id,
                    "point_id": point_id,
                    "source_agent_id": thread.get("agent_id"),
                    "kind": thread.get("kind"),
                    "reason": "material point has no peer uptake from this agent",
                })
        for point_id in collaboration.get("summary_needs_integration_ids") or []:
            point_key = str(point_id or "").strip()
            if not point_key:
                continue
            thread = thread_by_point.get(point_key) or {}
            if thread.get("pending_uptake_agents"):
                continue
            append_collaboration_action_item(overview, {
                "type": "summary_integration_needed",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "point_id": point_key,
                "source_agent_id": thread.get("agent_id"),
                "kind": thread.get("kind") or "summary",
                "reason": "summary point has peer response but no integrated closure",
            })
        if closure_summary_needed > 0:
            candidate_agent = str(collaboration.get("closure_summary_candidate_agent") or "").strip()
            if candidate_agent not in LOCAL_AGENTS:
                candidate_agent = ROOM_RUNTIME_AGENT
            overview["closure_summary_candidate_agents_by_task"][task_id] = candidate_agent
            candidate_point_ids = [
                str(value)
                for value in (collaboration.get("closure_summary_candidate_point_ids") or [])
                if str(value)
            ]
            append_collaboration_action_item(overview, {
                "type": "summary_integration_needed",
                "task_id": task_id,
                "agent_id": candidate_agent,
                "point_id": candidate_point_ids[0] if candidate_point_ids else None,
                "source_agent_id": candidate_agent if candidate_agent != ROOM_RUNTIME_AGENT else None,
                "reason": "peer-reviewed material exists but no closure summary point is recorded",
                "next_action": "produce an integrated closure summary or explicit handoff so main is not the manual integrator",
            })
        merge_counts(overview["per_agent_material_points"], collaboration.get("point_counts_by_agent") or {})
        merge_counts(overview["per_agent_peer_uptakes"], collaboration.get("peer_uptake_counts_by_agent") or {})
        merge_counts(overview["per_agent_peer_challenges"], collaboration.get("peer_challenge_counts_by_agent") or {})
        merge_counts(overview["per_agent_blockers"], collaboration.get("blocker_counts_by_agent") or {})
        merge_counts(overview["per_agent_expired_claims"], collaboration.get("expired_claim_counts_by_agent") or {})
        merge_counts(overview["per_agent_declared_scope_paths"], collaboration.get("declared_scope_path_counts_by_agent") or {})
        per_agent_progress = task.get("per_agent_progress") if isinstance(task.get("per_agent_progress"), dict) else {}
        merge_per_agent_discussion_progress(overview["per_agent_discussion_progress"], per_agent_progress)
        task_efficiency = task.get("efficiency") if isinstance(task.get("efficiency"), dict) else None
        if task_efficiency is not None:
            task_overall = task_efficiency.get("overall")
            if isinstance(task_overall, (int, float)):
                efficiency_overalls.append(float(task_overall))
                if task_efficiency.get("grade") in ("low", "medium"):
                    overview["per_task_efficiency_breakdown"][task_id] = {
                        "overall": task_efficiency.get("overall"),
                        "grade": task_efficiency.get("grade"),
                        "peer_uptake_ratio": task_efficiency.get("peer_uptake_ratio"),
                        "integration_ratio": task_efficiency.get("integration_ratio"),
                        "claim_efficiency": task_efficiency.get("claim_efficiency"),
                        "stall_indicator": task_efficiency.get("stall_indicator"),
                    }
                if task_efficiency.get("grade") == "low":
                    overview["efficiency_low_task_ids"].append(task_id)
    for task_id in overview["runner_attention_task_ids"]:
        if task_id not in overview["needs_collaboration_attention_task_ids"]:
            overview["needs_collaboration_attention_task_ids"].append(task_id)
        append_collaboration_action_item(overview, {
            "type": "runner_attention_needed",
            "task_id": task_id,
            "agent_id": ROOM_RUNTIME_AGENT,
            "reason": "active runner needs harvest or failure handling",
            "next_action": RUNNER_ATTENTION_NEXT_ACTION,
        })
    for task_id in overview["runner_degraded_quorum_task_ids"]:
        if task_id not in overview["degraded_quorum_task_ids"]:
            overview["degraded_quorum_task_ids"].append(task_id)
            overview["degraded_quorum_task_count"] += 1
    material_stall_agents_by_task = overview.get("material_stall_agents_by_task") or {}
    if isinstance(material_stall_agents_by_task, dict):
        for task_id, agents in sorted(material_stall_agents_by_task.items()):
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
            for agent_id in sorted(str(value) for value in (agents or []) if str(value)):
                increment_count(overview["per_agent_material_stalls"], agent_id)
                append_collaboration_action_item(overview, {
                    "type": "material_progress_needed",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "reason": "agent is present for an attention task but has no material ledger point",
                    "next_action": POST_SOFT_MATERIAL_PROGRESS_NEXT_ACTION,
                })
    active_silent_agents_by_task = overview.get("active_material_silent_agents_by_task") or {}
    post_soft_deadline_agents_by_task = overview.get("active_material_silence_post_soft_deadline_agents_by_task") or {}
    runner_attention_task_ids = set(str(value) for value in (overview.get("runner_attention_task_ids") or []) if str(value))
    if isinstance(active_silent_agents_by_task, dict):
        for task_id, agents in sorted(active_silent_agents_by_task.items()):
            if task_id in runner_attention_task_ids:
                continue
            silent_agents = sorted(str(value) for value in (agents or []) if str(value))
            if not silent_agents:
                continue
            append_collaboration_action_item(overview, {
                "type": "active_material_silence_watch",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "silent_agents": silent_agents,
                "reason": "live runner has no material point/uptake/challenge before attention boundary; keep running until soft deadline then reassess",
                "next_action": ACTIVE_MATERIAL_SILENCE_NEXT_ACTION,
            })
            for agent_id in silent_agents:
                increment_count(overview["per_agent_action_items"], agent_id)
    if isinstance(post_soft_deadline_agents_by_task, dict):
        for task_id, agents in sorted(post_soft_deadline_agents_by_task.items()):
            if task_id in runner_attention_task_ids:
                continue
            silent_agents = sorted(str(value) for value in (agents or []) if str(value))
            if not silent_agents:
                continue
            if task_id not in overview["needs_collaboration_attention_task_ids"]:
                overview["needs_collaboration_attention_task_ids"].append(task_id)
            append_collaboration_action_item(overview, {
                "type": "collaboration_repair_needed",
                "task_id": task_id,
                "agent_id": ROOM_RUNTIME_AGENT,
                "reason": "active material silence crossed soft deadline with no material point/uptake/challenge",
                "next_action": POST_SOFT_MATERIAL_PROGRESS_NEXT_ACTION,
            })
            for agent_id in silent_agents:
                append_collaboration_action_item(overview, {
                    "type": "material_progress_needed",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "reason": "runner remained silent past soft deadline; provide material point or uptake/challenge",
                    "next_action": POST_SOFT_MATERIAL_PROGRESS_NEXT_ACTION,
                })
    overview["recent_material_threads"] = overview["recent_material_threads"][:8]
    if efficiency_overalls:
        overview["efficiency_score_avg"] = round(sum(efficiency_overalls) / len(efficiency_overalls), 3)
    overview["action_items"] = prioritized_action_items(overview.get("action_items"))
    overview["per_agent_next_actions"] = per_agent_next_action_queue(overview.get("action_items"))
    return overview


def build_status(room_id: str = DEFAULT_ROOM_ID, *, include_background: bool = False) -> dict[str, Any]:
    daemon = read_json(DAEMON_STATUS, {}) or {}
    active = active_runner_rows(room_id, include_background=include_background)
    current_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in active
        if row.get("task_id") or row.get("run_id")
    ], room_id)
    participant_presence = participant_presence_rows(current_task_ids, active)
    per_agent = agent_engagement_rows(active, participant_presence)
    live_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in active
        if row.get("alive") and not row.get("ledger_completed") and (row.get("task_id") or row.get("run_id"))
    ], room_id)
    attention_task_ids = ordered_task_ids([
        str(row.get("task_id") or row.get("run_id") or "")
        for row in active
        if row.get("needs_attention") and (row.get("task_id") or row.get("run_id"))
    ], room_id)
    recent_tasks = recent_task_rows(
        room_id=room_id,
        include_background=include_background,
        pinned_task_ids=current_task_ids,
    )
    runner_attention = runner_attention_overview(active)
    collaboration_summary = collaboration_overview(recent_tasks, runner_attention)
    provider_health = provider_health_snapshot()
    return {
        "schema": "openclaw.agent_room.collaboration_status.v0",
        "room_id": room_id,
        "generated_at": now_iso(),
        "visibility_state": classify_visibility_state(active, daemon if isinstance(daemon, dict) else {}),
        "runner_record_count": len(active),
        "active_runner_count": sum(
            1 for row in active
            if row.get("alive") and not row.get("result_exists") and not row.get("ledger_completed")
        ),
        "active_task_ids": current_task_ids,
        "live_task_ids": live_task_ids,
        "attention_task_ids": attention_task_ids,
        "per_agent_engagement": per_agent,
        "agent_liveness": per_agent,
        "one_glance": one_glance_status(per_agent, room_id=room_id),
        "activity_dashboard": activity_dashboard(active, per_agent),
        "active_runners": active,
        "participant_presence": participant_presence,
        "daemon": {
            "status": daemon.get("status") if isinstance(daemon, dict) else None,
            "tick": daemon.get("tick") if isinstance(daemon, dict) else None,
            "last_tick_ok": daemon.get("last_tick_ok") if isinstance(daemon, dict) else None,
            "last_tick_finished_at": daemon.get("last_tick_finished_at") if isinstance(daemon, dict) else None,
            "telegram_outbound_enabled": daemon.get("telegram_outbound_enabled") if isinstance(daemon, dict) else None,
            "standing_agenda_tick": daemon.get("standing_agenda_tick") if isinstance(daemon, dict) else None,
            "continuation_tick": daemon.get("continuation_tick") if isinstance(daemon, dict) else None,
        },
        "collaboration_overview": collaboration_summary,
        "provider_health": provider_health,
        "recent_tasks": recent_tasks,
        "user_visible_summary": "; ".join(
            f"{agent}: {data.get('engagement_state')} ({data.get('active_runner_count')} active, {data.get('pending_harvest_count')} pending harvest)"
            for agent, data in sorted(per_agent.items())
        ),
        "noise_policy": "state_transition_only_no_periodic_chat_heartbeat",
        "room_scope_policy": "room_id_filtered_background_excluded_unless_room_visible",
        "include_background": include_background,
        "tokens_printed": False,
    }


def task_status_value(rows: list[dict[str, Any]], ledger_status: Any) -> str:
    if any(row.get("runner_state") in {"dead_without_result", "hard_deadline_exceeded_no_result"} for row in rows):
        return "runner_attention_needed"
    if any(row.get("needs_attention") for row in rows):
        return "runner_attention_needed"
    if any(row.get("result_exists") for row in rows):
        return "result_pending_harvest"
    if any(row.get("alive") for row in rows):
        return "running"
    return str(ledger_status or "open")


def task_ledger_summary(task_id: str) -> dict[str, Any]:
    path = collaboration_ledger_path_for_task(task_id)
    ledger = read_json(path, None)
    if not isinstance(ledger, dict) or ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return {
            "available": False,
            "path": str(path),
            "status": None,
            "work_items": [],
            "claims": [],
            "artifact_count": 0,
            "blocker_count": 0,
            "handoff_count": 0,
        }
    if str(ledger.get("task_id") or ledger.get("run_id") or "") not in {"", task_id}:
        return {
            "available": False,
            "path": str(path),
            "status": None,
            "work_items": [],
            "claims": [],
            "artifact_count": 0,
            "blocker_count": 0,
            "handoff_count": 0,
        }
    work_items = ledger.get("work_items") if isinstance(ledger.get("work_items"), list) else []
    claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
    artifacts = ledger.get("artifacts") if isinstance(ledger.get("artifacts"), list) else []
    blockers = ledger.get("blockers") if isinstance(ledger.get("blockers"), list) else []
    handoffs = ledger.get("handoffs") if isinstance(ledger.get("handoffs"), list) else []
    return {
        "available": True,
        "path": str(path),
        "status": ledger.get("status"),
        "updated_at": ledger.get("updated_at"),
        "work_items": [
                {
                    "id": item.get("id"),
                    "assigned_to": item.get("assigned_to"),
                    "status": item.get("status"),
                    "claimed_by": item.get("claimed_by"),
                    "lease_expiry": item.get("lease_expiry"),
                    "lease_state": lease_state(item.get("lease_expiry")),
                    "declared_scope": item.get("declared_scope") if isinstance(item.get("declared_scope"), dict) else None,
                }
                for item in work_items
                if isinstance(item, dict)
        ],
        "claims": [
            {
                "work_item_id": claim.get("work_item_id"),
                "agent_id": claim.get("agent_id"),
                "status": claim.get("status"),
                "claimed_at": claim.get("claimed_at"),
                "lease_expiry": claim.get("lease_expiry"),
                "lease_state": lease_state(claim.get("lease_expiry")),
            }
            for claim in claims
            if isinstance(claim, dict)
        ],
        "artifact_count": len(artifacts),
        "blocker_count": len(blockers),
        "handoff_count": len(handoffs),
    }


def task_agent_liveness(rows: list[dict[str, Any]], participants: list[str], ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    agent_ids = {
        str(agent_id)
        for agent_id in participants
        if str(agent_id or "").strip()
    } | {
        str(row.get("agent_id") or "")
        for row in rows
        if str(row.get("agent_id") or "").strip()
    }
    out: dict[str, dict[str, Any]] = {}
    priority = {
        "dead_without_result": 100,
        "hard_deadline_exceeded_no_result": 95,
        "over_soft_deadline_no_output": 90,
        "over_soft_deadline_with_output": 80,
        "result_ready_for_harvest": 70,
        "result_pending_harvest_process_alive": 70,
        "working_silent_before_soft_deadline": 60,
        "working_with_local_output": 50,
        "claimed_no_live_runner": 40,
        "completed": 20,
        "blocked": 20,
        "not_observed": 0,
    }
    work_items = ledger.get("work_items") if isinstance(ledger.get("work_items"), list) else []
    claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
    for agent_id in sorted(agent_ids):
        agent_rows = [row for row in rows if row.get("agent_id") == agent_id]
        agent_work_items = [
            item for item in work_items
            if isinstance(item, dict) and (item.get("assigned_to") == agent_id or item.get("claimed_by") == agent_id)
        ]
        agent_claims = [
            claim for claim in claims
            if isinstance(claim, dict) and claim.get("agent_id") == agent_id
        ]
        states = [str(row.get("runner_state") or "unknown") for row in agent_rows]
        if not states:
            if any(str(item.get("status") or "") == "claimed" for item in agent_work_items):
                states = ["claimed_no_live_runner"]
            elif any(str(item.get("status") or "") == "completed" for item in agent_work_items):
                states = ["completed"]
            elif any(str(item.get("status") or "") == "blocked" for item in agent_work_items):
                states = ["blocked"]
            else:
                states = ["not_observed"]
        state = max(states, key=lambda value: priority.get(value, 0))
        out[agent_id] = {
            "agent_id": agent_id,
            "state": state,
            "runner_count": len(agent_rows),
            "live_runner_count": sum(1 for row in agent_rows if row.get("alive") and not row.get("result_exists")),
            "needs_attention": any(row.get("needs_attention") for row in agent_rows) or state == "claimed_no_live_runner",
            "black_box_runner_count": sum(1 for row in agent_rows if row.get("is_black_box")),
            "work_items": agent_work_items,
            "claims": agent_claims,
            "latest_runner": agent_rows[-1] if agent_rows else None,
        }
    return out


def existing_task_degraded_quorum(manifest: dict[str, Any]) -> dict[str, Any] | None:
    degraded = manifest.get("degraded_quorum")
    if isinstance(degraded, dict):
        return degraded
    collaboration = manifest.get("collaboration") if isinstance(manifest.get("collaboration"), dict) else {}
    degraded = collaboration.get("degraded_quorum")
    return degraded if isinstance(degraded, dict) else None


def task_degraded_quorum_record(
    status: dict[str, Any],
    manifest: dict[str, Any],
    task_id: str,
    participants: list[str],
    liveness: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    existing = existing_task_degraded_quorum(manifest)
    if existing:
        return existing
    local_participants = [agent_id for agent_id in participants if agent_id in LOCAL_AGENTS]
    if len(local_participants) <= 1:
        return None

    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    degraded_ids = set(str(value) for value in (overview.get("degraded_quorum_task_ids") or []))
    runner_degraded_ids = set(str(value) for value in (overview.get("runner_degraded_quorum_task_ids") or []))
    runner_summary = manifest.get("runner_summary") if isinstance(manifest.get("runner_summary"), dict) else {}
    quality_gate = runner_summary.get("collaboration_quality_gate") if isinstance(runner_summary.get("collaboration_quality_gate"), dict) else {}
    if task_id not in degraded_ids and task_id not in runner_degraded_ids:
        return None

    if task_id in runner_degraded_ids:
        reason = "all_local_agents_need_attention"
        evidence = "active_runner_liveness"
    else:
        reason = str(quality_gate.get("reason") or manifest.get("quality_gate_reason") or "quality_gate_degraded_quorum")
        evidence = "task_quality_gate_status"

    unavailable = [
        str(agent_id)
        for agent_id in (quality_gate.get("missing_agents") or [])
        if str(agent_id) in local_participants
    ]
    if not unavailable:
        unavailable = [
            agent_id
            for agent_id in local_participants
            if liveness.get(agent_id, {}).get("needs_attention")
        ]
    if not unavailable and reason == "missing_local_agent_results":
        completed = {
            str(agent_id)
            for agent_id in (runner_summary.get("completed_agents") or [])
            if str(agent_id) in local_participants
        }
        blocked = {
            str(agent_id)
            for agent_id in (runner_summary.get("blocked_agents") or [])
            if str(agent_id) in local_participants
        }
        failed = {
            str(agent_id)
            for agent_id in (runner_summary.get("failed_agents") or [])
            if str(agent_id) in local_participants
        }
        unavailable = sorted(set(local_participants).difference(completed | blocked | failed))
    if not unavailable and task_id in runner_degraded_ids:
        unavailable = local_participants

    unavailable = sorted(set(unavailable))
    continued_by = [agent_id for agent_id in local_participants if agent_id not in set(unavailable)]
    runner_states = {
        agent_id: str(liveness.get(agent_id, {}).get("state") or "unknown")
        for agent_id in local_participants
    }
    return {
        "schema": "openclaw.agent_room.degraded_quorum.v0",
        "mode": "status_snapshot",
        "status": "degraded_quorum_observed",
        "created_at": status.get("generated_at") or now_iso(),
        "parent_task_id": task_id,
        "reason": reason,
        "unavailable_agents": [
            {
                "agent_id": agent_id,
                "reason": reason,
                "evidence": evidence,
            }
            for agent_id in unavailable
        ],
        "continued_by": continued_by,
        "follow_up_review_needed_by": unavailable,
        "main_review_needed": True,
        "main_review_reason": "status surface observed collaboration without full local agent quorum",
        "detail": {
            "runner_states": runner_states,
            "quality_gate": quality_gate,
        },
    }


def build_task_status_snapshot(status: dict[str, Any], task_id: str) -> dict[str, Any]:
    rows = [
        row for row in (status.get("active_runners") or [])
        if isinstance(row, dict) and str(row.get("task_id") or row.get("run_id") or "") == task_id
    ]
    manifest = read_json(task_manifest_path_for_task(task_id), {}) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    target_agents = [
        str(agent_id)
        for agent_id in (manifest.get("target_agents") or [])
        if str(agent_id)
    ]
    expected_agents = [
        str(agent_id)
        for row in rows
        for agent_id in (row.get("expected_agents") or [])
        if str(agent_id)
    ]
    participants = list(dict.fromkeys(target_agents + expected_agents))
    ledger = task_ledger_summary(task_id)
    run_id = str(manifest.get("run_id") or task_id)
    task_status = task_status_value(rows, ledger.get("status") or manifest.get("status"))
    liveness = task_agent_liveness(rows, participants, ledger)
    collaboration = collaboration_snapshot(manifest)
    collaboration_health = collaboration_metrics(collaboration, target_agents)
    efficiency = collaboration_efficiency_score(collaboration_health)
    per_agent_progress = per_agent_collaboration_progress(collaboration, target_agents)
    overview = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    task_actions = [
        item
        for item in (overview.get("action_items") or [])
        if isinstance(item, dict) and str(item.get("task_id") or "") == str(task_id)
    ]
    return {
        "schema": "openclaw.agent_room.collaboration_status.v0",
        "room_id": manifest.get("room_id"),
        "task_id": task_id,
        "run_id": run_id,
        "phase": "collaboration_status_refresh",
        "status": task_status,
        "updated_at": status.get("generated_at") or now_iso(),
        "participants": participants,
        "degraded_quorum": task_degraded_quorum_record(status, manifest, task_id, participants, liveness),
        "active_runners": rows,
        "ledger": ledger,
        "collaboration_health": collaboration_health,
        "per_agent_collaboration_progress": per_agent_progress,
        "collaboration_action_items": task_actions,
        "efficiency": efficiency,
        "agent_liveness": liveness,
        "provider_health": status.get("provider_health") if isinstance(status.get("provider_health"), dict) else {},
        "agent_runs": [],
        "room_visibility": {
            "telegram_projection": "final_comments_or_explicit_status_only",
            "status_surface": str(STATUS_DIR / f"{safe_slug(task_id)}.json"),
        },
        "derived_from": str(STATUS_DIR / "latest.json"),
        "tokens_printed": False,
    }


def write_task_status_snapshots(status: dict[str, Any]) -> list[str]:
    task_ids = [
        str(task_id)
        for task_id in (status.get("active_task_ids") or [])
        if str(task_id or "").strip()
    ]
    task_ids.extend(
        str(task.get("task_id") or "")
        for task in (status.get("recent_tasks") or [])
        if isinstance(task, dict) and str(task.get("task_id") or "").strip()
    )
    written: list[str] = []
    for task_id in dict.fromkeys(task_ids):
        snapshot = build_task_status_snapshot(status, task_id)
        path = STATUS_DIR / f"{safe_slug(task_id)}.json"
        write_json(path, snapshot)
        written.append(str(path))
    return written


def render_markdown_status(status: dict[str, Any]) -> str:
    lines = [
        "# Agent Room status",
        "",
        f"generated_at: {status.get('generated_at')}",
        f"visibility_state: {status.get('visibility_state')}",
        f"active_runner_count: {status.get('active_runner_count')}",
        "",
        "## One-glance",
    ]
    one_glance = status.get("one_glance") if isinstance(status.get("one_glance"), dict) else {}
    if one_glance.get("summary_line"):
        lines.append(f"summary: {one_glance.get('summary_line')}")
    for card in one_glance.get("cards") or []:
        if not isinstance(card, dict):
            continue
        task_ids = card.get("current_task_ids") or []
        task_text = ", ".join(task_ids) if task_ids else "(none)"
        hidden = int(card.get("hidden_task_count") or 0)
        if hidden:
            task_text += f", +{hidden} more"
        lines.append(
            "- {agent}: {badge}; work={work}; state={state}; working={working}; active={active}; pending_harvest={pending}; attention={attention}; tasks={tasks}; next_soft={soft}; action={action}".format(
                agent=card.get("agent_id"),
                badge=card.get("badge"),
                work=card.get("work_status"),
                state=card.get("engagement_state"),
                working=card.get("working_runner_count"),
                active=card.get("active_runner_count"),
                pending=card.get("pending_harvest_count"),
                attention=card.get("needs_attention_count"),
                tasks=task_text,
                soft=card.get("next_soft_deadline_at"),
                action=card.get("action"),
            )
        )
    lines.extend([
        "",
        "## Per-agent engagement",
    ]
    )
    per_agent = status.get("per_agent_engagement") if isinstance(status.get("per_agent_engagement"), dict) else {}
    for agent_id, data in sorted(per_agent.items()):
        if not isinstance(data, dict):
            continue
        lines.extend([
            f"- {agent_id}: {data.get('engagement_state')}",
            f"  - working: {data.get('working_runner_count')}; active: {data.get('active_runner_count')}; pending_harvest: {data.get('pending_harvest_count')}; completed_presence: {data.get('completed_presence_count')}; black_box: {data.get('black_box_runner_count')}; needs_attention: {data.get('needs_attention_count')}",
            f"  - tasks: {', '.join(data.get('active_task_ids') or []) if data.get('active_task_ids') else '(none)'}",
            f"  - last_chat_action_age_seconds: {data.get('last_chat_action_age_seconds')}",
            f"  - next_soft: {data.get('next_soft_deadline_at')}; next_hard: {data.get('next_hard_deadline_at')}",
        ])
        for presence in data.get("participant_presence") or []:
            if not isinstance(presence, dict):
                continue
            lines.append(
                "  - presence: {task} state={state}; detail={detail}; updated={updated}; comment={comment}".format(
                    task=presence.get("task_id"),
                    state=presence.get("presence_state"),
                    detail=presence.get("presence_detail"),
                    updated=presence.get("presence_updated_at"),
                    comment=presence.get("presence_comment_title") or "",
                )
            )
    lines.extend(["", "## Active runner states"])
    for row in status.get("active_runners") or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            "- {agent} {run}: {state}; presence={presence}; alive={alive}; result={result}; stdout={stdout}; chat_action_age={chat}; soft={soft}; hard={hard}".format(
                agent=row.get("agent_id"),
                run=row.get("run_id"),
                state=row.get("runner_state"),
                presence=row.get("presence_state") or "none",
                alive=row.get("alive"),
                result=row.get("result_exists"),
                stdout=row.get("stdout_size"),
                chat=row.get("last_chat_action_age_seconds"),
                soft=row.get("soft_deadline_at"),
                hard=row.get("hard_deadline_at"),
            )
        )
    collaboration = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    lines.extend(["", "## Collaboration health"])
    lines.extend([
        f"tracked_tasks: {collaboration.get('tracked_tasks')}",
        f"peer_reviewed_task_count: {collaboration.get('peer_reviewed_task_count')}",
        f"degraded_quorum_task_count: {collaboration.get('degraded_quorum_task_count')}",
        f"needs_collaboration_review_count: {collaboration.get('needs_collaboration_review_count')}",
        f"needs_collaboration_repair_count: {collaboration.get('needs_collaboration_repair_count')}",
        f"runner_attention_task_count: {collaboration.get('runner_attention_task_count')}",
        f"runner_degraded_quorum_task_count: {collaboration.get('runner_degraded_quorum_task_count')}",
        f"runner_partial_attention_task_count: {collaboration.get('runner_partial_attention_task_count')}",
        f"material_stall_task_count: {collaboration.get('material_stall_task_count')}",
        f"material_stall_task_ids: {', '.join((collaboration.get('material_stall_task_ids') or ['(none)']))}",
        f"material_stall_agents_by_task: {collaboration.get('material_stall_agents_by_task')}",
        f"active_material_silence_task_count: {collaboration.get('active_material_silence_task_count')}",
        f"active_material_silence_task_ids: {', '.join((collaboration.get('active_material_silence_task_ids') or ['(none)']))}",
        f"active_material_silent_agents_by_task: {collaboration.get('active_material_silent_agents_by_task')}",
        f"active_material_silence_post_soft_deadline_task_count: {collaboration.get('active_material_silence_post_soft_deadline_task_count')}",
        f"active_material_silence_post_soft_deadline_task_ids: {', '.join((collaboration.get('active_material_silence_post_soft_deadline_task_ids') or ['(none)']))}",
        f"active_material_silence_post_soft_deadline_agents_by_task: {collaboration.get('active_material_silence_post_soft_deadline_agents_by_task')}",
        f"material_progress_agents_by_task: {collaboration.get('material_progress_agents_by_task')}",
        f"material_point_count: {collaboration.get('material_point_count')}",
        f"peer_uptake_count: {collaboration.get('peer_uptake_count')}",
        f"peer_challenge_count: {collaboration.get('peer_challenge_count')}",
        f"summary_point_count: {collaboration.get('summary_point_count')}",
        f"summary_peer_uptake_count: {collaboration.get('summary_peer_uptake_count')}",
        f"integrated_summary_count: {collaboration.get('integrated_summary_count')}",
        f"summary_needs_integration_count: {collaboration.get('summary_needs_integration_count')}",
        f"closure_summary_needed_count: {collaboration.get('closure_summary_needed_count')}",
        f"integration_signal_count: {collaboration.get('integration_signal_count')}",
        f"blocker_count: {collaboration.get('blocker_count')}",
        f"open_blocker_count: {collaboration.get('open_blocker_count')}",
        f"blocker_task_count: {collaboration.get('blocker_task_count')}",
        f"active_claim_count: {collaboration.get('active_claim_count')}",
        f"expired_claim_count: {collaboration.get('expired_claim_count')}",
        f"claim_lease_expired_task_count: {collaboration.get('claim_lease_expired_task_count')}",
        f"missing_claim_lease_count: {collaboration.get('missing_claim_lease_count')}",
        f"declared_scope_work_item_count: {collaboration.get('declared_scope_work_item_count')}",
        f"declared_scope_path_count: {collaboration.get('declared_scope_path_count')}",
        f"declared_scope_unique_path_count: {collaboration.get('declared_scope_unique_path_count')}",
        f"scope_conflict_count: {collaboration.get('scope_conflict_count')}",
        f"scope_conflict_task_count: {collaboration.get('scope_conflict_task_count')}",
        f"tasks_missing_peer_uptake_count: {collaboration.get('tasks_missing_peer_uptake_count')}",
        f"tasks_missing_summary_uptake_count: {collaboration.get('tasks_missing_summary_uptake_count')}",
        f"tasks_needing_summary_integration_count: {collaboration.get('tasks_needing_summary_integration_count')}",
        f"tasks_needing_closure_summary_count: {collaboration.get('tasks_needing_closure_summary_count')}",
        f"needs_attention_task_ids: {', '.join((collaboration.get('needs_collaboration_attention_task_ids') or ['(none)']))}",
        f"tasks_missing_peer_uptake_ids: {', '.join((collaboration.get('tasks_missing_peer_uptake_ids') or ['(none)']))}",
        f"tasks_missing_summary_uptake_ids: {', '.join((collaboration.get('tasks_missing_summary_uptake_ids') or ['(none)']))}",
        f"tasks_needing_summary_integration_ids: {', '.join((collaboration.get('tasks_needing_summary_integration_ids') or ['(none)']))}",
        f"tasks_needing_closure_summary_ids: {', '.join((collaboration.get('tasks_needing_closure_summary_ids') or ['(none)']))}",
        f"closure_summary_candidate_agents_by_task: {collaboration.get('closure_summary_candidate_agents_by_task')}",
        f"blocker_task_ids: {', '.join((collaboration.get('blocker_task_ids') or ['(none)']))}",
        f"claim_lease_expired_task_ids: {', '.join((collaboration.get('claim_lease_expired_task_ids') or ['(none)']))}",
        f"scope_conflict_task_ids: {', '.join((collaboration.get('scope_conflict_task_ids') or ['(none)']))}",
        f"per_agent_material_points: {collaboration.get('per_agent_material_points')}",
        f"per_agent_peer_uptakes: {collaboration.get('per_agent_peer_uptakes')}",
        f"per_agent_peer_challenges: {collaboration.get('per_agent_peer_challenges')}",
        f"per_agent_blockers: {collaboration.get('per_agent_blockers')}",
        f"per_agent_expired_claims: {collaboration.get('per_agent_expired_claims')}",
        f"per_agent_declared_scope_paths: {collaboration.get('per_agent_declared_scope_paths')}",
        f"per_agent_material_stalls: {collaboration.get('per_agent_material_stalls')}",
        f"per_agent_action_items: {collaboration.get('per_agent_action_items')}",
        f"action_item_count: {collaboration.get('action_item_count')}",
        f"efficiency_score_avg: {collaboration.get('efficiency_score_avg')}",
        f"efficiency_low_task_ids: {', '.join((collaboration.get('efficiency_low_task_ids') or ['(none)']))}",
    ])
    per_task_eff = collaboration.get("per_task_efficiency_breakdown")
    if isinstance(per_task_eff, dict) and per_task_eff:
        lines.append("per_task_efficiency_breakdown:")
        for tid, breakdown in sorted(per_task_eff.items()):
            if not isinstance(breakdown, dict):
                continue
            lines.append(
                "- {tid}: overall={overall} grade={grade} uptake={uptake} integration={integration} claim_eff={claim_eff} stall={stall}".format(
                    tid=tid,
                    overall=breakdown.get("overall"),
                    grade=breakdown.get("grade"),
                    uptake=breakdown.get("peer_uptake_ratio"),
                    integration=breakdown.get("integration_ratio"),
                    claim_eff=breakdown.get("claim_efficiency"),
                    stall=breakdown.get("stall_indicator"),
                )
            )
    discussion_progress = collaboration.get("per_agent_discussion_progress")
    if isinstance(discussion_progress, dict) and discussion_progress:
        lines.append("per_agent_discussion_progress:")
        for agent_id, progress in sorted(discussion_progress.items()):
            if not isinstance(progress, dict):
                continue
            states = progress.get("liveness_vs_progress_counts") if isinstance(progress.get("liveness_vs_progress_counts"), dict) else {}
            state_text = ", ".join(
                f"{state}:{count}"
                for state, count in sorted(states.items())
            ) or "(none)"
            lines.append(
                "- {agent}: points={points}; with_peer={with_peer}; uptakes={uptakes}; challenges={challenges}; integrations={integrations}; summaries={summaries}/{integrated}; states={states}".format(
                    agent=agent_id,
                    points=progress.get("points_produced") or 0,
                    with_peer=progress.get("points_with_peer_uptake") or 0,
                    uptakes=progress.get("peer_points_uptaken") or 0,
                    challenges=progress.get("peer_challenges") or 0,
                    integrations=progress.get("peer_integrations") or 0,
                    summaries=progress.get("summaries_produced") or 0,
                    integrated=progress.get("summaries_integrated") or 0,
                    states=state_text,
                )
            )
    next_actions = collaboration.get("per_agent_next_actions")
    if isinstance(next_actions, dict) and next_actions:
        lines.append("per_agent_next_actions:")
        for agent_id, queue in sorted(next_actions.items()):
            if not isinstance(queue, dict):
                continue
            lines.append(
                "- {agent}: count={count}; hidden={hidden}".format(
                    agent=agent_id,
                    count=queue.get("action_count") or 0,
                    hidden=queue.get("hidden_count") or 0,
                )
            )
            for action in (queue.get("actions") or [])[:5]:
                if not isinstance(action, dict):
                    continue
                detail = f"; detail={action.get('detail')}" if action.get("detail") else ""
                source = f"; source={action.get('source_agent_id')}" if action.get("source_agent_id") else ""
                primary = f"; primary={action.get('primary_agent_id')}" if action.get("primary_agent_id") and action.get("role") != "primary" else ""
                next_action = f"; next={action.get('next_action')}" if action.get("next_action") else ""
                lines.append(
                    "  - {kind}: task={task}; role={role}{primary}{detail}{source}{next_action}; reason={reason}".format(
                        kind=action.get("type") or "unknown",
                        task=action.get("task_id"),
                        role=action.get("role") or "unknown",
                        primary=primary,
                        detail=detail,
                        source=source,
                        next_action=next_action,
                        reason=action.get("reason") or "",
                    )
                )
    action_items = collaboration.get("action_items") if isinstance(collaboration.get("action_items"), list) else []
    if action_items:
        lines.append("action_items:")
        for item in prioritized_action_items(action_items, limit=ACTION_ITEM_SURFACE_LIMIT):
            if not isinstance(item, dict):
                continue
            detail_parts: list[str] = []
            if item.get("point_id"):
                detail_parts.append(f"point={item.get('point_id')}")
            if item.get("source_agent_id"):
                detail_parts.append(f"source={item.get('source_agent_id')}")
            if item.get("claim_ids"):
                detail_parts.append(f"claims={','.join(str(value) for value in item.get('claim_ids') or [])}")
            if item.get("blocker_id"):
                detail_parts.append(f"blocker={item.get('blocker_id')}")
            if item.get("work_item_id"):
                detail_parts.append(f"work_item={item.get('work_item_id')}")
            if item.get("path"):
                detail_parts.append(f"path={item.get('path')}")
            if item.get("silent_agents"):
                detail_parts.append(f"silent_agents={','.join(str(value) for value in item.get('silent_agents') or [])}")
            if item.get("next_action"):
                detail_parts.append(f"next={item.get('next_action')}")
            detail = "; ".join(detail_parts) or "detail=(none)"
            lines.append(
                "- {agent}/{kind}: task={task}; {detail}; reason={reason}".format(
                    agent=item.get("agent_id") or "unknown",
                    kind=item.get("type") or "unknown",
                    task=item.get("task_id"),
                    detail=detail,
                    reason=item.get("reason") or "",
                )
            )
    recent_threads = collaboration.get("recent_material_threads") if isinstance(collaboration.get("recent_material_threads"), list) else []
    if recent_threads:
        lines.append("recent_material_threads:")
        for thread in recent_threads[:8]:
            if not isinstance(thread, dict):
                continue
            uptakes = []
            for uptake in thread.get("peer_uptakes") or []:
                if not isinstance(uptake, dict):
                    continue
                uptake_agent = uptake.get("by_agent") or "unknown"
                uptake_status = uptake.get("status") or "unknown"
                uptakes.append(f"{uptake_agent}={uptake_status}")
            pending = ", ".join(thread.get("pending_uptake_agents") or []) or "(none)"
            lines.append(
                "- {task} {point} {agent}/{kind}: {text}; uptakes={uptakes}; pending={pending}".format(
                    task=thread.get("task_id"),
                    point=thread.get("point_id"),
                    agent=thread.get("agent_id"),
                    kind=thread.get("kind"),
                    text=thread.get("text"),
                    uptakes=", ".join(uptakes) if uptakes else "(none)",
                    pending=pending,
                )
            )
    provider = status.get("provider_health") if isinstance(status.get("provider_health"), dict) else {}
    if provider:
        lines.extend([
            "",
            "## Provider health",
            f"signal_fresh: {provider.get('signal_fresh')}",
            f"degraded_agents: {', '.join((provider.get('degraded_agents') or ['(none)']))}",
            f"unknown_agents: {', '.join((provider.get('unknown_agents') or ['(none)']))}",
            f"remaining_unknown_channels: {', '.join((provider.get('remaining_unknown_channels') or ['(none)']))}",
        ])
        per_agent_provider = provider.get("per_agent") if isinstance(provider.get("per_agent"), dict) else {}
        for agent_id, data in sorted(per_agent_provider.items()):
            if not isinstance(data, dict):
                continue
            lines.append(
                "- {agent}: availability={availability}; active_cooldowns={cooldowns}; available_models={available}; stale_depleted={stale}; channels={channels}".format(
                    agent=agent_id,
                    availability=data.get("availability"),
                    cooldowns=data.get("active_cooldown_count"),
                    available=data.get("available_model_count"),
                    stale=data.get("stale_depleted_model_count"),
                    channels=",".join(data.get("token_channels") or []),
                )
            )
        token_channels = provider.get("token_channels") if isinstance(provider.get("token_channels"), dict) else {}
        for channel_id, channel in sorted(token_channels.items()):
            if not isinstance(channel, dict):
                continue
            lines.append(
                "- channel {channel}: status={status}; remaining_known={remaining}; available_models={available}/{total}; usage_api_status={usage}".format(
                    channel=channel_id,
                    status=channel.get("status"),
                    remaining=channel.get("remaining_known"),
                    available=channel.get("available_models"),
                    total=channel.get("total_models"),
                    usage=channel.get("usage_api_status"),
                )
            )
    lines.extend(["", "noise_policy: state-transition-only; no periodic heartbeat spam"])
    return "\n".join(lines) + "\n"


ENGAGEMENT_EMOJI: dict[str, str] = {
    "working_with_local_output": "🟢",
    "working_silent_before_soft_deadline": "🟡",
    "result_ready_for_harvest": "🔵",
    "result_pending_harvest_process_alive": "🔵",
    "completed_awaiting_integration": "✅",
    "over_soft_deadline_no_output": "🟠",
    "over_soft_deadline_with_output": "🟠",
    "mixed_pending_harvest_and_active": "🟡",
    "needs_attention": "🔴",
    "blocked_or_failed_after_presence": "⛔",
    "not_currently_participating": "⚪",
}

ENGAGEMENT_CHINESE: dict[str, str] = {
    "working_with_local_output": "执行中",
    "working_silent_before_soft_deadline": "等待输出",
    "result_ready_for_harvest": "待收集",
    "result_pending_harvest_process_alive": "待收集",
    "completed_awaiting_integration": "已完成",
    "over_soft_deadline_no_output": "超时未出",
    "over_soft_deadline_with_output": "超时(有输出)",
    "mixed_pending_harvest_and_active": "混合状态",
    "needs_attention": "需要关注",
    "blocked_or_failed_after_presence": "阻塞/失败",
    "not_currently_participating": "空闲",
}

# At-a-glance work-status badge: answers "这个 agent 在工作吗？" directly.
WORK_BADGE: dict[str, str] = {
    "working": "🟢工作中",
    "working_with_attention": "🟡执行+异常",
    "needs_attention": "🔴异常",
    "pending_harvest": "🔵待收",
    "completed_waiting": "✅已完成",
    "idle": "⚪空闲",
}


def render_compact_status(status: dict[str, Any]) -> str:
    """Render the same tri-agent surface used by the pinned status card."""
    fixed_card = status.get("fixed_status_card") if isinstance(status.get("fixed_status_card"), dict) else {}
    if not fixed_card.get("text"):
        fixed_card = fixed_status_card(
            status,
            room_id=str(status.get("room_id") or DEFAULT_ROOM_ID),
            chat_id=str(status.get("chat_id") or "") or None,
        )
    return str(fixed_card.get("text") or "")


def status_watch_signature(status: dict[str, Any]) -> dict[str, Any]:
    """Stable signature for local watch transitions; excludes timestamps."""
    per_agent = status.get("per_agent_engagement") if isinstance(status.get("per_agent_engagement"), dict) else {}
    agents: dict[str, dict[str, Any]] = {}
    for agent_id, data in sorted(per_agent.items()):
        if not isinstance(data, dict):
            continue
        agents[str(agent_id)] = {
            "work_status": one_glance_work_status(data),
            "engagement_state": data.get("engagement_state"),
            "working_runner_count": int(data.get("working_runner_count") or 0),
            "active_runner_count": int(data.get("active_runner_count") or 0),
            "pending_harvest_count": int(data.get("pending_harvest_count") or 0),
            "completed_presence_count": int(data.get("completed_presence_count") or 0),
            "needs_attention_count": int(data.get("needs_attention_count") or 0),
            "task_ids": sorted(str(value) for value in (data.get("active_task_ids") or []) if str(value)),
        }
    collaboration = status.get("collaboration_overview") if isinstance(status.get("collaboration_overview"), dict) else {}
    attention_task_ids = (
        collaboration.get("needs_collaboration_attention_task_ids")
        or collaboration.get("needs_attention_task_ids")
        or []
    )
    provider = status.get("provider_health") if isinstance(status.get("provider_health"), dict) else {}
    discussion_progress_raw = collaboration.get("per_agent_discussion_progress") if isinstance(collaboration.get("per_agent_discussion_progress"), dict) else {}
    discussion_progress: dict[str, dict[str, Any]] = {}
    for agent_id, progress in sorted(discussion_progress_raw.items()):
        if not isinstance(progress, dict):
            continue
        states = progress.get("liveness_vs_progress_counts") if isinstance(progress.get("liveness_vs_progress_counts"), dict) else {}
        discussion_progress[str(agent_id)] = {
            "points_produced": int(progress.get("points_produced") or 0),
            "points_with_peer_uptake": int(progress.get("points_with_peer_uptake") or 0),
            "peer_points_uptaken": int(progress.get("peer_points_uptaken") or 0),
            "peer_challenges": int(progress.get("peer_challenges") or 0),
            "peer_integrations": int(progress.get("peer_integrations") or 0),
            "summaries_produced": int(progress.get("summaries_produced") or 0),
            "summaries_integrated": int(progress.get("summaries_integrated") or 0),
            "liveness_vs_progress_counts": {
                str(state): int(count or 0)
                for state, count in sorted(states.items())
            },
        }
    next_actions_raw = collaboration.get("per_agent_next_actions") if isinstance(collaboration.get("per_agent_next_actions"), dict) else {}
    per_agent_next_actions: dict[str, Any] = {}
    for agent_id, queue in sorted(next_actions_raw.items()):
        if not isinstance(queue, dict):
            continue
        per_agent_next_actions[str(agent_id)] = {
            "action_count": int(queue.get("action_count") or 0),
            "hidden_count": int(queue.get("hidden_count") or 0),
            "actions": [
                ":".join(
                    str(value or "")
                    for value in (
                        action.get("type"),
                        action.get("task_id"),
                        action.get("role"),
                        action.get("primary_agent_id"),
                        action.get("detail"),
                        action.get("next_action"),
                    )
                )
                for action in (queue.get("actions") or [])
                if isinstance(action, dict)
            ],
        }
    collaboration_health = {
        "material_point_count": int(collaboration.get("material_point_count") or 0),
        "peer_uptake_count": int(collaboration.get("peer_uptake_count") or 0),
        "peer_challenge_count": int(collaboration.get("peer_challenge_count") or 0),
        "summary_point_count": int(collaboration.get("summary_point_count") or 0),
        "summary_peer_uptake_count": int(collaboration.get("summary_peer_uptake_count") or 0),
        "integrated_summary_count": int(collaboration.get("integrated_summary_count") or 0),
        "summary_needs_integration_count": int(collaboration.get("summary_needs_integration_count") or 0),
        "closure_summary_needed_count": int(collaboration.get("closure_summary_needed_count") or 0),
        "integration_signal_count": int(collaboration.get("integration_signal_count") or 0),
        "blocker_count": int(collaboration.get("blocker_count") or 0),
        "open_blocker_count": int(collaboration.get("open_blocker_count") or 0),
        "blocker_task_count": int(collaboration.get("blocker_task_count") or 0),
        "blocker_task_ids": sorted(str(value) for value in (collaboration.get("blocker_task_ids") or []) if str(value)),
        "tasks_missing_peer_uptake_count": int(collaboration.get("tasks_missing_peer_uptake_count") or 0),
        "tasks_missing_summary_uptake_count": int(collaboration.get("tasks_missing_summary_uptake_count") or 0),
        "tasks_needing_summary_integration_count": int(collaboration.get("tasks_needing_summary_integration_count") or 0),
        "tasks_needing_closure_summary_count": int(collaboration.get("tasks_needing_closure_summary_count") or 0),
        "tasks_needing_closure_summary_ids": sorted(str(value) for value in (collaboration.get("tasks_needing_closure_summary_ids") or []) if str(value)),
        "closure_summary_candidate_agents_by_task": collaboration.get("closure_summary_candidate_agents_by_task") or {},
        "peer_reviewed_task_count": int(collaboration.get("peer_reviewed_task_count") or 0),
        "degraded_quorum_task_count": int(collaboration.get("degraded_quorum_task_count") or 0),
        "declared_scope_work_item_count": int(collaboration.get("declared_scope_work_item_count") or 0),
        "declared_scope_path_count": int(collaboration.get("declared_scope_path_count") or 0),
        "declared_scope_unique_path_count": int(collaboration.get("declared_scope_unique_path_count") or 0),
        "scope_conflict_count": int(collaboration.get("scope_conflict_count") or 0),
        "scope_conflict_task_count": int(collaboration.get("scope_conflict_task_count") or 0),
        "scope_conflict_task_ids": sorted(str(value) for value in (collaboration.get("scope_conflict_task_ids") or []) if str(value)),
        "material_stall_task_count": int(collaboration.get("material_stall_task_count") or 0),
        "material_stall_agents_by_task": collaboration.get("material_stall_agents_by_task") or {},
        "active_material_silence_task_count": int(collaboration.get("active_material_silence_task_count") or 0),
        "active_material_silent_agents_by_task": collaboration.get("active_material_silent_agents_by_task") or {},
        "per_agent_blockers": collaboration.get("per_agent_blockers") or {},
        "per_agent_declared_scope_paths": collaboration.get("per_agent_declared_scope_paths") or {},
        "per_agent_material_stalls": collaboration.get("per_agent_material_stalls") or {},
        "per_agent_discussion_progress": discussion_progress,
        "action_item_count": int(collaboration.get("action_item_count") or 0),
        "per_agent_action_items": collaboration.get("per_agent_action_items") or {},
        "per_agent_next_actions": per_agent_next_actions,
        "action_items": [
            ":".join(
                str(value or "")
                for value in (
                    item.get("type"),
                    item.get("task_id"),
                    item.get("agent_id"),
                    action_item_signature_detail(item),
                )
            )
            for item in prioritized_action_items(
                collaboration.get("action_items"),
                limit=ACTION_ITEM_SURFACE_LIMIT,
            )
        ],
        "efficiency_score_avg": collaboration.get("efficiency_score_avg"),
        "per_task_efficiency_breakdown": collaboration.get("per_task_efficiency_breakdown") or {},
    }
    provider_agents: dict[str, dict[str, Any]] = {}
    for agent_id, data in sorted((provider.get("per_agent") or {}).items()):
        if not isinstance(data, dict):
            continue
        provider_agents[str(agent_id)] = {
            "availability": data.get("availability"),
            "active_cooldown_count": int(data.get("active_cooldown_count") or 0),
            "available_model_count": int(data.get("available_model_count") or 0),
            "stale_depleted_model_count": int(data.get("stale_depleted_model_count") or 0),
        }
    channels: dict[str, dict[str, Any]] = {}
    for channel_id, channel in sorted((provider.get("token_channels") or {}).items()):
        if not isinstance(channel, dict):
            continue
        channels[str(channel_id)] = {
            "status": channel.get("status"),
            "remaining_known": channel.get("remaining_known"),
            "available_models": channel.get("available_models"),
            "total_models": channel.get("total_models"),
            "usage_api_status": channel.get("usage_api_status"),
        }
    return {
        "agents": agents,
        "visibility_state": status.get("visibility_state"),
        "active_task_ids": sorted(str(value) for value in (status.get("active_task_ids") or []) if str(value)),
        "needs_attention_task_ids": sorted(str(value) for value in attention_task_ids if str(value)),
        "runner_attention_task_count": int(collaboration.get("runner_attention_task_count") or 0),
        "collaboration_health": collaboration_health,
        "provider_health": {
            "signal_fresh": provider.get("signal_fresh"),
            "degraded_agents": sorted(str(value) for value in (provider.get("degraded_agents") or []) if str(value)),
            "unknown_agents": sorted(str(value) for value in (provider.get("unknown_agents") or []) if str(value)),
            "remaining_unknown_channels": sorted(str(value) for value in (provider.get("remaining_unknown_channels") or []) if str(value)),
            "per_agent": provider_agents,
            "token_channels": channels,
        },
    }


def render_watch_status(status: dict[str, Any], compact: str, *, changed: bool) -> str:
    generated = str(status.get("generated_at") or now_iso())
    lines = [
        compact,
        "",
        f"last_refresh: {generated}",
        f"state_changed: {'yes' if changed else 'no'}",
        "source: collaboration-status/latest.md",
        "transitions: collaboration-status/transitions.jsonl",
    ]
    return "\n".join(lines) + "\n"


def write_watch_artifacts(status: dict[str, Any], compact: str) -> dict[str, Any]:
    """Write a local, non-Telegram status surface for continuous human watch."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATUS_DIR / "watch-state.json"
    transitions_path = STATUS_DIR / "transitions.jsonl"
    watch_path = STATUS_DIR / "watch.txt"
    signature = status_watch_signature(status)
    previous = read_json(state_path, {}) or {}
    previous_signature = previous.get("signature") if isinstance(previous, dict) else None
    changed = previous_signature != signature
    transition_count = int(previous.get("transition_count") or 0) if isinstance(previous, dict) else 0
    if changed:
        transition_count += 1
        with transitions_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "changed_at": status.get("generated_at") or now_iso(),
                "transition_count": transition_count,
                "signature": signature,
                "compact_status": compact,
            }, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(state_path, {
        "schema": "openclaw.agent_room.collaboration_status_watch_state.v0",
        "updated_at": status.get("generated_at") or now_iso(),
        "changed": changed,
        "transition_count": transition_count,
        "signature": signature,
        "watch_path": str(watch_path),
        "transitions_path": str(transitions_path),
    })
    watch_path.write_text(render_watch_status(status, compact, changed=changed), encoding="utf-8")
    return {
        "watch_path": str(watch_path),
        "state_path": str(state_path),
        "transitions_path": str(transitions_path),
        "changed": changed,
        "transition_count": transition_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render room-scoped Agent Room status surfaces.")
    parser.add_argument("--room-id", default=DEFAULT_ROOM_ID)
    parser.add_argument("--chat-id", default="")
    parser.add_argument(
        "--include-background",
        action="store_true",
        help="Include standing/background tasks in the primary room status surface.",
    )
    args = parser.parse_args()
    status = build_status(room_id=args.room_id, include_background=args.include_background)
    if args.chat_id:
        status["chat_id"] = args.chat_id
    status["fixed_status_card"] = fixed_status_card(status, room_id=args.room_id, chat_id=args.chat_id or None)
    out = STATUS_DIR / "latest.json"
    write_json(out, status)
    task_status_paths = write_task_status_snapshots(status)
    (STATUS_DIR / "latest.md").write_text(render_markdown_status(status), encoding="utf-8")
    compact = render_compact_status(status)
    (STATUS_DIR / "compact.txt").write_text(compact + "\n", encoding="utf-8")
    fixed_card = status.get("fixed_status_card") if isinstance(status.get("fixed_status_card"), dict) else {}
    if fixed_card.get("text"):
        (STATUS_DIR / "fixed-card-preview.txt").write_text(str(fixed_card.get("text")) + "\n", encoding="utf-8")
        write_json(STATUS_DIR / "fixed-card-preview.json", fixed_card)
    watch = write_watch_artifacts(status, compact)
    today = datetime.now().strftime("%Y%m%d")
    write_json(STATUS_DIR / f"latest-{today}.json", status)
    (STATUS_DIR / f"latest-{today}.md").write_text(render_markdown_status(status), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out), "visibility_state": status.get("visibility_state"), "active_runner_count": status.get("active_runner_count"), "active_task_ids": status.get("active_task_ids"), "task_status_paths": task_status_paths, "compact_status": compact, "watch": watch, "tokens_printed": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
