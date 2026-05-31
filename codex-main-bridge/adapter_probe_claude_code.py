#!/usr/bin/env python3
"""Read-only Claude Code/ACP adapter probe.

No Claude process is launched. No auth files, tokens, or secrets are read. The
probe reports wrapper/dependency/session metadata that is already part of
OpenClaw state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

BRIDGE = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
OUT = BRIDGE / "adapter-probes" / "claude-code" / "latest.json"
WRAPPER = Path(os.environ.get("OPENCLAW_CLAUDE_ACP_WRAPPER", str(Path.home() / ".openclaw" / "acpx" / "claude-agent-acp-wrapper.mjs")))
SESSION_STATE_ENV = os.environ.get("OPENCLAW_CLAUDE_SESSION_STATE", "").strip()
SESSION_STATE = Path(SESSION_STATE_ENV).expanduser() if SESSION_STATE_ENV else None
AGENT_SESSIONS = Path(os.environ.get("OPENCLAW_CLAUDE_SESSIONS_JSON", str(Path.home() / ".openclaw" / "agents" / "claude" / "sessions" / "sessions.json")))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_read_error": str(exc)}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_installed_bin(wrapper_text: str) -> str | None:
    match = re.search(r'installedBinPath\s*=\s*"([^"]+)"', wrapper_text)
    return match.group(1) if match else None


def main() -> int:
    evidence: list[str] = []
    blockers: list[str] = []
    safe_next_actions: list[str] = []
    requires_alex_action: list[str] = []

    wrapper_text = None
    installed_bin = None
    if WRAPPER.exists():
        wrapper_text = WRAPPER.read_text(encoding="utf-8", errors="replace")
        installed_bin = extract_installed_bin(wrapper_text)
        evidence.append(f"ACP wrapper exists: {WRAPPER}")
    else:
        blockers.append(f"ACP wrapper missing: {WRAPPER}")

    installed_bin_path = Path(installed_bin) if installed_bin else None
    if installed_bin_path and installed_bin_path.exists():
        evidence.append(f"packaged claude-agent-acp runtime exists: {installed_bin_path}")
    elif installed_bin_path:
        blockers.append(f"packaged claude-agent-acp runtime missing at wrapper path: {installed_bin_path}")
    else:
        blockers.append("could not identify packaged claude-agent-acp runtime from wrapper")

    session_state = read_json(SESSION_STATE) if SESSION_STATE else None
    if session_state:
        evidence.append(f"previous OpenClaw ACP session metadata exists: {SESSION_STATE}")
    else:
        evidence.append("no explicit OPENCLAW_CLAUDE_SESSION_STATE metadata path configured")

    agent_sessions = read_json(AGENT_SESSIONS)
    if agent_sessions:
        evidence.append(f"OpenClaw claude agent sessions index exists: {AGENT_SESSIONS}")

    cli_presence = {cmd: shutil.which(cmd) is not None for cmd in ["claude", "claude-code"]}
    if any(cli_presence.values()):
        evidence.append(f"direct Claude command visible: {cli_presence}")
    else:
        blockers.append("direct claude/claude-code command not visible in current PATH")

    capabilities = {}
    if isinstance(session_state, dict):
        capabilities = session_state.get("agent_capabilities") or {}

    if WRAPPER.exists() and installed_bin_path and installed_bin_path.exists():
        adapter_status = "acp_wrapper_present_smoke_required"
        blockers.append("callable/auth status is unverified; read-only smoke required before claiming integration")
        safe_next_actions.append("run a controlled OpenClaw ACP read-only smoke only after confirming it will not expose secrets")
    elif WRAPPER.exists():
        adapter_status = "acp_wrapper_present_runtime_missing"
    else:
        adapter_status = "adapter_missing"

    requires_alex_action.append("Approve any Claude Code smoke that may consume Claude quota or require auth/login")

    payload: dict[str, Any] = {
        "schema": "openclaw.agent_adapter_probe.v0",
        "participant_id": "claude-code",
        "checked_at": now_iso(),
        "adapter_status": adapter_status,
        "capabilities_observed": {
            "probe": True,
            "acp_wrapper": WRAPPER.exists(),
            "packaged_runtime": bool(installed_bin_path and installed_bin_path.exists()),
            "previous_session_metadata": bool(session_state),
            "direct_cli": any(cli_presence.values()),
            "send_task": False,
            "read_result": False,
            "structured_artifacts": False,
            "requires_manual_auth": "unknown",
        },
        "evidence": evidence,
        "blockers": blockers,
        "safe_next_actions": safe_next_actions,
        "requires_alex_action": requires_alex_action,
        "observed_files": {
            "wrapper": str(WRAPPER) if WRAPPER.exists() else None,
            "installed_bin_from_wrapper": str(installed_bin_path) if installed_bin_path else None,
            "session_state": str(SESSION_STATE) if SESSION_STATE and SESSION_STATE.exists() else None,
            "agent_sessions_index": str(AGENT_SESSIONS) if AGENT_SESSIONS.exists() else None,
        },
        "direct_cli_presence": cli_presence,
        "previous_session_summary": {
            "acpx_record_id": session_state.get("acpx_record_id") if isinstance(session_state, dict) else None,
            "agent_command": session_state.get("agent_command") if isinstance(session_state, dict) else None,
            "closed": session_state.get("closed") if isinstance(session_state, dict) else None,
            "pid": session_state.get("pid") if isinstance(session_state, dict) else None,
            "agent_capabilities": capabilities,
        },
    }
    write_json_atomic(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
