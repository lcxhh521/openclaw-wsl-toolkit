#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "agent-room" / "tools"
DRY_RUN = ROOT / "agent-room" / "dry-runs" / "collaboration-status-provider-health-smoke"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(name: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(name)


def main() -> int:
    if DRY_RUN.exists():
        shutil.rmtree(DRY_RUN)
    bridge_root = DRY_RUN / "codex-main-bridge"
    room = bridge_root / "agent-room"
    status_dir = room / "collaboration-status"
    room.mkdir(parents=True, exist_ok=True)

    write_json(
        room / "model_quota_signal.json",
        {
            "schema": "openclaw.agent_room.model_quota_signal.v2",
            "generated_at": "2026-05-27T20:50:00+08:00",
            "updated_at": "2026-05-27T20:51:00+08:00",
            "expires_at": "2099-01-01T00:00:00+08:00",
            "token_channels": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex quota",
                    "status": "available",
                    "availability_known": True,
                    "remaining_known": False,
                    "available_models": 7,
                    "total_models": 7,
                    "source": "fixture",
                    "limitation": "remaining units intentionally unknown in fixture",
                },
                "ark-coding-plan": {
                    "id": "ark-coding-plan",
                    "display_name": "Ark Coding Plan",
                    "availability_known": True,
                    "remaining_known": False,
                    "live_usage_known": False,
                    "available_models": 3,
                    "total_models": 5,
                    "usage_api_status": "authorized_empty_or_unbound_plan",
                    "usage_api_credential_accepted": True,
                    "source": "fixture",
                    "limitation": "usage API accepted credentials but no authoritative plan window is present",
                    "windows": [
                        {
                            "label": "5h",
                            "remaining_known": False,
                            "observed_only": True,
                            "observed_used_requests": 44,
                        }
                    ],
                },
            },
        },
    )
    write_json(
        room / "agent_quota_state.json",
        {
            "agents": {
                "codex": {
                    "status": "available",
                    "models": {
                        "gpt-5.5": {"status": "available", "model": "gpt-5.5"}
                    },
                },
                "claude-code": {
                    "status": "available",
                    "models": {
                        "deepseek-v4-flash": {"status": "available", "model": "deepseek-v4-flash"},
                        "deepseek-v4-pro": {
                            "status": "depleted",
                            "reason": "usage_limit",
                            "model": "deepseek-v4-pro",
                            "cooldown_until": "2099-01-01T00:05:00+08:00",
                        },
                        "kimi-k2.6": {
                            "status": "depleted",
                            "reason": "usage_limit",
                            "model": "kimi-k2.6",
                            "cooldown_until": "2026-01-01T00:00:00+08:00",
                        },
                    },
                },
            }
        },
    )

    status_tool = load_module(TOOLS / "collaboration_status.py", "collaboration_status_provider_health_smoke")
    status_tool.ROOT = bridge_root
    status_tool.ROOM = room
    status_tool.ACTIVE_RUNNERS = room / "active-runners"
    status_tool.TASKS = room / "tasks"
    status_tool.STATUS_DIR = status_dir
    status_tool.COLLAB_LEDGER_DIR = room / "collaboration-ledgers"
    status_tool.DAEMON_STATUS = room / "agent_room_bridge_daemon.status.json"
    status_tool.AGENT_PRESENCE_DIR = room / "agent-presence"
    status_tool.MODEL_QUOTA_SIGNAL = room / "model_quota_signal.json"
    status_tool.AGENT_QUOTA_STATE = room / "agent_quota_state.json"

    status = status_tool.build_status(include_background=True)
    provider = status.get("provider_health") if isinstance(status.get("provider_health"), dict) else {}
    per_agent = provider.get("per_agent") if isinstance(provider.get("per_agent"), dict) else {}
    channels = provider.get("token_channels") if isinstance(provider.get("token_channels"), dict) else {}
    markdown = status_tool.render_markdown_status(status)
    signature = status_tool.status_watch_signature(status)

    failures: list[str] = []
    check("provider health is present", provider.get("schema") == "openclaw.agent_room.provider_health.v0", failures)
    check("fresh quota signal is recognized", provider.get("signal_fresh") is True, failures)
    check("codex channel model count is surfaced", (channels.get("codex") or {}).get("available_models") == 7, failures)
    check("ark channel model count is surfaced", (channels.get("ark-coding-plan") or {}).get("available_models") == 3, failures)
    check("codex is available", (per_agent.get("codex") or {}).get("availability") == "available", failures)
    check("claude-code degraded cooldown is surfaced", (per_agent.get("claude-code") or {}).get("availability") == "degraded", failures)
    check("claude-code active cooldown count is surfaced", (per_agent.get("claude-code") or {}).get("active_cooldown_count") == 1, failures)
    check("stale depleted model is not counted as active cooldown", (per_agent.get("claude-code") or {}).get("stale_depleted_model_count") == 1, failures)
    check("markdown renders provider health", "## Provider health" in markdown and "claude-code" in markdown, failures)
    check("watch signature includes provider health", (signature.get("provider_health") or {}).get("per_agent", {}).get("claude-code", {}).get("availability") == "degraded", failures)
    check("provider health is diagnostic only", (provider.get("safety") or {}).get("does_not_change_quality_gates") is True, failures)

    result = {
        "schema": "openclaw.agent_room.collaboration_status_provider_health_smoke.v0",
        "ok": not failures,
        "failures": failures,
        "dry_run": str(DRY_RUN),
        "provider_health": provider,
        "signature_provider_health": signature.get("provider_health"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
