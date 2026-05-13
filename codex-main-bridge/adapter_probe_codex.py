#!/usr/bin/env python3
"""Read-only Codex adapter probe for the OpenClaw agent-collab bridge.

This script does not wake Codex, does not launch a runner, does not inspect
secrets, and does not change watcher/service/timer behavior. It only reports
what can be observed from the current WSL/OpenClaw side.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRIDGE = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
OUT = BRIDGE / "adapter-probes" / "codex" / "latest.json"
ADAPTER = BRIDGE / "codex_adapter"


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


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def pgrep(pattern: str) -> list[str]:
    try:
        result = subprocess.run(
            ["pgrep", "-af", pattern],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except Exception:
        return []
    lines = []
    this_pid = os.getpid()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if str(this_pid) in line and "adapter_probe_codex" in line:
            continue
        lines.append(line[:500])
    return lines[:30]


def main() -> int:
    bridge_status = None
    bridge_status_path = BRIDGE / "bridge_status.latest.json"
    # Prefer the latest file if present; it is cheaper and avoids changing bridge state.
    bridge_status = read_json(bridge_status_path)
    if bridge_status is None:
        bridge_status = read_json(BRIDGE / "bridge_status.json")

    turn = read_json(BRIDGE / "turn.json")
    status_file = ADAPTER / "status.json"
    heartbeat_file = ADAPTER / "heartbeat.json"
    status = read_json(status_file)
    heartbeat = read_json(heartbeat_file)

    codex_cmds = ["codex", "codex.cmd", "openai-codex"]
    windows_transport = {
        "cmd.exe": command_exists("cmd.exe"),
        "powershell.exe": command_exists("powershell.exe"),
        "wslpath": command_exists("wslpath"),
    }

    evidence: list[str] = []
    blockers: list[str] = []
    safe_next_actions: list[str] = []

    if turn:
        evidence.append(f"turn.json present seq={turn.get('seq')} needs_reply={turn.get('needs_reply')}")
    else:
        blockers.append("turn.json missing")

    if bridge_status:
        evidence.append(f"bridge status observable: {bridge_status.get('status')}")
    else:
        blockers.append("no bridge_status JSON snapshot available")

    adapter_files_present = status is not None or heartbeat is not None
    if adapter_files_present:
        evidence.append("codex_adapter status/heartbeat files present")
    else:
        blockers.append("codex_adapter/status.json and heartbeat.json are not present; no first-class runner yet")

    if any(windows_transport.values()):
        evidence.append(f"Windows/WSL transport partly available: {windows_transport}")
    else:
        blockers.append("no cmd.exe/powershell.exe visible from current WSL shell; wake via Windows task cannot be driven here")

    codex_command_presence = {cmd: command_exists(cmd) for cmd in codex_cmds}
    if any(codex_command_presence.values()):
        evidence.append(f"Codex command candidate visible: {codex_command_presence}")
    else:
        blockers.append("no direct codex CLI command visible in current WSL PATH")

    processes = pgrep("[Cc]odex|openai-codex")
    if processes:
        evidence.append(f"process candidates visible from WSL: {len(processes)}")
    else:
        evidence.append("no Codex process visible from WSL ps; Windows-side GUI may still be invisible")

    if adapter_files_present:
        adapter_status = "adapter_status_files_present"
    elif bridge_status and turn:
        adapter_status = "mailbox_baseline_only_runner_missing"
    else:
        adapter_status = "blocked_missing_bridge_state"

    if not adapter_files_present:
        safe_next_actions.append("implement/read-only codex_adapter status + heartbeat files before claiming full backend integration")
    safe_next_actions.append("design smoke test only; do not enable Windows scheduled task or persistent runner without Alex approval")

    payload: dict[str, Any] = {
        "schema": "openclaw.agent_adapter_probe.v0",
        "participant_id": "codex",
        "checked_at": now_iso(),
        "adapter_status": adapter_status,
        "capabilities_observed": {
            "probe": True,
            "send_task_via_mailbox": bool(turn),
            "read_result_via_mailbox": (BRIDGE / "codex_to_main.md").exists(),
            "wake": False,
            "cancel": False,
            "resume": False,
            "structured_artifacts": adapter_files_present,
        },
        "evidence": evidence,
        "blockers": blockers,
        "safe_next_actions": safe_next_actions,
        "requires_alex_action": [
            "Approve a fixed Windows-side wrapper/scheduled task before enabling Codex wake/start",
        ],
        "observed_files": {
            "turn": str(BRIDGE / "turn.json"),
            "bridge_status_latest": str(bridge_status_path) if bridge_status_path.exists() else None,
            "adapter_status": str(status_file) if status_file.exists() else None,
            "adapter_heartbeat": str(heartbeat_file) if heartbeat_file.exists() else None,
        },
        "transport": {
            "windows": windows_transport,
            "codex_commands": codex_command_presence,
        },
        "process_candidates": processes,
        "raw_status": {
            "turn": turn,
            "bridge_status": bridge_status,
            "adapter_status_file": status,
            "adapter_heartbeat_file": heartbeat,
        },
    }
    write_json_atomic(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
