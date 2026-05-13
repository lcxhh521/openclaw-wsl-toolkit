#!/usr/bin/env python3
"""Read-only Antigravity adapter probe.

The probe inspects install metadata, WSL/Windows transport availability, visible
processes/ports, and extension command declarations. It does not launch
Antigravity, does not read/export tokens, and does not use UI automation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

BRIDGE = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
OUT = BRIDGE / "adapter-probes" / "antigravity" / "latest.json"
INSTALL = Path("/mnt/d/Antigravity")
EXT_PACKAGE = INSTALL / "resources/app/extensions/antigravity/package.json"
BIN_SH = INSTALL / "bin/antigravity"
BIN_CMD = INSTALL / "bin/antigravity.cmd"
EXE = INSTALL / "Antigravity.exe"


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
        if "adapter_probe_antigravity" in line or "/tmp/adapter_probe_antigravity" in line:
            continue
        if line.strip():
            lines.append(line[:500])
    return lines[:50]


def listen_ports() -> list[str]:
    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except Exception:
        return []
    hits = []
    for line in result.stdout.splitlines():
        low = line.lower()
        if any(term in low for term in ["antigravity", "language", "electron"]):
            hits.append(line[:500])
    return hits[:50]


def extension_commands(pkg: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(pkg, dict):
        return []
    contributes = pkg.get("contributes")
    if not isinstance(contributes, dict):
        return []
    commands = contributes.get("commands")
    if not isinstance(commands, list):
        return []
    out = []
    for cmd in commands:
        if isinstance(cmd, dict):
            out.append({"command": cmd.get("command"), "title": cmd.get("title")})
    return out


def main() -> int:
    evidence: list[str] = []
    blockers: list[str] = []
    safe_next_actions: list[str] = []
    requires_alex_action: list[str] = []

    files = {
        "install_dir": INSTALL.exists(),
        "exe": EXE.exists(),
        "bin_sh": BIN_SH.exists(),
        "bin_cmd": BIN_CMD.exists(),
        "extension_package": EXT_PACKAGE.exists(),
    }
    if files["install_dir"]:
        evidence.append(f"Antigravity install dir exists: {INSTALL}")
    else:
        blockers.append(f"Antigravity install dir missing: {INSTALL}")
    for key, ok in files.items():
        if key != "install_dir" and ok:
            evidence.append(f"{key} present")

    pkg = read_json(EXT_PACKAGE)
    commands = extension_commands(pkg)
    if commands:
        evidence.append(f"extension commands declared: {len(commands)}")
    elif files["extension_package"]:
        blockers.append("extension package readable but command declarations missing/invalid")

    transport = {
        "cmd.exe": shutil.which("cmd.exe") is not None,
        "powershell.exe": shutil.which("powershell.exe") is not None,
        "wslpath": shutil.which("wslpath") is not None,
    }
    if transport["cmd.exe"] or transport["powershell.exe"]:
        evidence.append(f"Windows command transport visible: {transport}")
    else:
        blockers.append("cmd.exe/powershell.exe not visible from current WSL shell")

    processes = pgrep("[Aa]ntigravity|language_server_windows_x64|Electron")
    ports = listen_ports()
    if processes:
        evidence.append(f"process candidates visible: {len(processes)}")
    else:
        evidence.append("no Antigravity process visible from WSL ps")
    if ports:
        evidence.append(f"Antigravity/language-server related listening ports visible: {len(ports)}")
    else:
        evidence.append("no Antigravity/language-server listening port visible from WSL ss")

    if not files["install_dir"]:
        adapter_status = "adapter_missing"
    elif not (transport["cmd.exe"] or transport["powershell.exe"]):
        adapter_status = "blocked_transport_missing"
    elif processes or ports:
        adapter_status = "local_service_probe_required"
    else:
        adapter_status = "installed_but_not_running_or_headless_unproven"

    safe_next_actions.extend([
        "create a non-secret local-service/interface probe if Antigravity is running",
        "do not rely on copyApiKey or token extraction",
        "do not use GUI automation unless Alex explicitly approves a separate experiment",
    ])
    if not (transport["cmd.exe"] or transport["powershell.exe"]):
        requires_alex_action.append("If Windows-side automation is desired, restore/approve a safe WSL→Windows transport path or run a Windows-side probe manually")

    payload: dict[str, Any] = {
        "schema": "openclaw.agent_adapter_probe.v0",
        "participant_id": "antigravity",
        "checked_at": now_iso(),
        "adapter_status": adapter_status,
        "capabilities_observed": {
            "probe": True,
            "installed": files["install_dir"],
            "cli_files_present": files["bin_sh"] or files["bin_cmd"],
            "extension_commands_present": bool(commands),
            "windows_transport": transport["cmd.exe"] or transport["powershell.exe"],
            "running_process_visible": bool(processes),
            "local_ports_visible": bool(ports),
            "send_task": False,
            "read_result": False,
            "structured_artifacts": False,
            "requires_gui": "unknown",
            "requires_manual_auth": "unknown",
        },
        "evidence": evidence,
        "blockers": blockers,
        "safe_next_actions": safe_next_actions,
        "requires_alex_action": requires_alex_action,
        "observed_files": {
            "install_dir": str(INSTALL) if INSTALL.exists() else None,
            "exe": str(EXE) if EXE.exists() else None,
            "bin_sh": str(BIN_SH) if BIN_SH.exists() else None,
            "bin_cmd": str(BIN_CMD) if BIN_CMD.exists() else None,
            "extension_package": str(EXT_PACKAGE) if EXT_PACKAGE.exists() else None,
        },
        "transport": transport,
        "extension_summary": {
            "name": pkg.get("name") if isinstance(pkg, dict) else None,
            "displayName": pkg.get("displayName") if isinstance(pkg, dict) else None,
            "version": pkg.get("version") if isinstance(pkg, dict) else None,
            "main": pkg.get("main") if isinstance(pkg, dict) else None,
            "commands": commands,
        },
        "process_candidates": processes,
        "port_candidates": ports,
    }
    write_json_atomic(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
