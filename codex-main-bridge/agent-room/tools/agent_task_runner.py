#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(WORKSPACE / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
COMMENT_ROOT = ROOT / "agent-comments"
RUN_ROOT = ROOM / "runner-runs"
COLLAB_LEDGER_DIR = ROOM / "collaboration-ledgers"
AGENT_PRESENCE_DIR = ROOM / "agent-presence"
CODEX_CMD = os.environ.get("AGENT_ROOM_CODEX_CMD", str(Path.home() / ".local" / "bin" / "codex"))
CODEX_MODEL_STATE = ROOM / "codex_model_state.json"
CLAUDE_MODEL_STATE = ROOM / "claude_model_state.json"
CODEX_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"
PROJECTION_EVENTS_FILE = ROOM / "projection_events.jsonl"
OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = ROOM / "projections" / "openclaw-main" / "projection_events.jsonl"
DEFAULT_CODEX_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2", "gpt-5.4-mini"]
CODEX_ARK_FALLBACK_ENABLED = str(os.environ.get("AGENT_ROOM_CODEX_ARK_FALLBACK_ENABLED", "1")).strip().lower() not in (
    "0",
    "false",
    "off",
    "no",
)
CODEX_NATIVE_JSON_EVENTS_ENABLED = str(os.environ.get("AGENT_ROOM_CODEX_NATIVE_JSON_EVENTS", "1")).strip().lower() not in (
    "0",
    "false",
    "off",
    "no",
)
CODEX_ARK_FALLBACK_SCOPE = str(os.environ.get("AGENT_ROOM_CODEX_ARK_FALLBACK_SCOPE", "collaboration_only")).strip().lower()
CLAUDE_CMD = str(Path.home() / ".local" / "bin" / "claude")
CODING_TASK_ENTRY = WORKSPACE / "tools" / "coding_task_entry.py"
CLAUDE_ARK_RUNNER = WORKSPACE / "tools" / "claude_code_ark_runner.py"
COLLAB_LEDGER_TOOL = Path(__file__).with_name("collaboration_ledger.py")
CLAUDE_MODEL_POLICY_FILE = ROOM / "config" / "claude-code-model-policy.json"
AGENT_PAUSE_FILE = ROOM / "config" / "paused-agents.json"
CLAUDE_KARPATHY_PLUGIN_DIR = Path(os.environ.get(
    "AGENT_ROOM_CLAUDE_KARPATHY_PLUGIN_DIR",
    str(Path.home() / ".claude" / "plugins" / "cache" / "karpathy-skills" / "andrej-karpathy-skills" / "1.0.0"),
)).expanduser()
CLAUDE_KARPATHY_SKILL = "/karpathy-guidelines"
CLAUDE_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
CLAUDE_MODEL_POLICY = "claude_code_agent_model_selector_latest_glm_kimi_minimax_no_keywords_doubao_family_fully_disabled"
CLAUDE_ARK_EFFORT_POLICY = "force_max_reasoning_for_all_claude_code_ark_calls"
CLAUDE_ARK_EFFORT_TIER_POLICY = "tiered_by_lane_and_collab_role"
# Lanes and roles that can safely use reduced effort for faster room response.
# Production/code-editing lanes and lead roles keep max; lightweight turns use
# high so the agent can keep up with real-time room conversation pace.
CLAUDE_EFFORT_TIER_OVERRIDES: dict[str, str] = {
    "peer_collaboration_followup": "high",
    "agent-room-bot-mention": "high",
}
CLAUDE_EFFORT_ROLE_OVERRIDES: dict[str, str] = {
    "co_producer": "high",
}
CLAUDE_EFFORT_MIN_FOR_SOURCE_EDIT = "max"
CLAUDE_MODEL_ROUTING_ENABLED = str(os.environ.get("AGENT_ROOM_CLAUDE_MODEL_ROUTING_ENABLED", "1")).strip().lower() not in ("0", "false", "off", "no")
INTERNAL_AGENT_ROOM_TRANSPORTS = {"agent-room-collab-followup", "agent-room-bot-mention"}
# --- Collaboration Quota Notice/Silence ---
# When one bot/model quota is depleted, the user should see one concise notice.
# Subsequent tasks for the same bot/model stay local until the cooldown expires
# or a successful call marks that model available again. Other models under the
# same bot keep their own one-notice lifecycle.
AGENT_QUOTA_STATE_FILE = ROOM / "agent_quota_state.json"
AGENT_ROOM_STATUS_FILE = ROOM / "agent_room_status.json"
MODEL_QUOTA_SIGNAL_FILE = ROOM / "model_quota_signal.json"
AGENT_QUOTA_DEPLETED_REASONS = {"usage_limit", "rate_limit", "model_overloaded", "model_unavailable"}
RETRYABLE_PROVIDER_REASONS = AGENT_QUOTA_DEPLETED_REASONS | {"quota_depleted", "quota_ledger_zero_remaining", "cooldown"}
AGENT_QUOTA_SILENCE_COOLDOWN_MINUTES = int(os.environ.get("AGENT_ROOM_QUOTA_SILENCE_COOLDOWN_MINUTES", "30"))
QUOTA_LEDGER_FILE = ROOM / "quota_ledger.json"
UNKNOWN_QUOTA_MODEL = "unknown-model"
CLAUDE_RETIRED_MODEL_REPLACEMENTS = {
    "deepseek-v3.2": "deepseek-v4-pro",
    "glm-4.7": "glm-5.1",
    "kimi-k2.5": "kimi-k2.6",
    "minimax-m2.5": "minimax-m2.7",
}
ENV_FILES = [
    Path.home() / ".openclaw" / "secrets" / "agent-room-deepseek.env",
    Path.home() / ".openclaw" / "secrets" / "agent-room-openai.env",
    Path.home() / ".openclaw" / "secrets" / "volcengine.env",
    Path.home() / ".openclaw" / ".env",
]
EXTERNAL_DEEPSEEK_ENV_FILE = Path.home() / ".openclaw" / "secrets" / "agent-room-deepseek.env"
DIRECT_PROVIDER_WORKER = WORKSPACE / "scripts" / "direct_provider_worker.py"
EXTERNAL_DEEPSEEK_BACKEND = "external-deepseek-openai-compatible-worker"
EXTERNAL_DEEPSEEK_CAPABILITY = "text_review_or_blocker_no_tools_no_file_edits"
EXTERNAL_DEEPSEEK_FAST_MODEL = "deepseek-v4-flash"
EXTERNAL_DEEPSEEK_QUALITY_MODEL = "deepseek-v4-pro"
EXTERNAL_DEEPSEEK_MODEL_ENV = "AGENT_ROOM_EXTERNAL_DEEPSEEK_MODEL"

DEFAULT_CLAUDE_MODEL_POLICY = {
    "default_model": "minimax-m2.7",
    "fallback_model": "glm-5.1",
    "latest_family_models": {
        "deepseek": "deepseek-v4-pro",
        "deepseek-flash": "deepseek-v4-flash",
        "deepseek-reasoner": "deepseek-reasoner",
        "glm": "glm-5.1",
        "kimi": "kimi-k2.6",
        "minimax": "minimax-m2.7",
    },
    "retired_family_replacements": {
        "deepseek-v3.2": "deepseek-v4-pro",
        "glm-4.7": "glm-5.1",
        "kimi-k2.5": "kimi-k2.6",
        "minimax-m2.5": "minimax-m2.7",
    },
    "doubao_family": {
        "allowed_tails": [],
        "allowed_route_keys": [],
    },
    "routes": {
        "plain_chat": {"candidates": ["minimax-m2.7", "deepseek-v4-flash", "deepseek-v4-pro", "glm-5.1", "kimi-k2.6"], "reason": "ordinary Agent Room Claude Code discussion; avoid Kimi overuse; V4-flash for speed, V4-pro as deeper backup; DeepSeek v3.2 retired after 2026-05-25 V4 smoke"},
        "workspace_write": {"candidates": ["glm-5.1", "deepseek-v4-pro", "deepseek-v4-flash", "minimax-m2.7", "kimi-k2.6"], "reason": "source-edit/runtime work; prefer GLM then DeepSeek V4-pro/V4-flash before MiniMax/Kimi; DeepSeek v3.2 retired after live V4 smoke"},
        "peer_review": {"candidates": ["minimax-m2.7", "deepseek-v4-pro", "deepseek-v4-flash", "glm-5.1", "kimi-k2.6"], "reason": "peer review / challenge / second opinion; prefer MiniMax then DeepSeek V4; DeepSeek v3.2 retired after live V4 smoke"},
        "long_context": {"candidates": ["kimi-k2.6", "deepseek-v4-pro", "glm-5.1", "minimax-m2.7"], "reason": "long-context synthesis is the main place where Kimi remains first choice; DeepSeek V4-pro as second; DeepSeek v3.2 retired after live V4 smoke"},
        "deep_reasoning": {"candidates": ["deepseek-reasoner", "deepseek-v4-pro", "glm-5.1", "minimax-m2.7", "kimi-k2.6"], "reason": "explicit deep reasoning / complex logic; DeepSeek-reasoner first, then DeepSeek V4-pro; DeepSeek v3.2 retired after live V4 smoke"},
        "low_stakes_formatting": {"candidates": ["deepseek-v4-flash", "minimax-m2.7", "glm-5.1"], "reason": "least-important formatting/low-risk cleanup only; V4-flash first for speed and cost; Doubao removed 2026-05-22; DeepSeek v3.2 retired after live V4 smoke"},
    },
}

def agent_pause_status(agent_id: str) -> dict[str, Any] | None:
    """Return a configured temporary pause record for an agent, if any.

    This is a runtime safety valve.  It intentionally lives in config rather
    than task manifests so we can stop launching a risky backend immediately
    while preserving queued task provenance and allowing other agents to keep
    working.
    """
    if not AGENT_PAUSE_FILE.exists():
        return None
    try:
        data = json.loads(AGENT_PAUSE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("enabled") is False:
        return None
    agents = data.get("agents")
    if isinstance(agents, dict):
        raw = agents.get(agent_id)
    elif isinstance(agents, list):
        raw = agent_id if agent_id in [str(item) for item in agents] else None
    else:
        raw = None
    if raw is None or raw is False:
        return None
    if isinstance(raw, dict):
        if raw.get("paused") is False or raw.get("enabled") is False:
            return None
        return dict(raw)
    return {"paused": True, "reason": str(raw)}


def claude_model_routing_enabled() -> bool:
    return CLAUDE_MODEL_ROUTING_ENABLED


def load_claude_model_policy() -> dict[str, Any]:
    if CLAUDE_MODEL_POLICY_FILE.exists():
        try:
            value = json.loads(CLAUDE_MODEL_POLICY_FILE.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                merged = dict(DEFAULT_CLAUDE_MODEL_POLICY)
                merged.update({k: v for k, v in value.items() if k != "routes"})
                routes = dict(DEFAULT_CLAUDE_MODEL_POLICY.get("routes") or {})
                if isinstance(value.get("routes"), dict):
                    routes.update(value["routes"])
                merged["routes"] = routes
                return merged
        except Exception:
            pass
    return dict(DEFAULT_CLAUDE_MODEL_POLICY)


def claude_policy_default_model() -> str:
    return str(os.environ.get("AGENT_ROOM_CLAUDE_ARK_MODEL") or load_claude_model_policy().get("default_model") or "minimax-m2.7")


def claude_default_model() -> str:
    return claude_policy_default_model()


CLAUDE_ARK_DEFAULT_MODEL = claude_policy_default_model()

def native_permission_mode_key_variants(source_edit: bool) -> list[str]:
    """Native Claude permission-mode keys for task model/effort overrides."""
    return ["acceptEdits" if source_edit else "dontAsk"]



def claude_model_policy_tail(model: str) -> str:
    normalized = str(model or "").strip().lower()
    return normalized.rsplit("/", 1)[-1]


def claude_model_is_doubao(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return "doubao" in normalized or "豆包" in normalized


def claude_retired_model_replacement(model: str) -> str | None:
    tail = claude_model_policy_tail(model)
    replacements = dict(CLAUDE_RETIRED_MODEL_REPLACEMENTS)
    try:
        configured = load_claude_model_policy().get("retired_family_replacements")
        if isinstance(configured, dict):
            for raw_model, raw_replacement in configured.items():
                key = claude_model_policy_tail(str(raw_model))
                replacement = str(raw_replacement or "").strip()
                if key and replacement:
                    replacements[key] = replacement
    except Exception:
        pass
    return replacements.get(tail)


def claude_policy_allowed_doubao_tails() -> set[str]:
    doubao = load_claude_model_policy().get("doubao_family")
    if isinstance(doubao, dict) and isinstance(doubao.get("allowed_tails"), list):
        return {str(x).strip().lower() for x in doubao["allowed_tails"] if str(x).strip()}
    return set()


def claude_policy_doubao_route_keys() -> set[str]:
    doubao = load_claude_model_policy().get("doubao_family")
    if isinstance(doubao, dict) and isinstance(doubao.get("allowed_route_keys"), list):
        return {str(x).strip() for x in doubao["allowed_route_keys"] if str(x).strip()}
    return {"low_stakes_formatting"}


def claude_model_allowed_by_policy(model: str, route_key: str | None = None) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    if claude_retired_model_replacement(normalized):
        return False
    if not claude_model_is_doubao(normalized):
        return True
    if claude_model_policy_tail(normalized) not in claude_policy_allowed_doubao_tails():
        return False
    return str(route_key or "") in claude_policy_doubao_route_keys()


def claude_policy_fallback_model(route_key: str | None = None, blocked_model: str | None = None) -> str:
    policy = load_claude_model_policy()
    fallback = str(policy.get("fallback_model") or policy.get("default_model") or "minimax-m2.7")
    default = str(policy.get("default_model") or "minimax-m2.7")
    candidates = [blocked_model or "", fallback, default, "minimax-m2.7"]
    for candidate in candidates:
        replacement = claude_retired_model_replacement(candidate)
        resolved = replacement or str(candidate or "").strip()
        if resolved and claude_model_allowed_by_policy(resolved, route_key):
            return resolved
    return "minimax-m2.7"


def enforce_claude_model_policy(profile: dict[str, Any]) -> dict[str, Any]:
    """Hard gate Claude Code model use before any Ark runner invocation.

    This is a model-safety/catalog gate, not a behavior profile. Doubao-family
    models are blocked except explicitly allowed tails on explicitly low-stakes
    Claude Code routes. Alex's 2026-05-21 correction: even Doubao Pro belongs
    only in the least-important work, not in normal discussion/review/edit runs.
    """
    model = str(profile.get("model") or "").strip()
    route_key = str(profile.get("model_route_key") or "")
    if claude_model_allowed_by_policy(model, route_key):
        patched = dict(profile)
        patched["model_policy"] = CLAUDE_MODEL_POLICY
        return patched
    patched = dict(profile)
    fallback = claude_policy_fallback_model(route_key, model)
    patched["model"] = fallback
    patched["model_policy"] = CLAUDE_MODEL_POLICY
    patched["model_policy_blocked_model"] = model
    patched["model_policy_fallback_model"] = fallback
    replacement = claude_retired_model_replacement(model)
    if replacement:
        patched["model_policy_replacement_reason"] = "retired_glm_kimi_minimax_family_model"
    source = str(profile.get("model_override_source") or profile.get("model_route_key") or "profile_default")
    patched["model_override_source"] = f"policy.blocked_model_for_route.{source}"
    return patched


def claude_model_override(task: dict[str, Any], source_edit: bool) -> tuple[str | None, str | None]:
    """Return an optional per-task Claude Code model override.

    Global env vars remain the default path. These task fields are intentionally
    narrow and artifact-visible so explicit A/B trials can opt in without changing
    existing task-flow defaults or unrelated business workflows.
    Supported forms:
      - claude_code_model: "kimi-k2.6"                    # all tasks
      - claude_code_models: {"acceptEdits": "..."}       # when Claude runs in acceptEdits
      - model_overrides: {"claude-code": {"default": "..."}}
    """
    permission_mode_keys = native_permission_mode_key_variants(source_edit)
    direct = str(task.get("claude_code_model") or task.get("agent_room_claude_model") or "").strip()
    if direct:
        return direct, "task.claude_code_model"
    for field in ("claude_code_models", "agent_room_claude_models"):
        mapping = task.get(field)
        if isinstance(mapping, dict):
            for key in [*permission_mode_keys, "default"]:
                value = str(mapping.get(key) or "").strip()
                if value:
                    return value, f"task.{field}.{key}"
    overrides = task.get("model_overrides")
    if isinstance(overrides, dict):
        for agent_key in ("claude-code", "claude_code", "claude"):
            mapping = overrides.get(agent_key)
            if isinstance(mapping, dict):
                for key in [*permission_mode_keys, "default"]:
                    value = str(mapping.get(key) or "").strip()
                    if value:
                        return value, f"task.model_overrides.{agent_key}.{key}"
            elif isinstance(mapping, str) and mapping.strip():
                return mapping.strip(), f"task.model_overrides.{agent_key}"
    return None, None




def normalize_claude_effort(value: Any) -> str | None:
    effort = str(value or "").strip().lower()
    aliases = {
        "extra_high": "xhigh",
        "extra-high": "xhigh",
        "x-high": "xhigh",
        "maximum": "max",
    }
    effort = aliases.get(effort, effort)
    return effort if effort in CLAUDE_EFFORT_LEVELS else None


def claude_effort_override(task: dict[str, Any], source_edit: bool) -> tuple[str | None, str | None]:
    """Return an optional per-task Claude Code effort override.

    The final Agent Room policy currently forces every Claude Code Ark call
    back to max after explicit task overrides are recorded.
    Supported forms mirror model overrides:
      - claude_code_effort: "high"
      - claude_code_efforts: {"acceptEdits": "high"}
      - effort_overrides: {"claude-code": {"dontAsk": "medium"}}
    """
    permission_mode_keys = native_permission_mode_key_variants(source_edit)
    direct_fields = (
        "claude_code_effort",
        "agent_room_claude_effort",
        "claude_reasoning_effort",
        "reasoning_effort",
    )
    for field in direct_fields:
        raw = task.get(field)
        effort = normalize_claude_effort(raw)
        if effort:
            return effort, f"task.{field}"
    for field in (
        "claude_code_efforts",
        "agent_room_claude_efforts",
        "claude_reasoning_efforts",
        "reasoning_efforts",
    ):
        mapping = task.get(field)
        if isinstance(mapping, dict):
            for key in [*permission_mode_keys, "default"]:
                effort = normalize_claude_effort(mapping.get(key))
                if effort:
                    return effort, f"task.{field}.{key}"
    for field in ("effort_overrides", "reasoning_effort_overrides"):
        overrides = task.get(field)
        if not isinstance(overrides, dict):
            continue
        for agent_key in ("claude-code", "claude_code", "claude"):
            mapping = overrides.get(agent_key)
            if isinstance(mapping, dict):
                for key in [*permission_mode_keys, "default"]:
                    effort = normalize_claude_effort(mapping.get(key))
                    if effort:
                        return effort, f"task.{field}.{agent_key}.{key}"
            else:
                effort = normalize_claude_effort(mapping)
                if effort:
                    return effort, f"task.{field}.{agent_key}"
    return None, None


def with_claude_model_override(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    override, source = claude_model_override(task, bool(profile.get("source_edit")))
    if not override:
        return enforce_claude_model_policy(profile)
    patched = dict(profile)
    patched["model"] = override
    patched["model_override_source"] = source
    return enforce_claude_model_policy(patched)



def claude_model_route_table() -> dict[str, dict[str, Any]]:
    """Agent Room-local policy catalog for Claude Code model selection.

    This selector is scoped only to choosing the Ark model passed to Claude Code.
    It intentionally uses a candidate list rather than a single hardcoded model,
    so GLM/MiniMax/Kimi/other Ark models can be evaluated and rotated by policy.
    """
    routes = load_claude_model_policy().get("routes")
    return routes if isinstance(routes, dict) else dict(DEFAULT_CLAUDE_MODEL_POLICY["routes"])


def claude_configured_route_key(task: dict[str, Any]) -> str | None:
    for key in ("claude_code_route_key", "model_route_key", "route_key"):
        value = str(task.get(key) or "").strip()
        if value and value in claude_model_route_table():
            return value
    return None


def claude_model_route_key(task: dict[str, Any], profile: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    transport = str(source.get("transport") or "")
    lane = str(task.get("lane") or "")
    requested_by = str(task.get("requested_by") or "")
    explicit_route_key = claude_configured_route_key(task)
    structural_peer_followup = (
        transport == "agent-room-collab-followup"
        or requested_by == "agent-room-collab-followup"
        or lane == "peer_collaboration_followup"
        or bool(task.get("collab_parent_agent_id"))
    )
    signals = {
        "source_edit": bool(profile.get("source_edit")),
        "transport": transport,
        "lane": lane,
        "requested_by": requested_by,
        "has_collab_parent_agent_id": bool(task.get("collab_parent_agent_id")),
        "explicit_route_key": explicit_route_key,
    }
    if explicit_route_key:
        return explicit_route_key, signals
    if structural_peer_followup:
        return "peer_review", signals
    if bool(profile.get("source_edit")):
        return "workspace_write", signals
    return "plain_chat", signals


def claude_candidate_models_for_route(route_key: str) -> list[str]:
    entry = claude_model_route_table().get(route_key) or claude_model_route_table().get("plain_chat") or {}
    raw = entry.get("candidates") if isinstance(entry, dict) else []
    if isinstance(raw, list):
        candidates = [str(x).strip() for x in raw if str(x).strip()]
    else:
        candidates = []
    if not candidates:
        candidates = [claude_policy_default_model()]
    return candidates


def claude_select_model_for_route(route_key: str) -> tuple[str, list[str], list[dict[str, Any]]]:
    candidates = claude_candidate_models_for_route(route_key)
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        if not claude_model_allowed_by_policy(candidate, route_key):
            skipped.append({"model": candidate, "reason": "blocked_by_model_policy_for_route"})
            continue
        depleted, reason = agent_quota_is_depleted("claude-code", candidate)
        if depleted:
            skipped.append({"model": candidate, "reason": "quota_depleted", "quota_reason": reason})
            continue
        return candidate, candidates, skipped
    fallback = claude_policy_fallback_model(route_key)
    return fallback, candidates, skipped


def claude_route_table_model(task: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Resolve the catalog-selected model for a Claude Code Agent Room turn."""
    route_key, signals = claude_model_route_key(task, profile)
    selected, candidates, skipped = claude_select_model_for_route(route_key)
    signals = dict(signals)
    signals["candidate_models"] = candidates
    signals["skipped_candidates"] = skipped
    return selected, route_key, signals


def claude_model_routing_advisory(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Return a Claude Code model routing decision record.

    When enabled, the catalog-selected model is the effective default unless an
    explicit task override or model-policy fallback wins. The record is scoped
    only to Claude Code agent model selection.
    """
    current_model = str(profile.get("model") or CLAUDE_ARK_DEFAULT_MODEL)
    explicit_source = profile.get("model_override_source")
    route_key, route_signals = claude_model_route_key(task, profile)
    if explicit_source:
        return {
            "schema": "openclaw.agent_room.claude_code_model_routing_advisory.v0",
            "scope": "claude_code_agent_model_selection_only",
            "mode": "enabled" if CLAUDE_MODEL_ROUTING_ENABLED else "dry_run",
            "execution_changed": False,
            "decision": "explicit_task_override_wins",
            "route_key": route_key,
            "selected_model": current_model,
            "selected_model_source": explicit_source,
            "selected_model_allowed_by_policy": claude_model_allowed_by_policy(current_model, route_key),
        }
    candidate_model, route_key, signals = claude_route_table_model(task, profile)
    entry = claude_model_route_table().get(route_key) or claude_model_route_table().get("plain_chat") or {}
    resolved_model = candidate_model if claude_model_allowed_by_policy(candidate_model, route_key) else claude_policy_fallback_model(route_key)
    if CLAUDE_MODEL_ROUTING_ENABLED:
        return {
            "schema": "openclaw.agent_room.claude_code_model_routing_advisory.v0",
            "scope": "claude_code_agent_model_selection_only",
            "mode": "enabled",
            "execution_changed": resolved_model != current_model,
            "decision": "catalog_selected",
            "route_key": route_key,
            "reason": entry.get("reason") if isinstance(entry, dict) else None,
            "candidate_model": candidate_model,
            "candidate_models": signals.get("candidate_models"),
            "skipped_candidates": signals.get("skipped_candidates"),
            "resolved_model": resolved_model,
            "candidate_allowed_by_policy": claude_model_allowed_by_policy(candidate_model, route_key),
            "selected_model": resolved_model,
            "selected_model_source": f"model_policy_catalog.{route_key}",
            "signals": signals,
        }
    return {
        "schema": "openclaw.agent_room.claude_code_model_routing_advisory.v0",
        "scope": "claude_code_agent_model_selection_only",
        "mode": "dry_run",
        "execution_changed": False,
        "decision": "would_select_if_enabled",
        "route_key": route_key,
        "reason": entry.get("reason") if isinstance(entry, dict) else None,
        "candidate_model": candidate_model,
        "candidate_models": signals.get("candidate_models"),
        "skipped_candidates": signals.get("skipped_candidates"),
        "resolved_model": resolved_model,
        "candidate_allowed_by_policy": claude_model_allowed_by_policy(candidate_model, route_key),
        "selected_model": current_model,
        "selected_model_source": profile.get("model_override_source") or "default",
        "signals": signals,
    }


def with_claude_effort_override(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    effort, source = claude_effort_override(task, bool(profile.get("source_edit")))
    if not effort:
        return profile
    patched = dict(profile)
    patched["effort"] = effort
    patched["effort_override_source"] = source
    return patched


def _claude_effort_tier(task: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """Return a tiered effort level based on lane, role, and source_edit.

    Returns None when the default max should be used (production/code-edit turns).
    Returns a lower effort level for lightweight turns to reduce room response
    latency without sacrificing quality on complex work.

    Rationale: Alex identified that Claude Code is too slow to follow room
    conversation. The root cause is that *every* Claude Code turn uses effort=max,
    which maximises reasoning tokens even for simple followups. Tiered effort
    lets lightweight turns respond faster while complex turns keep max.
    """
    # source_edit turns always need max reasoning — they may write production code.
    if profile.get("source_edit"):
        return None
    # Check lane-based override (peer_collaboration_followup, bot-mention, etc.)
    lane = str(task.get("lane") or "")
    if lane in CLAUDE_EFFORT_TIER_OVERRIDES:
        return CLAUDE_EFFORT_TIER_OVERRIDES[lane]
    # Check collaboration role override (co_producer uses high, lead uses max)
    collab = task.get("collaboration") or {}
    roles = collab.get("roles") or []
    for role_entry in roles:
        if role_entry.get("agent_id") == "claude-code":
            role = str(role_entry.get("role") or "")
            if role in CLAUDE_EFFORT_ROLE_OVERRIDES:
                return CLAUDE_EFFORT_ROLE_OVERRIDES[role]
    return None


def enforce_claude_effort_policy(profile: dict[str, Any], *, task: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply effort policy for Claude Code room calls.

    By default, forces max reasoning for every call (original policy).
    When the tiered policy is active and the task/role qualifies, uses a
    lower effort level to reduce room response latency.
    """
    patched = dict(profile)
    previous = str(patched.get("effort") or "").strip()

    # Try tiered effort selection first
    tier_effort = _claude_effort_tier(task or {}, patched) if task else None
    if tier_effort and tier_effort in CLAUDE_EFFORT_LEVELS:
        if previous and previous != tier_effort:
            patched["effort_policy_previous_effort"] = previous
            if patched.get("effort_override_source"):
                patched["effort_policy_previous_source"] = patched.get("effort_override_source")
        patched["effort"] = tier_effort
        patched["effort_policy"] = CLAUDE_ARK_EFFORT_TIER_POLICY
        patched["effort_override_source"] = "policy.tiered_by_lane_and_collab_role"
        return patched

    # Fallback: original unconditional max policy
    if previous and previous != "max":
        patched["effort_policy_previous_effort"] = previous
        if patched.get("effort_override_source"):
            patched["effort_policy_previous_source"] = patched.get("effort_override_source")
    patched["effort"] = "max"
    patched["effort_policy"] = CLAUDE_ARK_EFFORT_POLICY
    patched["effort_override_source"] = "policy.force_max_after_alex_2026_05_21"
    return patched


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def projection_event_seen(dedupe_key: str) -> bool:
    if not dedupe_key:
        return False
    needle = f'"dedupe_key": "{dedupe_key}"'
    for path in [PROJECTION_EVENTS_FILE, OPENCLAW_MAIN_PROJECTION_EVENTS_FILE]:
        try:
            if path.exists() and needle in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except Exception:
            continue
    return False


def append_openclaw_main_projection_event(event: dict[str, Any]) -> bool:
    dedupe_key = str(event.get("dedupe_key") or "")
    if projection_event_seen(dedupe_key):
        return False
    append_jsonl(PROJECTION_EVENTS_FILE, event)
    if OPENCLAW_MAIN_PROJECTION_EVENTS_FILE != PROJECTION_EVENTS_FILE:
        append_jsonl(OPENCLAW_MAIN_PROJECTION_EVENTS_FILE, event)
    return True


def run_cmd(args: list[str], timeout: int = 30, input_text: str | None = None, env: dict[str, str] | None = None, cwd: Path | str | None = None) -> dict[str, Any]:
    try:
        kwargs: dict[str, Any] = {
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout,
        }
        if env is not None:
            kwargs["env"] = env
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        if input_text is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = input_text
        proc = subprocess.run(args, **kwargs)
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "exit_code": 127, "stdout": "", "stderr": "command not found"}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "exit_code": 124, "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "", "stderr": "timeout"}


def write_codex_native_event_log(run_dir: Path, model: str, stdout: str) -> Path | None:
    if not CODEX_NATIVE_JSON_EVENTS_ENABLED or not stdout.strip():
        return None
    path = run_dir / f"codex.{safe_run_id(model)}.native-events.jsonl"
    path.write_text(stdout.rstrip() + "\n", encoding="utf-8")
    return path


def parse_json_output(result: dict[str, Any]) -> dict[str, Any]:
    text = str(result.get("stdout") or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def collaboration_work_item_id(task: dict[str, Any], agent_id: str) -> str | None:
    collaboration = task.get("collaboration")
    if not isinstance(collaboration, dict):
        return None
    participants = collaboration.get("participants")
    if isinstance(participants, list) and participants and agent_id not in participants:
        return None
    work_items = collaboration.get("work_items")
    if not isinstance(work_items, list):
        return None
    for item in work_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        assigned = item.get("assigned_to") or item.get("agent_id") or item.get("owner")
        if assigned in (None, "", agent_id):
            return item_id
        if isinstance(assigned, list) and agent_id in assigned:
            return item_id
    return None


def collaboration_agent_excluded(task: dict[str, Any], agent_id: str) -> bool:
    collaboration = task.get("collaboration")
    if not isinstance(collaboration, dict):
        return False
    participants = collaboration.get("participants")
    return isinstance(participants, list) and bool(participants) and agent_id not in participants


def collaboration_agent_role(task: dict[str, Any], agent_id: str) -> str:
    collaboration = task.get("collaboration")
    if not isinstance(collaboration, dict):
        return ""
    roles = collaboration.get("roles")
    if not isinstance(roles, list):
        return ""
    for role in roles:
        if not isinstance(role, dict):
            continue
        if str(role.get("agent_id") or "").strip() != agent_id:
            continue
        return str(role.get("role") or "").strip()
    return ""


def collaboration_claim_failure_reasons(ledger: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("init", "claim"):
        value = ledger.get(key)
        if isinstance(value, dict) and not value.get("ok", False):
            reasons.append(str(value.get("error") or f"{key}_failed"))
    return reasons


def collaboration_claim_conflict_with_peer(ledger: dict[str, Any]) -> bool:
    claim = ledger.get("claim")
    if not isinstance(claim, dict) or claim.get("ok", False):
        return False
    error = str(claim.get("error") or "").strip().lower()
    return "already claimed by" in error or "work item already claimed by" in error


def collaboration_soft_contribution_allowed(task: dict[str, Any], agent_id: str, ledger: dict[str, Any]) -> bool:
    if not collaboration_claim_conflict_with_peer(ledger):
        return False
    assignment = task.get("collaboration_assignment")
    if isinstance(assignment, dict):
        # Explicit per-turn assignment is more specific than the durable role
        # table.  A lead/reviewer that lost a claim should not silently become a
        # soft co-producer just because the room-level role table still lists a
        # co_producer role for that agent.
        return str(assignment.get("turn_position") or "").strip() == "co_producer"
    return collaboration_agent_role(task, agent_id) == "co_producer"


def mark_collaboration_soft_unclaimed_contribution(ledger: dict[str, Any]) -> None:
    """Let a co-producer continue without writing artifacts to the claimed item."""
    if ledger.get("work_item_id"):
        ledger["unclaimed_work_item_id"] = ledger.get("work_item_id")
    ledger["work_item_id"] = None
    ledger["soft_unclaimed_contribution"] = True
    ledger["soft_unclaimed_reason"] = "co_producer_claim_conflict_with_peer"


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(path)


def ledger_path_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug[:120] or "agent-room-task"


def write_agent_presence(
    task: dict[str, Any],
    agent_id: str,
    state: str,
    detail: str,
    *,
    run_dir: Path | None = None,
    work_item_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort per-agent presence claim for the status surface.

    This is intentionally local-only: it lets main/Alex distinguish "runner is
    alive but black-box" from "agent has at least reached backend invocation" or
    "agent completed/blocked" without sending chat heartbeats.
    """
    run_id = str(task.get("run_id") or task.get("task_id") or "agent-room-task")
    record: dict[str, Any] = {
        "schema": "openclaw.agent_room.agent_presence.v0",
        "agent_id": agent_id,
        "room_id": task.get("room_id"),
        "task_id": task.get("task_id"),
        "run_id": run_id,
        "state": state,
        "detail": detail[:500],
        "work_item_id": work_item_id,
        "pid": os.getpid(),
        "updated_at": now_iso(),
        "runner_dir": str(run_dir) if run_dir is not None else None,
        "tokens_printed": False,
    }
    if extra:
        record.update(extra)
    try:
        run_path = AGENT_PRESENCE_DIR / "runs" / ledger_path_slug(run_id) / f"{ledger_path_slug(agent_id)}.json"
        latest_path = AGENT_PRESENCE_DIR / "agents" / f"{ledger_path_slug(agent_id)}.json"
        archive_path = AGENT_PRESENCE_DIR / "events.jsonl"
        write_json(run_path, record)
        write_json(latest_path, record)
        append_jsonl(archive_path, record)
    except Exception:
        return {"ok": False, "error": "presence_write_failed"}
    return {"ok": True, "state": state, "run_id": run_id, "agent_id": agent_id}


def collaboration_ledger_paths(task: dict[str, Any]) -> tuple[Path, Path]:
    """Return per-task ledger paths.

    The collaboration ledger is mutable state. Keeping it in one global
    `collaboration_ledger.json` made concurrent peer-followup tasks overwrite
    each other's participant/work-item set, so a valid Claude Code claim could
    be rejected because Codex had just initialized another task. Agent runners
    therefore use one state/archive pair per task id.
    """
    task_key = str(task.get("task_id") or task.get("run_id") or "agent-room-task")
    slug = ledger_path_slug(task_key)
    return COLLAB_LEDGER_DIR / f"{slug}.json", COLLAB_LEDGER_DIR / f"{slug}.jsonl"


def run_collaboration_ledger(args: list[str], *, state_file: Path | None = None, archive_file: Path | None = None) -> dict[str, Any]:
    if not COLLAB_LEDGER_TOOL.exists():
        return {"ok": False, "error": "collaboration_ledger_tool_missing", "tool": str(COLLAB_LEDGER_TOOL)}
    cmd = [sys.executable, str(COLLAB_LEDGER_TOOL)]
    if state_file is not None:
        cmd.extend(["--state-file", str(state_file)])
    if archive_file is not None:
        cmd.extend(["--archive-file", str(archive_file)])
    cmd.extend(args)
    result = run_cmd(cmd, timeout=30)
    parsed = parse_json_output(result)
    if parsed:
        parsed.setdefault("exit_code", result.get("exit_code"))
        return parsed
    return {
        "ok": bool(result.get("ok")),
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout") or "")[-1000:],
        "stderr": str(result.get("stderr") or "")[-1000:],
    }


def collaboration_begin(task: dict[str, Any], agent_id: str, task_file: Path | None) -> dict[str, Any]:
    work_item_id = collaboration_work_item_id(task, agent_id)
    if not work_item_id:
        return {"enabled": False}
    state_file, archive_file = collaboration_ledger_paths(task)
    task_path = task_file
    temp_task_file: Path | None = None
    if task_path is None:
        task_id = str(task.get("task_id") or task.get("run_id") or "unknown-task")
        safe_task_id = re.sub(r"[^A-Za-z0-9._-]", "-", task_id)[:64] or "task"
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=COLLAB_LEDGER_DIR,
                prefix=f"bootstrap-{safe_task_id}-",
                suffix=".json",
            ) as f:
                json.dump(task, f, ensure_ascii=False, indent=2)
                f.write("\n")
                temp_task_file = Path(f.name)
            task_path = temp_task_file
        except Exception as exc:
            return {
                "enabled": True,
                "work_item_id": work_item_id,
                "state_file": str(state_file),
                "archive_file": str(archive_file),
                "init": {"ok": False, "error": "missing_task_file", "detail": f"bootstrap_task_file_failed: {exc}"},
            }
    try:
        init = run_collaboration_ledger(
            ["init", "--task-file", str(task_path), "--if-needed"],
            state_file=state_file,
            archive_file=archive_file,
        )
        claim = run_collaboration_ledger([
            "claim",
            "--work-item-id", work_item_id,
            "--agent-id", agent_id,
            "--status", "active",
            "--note", "agent_task_runner started this work item",
        ], state_file=state_file, archive_file=archive_file)
    finally:
        if temp_task_file is not None and temp_task_file.exists():
            temp_task_file.unlink(missing_ok=True)
    return {
        "enabled": True,
        "work_item_id": work_item_id,
        "state_file": str(state_file),
        "archive_file": str(archive_file),
        "init": init,
        "claim": claim,
    }


def collaboration_finish(task: dict[str, Any], agent_id: str, work_item_id: str | None, comment: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    if not work_item_id:
        return None
    state_file, archive_file = collaboration_ledger_paths(task)
    blockers = comment.get("blockers") if isinstance(comment.get("blockers"), list) else []
    failed = bool(blockers) or not bool(result.get("ok", True))
    retryable_failure = comment.get("retryable_failure") if isinstance(comment.get("retryable_failure"), dict) else None
    if failed and retryable_failure:
        reason = str(retryable_failure.get("reason") or (blockers[0] if blockers else "provider_retryable_failure"))
        retry_after = str(retryable_failure.get("retry_after") or retryable_failure.get("cooldown_until") or "").strip()
        note = f"retryable_provider_failure:{reason}"
        if retry_after:
            note += f"; retry_after={retry_after}"
        return run_collaboration_ledger([
            "status",
            "--work-item-id", work_item_id,
            "--agent-id", agent_id,
            "--status", "retryable",
            "--note", note[:500],
        ], state_file=state_file, archive_file=archive_file)
    if failed:
        reason = str(blockers[0] if blockers else f"agent_runner_exit_{result.get('exit_code')}")
        detail = str(comment.get("body") or comment.get("title") or reason)[:1000]
        return run_collaboration_ledger([
            "blocker",
            "--work-item-id", work_item_id,
            "--agent-id", agent_id,
            "--reason", reason,
            "--detail", detail,
        ], state_file=state_file, archive_file=archive_file)
    title = str(comment.get("title") or f"{agent_id} room comment")[:240]
    artifact_result = run_collaboration_ledger([
        "artifact",
        "--work-item-id", work_item_id,
        "--agent-id", agent_id,
        "--type", "comment_jsonl",
        "--title", title,
        "--path", relative_to_root(comment_path(agent_id)),
        "--status", "completed",
    ], state_file=state_file, archive_file=archive_file)

    # Record a material point for standing mainline and non-followup tasks.
    # Structural peer follow-up tasks already have point recording via
    # ensure_parent_followup_point / record_parent_peer_followup_uptake.
    should_record_point = not is_structural_peer_followup_task(task)
    if should_record_point and standing_mainline_has_peer_point(task, agent_id):
        # In a standing discussion, a later peer contribution is primarily the
        # uptake of an existing point.  Recording it as another open point makes
        # the compact status falsely report "pending uptake" after the peer has
        # already responded.
        if isinstance(artifact_result, dict):
            artifact_result["point_recording"] = "skipped_standing_uptake_response"
        should_record_point = False
    if should_record_point:
        body_summary = str(comment.get("body") or title)[:2000]
        kind = "evidence"
        if comment.get("blockers"):
            kind = "risk"
        elif any(token in body_summary.lower() for token in ("建议", "proposal", "下一步", "next step")):
            kind = "proposal"
        point_text = f"{agent_id}: {body_summary}"
        try:
            point_result = run_collaboration_ledger([
                "point",
                "--agent-id", agent_id,
                "--kind", kind,
                "--text", point_text,
                "--work-item-id", work_item_id,
                "--source-message-id", f"agent-comment:{agent_id}:{task.get('task_id', '')}",
            ], state_file=state_file, archive_file=archive_file)
            if point_result.get("ok"):
                artifact_result = dict(artifact_result or {})
                artifact_result["point_recorded"] = True
                artifact_result["point_id"] = point_result.get("point_id")
        except Exception:
            pass  # Point recording is best-effort; do not block finish on failure.

    return artifact_result


def is_structural_peer_followup_task(task: dict[str, Any]) -> bool:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return (
        str(source.get("transport") or "") == "agent-room-collab-followup"
        or str(task.get("requested_by") or "") == "agent-room-collab-followup"
        or str(task.get("lane") or "") == "peer_collaboration_followup"
        or bool(task.get("collab_parent_task_id"))
    )


def task_manifest_path_for_id(task_id: str) -> Path | None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    path = ROOM / "tasks" / task_id / "manifest.json"
    return path if path.exists() else None


def read_json_if_exists(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
    except Exception:
        return records
    return records


def latest_comment_for_task(agent_id: str, task_id: str) -> dict[str, Any] | None:
    for record in reversed(read_jsonl_records(comment_path(agent_id))):
        if str(record.get("agent_id") or "") != agent_id:
            continue
        if str(record.get("task_id") or record.get("run_id") or "") == task_id:
            return record
    return None


def collaboration_work_item_for_agent(task: dict[str, Any], agent_id: str) -> str | None:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        assigned = item.get("assigned_to") or item.get("agent_id") or item.get("owner")
        if assigned == agent_id or (isinstance(assigned, list) and agent_id in assigned):
            return item_id
        if str(item.get("claimed_by") or "").strip() == agent_id:
            return item_id
    return None


def collaboration_completed_agents(task: dict[str, Any]) -> set[str]:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    completed: set[str] = set()
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict) or str(item.get("status") or "") != "completed":
            continue
        for value in (item.get("assigned_to"), item.get("agent_id"), item.get("owner"), item.get("claimed_by")):
            if isinstance(value, str) and value in {"codex", "claude-code"}:
                completed.add(value)
            elif isinstance(value, list):
                completed.update(str(agent_id) for agent_id in value if str(agent_id) in {"codex", "claude-code"})
    return completed


def point_kind_for_comment(comment: dict[str, Any] | None) -> str:
    kind = str((comment or {}).get("kind") or "").strip()
    if kind in {"claim", "proposal", "risk", "evidence", "question", "decision", "summary"}:
        return kind
    if kind in {"status", "review"}:
        return "evidence"
    return "summary"


def point_text_for_comment(source_agent: str, parent_task_id: str, comment: dict[str, Any] | None) -> str:
    title = str((comment or {}).get("title") or "").strip()
    body = str((comment or {}).get("body") or "").strip()
    text = "\n\n".join(part for part in (title, body) if part).strip()
    if text:
        return text[:2000]
    return f"{source_agent} produced a material Agent Room comment on parent task {parent_task_id}."[:2000]


def ensure_parent_followup_point(parent_task: dict[str, Any], source_agent: str, parent_task_id: str) -> dict[str, Any]:
    state_file, archive_file = collaboration_ledger_paths(parent_task)
    parent_path = task_manifest_path_for_id(parent_task_id)
    if not state_file.exists() and parent_path is not None:
        init = run_collaboration_ledger(
            ["init", "--task-file", str(parent_path), "--if-needed"],
            state_file=state_file,
            archive_file=archive_file,
        )
        if not init.get("ok"):
            return {"ok": False, "error": "parent_ledger_init_failed", "detail": init}
    ledger = read_json_if_exists(state_file)
    if not isinstance(ledger, dict):
        return {"ok": False, "error": "parent_ledger_missing", "state_file": str(state_file)}
    points = ledger.get("points") if isinstance(ledger.get("points"), list) else []
    for point in reversed(points):
        if isinstance(point, dict) and str(point.get("agent_id") or "") == source_agent and str(point.get("id") or ""):
            return {"ok": True, "point_id": str(point.get("id")), "created": False}

    source_comment = latest_comment_for_task(source_agent, parent_task_id)
    args = [
        "point",
        "--agent-id", source_agent,
        "--kind", point_kind_for_comment(source_comment),
        "--text", point_text_for_comment(source_agent, parent_task_id, source_comment),
        "--source-message-id", f"agent-comment:{source_agent}:{parent_task_id}",
    ]
    work_item_id = collaboration_work_item_for_agent(parent_task, source_agent)
    if work_item_id:
        args.extend(["--work-item-id", work_item_id])
    comment_file = comment_path(source_agent)
    if comment_file.exists():
        args.extend(["--source-artifact", relative_to_root(comment_file)])
    point_result = run_collaboration_ledger(args, state_file=state_file, archive_file=archive_file)
    if not point_result.get("ok"):
        return {"ok": False, "error": "parent_point_record_failed", "detail": point_result}
    return {"ok": True, "point_id": point_result.get("point_id"), "created": True}


def infer_followup_uptake_status(task: dict[str, Any], comment: dict[str, Any]) -> str:
    body = f"{comment.get('title') or ''}\n{comment.get('body') or ''}".lower()
    if any(token in body for token in ("不同意", "反对", "challenge", "disagree", "纠正", "修正")):
        return "challenged"
    if any(token in body for token in ("拒绝", "rejected")):
        return "rejected"
    expected = []
    action = task.get("collaboration_action") if isinstance(task.get("collaboration_action"), dict) else {}
    if isinstance(action.get("expected_outputs"), list):
        expected = [str(item) for item in action.get("expected_outputs")]
    if any(item in {"patch", "smoke", "artifact", "blocker", "design", "uptake_decision", "evidence"} for item in expected):
        return "incorporated"
    if any(token in body for token in ("同意", "接受", "确认", "agree", "accepted")):
        return "accepted"
    return "incorporated"


def refresh_task_collaboration_quality_from_ledger(task_path: Path, task: dict[str, Any]) -> dict[str, Any]:
    current = read_json_if_exists(task_path)
    if not isinstance(current, dict):
        current = dict(task)
    sync_collaboration_from_ledger_snapshot(current)
    targets = [str(agent_id) for agent_id in (current.get("target_agents") or []) if str(agent_id)]
    summary = current.get("runner_summary") if isinstance(current.get("runner_summary"), dict) else {}
    completed_set = {str(agent_id) for agent_id in (summary.get("completed_agents") or []) if str(agent_id)}
    completed_set.update(collaboration_completed_agents(current))
    quality_gate = collaboration_quality_gate(current, targets, completed_set)
    next_summary = dict(summary)
    next_summary["completed_agents"] = sorted(completed_set)
    next_summary["collaboration_quality_gate"] = quality_gate
    current["runner_summary"] = next_summary
    current["quality_gate_status"] = quality_gate.get("status")
    if quality_gate.get("status") in {"peer_reviewed", "needs_collaboration_review", "needs_collaboration_repair", "degraded_quorum"}:
        current["review_status"] = quality_gate.get("status")
    current["updated_at"] = now_iso()
    write_json(task_path, current)
    return quality_gate


def record_parent_peer_followup_uptake(task: dict[str, Any], agent_id: str, comment: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if not is_structural_peer_followup_task(task):
        return {"ok": True, "status": "not_applicable"}
    if not result.get("ok", True) or comment.get("blockers"):
        return {"ok": True, "status": "skipped_failed_or_blocked_followup"}
    parent_task_id = str(task.get("collab_parent_task_id") or "").strip()
    action = task.get("collaboration_action") if isinstance(task.get("collaboration_action"), dict) else {}
    source_agent = str(
        action.get("source_agent_id")
        or task.get("collab_parent_agent_id")
        or ""
    ).strip()
    if not parent_task_id or source_agent not in {"codex", "claude-code"} or source_agent == agent_id:
        return {"ok": True, "status": "skipped_missing_parent_or_source"}
    parent_path = task_manifest_path_for_id(parent_task_id)
    parent_task = read_json_if_exists(parent_path)
    if not isinstance(parent_task, dict) or parent_path is None:
        return {"ok": False, "status": "parent_task_manifest_missing", "parent_task_id": parent_task_id}

    state_file, archive_file = collaboration_ledger_paths(parent_task)
    requested_point_id = str(task.get("collab_source_point_id") or action.get("source_point_id") or "").strip()
    point_result: dict[str, Any] = {}
    if requested_point_id:
        ledger = read_json_if_exists(state_file)
        points = ledger.get("points") if isinstance(ledger, dict) and isinstance(ledger.get("points"), list) else []
        if any(
            isinstance(point, dict)
            and str(point.get("id") or "") == requested_point_id
            and str(point.get("agent_id") or "") == source_agent
            for point in points
        ):
            point_result = {"ok": True, "point_id": requested_point_id, "created": False, "source": "followup_manifest"}
    if not point_result:
        point_result = ensure_parent_followup_point(parent_task, source_agent, parent_task_id)
    if not point_result.get("ok") or not point_result.get("point_id"):
        return {"ok": False, "status": "parent_point_unavailable", "detail": point_result}

    ledger = read_json_if_exists(state_file)
    point_id = str(point_result.get("point_id"))
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    if isinstance(ledger, dict):
        for uptake in ledger.get("uptakes") or []:
            if not isinstance(uptake, dict):
                continue
            uptake_agent = str(uptake.get("by_agent") or uptake.get("agent_id") or "")
            if (
                uptake_agent == agent_id
                and str(uptake.get("point_id") or "") == point_id
                and task_id
                and task_id in f"{uptake.get('reason') or ''}\n{uptake.get('behavior_impact') or ''}"
            ):
                gate = refresh_task_collaboration_quality_from_ledger(parent_path, parent_task)
                return {"ok": True, "status": "already_recorded", "point_id": point_id, "quality_gate": gate}

    title = str(comment.get("title") or "peer follow-up completed").strip()
    uptake_status = infer_followup_uptake_status(task, comment)
    reason = f"peer follow-up {task_id} completed by {agent_id}; responded to {source_agent}'s point: {title}"[:2000]
    expected = []
    if isinstance(action.get("expected_outputs"), list):
        expected = [str(item) for item in action.get("expected_outputs")]
    behavior_impact = (
        f"collab_intent={task.get('collab_intent') or action.get('action') or ''}; "
        f"expected_outputs={','.join(expected)}; comment_path={relative_to_root(comment_path(agent_id))}"
    )[:2000]
    uptake_result = run_collaboration_ledger(
        [
            "uptake",
            "--point-id", point_id,
            "--by-agent", agent_id,
            "--status", uptake_status,
            "--reason", reason,
            "--behavior-impact", behavior_impact,
            "--artifact-path", relative_to_root(comment_path(agent_id)),
        ],
        state_file=state_file,
        archive_file=archive_file,
    )
    if not uptake_result.get("ok"):
        return {"ok": False, "status": "parent_uptake_record_failed", "detail": uptake_result}
    gate = refresh_task_collaboration_quality_from_ledger(parent_path, parent_task)
    return {"ok": True, "status": "recorded", "point_id": point_id, "uptake": uptake_result, "quality_gate": gate}


def is_standing_mainline_task(task: dict[str, Any]) -> bool:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return (
        str(source.get("transport") or "") == "agent-room-standing-mainline"
        or str(task.get("lane") or "") == "standing_mainline_discussion"
        or bool(task.get("standing_mainline") or task.get("standing_agenda"))
    )


def standing_mainline_has_peer_point(task: dict[str, Any], agent_id: str) -> bool:
    if not is_standing_mainline_task(task):
        return False
    state_file, _archive_file = collaboration_ledger_paths(task)
    ledger = read_json_if_exists(state_file)
    if not isinstance(ledger, dict):
        return False
    points = ledger.get("points") if isinstance(ledger.get("points"), list) else []
    return any(
        isinstance(point, dict)
        and str(point.get("id") or "")
        and str(point.get("agent_id") or "") not in {"", agent_id}
        for point in points
    )


def record_standing_mainline_peer_uptake(task: dict[str, Any], agent_id: str, comment: dict[str, Any]) -> dict[str, Any]:
    """Record uptake against a peer's point in a standing mainline discussion.

    When multiple agents contribute to the same standing task, each agent's
    response is recorded as uptake against the most recent open point from a
    different agent. This creates the discussion thread that the collaboration
    ledger's points/uptakes were designed to capture, but which was previously
    only wired for structural peer follow-up sub-tasks.
    """
    if not is_standing_mainline_task(task):
        return {"ok": True, "status": "not_applicable"}
    state_file, archive_file = collaboration_ledger_paths(task)
    ledger = read_json_if_exists(state_file)
    if not isinstance(ledger, dict):
        return {"ok": True, "status": "skipped_no_ledger"}
    points = ledger.get("points") if isinstance(ledger.get("points"), list) else []
    # Find the most recent open point from a different agent.
    peer_point = None
    for point in reversed(points):
        if not isinstance(point, dict):
            continue
        point_agent = str(point.get("agent_id") or "")
        if point_agent and point_agent != agent_id and str(point.get("status") or "") in ("open", "accepted", "challenged", "incorporated"):
            peer_point = point
            break
    if peer_point is None:
        return {"ok": True, "status": "skipped_no_peer_point"}
    point_id = str(peer_point.get("id") or "")
    if not point_id:
        return {"ok": True, "status": "skipped_point_no_id"}
    # Check for duplicate uptake.
    uptakes = ledger.get("uptakes") if isinstance(ledger.get("uptakes"), list) else []
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    for uptake in uptakes:
        if not isinstance(uptake, dict):
            continue
        if (
            str(uptake.get("by_agent") or "") == agent_id
            and str(uptake.get("point_id") or "") == point_id
        ):
            return {"ok": True, "status": "skipped_already_recorded", "point_id": point_id}
    # Classify uptake.
    title = str(comment.get("title") or "standing mainline contribution").strip()
    body = f"{title}\n{comment.get('body') or ''}".lower()
    uptake_status = "incorporated"
    if any(token in body for token in ("不同意", "反对", "challenge", "disagree", "纠正", "修正")):
        uptake_status = "challenged"
    elif any(token in body for token in ("拒绝", "rejected")):
        uptake_status = "rejected"
    elif any(token in body for token in ("同意", "接受", "确认", "agree", "accepted")):
        uptake_status = "accepted"
    reason = f"standing mainline response by {agent_id} to {peer_point.get('agent_id')}: {title}"[:2000]
    behavior_impact = f"standing_task={task_id}; comment_path={relative_to_root(comment_path(agent_id))}"[:2000]
    try:
        uptake_result = run_collaboration_ledger(
            [
                "uptake",
                "--point-id", point_id,
                "--by-agent", agent_id,
                "--status", uptake_status,
                "--reason", reason,
                "--behavior-impact", behavior_impact,
                "--artifact-path", relative_to_root(comment_path(agent_id)),
            ],
            state_file=state_file,
            archive_file=archive_file,
        )
        if uptake_result.get("ok"):
            task_path = task_manifest_path_for_id(task_id)
            if task_path:
                refresh_task_collaboration_quality_from_ledger(task_path, task)
            return {"ok": True, "status": "recorded", "point_id": point_id, "uptake_status": uptake_status}
        return {"ok": False, "status": "uptake_record_failed", "detail": uptake_result}
    except Exception:
        return {"ok": True, "status": "skipped_uptake_error"}


def sync_collaboration_from_ledger_snapshot(task: dict[str, Any]) -> None:
    collaboration = task.get("collaboration")
    if not isinstance(collaboration, dict):
        return
    state_file, _archive_file = collaboration_ledger_paths(task)
    if not state_file.exists():
        return
    try:
        ledger = read_json(state_file)
    except Exception:
        return
    if not isinstance(ledger, dict):
        return
    if ledger.get("schema") != "openclaw.agent_room.collaboration_ledger.v0":
        return
    if str(ledger.get("task_id") or "") != str(task.get("task_id") or ""):
        return
    synced = dict(collaboration)
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
            synced[key] = ledger[key]
    task["collaboration"] = synced


def collaboration_quality_gate(task: dict[str, Any], targets: list[str], completed_set: set[str]) -> dict[str, Any]:
    """Classify whether a multi-agent task produced real collaboration evidence.

    Starting two runners is only dispatch.  A clean multi-agent completion should
    leave at least one durable sign that the peers actually coordinated or that
    the system knowingly continued with degraded quorum.  This gate is advisory
    for now (it does not block artifact harvest), but it prevents manifests from
    looking fully healthy when the collaboration layer only produced parallel
    monologues.
    """
    local_targets = sorted({str(agent_id) for agent_id in targets if str(agent_id) in {"codex", "claude-code"}})
    if len(local_targets) <= 1:
        return {"status": "not_applicable", "reason": "single_local_agent"}
    missing = sorted(set(local_targets).difference(completed_set))
    if missing:
        return {"status": "degraded_quorum", "reason": "missing_local_agent_results", "missing_agents": missing}
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    artifacts = collaboration.get("artifacts") if isinstance(collaboration.get("artifacts"), list) else []
    blockers = collaboration.get("blockers") if isinstance(collaboration.get("blockers"), list) else []
    handoffs = collaboration.get("handoffs") if isinstance(collaboration.get("handoffs"), list) else []
    uptakes = collaboration.get("uptakes") if isinstance(collaboration.get("uptakes"), list) else []
    artifact_agents = {
        str(item.get("agent_id") or item.get("produced_by") or item.get("created_by") or item.get("assigned_to") or "")
        for item in artifacts
        if isinstance(item, dict)
    }
    blocker_agents = {str(item.get("agent_id") or "") for item in blockers if isinstance(item, dict)}
    if blocker_agents:
        return {"status": "degraded_quorum", "reason": "collaboration_blockers_recorded", "blocker_agents": sorted(blocker_agents)}
    if handoffs:
        return {"status": "peer_reviewed", "reason": "handoff_recorded", "handoffs": len(handoffs)}
    material_uptakes = [
        item for item in uptakes
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"accepted", "challenged", "incorporated", "rejected", "superseded"}
        and str(item.get("by_agent") or "") != str(item.get("point_agent_id") or "")
    ]
    if material_uptakes:
        return {"status": "peer_reviewed", "reason": "point_uptake_recorded", "uptakes": len(material_uptakes)}
    if set(local_targets).issubset(artifact_agents):
        # Both peers produced artifacts, but without a handoff/challenge/summary
        # it may still be parallel output. Mark review-needed rather than clean.
        return {"status": "needs_collaboration_review", "reason": "parallel_artifacts_without_integration", "artifact_agents": sorted(artifact_agents)}
    return {"status": "needs_collaboration_repair", "reason": "missing_peer_interaction_evidence", "artifact_agents": sorted(artifact_agents)}


def load_agent_env() -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    loaded: list[str] = []
    for path in ENV_FILES:
        if not path.exists():
            continue
        loaded.append(str(path))
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            value = value.strip().strip('"').strip("'")
            env.setdefault(key, value)
    return env, loaded


def safe_run_id(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug[:96] or f"agent-room-claude-code-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def claude_ark_attempt_run_id(base_run_id: str, model: str, runner_dir: Path) -> str:
    """Unique coding-run id for repeated Agent Room dispatches of one task."""
    stamp = ""
    for part in reversed(runner_dir.resolve().parts):
        if re.fullmatch(r"\d{8}-\d{6}", part):
            stamp = part
            break
    if not stamp:
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    fingerprint = hashlib.sha1(str(runner_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return safe_run_id(f"{base_run_id}-{safe_run_id(model)}-{stamp}-{fingerprint}")


def parse_last_json_line(text: str) -> dict[str, Any]:
    for line in reversed([line.strip() for line in (text or "").splitlines() if line.strip()]):
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return {}


def parse_room_comment_json(text: str) -> dict[str, Any]:
    """Parse a model-produced Agent Room JSON object from raw text.

    Some Ark/Claude runs complete successfully but the helper does not create
    `claude_stdout.parsed.json`; the raw stdout can still be the exact room
    JSON object we asked for, sometimes with a short prose preface. In that
    case we should store the object's `body`, not send the whole JSON blob to
    Telegram.
    """
    stripped = (text or "").strip()
    if not stripped:
        return {}
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped).strip()
    candidates = [stripped, *extract_json_object_candidates(stripped)]
    best: dict[str, Any] = {}
    for candidate in candidates:
        value = parse_json_object_lenient(candidate)
        if not is_room_comment_json(value):
            continue
        if value.get("agent_id") == "claude-code":
            best = value
            break
        if not best:
            best = value
    if not best:
        return {}
    out: dict[str, Any] = {}
    for key in ("kind", "confidence", "title", "body", "blockers"):
        if key in best:
            out[key] = best[key]
    return out


def parse_json_object_lenient(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception:
        repaired = escape_raw_newlines_in_json_strings(text)
        if repaired == text:
            return {}
        try:
            value = json.loads(repaired)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def is_room_comment_json(value: dict[str, Any]) -> bool:
    if not isinstance(value.get("body"), str):
        return False
    return any(key in value for key in ("agent_id", "kind", "title", "blockers"))


def extract_json_object_candidates(text: str) -> list[str]:
    out: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:idx + 1])
                start = None
    return out


def escape_raw_newlines_in_json_strings(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            out.append(ch)
            in_string = False
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return "".join(out)


RAW_STATUS_MARKERS = (
    '"status"',
    '"accepted"',
    "entry_artifacts_missing",
    "failed_gate",
    "runner_failed_before_run_dir",
    "missing_required_artifacts",
    "worker_timeout",
)

def normalize_room_visible_text(text: str) -> str:
    """Clean transport artifacts without rewriting an agent's visible style."""
    if not text:
        return text
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"[ \t]+(\n)", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_claude_code_visible_text(text: str) -> str:
    """Clean Ark transport artifacts while preserving Claude Code's phrasing."""
    if not text:
        return text
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def internal_status_body_reason(body: str) -> str | None:
    stripped = (body or "").strip()
    if not stripped:
        return None
    first_line = next((line.strip() for line in stripped.splitlines() if line.strip()), "")
    parsed: Any | None = None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
    if isinstance(parsed, dict) and ("status" in parsed or "accepted" in parsed):
        return "raw_internal_json_body"
    looks_like_status_payload = (
        first_line.startswith(("status:", "accepted:", "run_id:", "{", "["))
        or first_line.startswith('"status"')
        or bool(re.search(r'^\s*[\[{]?\s*"status"\s*:', first_line))
    )
    if looks_like_status_payload and any(marker in stripped for marker in RAW_STATUS_MARKERS):
        return "raw_internal_status_body"
    return None


def readable_internal_status_blocker(agent_id: str, run_id: str | None, reason: str, body: str) -> str:
    status = None
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            status = parsed.get("status")
    except Exception:
        status = None
    detail = f"，状态：{status}" if status else ""
    return (
        f"{agent_id} 本轮只返回了内部运行状态，没有形成正常房间发言。"
        f"我已把原始 JSON 状态转成可读 blocker，避免把内部状态直接发进群。"
        f"原因：{reason}{detail}。run_id: {run_id or 'unknown'}。"
    )


def readable_runner_failure_body(agent_id: str, run_id: str | None, result: dict[str, Any]) -> str:
    """Convert noisy CLI transcripts into a short visible-room blocker.

    Codex sometimes prints the entire prompt/context to stderr before the real
    error (for example usage-limit failures). That must never be forwarded as a
    room message.
    """
    stderr = str(result.get("stderr") or "")
    stdout = str(result.get("stdout") or "")
    combined = "\n".join(part for part in [stderr, stdout] if part).strip()
    reason = f"exit_{result.get('exit_code')}"
    if "usage limit" in combined.lower():
        reason = "usage_limit"
    elif "timeout" in combined.lower():
        reason = "timeout"
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    useful = []
    for line in reversed(lines):
        low = line.lower()
        if any(marker in low for marker in ["error:", "usage limit", "timeout", "rate limit", "try again"]):
            useful.append(line[:240])
        if len(useful) >= 2:
            break
    detail = "；".join(reversed(useful)) if useful else (lines[-1][:240] if lines else "no stderr/stdout")
    return (
        f"{agent_id} 本轮 CLI 执行失败，已拦截原始运行日志，避免把 prompt/上下文刷进群。"
        f"原因：{reason}。run_id: {run_id or 'unknown'}。摘要：{detail}"
    )


def load_codex_model_state() -> dict[str, Any]:
    state = read_json_if_exists(CODEX_MODEL_STATE)
    if not isinstance(state.get("models"), dict):
        state["models"] = {}
    # Normalize expired cooldown records so the state file stays consistent
    # with the runtime cooldown_active_until gate, which already ignores
    # expired cooldowns. Without this, stale expired records persist until a
    # specific model succeeds, making the JSON misleading and debugging harder.
    now = datetime.now(timezone.utc).astimezone()
    models = state["models"]
    changed = False
    for model, record in list(models.items()):
        if not isinstance(record, dict) or record.get("status") != "cooldown":
            continue
        until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
        if until and until <= now:
            record["status"] = "available"
            record.pop("reason", None)
            record.pop("cooldown_until", None)
            changed = True
    if changed:
        save_codex_model_state(state)
    return state


def save_codex_model_state(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(CODEX_MODEL_STATE, state)


def active_codex_catalog_slugs() -> set[str]:
    data = read_json_if_exists(CODEX_MODELS_CACHE)
    slugs: set[str] = set()
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if slug and str(item.get("visibility") or "") != "hide":
            slugs.add(slug)
    return slugs


def codex_model_candidates(task: dict[str, Any]) -> list[str]:
    """Return ordered Codex models for this Agent Room task.

    Keep the list configurable, but default to the visible local Codex catalog:
    strong default, strong fallback, coding-specific fallbacks including Spark,
    long-agent fallback, then mini as a light last resort.
    """
    configured = os.environ.get("AGENT_ROOM_CODEX_MODELS")
    if configured is None and isinstance(task.get("codex"), dict):
        configured = (task.get("codex") or {}).get("models")
    raw = configured if configured else ",".join(DEFAULT_CODEX_MODELS)
    if isinstance(raw, list):
        values = [str(x).strip() for x in raw]
    else:
        raw = str(raw)
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    values = [str(x).strip() for x in parsed]
                else:
                    values = []
            except Exception:
                values = []
        else:
            values = [part.strip() for part in raw.split(",")]
    values = [value for value in values if value]
    if not values:
        values = list(DEFAULT_CODEX_MODELS)
    catalog = active_codex_catalog_slugs()
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        # If the catalog is unavailable, keep the configured candidate. If it is
        # available, skip hidden/unknown catalog entries rather than discovering
        # failures by repeatedly launching Codex.
        if catalog and value not in catalog:
            continue
        seen.add(value)
        out.append(value)
    return out or list(DEFAULT_CODEX_MODELS)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def cooldown_active_until(state: dict[str, Any], model: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc).astimezone()
    record = (state.get("models") or {}).get(model) or {}
    until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
    if until and until > now:
        return until
    quota_until = quota_ledger_active_until(model, now)
    if quota_until:
        return quota_until
    return None


def cooldown_skip_reason(state: dict[str, Any], model: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc).astimezone()
    record = (state.get("models") or {}).get(model) or {}
    until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
    if until and until > now:
        return str(record.get("reason") or "cooldown")
    if quota_ledger_active_until(model, now):
        return "quota_ledger_zero_remaining"
    return "cooldown"


def classify_codex_failure(result: dict[str, Any]) -> str | None:
    exit_code = result.get("exit_code")
    # Exit code 124 is the canonical timeout signal from both the Ark runner
    # (claude_code_ark_runner.py L601) and the outer run_cmd wrapper (L533).
    # A timed-out model call is transient: the model was responsive but too
    # slow or verbose to finish within the budget, so the next candidate
    # should be tried instead of breaking the chain.
    if exit_code == 124:
        return "timeout"
    combined = "\n".join(str(result.get(k) or "") for k in ("stderr", "stdout", "_artifact_failure_text")).lower()
    if "timeout" in combined:
        return "timeout"
    if (
        "usage limit" in combined
        or "usage quota" in combined
        or "accountquotaexceeded" in combined
        or "quota exceeded" in combined
        or "quota_exceeded" in combined
        or "you have exceeded" in combined
        or "you've hit your usage limit" in combined
    ):
        return "usage_limit"
    if "rate limit" in combined or "too many requests" in combined or "429" in combined:
        return "rate_limit"
    if "overloaded" in combined or "temporarily unavailable" in combined or "try again later" in combined:
        return "model_overloaded"
    if (
        "unsupported model" in combined
        or "model not found" in combined
        or "unknown model" in combined
        or "invalid model" in combined
        or "model is not available" in combined
        or "not available for" in combined
    ):
        return "model_unavailable"
    return None


def parse_provider_retry_time(result: dict[str, Any]) -> datetime | None:
    combined = "\n".join(str(result.get(k) or "") for k in ("stderr", "stdout"))
    reset_match = re.search(
        r"(?:reset|try again)\s+at\s+(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2})\s+([+-]\d{4})(?:\s+[A-Z]{2,5})?",
        combined,
        re.I,
    )
    if reset_match:
        raw = f"{reset_match.group(1)} {reset_match.group(2)}"
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z").astimezone()
        except Exception:
            pass
    match = re.search(r"try again at\s+([0-9]{1,2}:[0-9]{2}\s*[AP]M)", combined, re.I)
    if not match:
        return None
    now = datetime.now(timezone.utc).astimezone()
    try:
        parsed_time = datetime.strptime(match.group(1).upper().replace(" ", ""), "%I:%M%p").time()
    except Exception:
        return None
    until = now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
    if until <= now:
        until += timedelta(days=1)
    return until


def parse_codex_retry_time(result: dict[str, Any]) -> datetime | None:
    return parse_provider_retry_time(result)


def retryable_provider_attempt(attempt: dict[str, Any]) -> bool:
    status = str(attempt.get("status") or "").strip().lower()
    reason = str(attempt.get("reason") or "").strip().lower()
    if status in {"skipped_cooldown", "cooldown"}:
        return True
    if attempt.get("cooldown_until"):
        return True
    return status == "failed" and reason in RETRYABLE_PROVIDER_REASONS


def retryable_failure_from_attempts(attempts: list[dict[str, Any]]) -> bool:
    relevant = [
        attempt
        for attempt in attempts
        if str(attempt.get("status") or "").strip().lower() in {"failed", "skipped_cooldown", "cooldown"}
        or bool(attempt.get("cooldown_until"))
    ]
    return bool(relevant) and all(retryable_provider_attempt(attempt) for attempt in relevant)


def retry_after_from_records(records: list[dict[str, Any]]) -> str | None:
    values: list[datetime] = []
    for record in records:
        until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
        if until:
            values.append(until)
    if not values:
        return None
    return min(values).isoformat(timespec="seconds")


def retryable_failure_metadata(
    *,
    agent_id: str,
    reason: str | None,
    comment_fields: dict[str, Any],
    depletion_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in RETRYABLE_PROVIDER_REASONS:
        return None
    attempts = [
        attempt
        for attempt in (comment_fields.get("model_attempts") or [])
        if isinstance(attempt, dict)
    ]
    if attempts and not retryable_failure_from_attempts(attempts):
        return None
    cooldown_until = retry_after_from_records([*attempts, *depletion_records])
    metadata: dict[str, Any] = {
        "schema": "openclaw.agent_room.retryable_provider_failure.v0",
        "status": "retryable",
        "agent_id": agent_id,
        "reason": normalized_reason,
        "retry_after": cooldown_until,
        "cooldown_until": cooldown_until,
        "source": "agent_task_runner.provider_quota_or_cooldown",
    }
    models = [
        str(attempt.get("model") or "")
        for attempt in attempts
        if str(attempt.get("model") or "").strip()
    ]
    if models:
        metadata["models"] = list(dict.fromkeys(models))
    return metadata


def mark_model_cooldown(state: dict[str, Any], model: str, reason: str, result: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).astimezone()
    until = parse_provider_retry_time(result)
    if until is None:
        # Usage-limit messages usually include a precise reset time. For other
        # transient pressure signals, keep the circuit breaker short and local
        # so a single failure does not disable an agent for the day. If a visible
        # catalog model is rejected on the current route, cool it down longer
        # and keep trying the remaining models.
        if reason == "usage_limit":
            until = now + timedelta(minutes=45)
        elif reason == "model_unavailable":
            until = now + timedelta(hours=6)
        else:
            until = now + timedelta(minutes=10)
    models = state.setdefault("models", {})
    models[model] = {
        "status": "cooldown",
        "reason": reason,
        "cooldown_until": until.isoformat(timespec="seconds"),
        "last_failure_at": now.isoformat(timespec="seconds"),
    }


def model_recovery_projection_event(
    agent_id: str,
    model: str,
    previous_record: dict[str, Any],
    *,
    run_id: str | None = None,
    room_id: str | None = None,
    source: str,
) -> dict[str, Any] | None:
    previous_status = str(previous_record.get("status") or "").strip()
    previous_reason = str(previous_record.get("reason") or previous_record.get("previous_reason") or "").strip()
    previous_cooldown = str(previous_record.get("cooldown_until") or "").strip()
    if previous_status not in {"cooldown", "depleted"} and not previous_cooldown:
        return None
    created_at = now_iso()
    dedupe_material = "|".join([
        "model_quota_recovered",
        agent_id,
        model,
        str(run_id or ""),
        previous_status,
        previous_reason,
        previous_cooldown,
    ])
    dedupe_key = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()
    return {
        "schema": "openclaw.agent_room.openclaw_main_projection_event.v0",
        "row_type": "model_quota_recovered",
        "projection_state": "actionable_status",
        "trigger_kind": "model_quota_recovery",
        "created_at": created_at,
        "room_id": room_id or "openclaw-evolution",
        "target_agent": "openclaw-main",
        "source_agent_id": agent_id,
        "source_task_id": run_id,
        "dedupe_key": dedupe_key,
        "canonical_state_advanced": False,
        "telegram_outbound_sent": False,
        "summary": f"{agent_id}/{model} recovered from {previous_reason or previous_status or 'cooldown'}; numeric remaining quota is still unknown.",
        "model_quota_recovery": {
            "agent_id": agent_id,
            "model": model,
            "previous_status": previous_status or None,
            "previous_reason": previous_reason or None,
            "previous_cooldown_until": previous_cooldown or None,
            "recovered_at": created_at,
            "source": source,
            "remaining_known": False,
            "remaining_percent": None,
            "remaining_units": None,
            "limitation": "Recovery is observed from a successful model call; the runtime still has no provider-backed numeric remaining-quota value.",
        },
    }


def mark_model_recovered(
    state: dict[str, Any],
    model: str,
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    room_id: str | None = None,
) -> dict[str, Any]:
    """Clear a successful model's stale cooldown record.

    The runtime selection gate reads codex_model_state/claude_model_state before
    launching the CLI. If a model succeeds but its previous usage-limit record
    remains in that file, later turns can keep skipping the restored primary and
    fall through to weaker candidates (for example gpt-5.3-codex-spark). A live
    success is the strongest recovery signal, so persist it immediately.
    """
    now = datetime.now(timezone.utc).astimezone()
    models = state.setdefault("models", {})
    record = dict(models.get(model) or {})
    previous_record = dict(record)
    record["status"] = "available"
    record["last_success_at"] = now.isoformat(timespec="seconds")
    record.pop("reason", None)
    record.pop("cooldown_until", None)
    models[model] = record
    event_written = False
    if agent_id and run_id:
        event = model_recovery_projection_event(
            agent_id,
            model,
            previous_record,
            run_id=run_id,
            room_id=room_id,
            source="model_state_success",
        )
        if event:
            event_written = append_openclaw_main_projection_event(event)
    return {
        "model": model,
        "previous_status": previous_record.get("status"),
        "previous_reason": previous_record.get("reason"),
        "previous_cooldown_until": previous_record.get("cooldown_until"),
        "recovered_at": record.get("last_success_at"),
        "projection_event_written": event_written,
    }


def mark_codex_model_cooldown(state: dict[str, Any], model: str, reason: str, result: dict[str, Any]) -> None:
    mark_model_cooldown(state, model, reason, result)


def mark_codex_model_recovered(
    state: dict[str, Any],
    model: str,
    *,
    run_id: str | None = None,
    room_id: str | None = None,
) -> dict[str, Any]:
    return mark_model_recovered(state, model, agent_id="codex", run_id=run_id, room_id=room_id)


def load_claude_model_state() -> dict[str, Any]:
    state = read_json_if_exists(CLAUDE_MODEL_STATE)
    if not isinstance(state.get("models"), dict):
        state["models"] = {}
    # Mirror the same expired-cooldown normalization as load_codex_model_state.
    now = datetime.now(timezone.utc).astimezone()
    models = state["models"]
    changed = False
    for model, record in list(models.items()):
        if not isinstance(record, dict) or record.get("status") != "cooldown":
            continue
        until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
        if until and until <= now:
            record["status"] = "available"
            record.pop("reason", None)
            record.pop("cooldown_until", None)
            changed = True
    if changed:
        save_claude_model_state(state)
    return state


def save_claude_model_state(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(CLAUDE_MODEL_STATE, state)


def mark_claude_model_cooldown(state: dict[str, Any], model: str, reason: str, result: dict[str, Any]) -> None:
    mark_model_cooldown(state, model, reason, result)


def mark_claude_model_recovered(
    state: dict[str, Any],
    model: str,
    *,
    run_id: str | None = None,
    room_id: str | None = None,
) -> dict[str, Any]:
    return mark_model_recovered(state, model, agent_id="claude-code", run_id=run_id, room_id=room_id)


def codex_failure_body(agent_id: str, run_id: str | None, attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return f"{agent_id} 本轮没有可用模型候选，未启动 CLI。run_id: {run_id or 'unknown'}。"
    compact: list[str] = []
    for attempt in attempts:
        model = attempt.get("model")
        status = attempt.get("status")
        reason = attempt.get("reason") or f"exit_{attempt.get('exit_code')}"
        if status == "skipped_cooldown":
            compact.append(f"{model}=cooldown到{attempt.get('cooldown_until')}")
        else:
            compact.append(f"{model}={reason}")
    return (
        f"{agent_id} 本轮所有可用 Codex 模型都未形成正常回复，已拦截原始 CLI 日志，避免把 prompt/上下文刷进群。"
        f"run_id: {run_id or 'unknown'}。模型尝试：" + "；".join(compact[:6])
    )


def load_agent_quota_state() -> dict[str, Any]:
    """Load the per-agent quota state for collaboration silence decisions."""
    state = read_json_if_exists(AGENT_QUOTA_STATE_FILE)
    if not isinstance(state.get("agents"), dict):
        state["agents"] = {}
    return state


def save_agent_quota_state(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(AGENT_QUOTA_STATE_FILE, state)


def load_agent_room_status() -> dict[str, Any]:
    state = read_json_if_exists(AGENT_ROOM_STATUS_FILE)
    if not isinstance(state.get("agents"), dict):
        state["agents"] = {}
    return state


def save_agent_room_status(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(AGENT_ROOM_STATUS_FILE, state)


def model_quota_signal_path() -> Path:
    raw = str(
        os.environ.get("AGENT_ROOM_MODEL_QUOTA_SIGNAL_FILE")
        or os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_FILE")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else MODEL_QUOTA_SIGNAL_FILE


def quota_ledger_path() -> Path:
    raw = str(
        os.environ.get("AGENT_ROOM_QUOTA_LEDGER_FILE")
        or os.environ.get("OPENCLAW_QUOTA_LEDGER_FILE")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else QUOTA_LEDGER_FILE


def quota_signal_lookup_keys(model: str | None) -> list[str]:
    value = quota_model_key(model)
    keys = [value]
    if "/" in value:
        keys.append(value.rsplit("/", 1)[-1])
    else:
        keys.append(f"openai-codex/{value}")
    lowered = [key.lower() for key in keys]
    out: list[str] = []
    for key in [*keys, *lowered]:
        if key and key not in out:
            out.append(key)
    return out


def quota_signal_model_record(agent_signal: dict[str, Any], model: str | None) -> dict[str, Any] | None:
    models = agent_signal.get("models")
    if not isinstance(models, dict):
        return agent_signal if isinstance(agent_signal, dict) else None
    for key in quota_signal_lookup_keys(model):
        record = models.get(key)
        if isinstance(record, dict):
            return record
    lower_map = {str(key).lower(): value for key, value in models.items() if isinstance(value, dict)}
    for key in quota_signal_lookup_keys(model):
        record = lower_map.get(key.lower())
        if isinstance(record, dict):
            return record
    return None


def normalize_trusted_quota_signal(record: dict[str, Any], default_source: str) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    expires_at = parse_iso_datetime(str(record.get("expires_at") or ""))
    if expires_at and expires_at <= datetime.now(timezone.utc).astimezone():
        return None
    remaining_fields = (
        "remaining_percent",
        "remaining_units",
        "remaining_requests",
        "remaining_tokens",
        "remaining_messages",
        "per_model_remaining_units",
    )
    remaining_known = bool(record.get("remaining_known")) or any(record.get(key) is not None for key in remaining_fields)
    if not remaining_known:
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
        "quota_signal_contract_version": str(record.get("quota_signal_contract_version") or "1-trusted-remaining"),
        "blocking_missing": list(record.get("blocking_missing") or []),
        "limitation": str(record.get("limitation") or "trusted remaining quota is wired; routing thresholds remain a separate policy"),
    }
    if per_model_units:
        signal["per_model_remaining_units"] = per_model_units
    for key in (
        "remaining_requests",
        "remaining_tokens",
        "remaining_messages",
        "limit_requests",
        "limit_tokens",
        "used_percent",
        "reset_at",
        "estimated_reset_at",
        "observed_at",
        "updated_at",
        "expires_at",
        "quota_scope",
        "per_model_remaining_known",
        "per_model_remaining_units",
        "provider_quota_window_known",
        "blocking_missing",
        "provider",
        "provider_display_name",
        "plan",
        "windows",
        "primary_window_label",
        "primary_window_remaining_percent",
        "canonical_model",
    ):
        if record.get(key) is not None:
            signal[key] = record.get(key)
    return signal


def trusted_remaining_quota_signal(agent_id: str, model: str | None = None) -> dict[str, Any] | None:
    data = read_json_if_exists(model_quota_signal_path())
    root = data.get("signals") if isinstance(data.get("signals"), dict) else data.get("agents")
    if not isinstance(root, dict):
        root = data
    agent_signal = root.get(agent_id) if isinstance(root.get(agent_id), dict) else None
    if agent_signal is None:
        agent_signal = root.get(agent_id.replace("-", "_")) if isinstance(root.get(agent_id.replace("-", "_")), dict) else None
    if not isinstance(agent_signal, dict):
        return trusted_quota_signal_from_ledger(model)
    record = quota_signal_model_record(agent_signal, model)
    signal = normalize_trusted_quota_signal(record or {}, str(data.get("source") or "model_quota_signal"))
    if signal:
        signal.setdefault("signal_file", str(model_quota_signal_path()))
        return signal
    ledger_signal = trusted_quota_signal_from_ledger(model)
    if ledger_signal:
        return ledger_signal
    return None


def quota_ledger_model_record(model: str | None) -> dict[str, Any] | None:
    ledger = read_json_if_exists(quota_ledger_path())
    models = ledger.get("models") if isinstance(ledger.get("models"), dict) else {}
    if not isinstance(models, dict):
        return None
    for key in quota_signal_lookup_keys(model):
        record = models.get(key)
        if isinstance(record, dict):
            return record
    lower_map = {str(key).lower(): value for key, value in models.items() if isinstance(value, dict)}
    for key in quota_signal_lookup_keys(model):
        record = lower_map.get(key.lower())
        if isinstance(record, dict):
            return record
    return None


def trusted_quota_signal_from_ledger(model: str | None) -> dict[str, Any] | None:
    record = quota_ledger_model_record(model)
    if not isinstance(record, dict):
        return None
    updated_at = parse_iso_datetime(str(record.get("last_updated") or ""))
    expires_at = None
    if updated_at:
        try:
            ttl_seconds = int(os.environ.get("OPENCLAW_MODEL_QUOTA_SIGNAL_TTL_SECONDS", "900"))
        except (TypeError, ValueError):
            ttl_seconds = 900
        expires_at = updated_at + timedelta(seconds=max(60, ttl_seconds))
        if expires_at <= datetime.now(timezone.utc).astimezone():
            return None
    projected = {
        "remaining_requests": record.get("requests_remaining"),
        "remaining_tokens": record.get("tokens_remaining"),
        "limit_requests": record.get("limit_requests"),
        "limit_tokens": record.get("limit_tokens"),
        "reset_at": record.get("reset_at"),
        "observed_at": record.get("last_updated"),
        "updated_at": record.get("last_updated"),
        "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
        "quota_scope": "provider_header_model_or_account",
        "per_model_remaining_known": None,
        "source": "quota_ledger",
        "trusted_remaining_source": "quota_ledger." + str(record.get("source") or "record").removeprefix("quota_ledger."),
        "proactive_switching_ready": True,
        "routing_ready": True,
        "quota_signal_contract_version": "2-provider-header",
        "blocking_missing": [],
        "limitation": "quota_ledger response headers are trusted for numeric visibility; runtime routing currently uses them only for hard zero-with-reset skips.",
    }
    signal = normalize_trusted_quota_signal(projected, "quota_ledger")
    if signal:
        signal["signal_file"] = str(quota_ledger_path())
    return signal


def quota_ledger_active_until(model: str | None, now: datetime | None = None) -> datetime | None:
    record = quota_ledger_model_record(model)
    if not isinstance(record, dict):
        return None
    remaining_values = []
    for key in ("requests_remaining", "tokens_remaining"):
        try:
            remaining_values.append(int(record.get(key)))
        except (TypeError, ValueError):
            continue
    if 0 not in remaining_values:
        return None
    reset_at = parse_iso_datetime(str(record.get("reset_at") or ""))
    if not reset_at:
        return None
    current = now or datetime.now(timezone.utc).astimezone()
    return reset_at if reset_at > current else None


def reactive_quota_signal(
    source: str,
    run_id: str | None = None,
    *,
    agent_id: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    signal: dict[str, Any] = {
        "mode": "reactive_failure_cooldown_and_success_recovery",
        "remaining_known": False,
        "remaining_percent": None,
        "remaining_units": None,
        "source": source,
        "proactive_switching_ready": False,
        "trusted_remaining_source": "not_wired",
        "quota_signal_contract_version": "0-no-numeric",
        "blocking_missing": ["per_model_remaining_units"],
        "limitation": "available/exhausted/cooldown states are observed runtime signals, not numeric remaining-quota measurements",
    }
    if run_id:
        signal["last_run_id"] = run_id
    if agent_id:
        trusted = trusted_remaining_quota_signal(agent_id, model)
        if trusted:
            signal.update(trusted)
            signal["reactive_fallback_source"] = source
            if run_id:
                signal["last_run_id"] = run_id
    return signal


def update_agent_room_quota_status(
    agent_id: str,
    model: str | None,
    quota_state: str,
    *,
    reason: str | None = None,
    cooldown_until: str | None = None,
    fallback_available: bool | None = None,
    active_model: str | None = None,
    run_id: str | None = None,
) -> None:
    """Project per-agent/per-model quota state into Agent Room status.

    This is the cross-run state Claude Code proposed: cooldown inside one CLI
    attempt is not enough. Dispatch and peers need a durable status plane keyed
    by (agent, model), while recovery remains automatic when a later call works.
    """
    state = load_agent_room_status()
    agents = state.setdefault("agents", {})
    agent_record = agents.setdefault(agent_id, {})
    if not isinstance(agent_record, dict):
        agent_record = {}
        agents[agent_id] = agent_record
    models = agent_record.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        agent_record["models"] = models
    key = quota_model_key(model)
    record: dict[str, Any] = {
        "quota_state": quota_state,
        "model": key,
        "updated_at": now_iso(),
        "quota_signal": reactive_quota_signal("agent_room_status model record", run_id, agent_id=agent_id, model=key),
    }
    if reason:
        record["reason"] = reason
    if cooldown_until:
        record["cooldown_until"] = cooldown_until
        record["estimated_recovery"] = cooldown_until
    if fallback_available is not None:
        record["fallback_available"] = bool(fallback_available)
    if active_model:
        record["active_model"] = active_model
    if run_id:
        record["last_run_id"] = run_id
    previous = models.get(key) if isinstance(models.get(key), dict) else {}
    if isinstance(previous, dict) and previous.get("first_notification_sent"):
        record["first_notification_sent"] = previous.get("first_notification_sent")
        record["first_notification_sent_at"] = previous.get("first_notification_sent_at")
    models[key] = record
    # Normalize expired per-model records so agent_room_status.json does not look
    # stale after cooldown expires. The runtime active check already ignores expired
    # cooldowns; this keeps the visible JSON consistent with that behavior.
    now_ts = datetime.now(timezone.utc).astimezone()
    for mkey, mrec in list(models.items()):
        if not isinstance(mrec, dict) or mrec.get("quota_state") not in {"exhausted", "cooldown"}:
            continue
        until = parse_iso_datetime(str(mrec.get("cooldown_until") or ""))
        if until and until <= now_ts:
            mrec["quota_state"] = "available"
            mrec.pop("cooldown_until", None)
            mrec.pop("reason", None)
            mrec.pop("estimated_recovery", None)
    active_exhausted_models: list[str] = []
    available_models: list[str] = []
    for mkey, mrec in models.items():
        if not isinstance(mrec, dict):
            continue
        active_exhausted = quota_record_is_active({
            "status": "depleted" if mrec.get("quota_state") == "exhausted" else mrec.get("status"),
            "cooldown_until": mrec.get("cooldown_until"),
        })
        if active_exhausted:
            active_exhausted_models.append(str(mkey))
        elif mrec.get("quota_state") == "available" and agent_model_counts_as_available(agent_id, str(mkey)):
            available_models.append(str(mkey))
    if (
        active_model
        and str(active_model) not in active_exhausted_models
        and str(active_model) not in available_models
        and agent_model_counts_as_available(agent_id, str(active_model))
    ):
        available_models.append(str(active_model))
    has_fallback = bool(available_models)
    agent_record["quota_state"] = (
        "fallback_active" if active_exhausted_models and has_fallback
        else "exhausted" if active_exhausted_models
        else "available"
    )
    agent_record["fallback_active"] = bool(active_exhausted_models and has_fallback)
    agent_record["active_exhausted_models"] = sorted(active_exhausted_models)
    agent_record["available_models"] = sorted(available_models)
    if active_model:
        agent_record["active_model"] = active_model
    agent_record["quota_signal"] = reactive_quota_signal(
        "agent_room_status agent model records",
        run_id,
        agent_id=agent_id,
        model=active_model or model,
    )
    agent_record["updated_at"] = now_iso()
    save_agent_room_status(state)


def quota_model_key(model: str | None) -> str:
    value = str(model or "").strip()
    return value or UNKNOWN_QUOTA_MODEL


def agent_model_counts_as_available(agent_id: str, model: str) -> bool:
    """Return whether a status-plane available model is still dispatchable."""
    if agent_id == "claude-code":
        if str(model or "").startswith(f"{EXTERNAL_DEEPSEEK_BACKEND}/"):
            return True
        return claude_model_allowed_by_policy(model, None)
    return True


def quota_record_is_active(record: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if not isinstance(record, dict) or record.get("status") != "depleted":
        return False
    until = parse_iso_datetime(str(record.get("cooldown_until") or ""))
    if not until:
        return True
    current = now or datetime.now(timezone.utc).astimezone()
    return until > current


def agent_quota_model_records(state: dict[str, Any], agent_id: str) -> dict[str, dict[str, Any]]:
    agent_record = (state.get("agents") or {}).get(agent_id)
    if not isinstance(agent_record, dict):
        return {}
    models = agent_record.get("models")
    if isinstance(models, dict):
        return {str(key): value for key, value in models.items() if isinstance(value, dict)}
    # Backward compatibility for the old agent-level state shape.
    if agent_record.get("status") == "depleted":
        return {quota_model_key(agent_record.get("model")): agent_record}
    return {}


def agent_quota_depleted_record(agent_id: str, model: str | None = None) -> dict[str, Any] | None:
    """Return the active quota depletion record for one bot/model, if present."""
    state = load_agent_quota_state()
    records = agent_quota_model_records(state, agent_id)
    now = datetime.now(timezone.utc).astimezone()
    if model is not None:
        record = records.get(quota_model_key(model))
        return record if quota_record_is_active(record, now) else None
    for record in records.values():
        if quota_record_is_active(record, now):
            return record
    return None


def agent_quota_is_depleted(agent_id: str, model: str | None = None) -> tuple[bool, str | None]:
    """Check if an agent/model quota is currently depleted."""
    record = agent_quota_depleted_record(agent_id, model)
    if not record:
        return False, None
    return True, str(record.get("reason") or "")


def mark_agent_quota_depleted(agent_id: str, reason: str, model: str | None = None) -> dict[str, Any]:
    """Record that one agent/model quota is depleted.

    The returned record includes ``notification_required`` when this depletion
    has not yet produced the per-bot/model user-visible notice.

    Alex clarified (2026-05-22): each bot/model pair should send one visible
    quota-exhausted notice. A different model under the same bot gets its own
    first notice; repeats for the same bot/model stay local until recovery or
    cooldown expiry.
    """
    state = load_agent_quota_state()
    now = datetime.now(timezone.utc).astimezone()
    until = now + timedelta(minutes=AGENT_QUOTA_SILENCE_COOLDOWN_MINUTES)
    agents = state.setdefault("agents", {})
    agent_record = agents.setdefault(agent_id, {})
    if not isinstance(agent_record, dict):
        agent_record = {}
        agents[agent_id] = agent_record
    models = agent_record.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        agent_record["models"] = models
    key = quota_model_key(model)
    previous = models.get(key) if isinstance(models.get(key), dict) else {}
    previous_active = quota_record_is_active(previous, now)
    notification_sent = bool(previous.get("first_notification_sent")) if previous_active else False
    record = {
        "status": "depleted",
        "reason": reason,
        "model": key,
        "depleted_at": now.isoformat(timespec="seconds"),
        "cooldown_until": until.isoformat(timespec="seconds"),
        "first_notification_sent": notification_sent,
    }
    if notification_sent:
        record["first_notification_sent_at"] = previous.get("first_notification_sent_at") or agent_record.get("first_notification_sent_at")
        record["first_notification_run_id"] = previous.get("first_notification_run_id") or agent_record.get("first_notification_run_id")
    models[key] = record
    agent_record.update({
        "status": "depleted",
        "reason": reason,
        "model": key,
        "depleted_at": record["depleted_at"],
        "cooldown_until": record["cooldown_until"],
    })
    save_agent_quota_state(state)
    update_agent_room_quota_status(
        agent_id,
        key,
        "exhausted",
        reason=reason,
        cooldown_until=record.get("cooldown_until"),
        fallback_available=False,
    )
    returned = dict(record)
    returned["notification_required"] = not notification_sent
    return returned


def mark_agent_quota_depleted_for_attempts(agent_id: str, attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Record quota depletion for each model that actually hit a quota reason."""
    records: list[dict[str, Any]] = []
    for attempt in attempts:
        if str(attempt.get("status") or "") not in {"failed", "skipped_cooldown"}:
            continue
        reason = str(attempt.get("reason") or "")
        if reason not in AGENT_QUOTA_DEPLETED_REASONS:
            continue
        model = quota_model_key(str(attempt.get("model") or ""))
        records.append(mark_agent_quota_depleted(agent_id, reason, model))
    return records


def mark_agent_quota_notification_sent(agent_id: str, model: str | None, run_id: str | None) -> None:
    """Mark that the per-bot/model quota notification has been sent."""
    state = load_agent_quota_state()
    agents = state.setdefault("agents", {})
    agent_record = agents.get(agent_id)
    if not isinstance(agent_record, dict):
        return
    models = agent_record.get("models")
    if isinstance(models, dict):
        key = quota_model_key(model)
        record = models.get(key)
        if isinstance(record, dict):
            record["first_notification_sent"] = True
            record["first_notification_sent_at"] = now_iso()
            record["first_notification_run_id"] = run_id
    agent_record["last_quota_notification_model"] = quota_model_key(model)
    agent_record["last_quota_notification_run_id"] = run_id
    save_agent_quota_state(state)
    status = load_agent_room_status()
    status_agent = ((status.get("agents") or {}).get(agent_id) or {})
    status_models = status_agent.get("models") if isinstance(status_agent, dict) else {}
    key = quota_model_key(model)
    status_record = status_models.get(key) if isinstance(status_models, dict) else None
    if isinstance(status_record, dict):
        status_record["first_notification_sent"] = True
        status_record["first_notification_sent_at"] = now_iso()
        status_record["first_notification_run_id"] = run_id
        save_agent_room_status(status)


def mark_agent_quota_recovered(
    agent_id: str,
    model: str | None = None,
    *,
    run_id: str | None = None,
    room_id: str | None = None,
) -> None:
    """Record that an agent/model quota has recovered.

    Notification state is scoped to the concrete bot/model record, so a future
    depletion of the same model can produce a new notice after recovery.
    """
    state = load_agent_quota_state()
    agents = state.setdefault("agents", {})
    previous = agents.get(agent_id)
    if not isinstance(previous, dict):
        return
    models = previous.get("models")
    if isinstance(models, dict) and model:
        key = quota_model_key(model)
        old_model_record = models.get(key)
        if isinstance(old_model_record, dict) and old_model_record.get("status") == "depleted":
            event = (
                model_recovery_projection_event(
                    agent_id,
                    key,
                    old_model_record,
                    run_id=run_id,
                    room_id=room_id,
                    source="agent_quota_state_success",
                )
                if run_id
                else None
            )
            models[key] = {
                "status": "available",
                "previous_reason": old_model_record.get("reason"),
                "model": key,
                "recovered_at": now_iso(),
            }
            if not any(quota_record_is_active(value) for value in models.values() if isinstance(value, dict)):
                previous["status"] = "available"
                previous["recovered_at"] = now_iso()
            save_agent_quota_state(state)
            if event:
                append_openclaw_main_projection_event(event)
            update_agent_room_quota_status(agent_id, key, "available", active_model=key, run_id=run_id)
        return
    if previous.get("status") == "depleted":
        key = quota_model_key(model or previous.get("model"))
        event = (
            model_recovery_projection_event(
                agent_id,
                key,
                previous,
                run_id=run_id,
                room_id=room_id,
                source="agent_quota_state_success",
            )
            if run_id
            else None
        )
        agents[agent_id] = {
            "status": "available",
            "previous_reason": previous.get("reason"),
            "model": model or previous.get("model"),
            "recovered_at": now_iso(),
        }
        save_agent_quota_state(state)
        if event:
            append_openclaw_main_projection_event(event)
        update_agent_room_quota_status(
            agent_id,
            model or previous.get("model"),
            "available",
            active_model=model or previous.get("model"),
            run_id=run_id,
        )


def is_collaboration_task(task: dict[str, Any]) -> bool:
    """Determine if a task is a collaboration task eligible for silence."""
    if bool(task.get("collaboration")):
        return True
    if is_internal_agent_room_task(task):
        return True
    if bool(task.get("collab_parent_agent_id")):
        return True
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    transport = str(source.get("transport") or "")
    if transport in INTERNAL_AGENT_ROOM_TRANSPORTS:
        return True
    return False


def quota_human_reason(reason: str | None) -> str:
    labels = {
        "usage_limit": "usage limit",
        "rate_limit": "rate limit / 429",
        "model_overloaded": "model overloaded",
        "model_unavailable": "model unavailable",
        "quota_depleted": "quota depleted",
    }
    return labels.get(str(reason or ""), str(reason or "model quota depleted"))


def make_quota_notice_fields(task: dict[str, Any], agent_id: str, depletion: dict[str, Any]) -> dict[str, Any]:
    model = quota_model_key(str(depletion.get("model") or ""))
    reason = quota_human_reason(str(depletion.get("reason") or ""))
    cooldown_until = str(depletion.get("cooldown_until") or "").strip()
    # Collect all currently depleted models for this agent to show in one notice.
    all_depleted = agent_quota_model_records(load_agent_quota_state(), agent_id)
    depleted_models = sorted({
        quota_model_key(str(rec.get("model") or ""))
        for rec in all_depleted.values()
        if isinstance(rec, dict) and quota_record_is_active(rec)
    })
    if len(depleted_models) > 1:
        model_list = "、".join(f"`{m}`" for m in depleted_models)
        models_text = f"以下模型额度已耗尽：{model_list}（{reason}）。"
    else:
        models_text = f"当前使用的模型 `{model}` 额度已耗尽（{reason}）。"
    tail = f" 暂定重试时间：{cooldown_until}。" if cooldown_until else ""
    body = (
        f"{agent_id} {models_text}"
        "这条是该机器人/模型本轮耗尽后的唯一提示；后续同一机器人同一模型在恢复前会自动静默，不再反复发额度错误。"
        "如果同一机器人切到另一个模型且那个模型也耗尽，会按那个模型再提示一次。"
        "如果有其他可用模型或其他 agent，房间会继续按降级/协作路径处理。"
        f"{tail}"
    )
    return {
        "kind": "status",
        "confidence": "high",
        "title": f"{agent_id} model quota depleted: {model}",
        "body": body,
        "blockers": ["agent_model_quota_depleted", str(depletion.get("reason") or "quota_depleted")],
        "telegram_projection_status": "user_visible_quota_exhausted",
        "quota_notice": {
            "agent_id": agent_id,
            "model": model,
            "reason": depletion.get("reason"),
            "cooldown_until": depletion.get("cooldown_until"),
            "notification_policy": "once_per_agent_model_until_recovered",
        },
    }


def make_quota_silenced_comment(task: dict[str, Any], agent_id: str, depletion: dict[str, Any] | None) -> dict[str, Any]:
    """Produce a local-only blocker comment when an agent/model is quota-silenced."""
    permissions = effective_permissions(task, agent_id)
    depletion = depletion or {}
    raw_reason = str(depletion.get("reason") or "quota_depleted")
    readable_reason = quota_human_reason(raw_reason)
    model = quota_model_key(str(depletion.get("model") or ""))
    cooldown_until = str(depletion.get("cooldown_until") or "").strip() or None
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "status",
        "confidence": "high",
        "title": f"{agent_id} silenced for collaboration task (quota depleted)",
        "body": f"{agent_id}/{model} 仍处于额度耗尽静默期（{readable_reason}），已记录本轮跳过；用户提示已在首次耗尽时发送，不再重复刷屏。",
        "blocked_reason": f"quota_depleted:{readable_reason}",
        "diagnostics": {
            "silence_reason": readable_reason,
            "model": model,
            "silence_scope": "collaboration_only",
            "auto_recovery": True,
            "cooldown_until": cooldown_until,
            "notification_policy": "already_notified_once_per_agent_model",
        },
        "blockers": ["agent_quota_depleted_collaboration_silenced"],
        "retryable_failure": {
            "schema": "openclaw.agent_room.retryable_provider_failure.v0",
            "status": "retryable",
            "agent_id": agent_id,
            "reason": raw_reason,
            "retry_after": cooldown_until,
            "cooldown_until": cooldown_until,
            "models": [model] if model else [],
            "source": "agent_task_runner.quota_silence_gate",
        },
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": False,
        "effective_permissions": permissions,
        "telegram_projection_status": "local_only_quota_silenced",
    }


def codex_ark_model_candidates(route_key: str) -> list[str]:
    """Return Ark Coding Plan candidates for a Codex fallback route.

    The route table is shared with Claude Code by design: one policy catalog,
    two execution adapters. Codex fallback uses direct_provider_lane instead of
    starting a new Claude runner/room turn.
    """
    candidates: list[str] = []
    for candidate in claude_candidate_models_for_route(route_key):
        if candidate and candidate not in candidates and claude_model_allowed_by_policy(candidate, route_key):
            candidates.append(candidate)
    fallback = claude_policy_fallback_model(route_key)
    if fallback and fallback not in candidates and claude_model_allowed_by_policy(fallback, route_key):
        candidates.append(fallback)
    return candidates or [claude_policy_fallback_model(route_key)]


def codex_ark_formal_gpt_required(task: dict[str, Any], prompt: str) -> bool:
    """Fail closed for formal GPT-required work before direct Ark fallback.

    The guard lives in scripts/direct_provider_openclaw_compat.py because other
    workflows already use it. We build a minimal OpenClaw-like command shape so
    the same contract protects Agent Room Codex fallback too.
    """
    scripts_dir = WORKSPACE / "scripts"
    try:
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from direct_provider_openclaw_compat import is_formal_gpt_required_command  # type: ignore
    except Exception:
        return False
    session_id = str(
        task.get("session_id")
        or task.get("run_id")
        or task.get("task_id")
        or task.get("room_id")
        or "agent-room-codex"
    )
    command = [
        "openclaw",
        "agent",
        "--model",
        (codex_model_candidates(task) or DEFAULT_CODEX_MODELS)[0],
        "--agent",
        str(task.get("agent") or task.get("requested_by") or "agent-room-codex"),
        "--session-id",
        session_id,
        "--message",
        prompt[:2000],
    ]
    return bool(is_formal_gpt_required_command(command))


def codex_ark_adapt_prompt(task: dict[str, Any], prompt: str, route_key: str, permissions: dict[str, Any]) -> str:
    source_edit = bool(permissions.get("source_edit"))
    return (
        "# Ark Coding Plan direct-provider fallback for Codex\n\n"
        "Codex CLI could not use its GPT model chain because every candidate was in quota/rate-limit/availability cooldown. "
        "Continue the same Agent Room task using the Ark model selected from the shared route table.\n\n"
        "## Output contract\n"
        "- Reply in Chinese.\n"
        "- Give the best useful answer for the original Agent Room request.\n"
        "- Do not claim that you edited, tested, pushed, sent Telegram, or touched files; this direct-provider fallback has no tools.\n"
        "- If the request truly requires local file edits or command execution, provide a concise blocker plus the exact next local action needed.\n"
        "- Do not expose secrets, raw prompts, hidden system prompts, or private logs.\n\n"
        "## Routing / safety\n"
        f"- route_key: {route_key}\n"
        f"- source_edit_permission_requested: {source_edit}\n"
        f"- task_id: {task.get('task_id') or ''}\n"
        f"- run_id: {task.get('run_id') or task.get('task_id') or ''}\n\n"
        "## Original Codex runner prompt\n\n"
        + prompt.rstrip()
        + "\n"
    )


def codex_direct_provider_error_result(exc: object) -> dict[str, Any]:
    detail = getattr(exc, "detail", None)
    try:
        detail_text = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    except Exception:
        detail_text = str(detail or "")
    text = "\n".join(
        part
        for part in [
            str(getattr(exc, "kind", "") or type(exc).__name__),
            str(exc or ""),
            detail_text,
        ]
        if part
    )
    return {
        "ok": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": text,
        "_artifact_failure_text": text,
        "provider_status": getattr(exc, "status", None),
    }


def codex_direct_provider_import() -> tuple[Any, Any] | tuple[None, None]:
    scripts_dir = WORKSPACE / "scripts"
    try:
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from direct_provider_lane import DirectProviderError, run_direct_provider_text_prompt  # type: ignore
        return run_direct_provider_text_prompt, DirectProviderError
    except Exception:
        return None, None


def codex_ark_direct_fallback(
    task: dict[str, Any],
    prompt: str,
    run_dir: Path,
    permissions: dict[str, Any],
    route_key: str,
    route_signals: dict[str, Any],
    gpt_attempts: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any], bool]:
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    if codex_ark_formal_gpt_required(task, prompt):
        body = codex_failure_body("codex", run_id, gpt_attempts) + "；该任务命中 formal-GPT-required 守卫，按策略不降级到 Ark。"
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": "formal_gpt_required_no_ark_fallback",
        }, body, {
            "kind": "risk",
            "confidence": "high",
            "title": "Codex GPT quota depleted; Ark fallback blocked by formal GPT guard",
            "body": body,
            "backend": "codex-cli",
            "model": next((str(a.get("model")) for a in gpt_attempts if a.get("status") != "skipped_cooldown"), None),
            "model_attempts": gpt_attempts,
            "blockers": ["formal_gpt_required_no_ark_fallback"],
            "telegram_projection_status": "suppressed_runner_failure",
        }, False

    run_direct_provider_text_prompt, DirectProviderError = codex_direct_provider_import()
    if run_direct_provider_text_prompt is None or DirectProviderError is None:
        body = codex_failure_body("codex", run_id, gpt_attempts) + "；Ark direct-provider fallback import 失败。"
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "ark_direct_provider_import_failed"}, body, {
            "kind": "risk",
            "confidence": "high",
            "title": "Codex fallback to Ark failed",
            "body": body,
            "backend": "ark-coding-plan-direct-provider",
            "model_attempts": gpt_attempts,
            "blockers": ["ark_direct_provider_import_failed"],
            "telegram_projection_status": "suppressed_runner_failure",
        }, False

    env, _loaded_env_files = load_agent_env()
    os.environ.update(env)
    candidates = codex_ark_model_candidates(route_key)
    attempts = list(gpt_attempts)
    ark_attempts: list[dict[str, Any]] = []
    fallback_prompt = codex_ark_adapt_prompt(task, prompt, route_key, permissions)
    last_result: dict[str, Any] = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no_ark_attempt"}
    cooldown_state_changed = False
    transient_reasons = {"usage_limit", "rate_limit", "model_overloaded", "model_unavailable", "timeout"}
    base_task_id = safe_run_id(f"codex-ark-fallback-{run_id or task.get('task_id') or 'agent-room'}")

    for model in candidates:
        active_until = cooldown_active_until(state, model)
        if active_until:
            skipped = {
                "model": model,
                "backend": "ark-coding-plan-direct-provider",
                "status": "skipped_cooldown",
                "reason": cooldown_skip_reason(state, model),
                "cooldown_until": active_until.isoformat(timespec="seconds"),
            }
            ark_attempts.append(skipped)
            attempts.append(skipped)
            continue
        try:
            result = run_direct_provider_text_prompt(
                prompt=fallback_prompt,
                task_id=f"{base_task_id}-{safe_run_id(model)}",
                task_type="agent_room_codex_ark_fallback",
                model=model,
                system="You are an Ark Coding Plan fallback for OpenClaw Codex Agent Room tasks. Follow the user's Chinese output contract exactly.",
                output_dir=run_dir / "codex-ark-direct" / safe_run_id(model),
                max_tokens=int(os.environ.get("AGENT_ROOM_CODEX_ARK_FALLBACK_MAX_TOKENS", "4096")),
                temperature=0.2,
                timeout=int(os.environ.get("AGENT_ROOM_CODEX_ARK_FALLBACK_TIMEOUT", "240")),
                agent_id="codex",
            )
            body = str(result.get("text") or "").strip()
            if not body:
                last_result = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "ark_direct_provider_empty_reply"}
                failure_reason = "model_unavailable"
                mark_codex_model_cooldown(state, model, failure_reason, last_result)
                cooldown_state_changed = True
                cooldown_until = (((state.get("models") or {}).get(model) or {}).get("cooldown_until") or "")
                failed = {"model": model, "backend": "ark-coding-plan-direct-provider", "status": "failed", "reason": failure_reason}
                if cooldown_until:
                    failed["cooldown_until"] = str(cooldown_until)
                ark_attempts.append(failed)
                attempts.append(failed)
                continue
            ok_result = {"ok": True, "exit_code": 0, "stdout": body, "stderr": ""}
            succeeded = {"model": model, "backend": "ark-coding-plan-direct-provider", "status": "completed", "ok": True, "reason": None, "output_dir": result.get("output_dir")}
            ark_attempts.append(succeeded)
            attempts.append(succeeded)
            mark_codex_model_recovered(state, model, run_id=run_id, room_id=str(task.get("room_id") or ""))
            cooldown_state_changed = True
            fields = {
                "kind": "info",
                "confidence": "high",
                "title": f"Codex fallback to Ark succeeded ({model})",
                "body": body,
                "backend": "ark-coding-plan-direct-provider",
                "model": model,
                "model_attempts": attempts,
                "model_fallback": {
                    "from": next((a.get("model") for a in gpt_attempts if a.get("status") != "skipped_cooldown"), None),
                    "to": model,
                    "path": "gpt->ark-direct-provider",
                    "reason": "all_gpt_candidates_depleted",
                    "route_key": route_key,
                    "route_signals": route_signals,
                },
            }
            return ok_result, body, fields, cooldown_state_changed
        except DirectProviderError as exc:  # type: ignore[misc]
            last_result = codex_direct_provider_error_result(exc)
            failure_reason = classify_codex_failure(last_result) or "model_unavailable"
            failed = {
                "model": model,
                "backend": "ark-coding-plan-direct-provider",
                "status": "failed",
                "ok": False,
                "reason": failure_reason,
                "provider_status": getattr(exc, "status", None),
                "kind": getattr(exc, "kind", None),
            }
            ark_attempts.append(failed)
            attempts.append(failed)
            if failure_reason in transient_reasons:
                mark_codex_model_cooldown(state, model, failure_reason, last_result)
                cooldown_state_changed = True
                cooldown_until = (((state.get("models") or {}).get(model) or {}).get("cooldown_until") or "")
                if cooldown_until:
                    failed["cooldown_until"] = str(cooldown_until)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last_result = {"ok": False, "exit_code": 1, "stdout": "", "stderr": f"ark_direct_provider_exception:{type(exc).__name__}"}
            failed = {"model": model, "backend": "ark-coding-plan-direct-provider", "status": "failed", "ok": False, "reason": "direct_provider_exception"}
            ark_attempts.append(failed)
            attempts.append(failed)
            break

    body = codex_failure_body("codex", run_id, attempts) + "；Ark direct-provider 候选也没有形成可用回复。"
    fields = {
        "kind": "risk",
        "confidence": "high",
        "title": "Codex fallback to Ark failed",
        "body": body,
        "blockers": ["codex_ark_direct_fallback_failed"],
        "backend": "ark-coding-plan-direct-provider",
        "model": next((str(a.get("model")) for a in ark_attempts if a.get("status") != "skipped_cooldown"), candidates[0] if candidates else None),
        "model_attempts": attempts,
        "model_fallback": {
            "from": next((a.get("model") for a in gpt_attempts if a.get("status") != "skipped_cooldown"), None),
            "to": None,
            "path": "gpt->ark-direct-provider",
            "reason": "all_gpt_candidates_depleted_but_ark_failed",
            "route_key": route_key,
            "route_signals": route_signals,
        },
        "telegram_projection_status": "suppressed_runner_failure",
    }
    return last_result, body, fields, cooldown_state_changed


def run_codex_with_fallback(task: dict[str, Any], prompt: str, run_dir: Path, permissions: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    run_id = str(task.get("run_id") or task.get("task_id") or "")
    models = codex_model_candidates(task)
    state = load_codex_model_state()
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no_codex_attempt"}
    cooldown_state_changed = False
    source_dir = source_scope_dir(task)
    sandbox = "workspace-write" if permissions.get("source_edit") else "read-only"
    fallback_failure_reasons = {"usage_limit", "rate_limit", "model_overloaded", "model_unavailable"}

    for index, model in enumerate(models):
        active_until = cooldown_active_until(state, model)
        if active_until:
            cooldown_reason = cooldown_skip_reason(state, model)
            attempts.append({
                "model": model,
                "status": "skipped_cooldown",
                "reason": cooldown_reason,
                "cooldown_until": active_until.isoformat(timespec="seconds"),
            })
            quota_projection_state = "exhausted" if cooldown_reason in AGENT_QUOTA_DEPLETED_REASONS else "cooldown"
            update_agent_room_quota_status(
                "codex",
                model,
                quota_projection_state,
                reason=cooldown_reason,
                cooldown_until=active_until.isoformat(timespec="seconds"),
                fallback_available=any(
                    not cooldown_active_until(state, candidate)
                    for candidate in models[index + 1 :]
                ),
                run_id=run_id,
            )
            continue
        out_file = run_dir / f"codex.{safe_run_id(model)}.last-message.md"
        final_out_file = run_dir / "codex.last-message.md"
        try:
            out_file.unlink()
        except FileNotFoundError:
            pass
        cmd = [
            CODEX_CMD, "-a", "never", "exec",
            "--model", model,
            "--sandbox", sandbox,
            "--output-last-message", str(out_file),
            "--skip-git-repo-check",
        ]
        if CODEX_NATIVE_JSON_EVENTS_ENABLED:
            cmd.append("--json")
        cmd.append("-")
        result = run_cmd(cmd, timeout=600, input_text=prompt, cwd=source_dir)
        event_log_path = write_codex_native_event_log(run_dir, model, str(result.get("stdout") or ""))
        last_result = result
        failure_reason = None if result.get("ok") else classify_codex_failure(result)
        attempts.append({
            "model": model,
            "status": "completed" if result.get("ok") else "failed",
            "ok": bool(result.get("ok")),
            "exit_code": result.get("exit_code"),
            "reason": failure_reason,
            "output_file": str(out_file),
            "native_event_log": str(event_log_path) if event_log_path else None,
        })
        if result.get("ok") and out_file.exists() and out_file.stat().st_size:
            body = out_file.read_text(encoding="utf-8", errors="replace")
            try:
                shutil.copyfile(out_file, final_out_file)
            except Exception:
                pass
            fields: dict[str, Any] = {
                "model": model,
                "backend": "codex-cli",
                "model_attempts": attempts,
                "native_json_events": bool(event_log_path),
            }
            if model != models[0]:
                fields["model_fallback"] = {"from": models[0], "to": model, "reason": "previous_model_unavailable"}
            mark_codex_model_recovered(state, model, run_id=run_id, room_id=str(task.get("room_id") or ""))
            update_agent_room_quota_status("codex", model, "available", active_model=model, run_id=run_id)
            cooldown_state_changed = True
            if cooldown_state_changed:
                save_codex_model_state(state)
            return result, body, fields
        if failure_reason in {"usage_limit", "rate_limit", "model_overloaded", "model_unavailable"}:
            mark_codex_model_cooldown(state, model, failure_reason, result)
            cooldown_state_changed = True
            cooldown_until = (((state.get("models") or {}).get(model) or {}).get("cooldown_until") or "")
            if cooldown_until:
                attempts[-1]["cooldown_until"] = str(cooldown_until)
            update_agent_room_quota_status(
                "codex",
                model,
                "exhausted",
                reason=failure_reason,
                cooldown_until=str(cooldown_until),
                fallback_available=any(
                    not cooldown_active_until(state, candidate)
                    for candidate in models[index + 1 :]
                ),
                run_id=run_id,
            )
            continue
        # Non-transient execution failures usually mean the runner/task is wrong;
        # do not burn through more models for the same bad invocation.
        break

    def transiently_depleted() -> bool:
        # If all GPT candidates were skipped by cooldown or failed with
        # transient quota/availability errors, we can try Ark as a lower-priority
        # continuation path.
        for attempt in attempts:
            if attempt.get("status") == "skipped_cooldown":
                continue
            if attempt.get("status") != "failed":
                return False
            reason = attempt.get("reason")
            if reason not in fallback_failure_reasons:
                return False
        return True

    if CODEX_ARK_FALLBACK_ENABLED and codex_ark_fallback_allowed_for_task(task) and transiently_depleted():
        _route_model, route_key, route_signals = claude_route_table_model(
            task,
            {"source_edit": bool(permissions.get("source_edit"))},
        )
        route_signals = dict(route_signals)
        route_signals["codex_fallback_chain"] = models
        route_signals["codex_model_fallback_from"] = [
            a.get("model") for a in attempts if a.get("status") != "skipped_cooldown"
        ]
        ark_result, ark_body, ark_fields, ark_cooldown_changed = codex_ark_direct_fallback(
            task,
            prompt,
            run_dir,
            permissions,
            route_key,
            route_signals,
            attempts,
            state,
        )
        cooldown_state_changed = cooldown_state_changed or ark_cooldown_changed
        if cooldown_state_changed:
            save_codex_model_state(state)
        ark_attempts = [
            attempt for attempt in (ark_fields.get("model_attempts") or [])
            if isinstance(attempt, dict)
        ]
        ark_external_eligible = any(
            str(attempt.get("backend") or "") == "ark-coding-plan-direct-provider"
            and (
                str(attempt.get("status") or "") == "skipped_cooldown"
                or str(attempt.get("reason") or "") in (fallback_failure_reasons | {"timeout"})
            )
            for attempt in ark_attempts
        )
        if not ark_result.get("ok") and ark_external_eligible:
            external_fallback = run_external_deepseek_worker_fallback(
                task,
                prompt,
                run_dir,
                ark_attempts or attempts,
                {
                    "model_route_key": route_key,
                    "source_edit": bool(permissions.get("source_edit")),
                },
                agent_id="codex",
            )
            if external_fallback is not None:
                external_result, external_body, external_fields = external_fallback
                external_fields = dict(external_fields)
                external_fields["model_fallback"] = {
                    "from": next((a.get("model") for a in attempts if a.get("status") != "skipped_cooldown"), None),
                    "to": external_fields.get("model"),
                    "path": "gpt->ark-direct-provider->external-deepseek",
                    "reason": "codex_and_ark_candidates_depleted",
                    "route_key": route_key,
                    "route_signals": route_signals,
                }
                return external_result, external_body, external_fields
        return ark_result, ark_body, ark_fields

    if cooldown_state_changed:
        save_codex_model_state(state)
    body = codex_failure_body("codex", run_id, attempts)
    fields = {
        "kind": "risk",
        "confidence": "high",
        "title": "Codex CLI execution blocked",
        "body": body,
        "blockers": ["codex_cli_failed"],
        # This is runner/control-plane state, not an agent contribution. Keep it
        # in local comments/artifacts for diagnosis, but never let the Telegram
        # projection layer post cooldown/model-attempt dumps into Alex's room.
        "telegram_projection_status": "suppressed_runner_failure",
        "backend": "codex-cli",
        "model": next((str(a.get("model")) for a in attempts if a.get("status") != "skipped_cooldown"), models[0] if models else None),
        "model_attempts": attempts,
    }
    return last_result, body, fields


def read_json_if_exists(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(value, dict):
                return value
    except Exception:
        return {}
    return {}


def load_room_policy() -> dict[str, Any]:
    for path in [
        ROOM / "telegram-room-bindings.json",
        ROOT / "telegram-room-bindings.json",
    ]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                return {}
    return {}


def effective_permissions(task: dict[str, Any], agent_id: str) -> dict[str, bool]:
    permissions = {
        "source_edit": False,
        "telegram_send": False,
        "notion_publish": False,
        "github_push": False,
        "secrets_access": False,
        "global_state_change": False,
        "quality_surface_change": False,
    }
    raw = task.get("permissions")
    if isinstance(raw, dict):
        for key, value in raw.items():
            permissions[str(key)] = bool(value)
    policy = load_room_policy().get("agent_write_policy") or {}
    if isinstance(policy, dict) and policy.get("status") == "enabled":
        if agent_id in set(policy.get("source_edit_enabled_for") or []):
            permissions["source_edit"] = True
        if agent_id in set(policy.get("controlled_global_state_change_for") or []):
            permissions["global_state_change"] = True
    permissions["secrets_access"] = False
    permissions["github_push"] = False
    permissions["telegram_send"] = False
    # Internal peer/bot follow-ups are first-class room collaboration turns.
    # Do not downgrade them to read-only merely because they came from another
    # agent. Capability is bounded by task permissions, room write policy, and
    # safety gates; mentions only choose first response ownership.
    return permissions


def is_internal_agent_room_task(task: dict[str, Any]) -> bool:
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    return (
        str(source.get("transport") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS
        or str(task.get("requested_by") or "") in INTERNAL_AGENT_ROOM_TRANSPORTS
        or str(task.get("lane") or "") in {"peer_collaboration_followup", "agent_to_agent_mention"}
    )


def codex_ark_fallback_allowed_for_task(task: dict[str, Any]) -> bool:
    if CODEX_ARK_FALLBACK_SCOPE in {"all", "true", "1", "always", "enable", "enabled", "on"}:
        return True
    if CODEX_ARK_FALLBACK_SCOPE in {
        "collaboration_only",
        "collab_only",
        "collaboration",
        "collab",
    }:
        return (
            bool(task.get("collaboration"))
            or is_internal_agent_room_task(task)
            or bool(task.get("collab_parent_agent_id"))
        )
    if CODEX_ARK_FALLBACK_SCOPE in {"0", "false", "off", "no", "never", "disabled"}:
        return False
    # Backward-compatible: unknown values default to all-tasks behavior.
    return True


def source_scope_dir(task: dict[str, Any]) -> Path:
    raw = task.get("source_scope_dir") or task.get("scope_dir") or os.environ.get("AGENT_ROOM_SOURCE_SCOPE_DIR")
    candidate = Path(str(raw)).expanduser() if raw else WORKSPACE
    if not candidate.is_absolute():
        candidate = WORKSPACE / candidate
    candidate = candidate.resolve()
    workspace = WORKSPACE.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return workspace
    return candidate if candidate.exists() and candidate.is_dir() else workspace


def task_brief_text(task: dict[str, Any]) -> str:
    brief_path = task.get("brief_path")
    if not brief_path:
        return ""
    p = Path(str(brief_path))
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def task_user_message(task: dict[str, Any]) -> str:
    """Extract the human/agent-visible request from a generated room task brief."""
    brief = task_brief_text(task)
    for marker in ("## User message", "## Agent message", "## Visible source message"):
        if marker not in brief:
            continue
        after = brief.split(marker, 1)[1]
        if "\n## " in after:
            after = after.split("\n## ", 1)[0]
        return after.strip()
    return brief.strip()


def claude_room_run_spec(task: dict[str, Any], permissions: dict[str, bool], artifact_scope_dir: Path) -> dict[str, Any]:
    """Build Claude Code's neutral run spec from task permissions.

    This is not a Claude behavior profile. It is a thin translation from the
    task permission envelope to native Claude CLI parameters. There is no
    keyword gate here. Model selection uses the route table when enabled,
    otherwise falls back to CLAUDE_ARK_DEFAULT_MODEL.
    """
    source_edit = bool(permissions.get("source_edit"))
    native_permission_mode = "acceptEdits" if source_edit else "dontAsk"
    if claude_model_routing_enabled():
        route_model, route_key, _signals = claude_route_table_model(task, {"source_edit": source_edit})
    else:
        route_model = claude_default_model()
        route_key = "default_fallback"
    return {
        "source_edit": source_edit,
        "claude_cli_permission_mode": native_permission_mode,
        "scope_dir": source_scope_dir(task),
        "model": route_model,
        "model_route_key": route_key,
        "tools": None if source_edit else "Read,LS,Glob,Grep",
        "disallowed_tools": None if source_edit else "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch",
        "effort": os.environ.get("AGENT_ROOM_CLAUDE_EFFORT", "max"),
        "timeout": int(os.environ.get("AGENT_ROOM_CLAUDE_TIMEOUT", "300")),
    }


def agent_status(agent_id: str) -> dict[str, Any]:
    if agent_id == "codex":
        version = run_cmd([CODEX_CMD, "--version"])
        login = run_cmd([CODEX_CMD, "login", "status"])
        login_text = login["stdout"] or login["stderr"]
        logged_in = login["ok"] or ("Logged in" in login_text)
        return {
            "agent_id": agent_id,
            "command": "codex",
            "installed": version["ok"],
            "version": version["stdout"] or version["stderr"],
            "logged_in": logged_in,
            "login_status": login_text,
            "ready": bool(version["ok"] and logged_in),
        }
    if agent_id == "claude-code":
        version = run_cmd([CLAUDE_CMD, "--version"])
        env, loaded_env_files = load_agent_env()
        has_ark_key = bool(env.get("VOLCANO_ENGINE_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"))
        entry_exists = CLAUDE_ARK_RUNNER.exists()
        ready = bool(version["ok"] and entry_exists and has_ark_key)
        if not entry_exists:
            login_status = "ark_claude_runner_missing"
        elif not has_ark_key:
            login_status = "ark_coding_plan_key_missing"
        else:
            login_status = "ark_coding_plan_ready"
        return {
            "agent_id": agent_id,
            "command": "claude-code via ark-coding-plan",
            "installed": version["ok"],
            "version": version["stdout"] or version["stderr"],
            "backend": "ark-coding-plan-official-claude-endpoint",
            "model": claude_default_model(),
            "model_selection": "Claude Code agent model selector catalog; latest GLM/MiniMax/Kimi plus DeepSeek, explicit task override still wins after policy gates",
            "effort_policy": CLAUDE_ARK_EFFORT_POLICY,
            "effective_effort": "max",
            "native_claude_permission_modes": ["acceptEdits", "dontAsk"],
            "legacy_selector_policy": "ignored_for_claude_code_agent_room; effective boundary is native Claude --permission-mode plus allowed/disallowed tools",
            "auto_model_routing": {
                "enabled": claude_model_routing_enabled(),
                "scope": "claude_code_agent_model_selection_only",
                "policy": CLAUDE_MODEL_POLICY,
                "policy_file": str(CLAUDE_MODEL_POLICY_FILE),
                "selection": "catalog-selected candidate from latest GLM/MiniMax/Kimi plus DeepSeek; Doubao Pro only for low_stakes_formatting",
            },
            "credential_loaded": has_ark_key,
            "credential_sources_count": len(loaded_env_files),
            "claude_ark_runner": str(CLAUDE_ARK_RUNNER),
            "entry_exists": entry_exists,
            "login_status": login_status,
            "ready": ready,
        }
    return {"agent_id": agent_id, "ready": False, "login_status": "unsupported_agent"}


def comment_path(agent_id: str) -> Path:
    if agent_id == "claude-code":
        return COMMENT_ROOT / "claude.jsonl"
    return COMMENT_ROOT / f"{agent_id}.jsonl"


def update_task_manifest_after_results(task_path: Path, task: dict[str, Any], results: list[dict[str, Any]], result_path: Path) -> None:
    """Close a task manifest after local runner results are written.

    The immutable `tasks.jsonl` ledger records creation, but the per-task
    manifest is the operational status card used by later inspections. Without
    this reconciler, a task can have comments/artifacts while still looking
    queued, causing duplicate checks and slow follow-up.
    """
    if not task_path.exists():
        return
    current = read_json(task_path)
    targets = list(current.get("target_agents") or task.get("target_agents") or [])
    previous_summary = current.get("runner_summary") if isinstance(current.get("runner_summary"), dict) else {}
    previous_completed = [str(x) for x in (previous_summary.get("completed_agents") or []) if str(x)]
    previous_blocked = [str(x) for x in (previous_summary.get("blocked_agents") or []) if str(x)]
    previous_failed = [str(x) for x in (previous_summary.get("failed_agents") or []) if str(x)]
    previous_retryable = [str(x) for x in (previous_summary.get("retryable_agents") or []) if str(x)]
    retryable_records = [
        r
        for r in results
        if r.get("retryable")
        or (
            isinstance(r.get("comment"), dict)
            and isinstance((r.get("comment") or {}).get("retryable_failure"), dict)
        )
    ]
    retryable_agents = list(dict.fromkeys([*previous_retryable, *[str(r.get("agent_id")) for r in retryable_records]]))
    retryable_set = set(retryable_agents)
    comment_agents = [str(r.get("agent_id")) for r in results if r.get("comment_written")]
    result_agents = list(dict.fromkeys([*previous_completed, *[agent for agent in comment_agents if agent not in retryable_set]]))
    blocked_agents = list(dict.fromkeys([*previous_blocked, *[str(r.get("agent_id")) for r in results if r.get("blocked")]]))
    failed_agents = [
        str(r.get("agent_id"))
        for r in results
        if r.get("executed")
        and str(r.get("agent_id")) not in retryable_set
        and isinstance(r.get("result"), dict)
        and not r["result"].get("ok")
    ]
    failed_agents = list(dict.fromkeys([*previous_failed, *failed_agents]))
    unique_paths = list(dict.fromkeys([*(current.get("result_paths") or []), relative_to_root(result_path)]))
    for agent_id in result_agents:
        unique_paths.append(relative_to_root(comment_path(agent_id)))
    unique_paths = list(dict.fromkeys(unique_paths))
    completed_set = set(result_agents) | set(blocked_agents) | set(failed_agents) | set(retryable_agents)
    target_set = set(targets)
    if target_set and target_set.issubset(completed_set):
        if blocked_agents:
            new_status = "blocked"
        elif failed_agents:
            new_status = "failed"
        elif retryable_agents:
            new_status = "retryable"
        else:
            new_status = "completed"
    elif completed_set:
        new_status = "partial_failed" if failed_agents or blocked_agents else "retryable" if retryable_agents else "partial"
    else:
        new_status = "blocked" if blocked_agents else str(current.get("status") or "queued")
    retryable_failures = previous_summary.get("retryable_failures") if isinstance(previous_summary.get("retryable_failures"), dict) else {}
    retryable_failures = dict(retryable_failures)
    for record in retryable_records:
        agent_id = str(record.get("agent_id") or "")
        comment = record.get("comment") if isinstance(record.get("comment"), dict) else {}
        retryable_failure = comment.get("retryable_failure") if isinstance(comment.get("retryable_failure"), dict) else {}
        if agent_id and retryable_failure:
            retryable_failures[agent_id] = retryable_failure
    retry_after = retry_after_from_records([
        value
        for value in retryable_failures.values()
        if isinstance(value, dict)
    ])
    next_summary = dict(previous_summary)
    next_summary.update({
        "completed_agents": sorted(set(result_agents)),
        "blocked_agents": sorted(set(blocked_agents)),
        "failed_agents": sorted(set(failed_agents)),
        "retryable_agents": sorted(set(retryable_agents)),
        "retryable_failures": retryable_failures,
        "retry_after": retry_after,
        "targets": targets,
        "degraded_quorum": bool(target_set and not target_set.issubset(completed_set)),
    })
    current["runner_summary"] = next_summary
    sync_collaboration_from_ledger_snapshot(current)
    if new_status == "retryable":
        quality_gate = {
            "status": "retryable_provider_failure",
            "reason": "provider_quota_or_cooldown",
            "retryable_agents": sorted(set(retryable_agents)),
            "retry_after": retry_after,
        }
    else:
        quality_gate = collaboration_quality_gate(current, targets, completed_set)
    next_summary["collaboration_quality_gate"] = quality_gate
    current.update(
        {
            "status": new_status,
            "updated_at": now_iso(),
            "result_paths": unique_paths,
            "runner_result_path": relative_to_root(result_path),
            "runner_summary": next_summary,
            "quality_gate_status": quality_gate.get("status"),
        }
    )
    if retry_after and new_status == "retryable":
        current["retry_after"] = retry_after
        current["cooldown_until"] = retry_after
    if quality_gate.get("status") in {"needs_collaboration_review", "needs_collaboration_repair", "degraded_quorum"}:
        current["review_status"] = quality_gate.get("status")
    current.setdefault("heartbeat", {})["last_seen_at"] = now_iso()
    sync_collaboration_from_ledger_snapshot(current)
    write_json(task_path, current)
    canonical_manifest = str(task.get("canonical_manifest_path") or task.get("_canonical_manifest_path") or "").strip()
    if canonical_manifest:
        canonical_path = Path(canonical_manifest)
        if not canonical_path.is_absolute():
            canonical_path = ROOT / canonical_path
        if canonical_path.exists() and canonical_path.resolve() != task_path.resolve():
            update_task_manifest_after_results(canonical_path, read_json(canonical_path), results, result_path)


def make_blocked_comment(task: dict[str, Any], agent_id: str, status: dict[str, Any]) -> dict[str, Any]:
    permissions = effective_permissions(task, agent_id)
    reason = str(status.get("login_status") or "agent_not_ready")
    if not status.get("installed"):
        reason = "agent_cli_not_installed"
    elif status.get("login_status") == "unsupported_agent":
        reason = "unsupported_agent"
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "status",
        "confidence": "high",
        "title": f"{agent_id} task execution not ready",
        "body": "\u8fd9\u4e2a agent \u7684\u672c\u5730\u6267\u884c\u540e\u53f0\u8fd8\u6ca1\u51c6\u5907\u597d\u3002\u6211\u5df2\u7ecf\u628a\u8fd9\u6b21\u8bf7\u6c42\u8bb0\u5f55\u4e3a\u963b\u585e\u72b6\u6001\uff0c\u4e0d\u4f1a\u5728\u8fd9\u91cc\u53cd\u590d\u91cd\u8bd5\uff1b\u7b49\u672c\u5730\u6267\u884c\u540e\u53f0\u4fee\u597d\u540e\u518d\u7ee7\u7eed\u3002",
        "blocked_reason": reason,
        "diagnostics": {
            "installed": bool(status.get("installed")),
            "ready": bool(status.get("ready")),
            "backend": status.get("backend") or status.get("command"),
            "model": status.get("model"),
            "login_status_preview": str(status.get("login_status") or "")[:240],
        },
        "blockers": [reason],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": bool(permissions.get("source_edit") or permissions.get("global_state_change")),
        "effective_permissions": permissions,
    }


def make_collaboration_claim_blocked_comment(task: dict[str, Any], agent_id: str, ledger: dict[str, Any]) -> dict[str, Any]:
    permissions = effective_permissions(task, agent_id)
    failures = collaboration_claim_failure_reasons(ledger)
    reason = failures[0] if failures else "collaboration_claim_failed"
    return {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": "risk",
        "confidence": "high",
        "title": f"{agent_id} collaboration claim blocked",
        "body": "协作账本没有授予这个 agent 当前 work item 的所有权，所以本轮不继续执行，避免重复工作或污染协作账本。",
        "blocked_reason": reason,
        "diagnostics": {
            "work_item_id": ledger.get("work_item_id"),
            "ledger_failures": failures,
        },
        "blockers": ["collaboration_claim_failed", reason],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": bool(permissions.get("source_edit") or permissions.get("global_state_change")),
        "effective_permissions": permissions,
    }


def claude_resolved_run_spec(task: dict[str, Any], permissions: dict[str, bool], artifact_scope_dir: Path) -> dict[str, Any]:
    return enforce_claude_effort_policy(
        with_claude_effort_override(
            task,
            with_claude_model_override(
                task,
                claude_room_run_spec(task, permissions, artifact_scope_dir),
            ),
        ),
        task=task,
    )


def claude_runtime_model_candidates(task: dict[str, Any], run_spec: dict[str, Any]) -> list[str]:
    """Return ordered Ark model attempts for one Claude Code Agent Room run.

    Catalog routing picks the first model for the route. Runtime execution needs
    a second, bounded circuit breaker: if that specific Ark model is in quota
    cooldown / unavailable, try the next policy candidate instead of re-running
    the same failed model or spinning another room round. Explicit per-task model
    overrides still mean "try this model only"; policy-block replacements may
    continue through the route catalog because the requested model was already
    rejected by the local policy gate.
    """
    selected = str(run_spec.get("model") or claude_default_model()).strip()
    route_key = str(run_spec.get("model_route_key") or "plain_chat")
    explicit_source = str(run_spec.get("model_override_source") or "")
    explicit_task_override = explicit_source.startswith("task.")
    candidates: list[str] = []
    for value in [selected]:
        if value and value not in candidates and claude_model_allowed_by_policy(value, route_key):
            candidates.append(value)
    if not explicit_task_override:
        for candidate in claude_candidate_models_for_route(route_key):
            if candidate and candidate not in candidates and claude_model_allowed_by_policy(candidate, route_key):
                candidates.append(candidate)
        fallback = claude_policy_fallback_model(route_key)
        if fallback and fallback not in candidates and claude_model_allowed_by_policy(fallback, route_key):
            candidates.append(fallback)
    return candidates or [claude_policy_fallback_model(route_key)]


def claude_runtime_failure_body(run_id: str | None, attempts: list[dict[str, Any]]) -> str:
    compact: list[str] = []
    for attempt in attempts:
        model = attempt.get("model")
        status = attempt.get("status")
        reason = attempt.get("reason") or f"exit_{attempt.get('exit_code')}"
        if status == "skipped_cooldown":
            compact.append(f"{model}=cooldown到{attempt.get('cooldown_until')}")
        else:
            compact.append(f"{model}={reason}")
    return (
        "Claude Code 本轮可用 Ark 模型候选没有形成正常回复，已拦截原始 CLI 日志，避免把 prompt/上下文刷进群。"
        f"run_id: {run_id or 'unknown'}。模型尝试：" + "；".join(compact[:8])
    )


def claude_karpathy_skill_available() -> bool:
    """Return whether the Karpathy Claude Code plugin is installed locally."""
    return (
        CLAUDE_KARPATHY_PLUGIN_DIR.is_dir()
        and (CLAUDE_KARPATHY_PLUGIN_DIR / ".claude-plugin" / "plugin.json").is_file()
        and (CLAUDE_KARPATHY_PLUGIN_DIR / "skills" / "karpathy-guidelines" / "SKILL.md").is_file()
    )


def claude_karpathy_should_apply(task: dict[str, Any], permissions: dict[str, bool], run_spec: dict[str, Any]) -> bool:
    """Apply Karpathy coding discipline automatically for programming work.

    Claude Code currently runs with --bare for the Ark/sanitizer boundary, so we
    explicitly load the plugin dir and add an activation hint for tasks that are
    likely code writing/review/refactor/test/patch work.  This keeps ordinary
    chat lean while making the skill automatic for coding work.
    """
    if bool(permissions.get("source_edit")) or bool(run_spec.get("source_edit")):
        return True
    text = " ".join([
        task_user_message(task),
        str(task.get("lane") or ""),
        str(task.get("requested_by") or ""),
        json.dumps(task.get("collaboration") or {}, ensure_ascii=False) if isinstance(task.get("collaboration"), dict) else "",
    ]).lower()
    markers = (
        "code", "coding", "program", "bug", "fix", "patch", "test", "smoke", "lint", "typecheck",
        "refactor", "review", "pr", "github", "repo", "source", "implementation",
        "代码", "编程", "实现", "修复", "补丁", "测试", "回归", "重构", "审查", "仓库", "源码",
    )
    return any(marker in text for marker in markers)


def run_claude_code_ark_once(task: dict[str, Any], prompt: str, run_dir: Path, permissions: dict[str, bool]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    env, _loaded_env_files = load_agent_env()
    artifact_scope_dir = run_dir / "claude-code-scope"
    artifact_scope_dir.mkdir(parents=True, exist_ok=True)
    run_spec = claude_resolved_run_spec(task, permissions, artifact_scope_dir)
    source_edit = bool(run_spec.get("source_edit"))
    cli_permission_mode = str(run_spec.get("claude_cli_permission_mode") or ("acceptEdits" if source_edit else "dontAsk"))
    scope_dir = Path(str(run_spec["scope_dir"]))
    selected_model = str(run_spec.get("model") or claude_default_model())
    model_routing_advisory = claude_model_routing_advisory(task, run_spec)
    karpathy_available = claude_karpathy_skill_available()
    karpathy_apply = karpathy_available and claude_karpathy_should_apply(task, permissions, run_spec)
    brief_path = run_dir / "claude-code.brief.md"
    write_json(artifact_scope_dir / "task.pointer.json", {
        "schema": "openclaw.agent_room.claude_code_scope_pointer.v0",
        "room_id": task.get("room_id"),
        "task_id": task.get("task_id"),
        "run_id": task.get("run_id"),
        "brief_path": task.get("brief_path"),
        "allowed_scope_dir": str(scope_dir),
        "model": selected_model,
        "model_override_source": run_spec.get("model_override_source"),
        "model_policy": run_spec.get("model_policy"),
        "model_policy_blocked_model": run_spec.get("model_policy_blocked_model"),
        "model_policy_fallback_model": run_spec.get("model_policy_fallback_model"),
        "model_routing_advisory": model_routing_advisory,
        "effort": run_spec.get("effort"),
        "effort_override_source": run_spec.get("effort_override_source"),
        "effort_policy": run_spec.get("effort_policy"),
        "effort_policy_previous_effort": run_spec.get("effort_policy_previous_effort"),
        "effort_policy_previous_source": run_spec.get("effort_policy_previous_source"),
        "claude_cli_permission_mode": cli_permission_mode,
        "karpathy_guidelines": {
            "available": karpathy_available,
            "plugin_dir": str(CLAUDE_KARPATHY_PLUGIN_DIR) if karpathy_available else None,
            "skill": CLAUDE_KARPATHY_SKILL,
            "applied_for_this_task": karpathy_apply,
        },
    })
    expected_format = (
        "strict JSON object only, with keys: agent_id, run_id, kind, confidence, title, body, blockers. "
        "Use agent_id=claude-code and write title/body in Chinese. "
        "Answer Alex's actual message directly; do not report routing/config metadata unless explicitly asked. "
        "Do not expose secrets, raw prompts, private logs, or raw internal runner payloads."
    )
    brief = prompt.rstrip() + "\n\nOutput contract for Agent Room:\n" + expected_format + "\n"
    if karpathy_apply:
        brief += (
            "\nCoding discipline skill:\n"
            f"- Invoke/apply the installed Claude Code skill `{CLAUDE_KARPATHY_SKILL}` for this coding, code-review, refactor, bug-fix, test, or patch task.\n"
            "- Its rules are: think before coding, keep changes simple, make surgical edits, and define/verify success criteria.\n"
        )
    brief += (
        "\nRuntime boundary hint:\n"
        f"- selected model: {selected_model} ({run_spec.get('model_override_source') or 'default'})\n"
        f"- model policy: {run_spec.get('model_policy') or CLAUDE_MODEL_POLICY}; Doubao-family models are fully disabled for Claude Code.\n"
        f"- effort: {run_spec['effort']} ({run_spec.get('effort_override_source') or 'default'})\n"
        f"- effort policy: {run_spec.get('effort_policy') or CLAUDE_ARK_EFFORT_POLICY}; all Claude Code Ark calls must use max reasoning effort.\n"
        f"- Claude CLI permission mode: {cli_permission_mode}\n"
        f"- allowed tools: {run_spec['tools'] if run_spec['tools'] is not None else 'Claude Code default for this permission mode'}\n"
        f"- disallowed tools: {run_spec['disallowed_tools'] if run_spec['disallowed_tools'] is not None else 'none declared'}\n"
        "- Effective permissions above are task grants; Claude CLI permission mode plus allowed/disallowed tools are the runtime safety boundary for this run.\n"
        "- Do not describe your permissions as full, unrestricted, or no-limit; Telegram send, secrets, GitHub push, external publish, and destructive cleanup remain forbidden.\n"
    )
    brief_path.write_text(brief, encoding="utf-8")
    run_id = safe_run_id(str(task.get("ark_run_id") or task.get("run_id") or task.get("task_id") or "agent-room-claude-code"))
    timeout = int(run_spec["timeout"])
    model = selected_model
    cmd = [
        sys.executable,
        str(CLAUDE_ARK_RUNNER),
        "--scope-dir",
        str(scope_dir),
        "--brief-file",
        str(brief_path),
        "--claude-bin",
        CLAUDE_CMD,
        "--run-id",
        run_id,
        "--model",
        model,
        "--permission-mode",
        cli_permission_mode,
        "--expected-format",
        expected_format,
        "--timeout",
        str(timeout),
        "--bare",
        "--effort",
        str(run_spec["effort"]),
    ]
    if karpathy_available:
        cmd.extend(["--plugin-dir", str(CLAUDE_KARPATHY_PLUGIN_DIR)])
    if run_spec["tools"] is not None:
        cmd.extend(["--tools", str(run_spec["tools"])])
    if run_spec["disallowed_tools"] is not None:
        cmd.extend(["--disallowed-tools", str(run_spec["disallowed_tools"])])
    # Agent Room chat is not a coding-task delivery. It uses the Claude runner
    # directly instead of the heavier coding-task artifact gate. The safety
    # boundary here is: parse the requested JSON when available, keep runner
    # artifacts, and let telegram_agent_reply block raw JSON/mojibake.
    result = run_cmd(cmd, timeout=timeout + 120, env=env)
    stdout_json = parse_last_json_line(result.get("stdout") or "")
    duplicate_run_dir = ""
    if not result.get("ok"):
        match = re.search(r"Run directory already exists:\s*(\S+)", str(result.get("stderr") or ""))
        if match:
            duplicate_run_dir = match.group(1)
            stdout_json = {"status": "duplicate_run_dir", "output_dir": duplicate_run_dir}
    output_dir = Path(str(stdout_json.get("run_dir") or stdout_json.get("output_dir") or ""))
    parsed_path = output_dir / "artifacts" / "claude_stdout.parsed.json" if output_dir.is_absolute() else Path("")
    timing_path = output_dir / "artifacts" / "timing.json" if output_dir.is_absolute() else Path("")
    if output_dir.is_absolute():
        artifact_failure_parts: list[str] = []
        for artifact_name in ("claude_stderr.log", "claude_debug.log"):
            artifact_path = output_dir / "artifacts" / artifact_name
            if artifact_path.exists():
                try:
                    artifact_failure_parts.append(artifact_path.read_text(encoding="utf-8", errors="replace")[-4000:])
                except Exception:
                    pass
        if artifact_failure_parts:
            result = dict(result)
            result["_artifact_failure_text"] = "\n".join(artifact_failure_parts)
    runner_timing = read_json_if_exists(timing_path) if timing_path and timing_path.exists() else {}
    worker = read_json_if_exists(parsed_path) if parsed_path and parsed_path.exists() else {}
    if duplicate_run_dir and not worker and output_dir.is_absolute():
        existing_manifest = read_json_if_exists(output_dir / "manifest.json")
        existing_status = read_json_if_exists(output_dir / "status.json")
        existing_exit = existing_manifest.get("exit_code", existing_status.get("exit_code"))
        existing_completed = (
            str(existing_manifest.get("status") or existing_status.get("status") or "").lower() == "completed"
            or existing_exit == 0
        )
        if existing_completed:
            stdout_path = output_dir / "artifacts" / "claude_stdout.txt"
            raw_body = stdout_path.read_text(encoding="utf-8", errors="replace").strip() if stdout_path.exists() else ""
            parsed_room_json = parse_room_comment_json(raw_body)
            if parsed_room_json:
                worker = parsed_room_json
            else:
                worker = {
                    "kind": "comment" if raw_body else "status",
                    "confidence": "medium",
                    "title": "claude-code ark execution reused existing completed run",
                    "body": raw_body or "Claude Code Ark backend had an existing completed run and returned no visible body.",
                    "blockers": [],
                }
            result = dict(result)
            result["ok"] = True
            result["exit_code"] = 0
            result["reused_existing_run_dir"] = duplicate_run_dir
            result["duplicate_recovered"] = True
    if duplicate_run_dir and worker:
        result = dict(result)
        result["ok"] = True
        result["reused_existing_run_dir"] = duplicate_run_dir
    elif duplicate_run_dir:
        worker = {
            "kind": "risk",
            "confidence": "high",
            "title": "Claude Code room runner hit a duplicate run directory",
            "body": (
                "Claude Code 本轮没有正常启动：对应运行目录已经存在。"
                "这通常说明同一个协作任务被重复调度，或同一 tick 内多个任务复用了同一个临时 task-file 槽位。"
                "我已把它转成可读 blocker，避免把原始英文错误直接发到群里。"
            ),
            "blockers": ["duplicate_run_dir"],
        }
    if not worker and stdout_json:
        status = str(stdout_json.get("status") or "runner_failed")
        title = "Claude Code room runner did not produce parsed room JSON"
        if status == "completed":
            stdout_path = output_dir / "artifacts" / "claude_stdout.txt" if output_dir.is_absolute() else Path("")
            raw_body = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            parsed_room_json = parse_room_comment_json(raw_body)
            if parsed_room_json:
                worker = parsed_room_json
                body = ""
            else:
                body = raw_body.strip() or "Claude Code 本轮完成但没有形成可发布正文。"
                worker = {
                    "kind": "comment" if raw_body.strip() else "status",
                    "confidence": "medium",
                    "title": "claude-code ark execution completed",
                    "body": body,
                    "blockers": [],
                }
        else:
            body = (
                f"Claude Code 本轮 room runner 状态为 {status}，没有形成可直接发布的房间回复。"
                "这不是群聊讨论 gate；只是不把空输出、坏格式或内部状态原样发进群。"
            )
        if not worker:
            worker = {
                "kind": "risk" if status != "completed" else "status",
                "confidence": "high" if status != "completed" else "medium",
                "title": title,
                "body": body,
                "blockers": [] if status == "completed" else [status],
                "telegram_projection_status": "local_only_runner_failure" if status != "completed" else None,
            }
    title = str(worker.get("title") or ("claude-code ark execution completed" if result.get("ok") else "claude-code ark execution failed"))
    body = str(worker.get("body") or worker.get("verdict") or result.get("stdout") or result.get("stderr") or "")
    parsed_body_json = parse_room_comment_json(body)
    if parsed_body_json:
        worker.update(parsed_body_json)
        title = str(worker.get("title") or title)
        body = str(worker.get("body") or "")
    if not body.strip():
        body = "Claude Code Ark backend returned no visible body."
    blockers = worker.get("blockers") if isinstance(worker.get("blockers"), list) else []
    raw_status_reason = internal_status_body_reason(body)
    if raw_status_reason:
        title = "Claude Code room runner returned internal status"
        body = readable_internal_status_blocker("claude-code", str(task.get("run_id") or task.get("task_id") or ""), raw_status_reason, body)
        blockers = list(dict.fromkeys([*blockers, raw_status_reason]))
        worker["kind"] = "risk"
        worker["confidence"] = "high"
    normalized_title = normalize_claude_code_visible_text(normalize_room_visible_text(title))
    normalized_body = normalize_claude_code_visible_text(normalize_room_visible_text(body))
    room_text_normalized = normalized_title != title or normalized_body != body
    extra = {
        "kind": str(worker.get("kind") or ("status" if result.get("ok") else "risk")),
        "confidence": str(worker.get("confidence") or ("medium" if result.get("ok") else "high")),
        "title": normalized_title[:240],
        "body": normalized_body[:12000],
        "backend": "ark-coding-plan-official-claude-endpoint",
        "model": model,
        "model_override_source": run_spec.get("model_override_source"),
        "model_policy": run_spec.get("model_policy"),
        "model_policy_blocked_model": run_spec.get("model_policy_blocked_model"),
        "model_policy_fallback_model": run_spec.get("model_policy_fallback_model"),
        "effort": run_spec.get("effort"),
        "effort_override_source": run_spec.get("effort_override_source"),
        "effort_policy": run_spec.get("effort_policy"),
        "effort_policy_previous_effort": run_spec.get("effort_policy_previous_effort"),
        "effort_policy_previous_source": run_spec.get("effort_policy_previous_source"),
        "source_edit": source_edit,
        "claude_cli_permission_mode": cli_permission_mode,
        "room_runner_scope_dir": str(scope_dir),
        "effective_permissions": permissions,
        "runner_timing": runner_timing,
        "coding_run_dir": str(output_dir) if output_dir.is_absolute() else "",
        "blockers": blockers,
    }
    if worker.get("telegram_projection_status"):
        extra["telegram_projection_status"] = worker.get("telegram_projection_status")
    if room_text_normalized:
        extra["room_text_normalized"] = "claude_transport_cleaned"
    return result, extra["body"], extra


def external_deepseek_worker_available() -> bool:
    """Return whether Alex's local external DeepSeek fallback is installed.

    This is deliberately separate from Claude Code's Ark/Anthropic runner. The
    external DeepSeek API is OpenAI-compatible and can only provide a bounded
    text/review/blocker fallback; it cannot run Claude Code tools or edit files.
    """
    if not EXTERNAL_DEEPSEEK_ENV_FILE.exists() or not DIRECT_PROVIDER_WORKER.exists():
        return False
    text = EXTERNAL_DEEPSEEK_ENV_FILE.read_text(encoding="utf-8", errors="replace")
    return "OPENCLAW_WORKER_API_KEY=" in text and "OPENCLAW_WORKER_BASE_URL=" in text


def external_deepseek_status_model(model: str | None) -> str:
    value = str(model or "external-deepseek").strip() or "external-deepseek"
    return f"{EXTERNAL_DEEPSEEK_BACKEND}/{value}"


def external_deepseek_attempt_prefers_quality(attempts: list[dict[str, Any]] | None) -> bool:
    """Return whether Ark evidence should promote external DeepSeek to V4 Pro."""
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        model = str(attempt.get("model") or "").rsplit("/", 1)[-1].lower()
        if model != EXTERNAL_DEEPSEEK_QUALITY_MODEL:
            continue
        status = str(attempt.get("status") or "").lower()
        reason = str(attempt.get("reason") or attempt.get("kind") or "").lower()
        cooldown_until = str(attempt.get("cooldown_until") or "")
        if status in {"skipped_cooldown", "cooldown"} or cooldown_until:
            return True
        if status == "failed" and any(marker in reason for marker in ("usage_limit", "rate_limit", "quota", "cooldown")):
            return True
    return False


def external_deepseek_fallback_model(
    task: dict[str, Any],
    run_spec: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Select the explicit model for the external no-tools DeepSeek fallback.

    The direct provider worker can use either V4-pro or V4-flash. Do not let the
    fallback silently inherit whichever model happened to be written into the
    secret env file during key installation; route the model visibly here.
    """
    override = str(os.environ.get(EXTERNAL_DEEPSEEK_MODEL_ENV) or "").strip()
    if override:
        return override, f"env.{EXTERNAL_DEEPSEEK_MODEL_ENV}"

    if external_deepseek_attempt_prefers_quality(attempts):
        return EXTERNAL_DEEPSEEK_QUALITY_MODEL, f"ark_cooldown.{EXTERNAL_DEEPSEEK_QUALITY_MODEL}"

    spec = run_spec or {}
    route_key = str(
        spec.get("model_route_key")
        or task.get("claude_code_route_key")
        or task.get("model_route_key")
        or task.get("route_key")
        or ""
    ).strip()
    source_edit = bool(spec.get("source_edit"))
    if source_edit or route_key in {"workspace_write", "deep_reasoning", "long_context", "peer_review"}:
        return EXTERNAL_DEEPSEEK_QUALITY_MODEL, f"route.{route_key or 'source_edit'}"
    return EXTERNAL_DEEPSEEK_FAST_MODEL, f"route.{route_key or 'plain_or_fast'}"


def external_deepseek_fallback_model_candidates(
    task: dict[str, Any],
    run_spec: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    primary_model, primary_source = external_deepseek_fallback_model(task, run_spec, attempts)
    candidates = [(primary_model, primary_source)]
    if primary_model == EXTERNAL_DEEPSEEK_QUALITY_MODEL:
        candidates.append((EXTERNAL_DEEPSEEK_FAST_MODEL, "secondary_after_v4_pro"))
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for model, source in candidates:
        key = model.lower()
        if not model or key in seen:
            continue
        seen.add(key)
        deduped.append((model, source))
    return deduped


def bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def external_deepseek_fast_dm_profile(task: dict[str, Any]) -> bool:
    """Use tighter fallback bounds for direct private chats.

    Claude Code DMs are interactive operator conversations; when Ark candidates
    are unavailable, a concise degraded fallback is better than waiting for a
    long no-tools essay. Environment variables still override these defaults.
    """
    return str(task.get("room_id") or "").startswith("dm-")


def compact_text_for_external_fallback(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars < 200:
        return text[:max_chars]
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars].rstrip()
        + "\n\n...[compact external fallback prompt truncated]...\n\n"
        + text[-tail_chars:].lstrip()
    )


def external_deepseek_fallback_prompt(
    task: dict[str, Any],
    full_prompt: str,
    attempts: list[dict[str, Any]],
    agent_id: str = "claude-code",
) -> str:
    """Build a shorter, no-tools prompt for the direct-provider fallback."""
    fast_dm = external_deepseek_fast_dm_profile(task)
    brief = compact_text_for_external_fallback(
        task_brief_text(task),
        bounded_int_env("AGENT_ROOM_EXTERNAL_DEEPSEEK_BRIEF_MAX_CHARS", 4500 if fast_dm else 7000, minimum=1000, maximum=16000),
    )
    recent = recent_room_context(
        task,
        limit=bounded_int_env("AGENT_ROOM_EXTERNAL_DEEPSEEK_RECENT_LIMIT", 4 if fast_dm else 6, minimum=1, maximum=12),
    )
    attempts_view = [
        {
            key: attempt.get(key)
            for key in ("model", "status", "reason", "cooldown_until", "backend")
            if attempt.get(key) not in (None, "")
        }
        for attempt in attempts
        if isinstance(attempt, dict)
    ]
    compact = "\n".join(
        [
            f"You are the external DeepSeek direct-provider fallback for {agent_id} in OpenClaw Agent Room.",
            "",
            "Visible output contract:",
            "- Respond in Chinese.",
            "- Do not claim tool use, file reads, file edits, Telegram sends, secrets access, or local verification.",
            "- You have no local filesystem/tool access in this fallback. Do not say you created, wrote, patched, scanned, verified, or ran anything locally.",
            "- If an artifact or patch would help, describe it as a proposal/blocker or handoff target, not as completed work.",
            "- Answer the current user intent directly; if evidence is insufficient, state the precise blocker.",
            "- Keep the reply concise and material: evidence-based review, design, patch shape, smoke target, or blocker.",
            "",
            f"room_id: {task.get('room_id')}",
            f"task_id: {task.get('task_id')}",
            f"run_id: {task.get('run_id')}",
            "",
            current_turn_focus_guard(task).strip(),
            "",
            recent.strip(),
            "",
            f"{agent_id}/Ark attempt evidence before fallback:",
            json.dumps(attempts_view, ensure_ascii=False, indent=2),
            "",
            "Task brief:",
            brief,
        ]
    )
    max_prompt_chars = bounded_int_env("AGENT_ROOM_EXTERNAL_DEEPSEEK_PROMPT_MAX_CHARS", 8000 if fast_dm else 10000, minimum=2000, maximum=24000)
    if len(compact) <= max_prompt_chars:
        return compact
    return compact_text_for_external_fallback(compact, max_prompt_chars)


def run_external_deepseek_worker_fallback(
    task: dict[str, Any],
    prompt: str,
    run_dir: Path,
    attempts: list[dict[str, Any]],
    run_spec: dict[str, Any] | None = None,
    agent_id: str = "claude-code",
) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """Use installed external DeepSeek as a degraded Agent Room fallback.

    This path is for availability, not identity emulation: the resulting room
    comment remains in the original agent lane for continuity, but backend/model
    fields explicitly identify the external DeepSeek direct-provider worker and
    the no-tools/no-edit capability boundary.
    """
    if not external_deepseek_worker_available():
        return None
    safe_agent_id = str(agent_id or "agent").strip() or "agent"
    base_run_id = str(task.get("run_id") or task.get("task_id") or f"agent-room-{safe_agent_id}")
    output_dir = run_dir / "external-deepseek-worker"
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = output_dir / "prompt.md"
    fallback_prompt = external_deepseek_fallback_prompt(task, prompt, attempts, safe_agent_id)
    prompt_file.write_text(fallback_prompt, encoding="utf-8")
    fast_dm = external_deepseek_fast_dm_profile(task)
    max_tokens = bounded_int_env("AGENT_ROOM_EXTERNAL_DEEPSEEK_MAX_TOKENS", 900 if fast_dm else 1200, minimum=256, maximum=2200)
    provider_timeout = bounded_int_env("AGENT_ROOM_EXTERNAL_DEEPSEEK_TIMEOUT", 25 if fast_dm else 45, minimum=15, maximum=120)
    subprocess_timeout = bounded_int_env(
        "AGENT_ROOM_EXTERNAL_DEEPSEEK_SUBPROCESS_TIMEOUT",
        provider_timeout + 20,
        minimum=provider_timeout + 5,
        maximum=provider_timeout + 90,
    )
    worker_attempts: list[dict[str, Any]] = []
    selected_manifest: dict[str, Any] = {}
    selected_body = ""
    selected_output_dir = output_dir
    selected_model_source = ""
    for selected_external_model, selected_external_model_source in external_deepseek_fallback_model_candidates(task, run_spec, attempts):
        candidate_output_dir = output_dir / safe_run_id(selected_external_model)
        candidate_output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(DIRECT_PROVIDER_WORKER),
            "--profile",
            "openai-compatible",
            "--model",
            selected_external_model,
            "--input-file",
            str(prompt_file),
            "--system",
            (
                f"You are an external DeepSeek direct-provider fallback for a temporarily quota-limited {safe_agent_id} lane. "
                "You cannot run tools, inspect files beyond the prompt, edit files, send Telegram, or claim you changed local state. "
                "Do not state that you created, wrote, patched, read, verified, or ran local artifacts; propose patch shapes only. "
                "Give a concise Chinese contribution: evidence-based review, proposed patch shape, smoke target, or precise blocker."
            ),
            "--task-id",
            f"{base_run_id}-external-deepseek",
            "--task-type",
            f"agent_room_{safe_run_id(safe_agent_id)}_external_deepseek_fallback",
            "--output-dir",
            str(candidate_output_dir),
            "--max-tokens",
            str(max_tokens),
            "--timeout",
            str(provider_timeout),
        ]
        try:
            proc = subprocess.run(cmd, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=subprocess_timeout)
            exit_code = proc.returncode
            failure_reason = "external_deepseek_worker_failed"
        except subprocess.TimeoutExpired:
            exit_code = 124
            failure_reason = "external_deepseek_worker_timeout"
        manifest = read_json_if_exists(candidate_output_dir / "manifest.json")
        result_path = candidate_output_dir / "result.md"
        body = result_path.read_text(encoding="utf-8", errors="replace").strip() if result_path.exists() else ""
        ok = exit_code == 0 and bool(body)
        worker_attempts.append(
            {
                "model": manifest.get("model") or selected_external_model,
                "status": "completed" if ok else "failed",
                "ok": ok,
                "exit_code": exit_code,
                "reason": None if ok else manifest.get("error_kind") or failure_reason,
                "backend": EXTERNAL_DEEPSEEK_BACKEND,
                "output_dir": str(candidate_output_dir),
                "model_source": selected_external_model_source,
            }
        )
        if ok:
            selected_manifest = manifest
            selected_body = body
            selected_output_dir = candidate_output_dir
            selected_model_source = selected_external_model_source
            break
    if not selected_body:
        return None
    body = selected_body
    manifest = selected_manifest
    fallback_model = str(manifest.get("model") or "external-deepseek")
    status_model = external_deepseek_status_model(fallback_model)
    update_agent_room_quota_status(
        safe_agent_id,
        status_model,
        "available",
        reason="external_deepseek_direct_provider_fallback",
        active_model=status_model,
        run_id=base_run_id,
    )
    all_attempts = list(attempts) + worker_attempts
    fields = {
        "kind": "status",
        "confidence": "medium",
        "title": f"{safe_agent_id} external DeepSeek fallback completed",
        "body": normalize_room_visible_text(body)[:12000],
        "backend": EXTERNAL_DEEPSEEK_BACKEND,
        "model": fallback_model,
        "quota_recovery_model": status_model,
        "model_attempts": all_attempts,
        "external_deepseek_fallback": {
            "enabled": True,
            "capability": EXTERNAL_DEEPSEEK_CAPABILITY,
            "output_dir": str(selected_output_dir),
            "root_output_dir": str(output_dir),
            "fast_dm_profile": fast_dm,
            "max_tokens": max_tokens,
            "provider_timeout_seconds": provider_timeout,
            "subprocess_timeout_seconds": subprocess_timeout,
            "status_model": status_model,
            "model_source": selected_model_source,
            "prompt_compaction": {
                "enabled": True,
                "original_prompt_chars": len(prompt),
                "fallback_prompt_chars": len(fallback_prompt),
            },
        },
    }
    result = {"ok": True, "exit_code": 0, "stdout": body, "stderr": proc.stderr[-2000:]}
    return result, fields["body"], fields


def run_claude_code_ark(task: dict[str, Any], prompt: str, run_dir: Path, permissions: dict[str, bool]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Run Claude Code through Ark with a bounded model-attempt chain.

    Claude Code already runs on Ark, so the "model switch" problem here is not
    GPT→Ark; it is Ark model A quota/429/unavailable → Ark model B in the same
    runner turn. Keeping attempts inside one task prevents Agent Room from
    creating a new chat round just to discover the same quota failure again.
    """
    artifact_scope_dir = run_dir / "claude-code-scope"
    artifact_scope_dir.mkdir(parents=True, exist_ok=True)
    base_spec = claude_resolved_run_spec(task, permissions, artifact_scope_dir)
    candidates = claude_runtime_model_candidates(task, base_spec)
    state = load_claude_model_state()
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no_claude_attempt"}
    cooldown_state_changed = False
    transient_reasons = {"usage_limit", "rate_limit", "model_overloaded", "model_unavailable", "timeout"}
    base_run_id = str(task.get("run_id") or task.get("task_id") or "agent-room-claude-code")

    for index, model in enumerate(candidates):
        active_until = cooldown_active_until(state, model)
        if active_until:
            cooldown_reason = cooldown_skip_reason(state, model)
            quota_projection_state = "exhausted" if cooldown_reason in AGENT_QUOTA_DEPLETED_REASONS else "cooldown"
            attempts.append({
                "model": model,
                "status": "skipped_cooldown",
                "reason": cooldown_reason,
                "cooldown_until": active_until.isoformat(timespec="seconds"),
            })
            update_agent_room_quota_status(
                "claude-code",
                model,
                quota_projection_state,
                reason=cooldown_reason,
                cooldown_until=active_until.isoformat(timespec="seconds"),
                fallback_available=any(
                    not cooldown_active_until(state, candidate)
                    for candidate in candidates[index + 1 :]
                ),
                run_id=base_run_id,
            )
            continue

        attempt_task = dict(task)
        attempt_task["claude_code_model"] = model
        attempt_task["model_route_key"] = base_spec.get("model_route_key") or attempt_task.get("model_route_key")
        attempt_task["claude_code_route_key"] = base_spec.get("model_route_key") or attempt_task.get("claude_code_route_key")
        # Give every Ark model attempt a unique coding-run directory across
        # resident ticks while the final room comment still uses the parent
        # task run_id.
        attempt_task["ark_run_id"] = claude_ark_attempt_run_id(
            str(task.get("ark_run_id") or base_run_id),
            model,
            run_dir,
        )
        attempt_task["run_id"] = f"{base_run_id}-{safe_run_id(model)}" if len(candidates) > 1 else base_run_id
        result, body, fields = run_claude_code_ark_once(attempt_task, prompt, run_dir, permissions)
        last_result = result
        failure_reason = None if result.get("ok") else classify_codex_failure(result)
        attempts.append({
            "model": model,
            "status": "completed" if result.get("ok") else "failed",
            "ok": bool(result.get("ok")),
            "exit_code": result.get("exit_code"),
            "reason": failure_reason,
            "coding_run_dir": fields.get("coding_run_dir"),
        })
        if result.get("ok"):
            fields = dict(fields)
            fields["model"] = model
            fields["model_attempts"] = attempts
            if index > 0:
                fields["model_fallback"] = {
                    "from": candidates[0],
                    "to": model,
                    "path": "ark-model-chain",
                    "reason": "previous_model_quota_or_availability_failure",
                    "route_key": base_spec.get("model_route_key"),
                }
            if cooldown_state_changed:
                save_claude_model_state(state)
            mark_claude_model_recovered(state, model, run_id=base_run_id, room_id=str(task.get("room_id") or ""))
            save_claude_model_state(state)
            mark_agent_quota_recovered("claude-code", model, run_id=base_run_id, room_id=str(task.get("room_id") or ""))
            update_agent_room_quota_status("claude-code", model, "available", active_model=model, run_id=base_run_id)
            return result, body, fields
        if failure_reason in transient_reasons:
            mark_claude_model_cooldown(state, model, failure_reason, result)
            cooldown_state_changed = True
            cooldown_until = (((state.get("models") or {}).get(model) or {}).get("cooldown_until") or "")
            if cooldown_until:
                attempts[-1]["cooldown_until"] = str(cooldown_until)
            quota_projection_state = "exhausted" if failure_reason in AGENT_QUOTA_DEPLETED_REASONS else "cooldown"
            update_agent_room_quota_status(
                "claude-code",
                model,
                quota_projection_state,
                reason=failure_reason,
                cooldown_until=str(cooldown_until),
                fallback_available=any(
                    not cooldown_active_until(state, candidate)
                    for candidate in candidates[index + 1 :]
                ),
                run_id=base_run_id,
            )
            continue
        # A non-quota runner/schema/tool failure is not improved by burning more
        # Ark models; surface the blocker and let the terminal state close.
        break

    if cooldown_state_changed:
        save_claude_model_state(state)
    if any(str(attempt.get("reason") or "") in transient_reasons for attempt in attempts):
        external_fallback = run_external_deepseek_worker_fallback(task, prompt, run_dir, attempts, base_spec)
        if external_fallback is not None:
            return external_fallback
    body = claude_runtime_failure_body(base_run_id, attempts)
    fields = {
        "kind": "risk",
        "confidence": "high",
        "title": "Claude Code Ark model chain blocked",
        "body": body,
        "blockers": ["claude_ark_model_chain_failed"],
        "backend": "ark-coding-plan-official-claude-endpoint",
        "model": next((str(a.get("model")) for a in attempts if a.get("status") != "skipped_cooldown"), candidates[0] if candidates else None),
        "model_attempts": attempts,
        "telegram_projection_status": "suppressed_runner_failure",
    }
    return last_result, body, fields


def recent_room_context(task: dict[str, Any], limit: int = 12) -> str:
    room_id = str(task.get("room_id") or "")
    if not room_id:
        return ""
    path = ROOM / "rooms" / room_id / "messages.jsonl"
    if not path.exists():
        return ""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except Exception:
            continue
    if not records:
        return ""
    lines = ["# Recent Room Context", ""]
    for item in records[-limit:]:
        created = item.get("created_at") or ""
        targets = ",".join(item.get("target_agents") or [])
        text = str(item.get("text") or "").replace("\n", " ").strip()
        if len(text) > 360:
            text = text[:360] + "...[truncated]"
        lines.append(f"- {created} target=[{targets}] text={text}")
    return "\n".join(lines) + "\n"


def current_turn_focus_guard(task: dict[str, Any]) -> str:
    current_message = re.sub(r"\s+", " ", task_user_message(task)).strip()
    if not current_message:
        return ""
    lines = [
        "# Current Turn Focus Guard",
        "",
        "- Treat the Task brief's `User message` as the current user intent. It overrides `Recent Room Context` when they diverge.",
        "- Classify the current user message before changing direction: it may be a correction, supplement, clarification, status request, or new task. Do not abandon existing mainline work unless the current message actually supersedes it.",
        "- If the current message does not supersede or block active work, answer it directly and promptly while existing runners continue; do not make Alex wait for unrelated background work to finish.",
        "- If the current message affects active work, state how it is being incorporated/rebased; if it does not affect active work, state that current work continues and answer the message.",
        "- Use older room context as evidence for what changed, not as the topic to keep answering by inertia.",
        "- Treat one-off speaker/coordination instructions in Recent Room Context as historical unless repeated in the current User message; do not turn them into standing room policy.",
        "- A one-turn request for reduced chatter is not a standing policy; keep peer work local unless it has material evidence, correction, blocker, patch, or smoke value.",
        "- Do this on every turn from message ordering, not from detecting user correction keywords.",
    ]
    if str(task.get("room_id") or "").startswith("dm-"):
        lines.append(
            "- In private DM rooms, treat peer/other-agent status from recent context as background only; "
            "do not answer about another agent or lane unless the current User message asks for it or directly depends on it."
        )
    return "\n".join(lines) + "\n"


def _preflight_advisory_text(task: dict[str, Any], agent_id: str) -> str:
    """Build a concise preflight advisory section for the agent prompt.

    Calls collaboration_status.turn_preflight_advisory() and formats
    the key findings so the agent can see peer scope, pending uptake,
    scope conflicts, and efficiency signals before starting work.
    Returns an empty string if the advisory is unavailable or empty.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from agent_room.tools.collaboration_status import turn_preflight_advisory
    except Exception:
        return ""
    task_id = task.get("task_id") or task.get("run_id") or ""
    if not task_id:
        return ""
    try:
        result = turn_preflight_advisory(task_id, agent_id)
    except Exception:
        return ""
    if not isinstance(result, dict) or not result.get("ok"):
        return ""
    advisory = result.get("advisory") or {}
    lines: list[str] = []

    # Peer scope already declared
    peer_scopes = advisory.get("peer_declared_scopes") or {}
    if peer_scopes:
        for peer, paths in peer_scopes.items():
            if paths:
                lines.append(f"- Peer {peer} is editing: {', '.join(str(p) for p in paths[:5])}")

    # Scope conflicts
    conflict_paths = advisory.get("conflict_paths") or []
    if conflict_paths:
        lines.append(f"- ⚠ Scope conflict on: {', '.join(str(p) for p in conflict_paths[:5])}")

    # Pending uptake points
    pending = advisory.get("points_needing_uptake") or []
    if pending:
        for pt in pending[:4]:
            by = pt.get("by_agent") or "?"
            kind = pt.get("kind") or "point"
            text = (pt.get("text") or "")[:80]
            lines.append(f"- {by} raised a {kind} awaiting your uptake: {text}")

    # Peer degradation → scope expansion signal
    if advisory.get("should_expand_scope"):
        degraded = advisory.get("peer_degraded_agents") or []
        lines.append(f"- ⚠ Peer degraded ({', '.join(degraded)}): consider expanding your scope")

    # Efficiency grade
    eff = advisory.get("efficiency") or {}
    grade = eff.get("grade")
    if grade:
        overall = eff.get("overall", "?")
        lines.append(f"- Collaboration efficiency: {grade} ({overall})")

    if not lines:
        return ""
    return "## Preflight advisory (peer coverage & conflicts)\n" + "\n".join(lines) + "\n"


def task_prompt(task: dict[str, Any], agent_id: str, permissions: dict[str, bool]) -> str:
    brief_path = task.get("brief_path")
    brief_text = ""
    if brief_path:
        p = Path(str(brief_path))
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            brief_text = p.read_text(encoding="utf-8", errors="replace")
    context = recent_room_context(task)
    focus_guard = current_turn_focus_guard(task)
    scope = source_scope_dir(task)
    assignment = task.get("collaboration_assignment") if isinstance(task.get("collaboration_assignment"), dict) else {}
    assignment_text = ""
    if assignment:
        assignment_text = (
            "\nCollaboration assignment for this turn (soft, evidence-adjustable):\n"
            + json.dumps(assignment, ensure_ascii=False, indent=2)
            + "\nPeer agents in this turn: "
            + ", ".join(task.get("collaboration_peer_agents") or [])
            + "\n"
        )

    # Extract work items and find agent's assigned work
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
    agent_work_items = []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        assigned = item.get("assigned_to") or item.get("agent_id") or item.get("owner")
        if assigned == agent_id or (isinstance(assigned, list) and agent_id in assigned):
            agent_work_items.append(item)
    work_items_text = ""
    if work_items:
        work_items_text = "\n## 协作任务拆分（Work Items）\n\n"
        work_items_text += "完整任务已拆分成以下Work Items，每个Agent只负责自己的部分，不要重复造轮子：\n\n"
        for i, item in enumerate(work_items, 1):
            title = item.get("title") or item.get("role") or item.get("description") or "untitled"
            work_items_text += f"{i}. {item.get('id')}: {title}\n"
            work_items_text += f"   描述: {item.get('description')}\n"
            work_items_text += f"   分配给: {item.get('assigned_to') or item.get('agent_id') or item.get('owner')}\n"
            work_items_text += f"   状态: {item.get('status')}\n"
            work_items_text += f"   依赖: {item.get('dependencies') or []}\n"
            work_items_text += "\n"
        if agent_work_items:
            work_items_text += f"\n## 你当前负责的Work Items\n\n"
            for item in agent_work_items:
                title = item.get("title") or item.get("role") or item.get("description") or "untitled"
                work_items_text += f"- {item.get('id')}: {title}\n"
                work_items_text += f"  描述: {item.get('description')}\n"
                work_items_text += f"  优先级: {item.get('priority')}\n"
                work_items_text += f"  依赖项: {item.get('dependencies') or []}\n"
                work_items_text += "\n"
            work_items_text += (
                "请专注完成你负责的Work Items，不要重复其他Agent的部分；"
                "如果当前Work Item已被阻塞、完成或与你无直接关系，先在权限内寻找不重复的主线推进点，"
                "产出本地证据、patch/artifact、smoke、blocker或具体handoff后再回复；"
                "只有确认没有材料贡献时才输出NO_COMMENT。\n"
            )

    preflight_advisory_text = _preflight_advisory_text(task, agent_id)

    return f"""You are the selected Agent in Alex's OpenClaw Agent Room.

Respond in Chinese for visible room discussion.
You are a real peer participant in Alex's agent room, not a routing/status reporter.
Answer Alex's actual message in the Task brief directly, with concrete reasoning or next actions.
Do not say "this message was routed to...", "according to task config...", or merely confirm that you can participate unless Alex explicitly asks about routing or connectivity.
Mentions choose first-response ownership, not context visibility or permanent silence for everyone else.
Single visible-answer / "one person speaks" instructions are scoped to the current Task brief only. Do not infer a permanent main-only spokesperson, permanent peer silence, or a stop-work rule from an older room message.
If delivery_policy is broadcast_all_agents_decide, or the message has no @ mention, answer when your capability, local evidence, or reasoning makes you one of the right agents to help.
If another agent is the first-response owner, do not race generic answers; follow up only with material value: evidence, correction, blocker, risk, architecture/user-experience impact, patch, smoke, or concrete next action.
If you have no distinct value, output exactly NO_COMMENT so the room stays quiet.
If the current task genuinely has no direct work for you, do not treat that as passive silence by default. First look for a safe, scoped way to advance the OpenClaw mainline from your role: inspect local state, produce a patch/artifact/smoke/blocker, update a bounded task-state artifact, or hand off a concrete non-duplicative work item. Use NO_COMMENT only after that check finds no material, non-duplicative contribution.
When a collaboration assignment is present, treat it as the starting division of labor: cover your assigned production angle, challenge or complement peers, and avoid duplicate work. It is not a permanent role; if the assignment is wrong, say why and propose a smaller handoff.
For collaboration-mechanism or runtime-policy turns, first anchor your response to a visible peer claim, artifact, path, run result, or proposed next action when one exists: agree, challenge, refine, or hand it off. If this is a first-pass parallel turn and no peer output is visible yet, say that briefly and leave a concrete handoff/acceptance target for the next peer follow-up instead of writing a standalone essay.
Codex and Claude Code are not comment-only, reviewer-only, or executor-only. Use parallel production only when the task explicitly opts into a new Agent Room collaboration flow: split the work into non-overlapping work items, cross-review parts, integrate, run the smallest meaningful verification or QC gate, and iterate on concrete failures until it passes or a clear blocker is recorded. Do not reinterpret, replace, or modify existing production/task workflows such as Translation, People Daily/日报, market reports, Notion publishing, gateway/runtime timers, or provider lanes; those must keep their existing entrypoints and quality gates. They can discuss, challenge, design, verify, and execute when task permissions allow.
When you detect a peer's factual, boundary, workflow, or implementation error, correct it with inspected evidence and, when permissions allow, make the smallest local reversible fix or smoke artifact in the same turn. Do not wait for Alex to be the reviewer of last resort.
For collaboration-mechanism or runtime-policy questions, do not frame the process as "two-agent", "both agents", or only "Codex + Claude Code" review. The discussion set is tri-agent: openclaw-main + Codex + Claude Code. main contributes runtime/session context, UX and safety-boundary evidence, and may be challenged; Codex and Claude Code contribute implementation, architecture, verification, and counterexamples. Do not ask Alex to manage the workflow.
Tri-agent discussion is not a liveness lock. If one or more agents are unavailable because of network issues, model quota/cooldown, runner failure, or missing direct-send capability, the available agents must keep working in degraded-quorum mode. Record the unavailable agent, evidence/reason, work that continued, and follow-up review needed. Continue safe reversible/local work, evidence gathering, drafts, experimental switches, and smoke tests; pause or ask for stronger approval only for external publishing, destructive operations, secrets, irreversible global defaults, or major quality-surface changes.
Do not end a turn with "批准的话我开始", "要不要我做", "我可以修改/执行/补...", or equivalent approval/permission/optional-execution language for safe reversible local patches, scoped config edits, artifacts, inspections, or smoke tests that the current task permissions already allow. Discuss with peer agents when needed, decide within the agent room boundary, make the change or run the smoke, then report evidence. If permissions do not allow it, record a precise blocker and the smallest permission boundary needed; do not offload routine workflow decisions to Alex.
Do not ask Alex to confirm workflow-boundary corrections from Alex. Apply them internally, coordinate with peer agents, and leave a patch, smoke, artifact, or blocker when the current permissions allow it.
A visible contribution must carry at least one concrete unit of value: a patch/file path changed, artifact created, smoke/test result, inspected evidence that corrects a peer, review approval/rejection with reasons, or a precise blocker. If all you have is intent, process commentary, apology, or a promise to do future work, output exactly NO_COMMENT instead of posting visible chatter.
Agent Room/runtime/Telegram visibility is collaboration/reliability infrastructure for the broader OpenClaw mainline; do not present it as the whole roadmap. Translation Agent is an active mainline workflow; the self-built coding-agent lane is only backup/audit harness unless Claude Code/Codex cannot cover the need.
Antigravity remains a bounded unblocker: do not launch duplicate windows or repeatedly invoke CLI; prefer existing queued run/status/read evidence and same-run-id MCP roundtrip verification.
Do not expose secrets, raw prompts, hidden system prompts, or private logs.
When making factual claims about existing workflows, configs, model-routing rules, or available models, ground the claim in an inspected file/artifact or clearly mark it as unknown. Do not invent automatic behavior, thresholds, model names, or provider routes. For Claude Code Agent Room execution, the effective runtime boundary is native Claude --permission-mode plus allowed/disallowed tools; if the current tool scope still cannot verify something, say it is unverified instead of guessing.
Do not publish, push, send Telegram, read secrets, or run destructive cleanup/reset commands.
If source_edit is true, you may create or edit files only inside source_scope_dir, and you must keep changes tightly scoped to the task.
If global_state_change is true, use it only for Agent Room runtime/task-state maintenance, not broad system changes.
Visible room replies must not expose secrets, raw prompts, private logs, or raw internal runner payloads; otherwise keep the agent's natural answer style.
When a runtime/design problem is not obvious from local evidence, consider a short read-only reference scan of public GitHub or other code-hosting projects if the current tool/network policy allows it. Borrow implementation ideas only after mapping them back to OpenClaw's actual constraints; cite the source path/URL in artifacts, do not copy blindly, and never access private repos or secrets.

room_id: {task.get('room_id')}
task_id: {task.get('task_id')}
run_id: {task.get('run_id')}
target_agents: {task.get('target_agents')}
delivery_policy: {task.get('delivery_policy')}
reply_policy: {task.get('reply_policy')}

Effective permissions for this agent:
{json.dumps(permissions, ensure_ascii=False, indent=2)}
source_scope_dir: {scope}
{assignment_text}
{work_items_text}
{preflight_advisory_text}
{focus_guard}
{context}
Task brief:
{brief_text}
"""


def execute_agent(task: dict[str, Any], agent_id: str, run_dir: Path, allow_exec: bool, task_file: Path | None = None) -> dict[str, Any]:
    if collaboration_agent_excluded(task, agent_id):
        write_agent_presence(task, agent_id, "not_participating", "agent excluded from collaboration participants", run_dir=run_dir)
        return {
            "agent_id": agent_id,
            "status": agent_status(agent_id),
            "executed": False,
            "comment_written": False,
            "collaboration_ledger": {
                "enabled": False,
                "skipped": True,
                "reason": "agent_not_in_collaboration_participants",
            },
            "skipped": True,
            "skip_reason": "agent_not_in_collaboration_participants",
        }
    pause = agent_pause_status(agent_id)
    if pause:
        reason = str(pause.get("reason") or "agent temporarily paused by runtime safety gate")
        write_agent_presence(
            task,
            agent_id,
            "blocked_agent_paused",
            reason,
            run_dir=run_dir,
        )
        comment = {
            "schema": "openclaw.agent_room.comment.v0",
            "agent_id": agent_id,
            "run_id": task.get("run_id"),
            "task_id": task.get("task_id"),
            "room_id": task.get("room_id"),
            "kind": "risk",
            "confidence": "high",
            "title": f"{agent_id} temporarily paused",
            "body": reason,
            "blockers": [str(pause.get("blocker") or "agent_runtime_paused")],
            "created_at": now_iso(),
            "canonical_state_advanced": False,
            "side_effects_used": False,
            "telegram_projection_status": "local_only_main_summary",
            "pause_status": pause,
        }
        append_jsonl(comment_path(agent_id), comment)
        return {
            "agent_id": agent_id,
            "status": {"ready": False, "reason": reason, "paused": True},
            "executed": False,
            "comment_written": True,
            "blocked": True,
            "blocked_reason": "agent_runtime_paused",
            "comment": comment,
            "skipped": True,
            "skip_reason": "agent_runtime_paused",
        }
    ledger = collaboration_begin(task, agent_id, task_file)
    write_agent_presence(
        task,
        agent_id,
        "claimed_or_attempting_work_item",
        "agent runner started and is claiming/attempting its assigned work item",
        run_dir=run_dir,
        work_item_id=ledger.get("work_item_id"),
    )
    status = agent_status(agent_id)
    record = {
        "agent_id": agent_id,
        "status": status,
        "executed": False,
        "comment_written": False,
        "collaboration_ledger": ledger,
    }
    ledger_failed = any(
        isinstance(ledger.get(key), dict) and not ledger[key].get("ok", False)
        for key in ("init", "claim")
    )
    if ledger.get("enabled") and ledger_failed:
        if collaboration_soft_contribution_allowed(task, agent_id, ledger):
            mark_collaboration_soft_unclaimed_contribution(ledger)
        else:
            write_agent_presence(
                task,
                agent_id,
                "blocked_collaboration_claim",
                "collaboration work item claim failed; runner wrote blocker comment",
                run_dir=run_dir,
                work_item_id=ledger.get("work_item_id"),
            )
            comment = make_collaboration_claim_blocked_comment(task, agent_id, ledger)
            append_jsonl(comment_path(agent_id), comment)
            record.update({"blocked": True, "blocked_reason": comment["blocked_reason"], "comment": comment, "comment_written": True})
            return record
    if not status.get("ready"):
        write_agent_presence(
            task,
            agent_id,
            "blocked_not_ready",
            str(status.get("reason") or status.get("status") or "agent status not ready"),
            run_dir=run_dir,
            work_item_id=ledger.get("work_item_id"),
        )
        comment = make_blocked_comment(task, agent_id, status)
        append_jsonl(comment_path(agent_id), comment)
        ledger_final = collaboration_finish(task, agent_id, ledger.get("work_item_id"), comment, {"ok": False, "exit_code": 1})
        if ledger_final is not None:
            ledger["final"] = ledger_final
        record.update({"blocked": True, "blocked_reason": comment["title"], "comment": comment, "comment_written": True})
        return record
    if not allow_exec:
        write_agent_presence(
            task,
            agent_id,
            "blocked_exec_not_allowed",
            "agent_task_runner invoked without --allow-exec",
            run_dir=run_dir,
            work_item_id=ledger.get("work_item_id"),
        )
        ledger_final = collaboration_finish(
            task,
            agent_id,
            ledger.get("work_item_id"),
            {
                "title": "Agent Room execution not allowed",
                "body": "agent_task_runner was invoked without --allow-exec.",
                "blockers": ["exec_not_allowed_without_flag"],
            },
            {"ok": False, "exit_code": 1},
        )
        if ledger_final is not None:
            ledger["final"] = ledger_final
        record.update({"blocked": True, "blocked_reason": "exec_not_allowed_without_flag"})
        return record

    permissions = effective_permissions(task, agent_id)

    # --- Collaboration quota silence gate ---
    # Only block the concrete bot/model that already emitted its one visible
    # depletion notice. Claude Code routing can still switch to another model.
    if is_collaboration_task(task) and agent_id == "claude-code":
        probe_spec = claude_resolved_run_spec(task, permissions, run_dir / "quota-gate-scope")
        candidate_models = claude_runtime_model_candidates(task, probe_spec)
        active_depletion_records = [
            agent_quota_depleted_record(agent_id, candidate)
            for candidate in candidate_models
        ]
        notified_depletions = [record for record in active_depletion_records if record and record.get("first_notification_sent")]
        all_candidates_notified_depleted = bool(candidate_models) and len(notified_depletions) == len(candidate_models)
        if all_candidates_notified_depleted:
            depletion_record = notified_depletions[0]
            write_agent_presence(
                task,
                agent_id,
                "blocked_quota_silenced",
                "all candidate models have already emitted depletion notice and remain in cooldown",
                run_dir=run_dir,
                work_item_id=ledger.get("work_item_id"),
            )
            comment = make_quota_silenced_comment(task, agent_id, depletion_record)
            append_jsonl(comment_path(agent_id), comment)
            ledger_final = collaboration_finish(task, agent_id, ledger.get("work_item_id"), comment, {"ok": False, "exit_code": 1})
            if ledger_final is not None:
                ledger["final"] = ledger_final
            record.update({
                "retryable": True,
                "retry_after": (comment.get("retryable_failure") or {}).get("retry_after"),
                "skip_reason": "agent_quota_depleted_collaboration_silenced",
                "comment": comment,
                "comment_written": True,
            })
            return record

    prompt = task_prompt(task, agent_id, permissions)
    comment_fields: dict[str, Any] = {}
    write_agent_presence(
        task,
        agent_id,
        "invoking_agent_backend",
        "agent has entered provider/CLI execution; if no output follows before soft deadline, status surface should treat it as black-box risk",
        run_dir=run_dir,
        work_item_id=ledger.get("work_item_id"),
        extra={"backend": "codex" if agent_id == "codex" else "claude-code-ark" if agent_id == "claude-code" else "unsupported"},
    )
    if agent_id == "codex":
        result, body, comment_fields = run_codex_with_fallback(task, prompt, run_dir, permissions)
    elif agent_id == "claude-code":
        result, body, comment_fields = run_claude_code_ark(task, prompt, run_dir, permissions)
    else:
        result = {"ok": False, "exit_code": 2, "stdout": "", "stderr": "unsupported_agent"}
        body = "unsupported_agent"

    # --- Track agent quota state for collaboration silence ---
    executed_model = comment_fields.get("model")
    if result.get("ok"):
        recovery_model = comment_fields.get("quota_recovery_model") or executed_model
        # Successful execution → mark quota as recovered
        mark_agent_quota_recovered(
            agent_id,
            recovery_model,
            run_id=str(task.get("run_id") or task.get("task_id") or ""),
            room_id=str(task.get("room_id") or ""),
        )
    else:
        # Check if the failure was a quota-related transient failure
        quota_failure_reason = None
        if agent_id == "codex":
            # Codex failures are classified in model_attempts or by direct inspection
            combined = "\n".join(str(result.get(k) or "") for k in ("stderr", "stdout", "_artifact_failure_text")).lower()
            if "usage limit" in combined or "usage quota" in combined or "accountquotaexceeded" in combined or "quota" in combined:
                quota_failure_reason = "usage_limit"
            elif "rate limit" in combined or "too many requests" in combined or "429" in combined:
                quota_failure_reason = "rate_limit"
            elif "overloaded" in combined or "temporarily unavailable" in combined:
                quota_failure_reason = "model_overloaded"
            elif "model not found" in combined or "unsupported model" in combined:
                quota_failure_reason = "model_unavailable"
        elif agent_id == "claude-code":
            # Claude Code Ark failures
            combined = "\n".join(str(result.get(k) or "") for k in ("stderr", "stdout", "_artifact_failure_text")).lower()
            if "rate limit" in combined or "too many requests" in combined or "429" in combined:
                quota_failure_reason = "rate_limit"
            elif "usage limit" in combined or "usage quota" in combined or "accountquotaexceeded" in combined or "quota" in combined:
                quota_failure_reason = "usage_limit"
            elif "overloaded" in combined or "temporarily unavailable" in combined:
                quota_failure_reason = "model_overloaded"
            # Check if all model attempts were quota-related
            model_attempts = comment_fields.get("model_attempts")
            if isinstance(model_attempts, list) and not result.get("ok"):
                all_quota_related = all(
                    str(a.get("reason") or "") in AGENT_QUOTA_DEPLETED_REASONS
                    for a in model_attempts
                    if a.get("status") in ("failed", "skipped_cooldown")
                )
                if all_quota_related and any(
                    a.get("status") == "failed" for a in model_attempts
                ):
                    quota_failure_reason = quota_failure_reason or next(
                        (str(a.get("reason")) for a in model_attempts if a.get("status") == "failed"),
                        "quota_depleted",
                    )
        if quota_failure_reason:
            depletion_records: list[dict[str, Any]]
            if agent_id == "claude-code" and isinstance(comment_fields.get("model_attempts"), list):
                depletion_records = mark_agent_quota_depleted_for_attempts(
                    agent_id,
                    [attempt for attempt in comment_fields.get("model_attempts", []) if isinstance(attempt, dict)],
                )
            else:
                depletion_records = [mark_agent_quota_depleted(agent_id, quota_failure_reason, executed_model)]
            notification_records = [record for record in depletion_records if record.get("notification_required")]
            if notification_records:
                comment_fields = dict(comment_fields)
                comment_fields.update(make_quota_notice_fields(task, agent_id, notification_records[0]))
                body = str(comment_fields.get("body") or body)
                for depletion_record in notification_records:
                    mark_agent_quota_notification_sent(
                        agent_id,
                        str(depletion_record.get("model") or executed_model or ""),
                        str(task.get("run_id") or task.get("task_id") or ""),
                    )
            retryable_failure = retryable_failure_metadata(
                agent_id=agent_id,
                reason=quota_failure_reason,
                comment_fields=comment_fields,
                depletion_records=depletion_records,
            )
            if retryable_failure:
                comment_fields = dict(comment_fields)
                comment_fields["retryable_failure"] = retryable_failure
                if not comment_fields.get("quota_notice"):
                    comment_fields["telegram_projection_status"] = "local_only_retryable_runner_failure"

    # For broadcast replies (agent not explicitly in target_agents), keep local only
    target_agents = task.get("target_agents") or []
    explicitly_targeted = agent_id in target_agents
    comment = {
        "schema": "openclaw.agent_room.comment.v0",
        "agent_id": agent_id,
        "run_id": task.get("run_id"),
        "task_id": task.get("task_id"),
        "room_id": task.get("room_id"),
        "kind": comment_fields.get("kind") or ("status" if result.get("ok") else "risk"),
        "confidence": comment_fields.get("confidence") or "medium",
        "title": comment_fields.get("title") or f"{agent_id} execution {'completed' if result.get('ok') else 'failed'}",
        "body": (comment_fields.get("body") or body)[:12000],
        "seq_observed": None,
        "created_at": now_iso(),
        "canonical_state_advanced": False,
        "side_effects_used": bool(permissions.get("source_edit") or permissions.get("global_state_change")),
        "effective_permissions": permissions,
        "telegram_projection_status": comment_fields.get("telegram_projection_status") or ("local_only_main_summary" if not explicitly_targeted else None),
    }
    if comment_fields.get("backend"):
        comment["backend"] = comment_fields.get("backend")
    if comment_fields.get("model"):
        comment["model"] = comment_fields.get("model")
    if comment_fields.get("effort"):
        comment["effort"] = comment_fields.get("effort")
    if comment_fields.get("effort_override_source"):
        comment["effort_override_source"] = comment_fields.get("effort_override_source")
    if comment_fields.get("claude_cli_permission_mode"):
        comment["claude_cli_permission_mode"] = comment_fields.get("claude_cli_permission_mode")
    if comment_fields.get("coding_run_dir"):
        comment["coding_run_dir"] = comment_fields.get("coding_run_dir")
    if comment_fields.get("runner_timing"):
        comment["runner_timing"] = comment_fields.get("runner_timing")
    if comment_fields.get("model_attempts"):
        comment["model_attempts"] = comment_fields.get("model_attempts")
    if comment_fields.get("model_fallback"):
        comment["model_fallback"] = comment_fields.get("model_fallback")
    if comment_fields.get("external_deepseek_fallback"):
        comment["external_deepseek_fallback"] = comment_fields.get("external_deepseek_fallback")
    if comment_fields.get("quota_notice"):
        comment["quota_notice"] = comment_fields.get("quota_notice")
    if comment_fields.get("retryable_failure"):
        comment["retryable_failure"] = comment_fields.get("retryable_failure")
    if comment_fields.get("room_text_normalized"):
        comment["room_text_normalized"] = comment_fields.get("room_text_normalized")
    if isinstance(task.get("collaboration_assignment"), dict) and task.get("collaboration_assignment"):
        comment["collaboration_assignment"] = task.get("collaboration_assignment")
        comment["collaboration_peer_agents"] = task.get("collaboration_peer_agents") or []
    if comment_fields.get("blockers"):
        comment["blockers"] = comment_fields.get("blockers")
    if comment_fields.get("telegram_projection_status"):
        comment["telegram_projection_status"] = comment_fields.get("telegram_projection_status")
    append_jsonl(comment_path(agent_id), comment)
    ledger_final = collaboration_finish(task, agent_id, ledger.get("work_item_id"), comment, result)
    if ledger_final is not None:
        ledger["final"] = ledger_final
    parent_uptake = record_parent_peer_followup_uptake(task, agent_id, comment, result)
    if parent_uptake.get("status") != "not_applicable":
        ledger["parent_uptake"] = parent_uptake
    # For standing mainline tasks, record uptake against the peer's point.
    standing_uptake = record_standing_mainline_peer_uptake(task, agent_id, comment)
    if standing_uptake.get("status") not in ("not_applicable", "skipped"):
        ledger["standing_uptake"] = standing_uptake
    write_agent_presence(
        task,
        agent_id,
        "completed" if result.get("ok") else "blocked_or_failed",
        str(comment.get("title") or ("agent execution completed" if result.get("ok") else "agent execution failed")),
        run_dir=run_dir,
        work_item_id=ledger.get("work_item_id"),
        extra={"comment_written": True, "comment_title": comment.get("title"), "ok": bool(result.get("ok"))},
    )
    if isinstance(comment.get("retryable_failure"), dict):
        record["retryable"] = True
        record["retry_after"] = (comment.get("retryable_failure") or {}).get("retry_after")
    record.update({"executed": True, "result": result, "comment": comment, "comment_written": True})
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or dry-run one Agent Room task for Codex/Claude.")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--allow-exec", action="store_true", help="Actually call a ready agent CLI. Without this, only readiness/block artifacts are written.")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    task_path = Path(args.task_file)
    task = read_json(task_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out_dir) if args.out_dir else RUN_ROOT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    targets = list(task.get("target_agents") or [])
    results = [execute_agent(task, agent_id, run_dir, args.allow_exec, task_path) for agent_id in targets]
    summary = {
        "schema": "openclaw.agent_room.agent_task_runner.v0",
        "ok": True,
        "task_file": str(task_path),
        "run_dir": str(run_dir),
        "allow_exec": bool(args.allow_exec),
        "results": results,
        "telegram_outbound": False,
        "external_side_effects": any(bool((r.get("comment") or {}).get("side_effects_used")) for r in results),
        "tokens_printed": False,
    }
    result_path = run_dir / "result.json"
    write_json(result_path, summary)
    update_task_manifest_after_results(task_path, task, results, result_path)
    # Write exit marker so active_runner_alive() can immediately detect this
    # runner has finished, even between harvest ticks. Without this marker,
    # exited runners leave stale active-runner files that produce "same run
    # already running" noise until the next harvest cycle runs.
    try:
        (run_dir / ".runner-exit-marker").write_text(
            json.dumps({"finished_at": now_iso(), "ok": bool(summary.get("ok"))}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
