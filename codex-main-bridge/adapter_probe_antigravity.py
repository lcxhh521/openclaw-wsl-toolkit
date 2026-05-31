#!/usr/bin/env python3
"""Read-only Antigravity adapter probe.

The probe inspects install metadata, WSL/Windows transport availability, visible
processes/ports, and extension command declarations. It does not launch
Antigravity, does not read/export tokens, and does not use UI automation.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

BRIDGE = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
OUT = BRIDGE / "adapter-probes" / "antigravity" / "latest.json"
WINDOWS_PROBE_PATH = BRIDGE / "antigravity-adapter" / "windows-probe.json"
INSTALL = Path("/mnt/d/Antigravity")
EXT_PACKAGE = INSTALL / "resources/app/extensions/antigravity/package.json"
BIN_SH = INSTALL / "bin/antigravity"
BIN_CMD = INSTALL / "bin/antigravity.cmd"
EXE = INSTALL / "Antigravity.exe"
CLI_JS_WIN = r"D:\Antigravity\resources\app\out\cli.js"
SYSTEM32_CMD = Path("/mnt/c/Windows/System32/cmd.exe")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
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
        pid = line.split(maxsplit=1)[0] if line.split(maxsplit=1) else ""
        if pid.isdigit() and int(pid) == this_pid:
            continue
        if any(marker in line for marker in [
            "adapter_probe_antigravity",
            "/tmp/adapter_probe_antigravity",
            "antigravity_adapter.py probe",
            "bwrap ",
        ]):
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


def cmd_exe_path() -> str | None:
    found = shutil.which("cmd.exe")
    if found:
        return found
    if SYSTEM32_CMD.exists():
        return str(SYSTEM32_CMD)
    return None



def read_windows_probe_artifact() -> dict[str, Any]:
    probe = read_json(WINDOWS_PROBE_PATH)
    if not isinstance(probe, dict):
        return {"attempted": False, "available": False, "reason": "windows-probe.json missing"}
    processes = probe.get("processes") if isinstance(probe.get("processes"), list) else []
    return {
        "attempted": True,
        "available": bool(probe.get("bridge_root_exists")) and isinstance(processes, list),
        "source": str(WINDOWS_PROBE_PATH),
        "checked_at": probe.get("checked_at"),
        "candidate_count": int(probe.get("process_count") or len(processes)),
        "candidates": [
            {
                "image_name": item.get("process_name", "Antigravity"),
                "pid": str(item.get("pid", "")),
                "main_window_title": item.get("main_window_title", ""),
                "path": item.get("path", ""),
            }
            for item in processes[:20]
            if isinstance(item, dict)
        ],
        "bridge_root_exists": bool(probe.get("bridge_root_exists")),
        "adapter_root_exists": bool(probe.get("adapter_root_exists")),
        "comment_lane_exists": bool(probe.get("comment_lane_exists")),
        "policy": probe.get("policy") if isinstance(probe.get("policy"), dict) else {},
    }


def windows_tasklist_antigravity(cmd_path: str | None) -> dict[str, Any]:
    """Best-effort Windows process-count proof without command-line/token data."""
    if not cmd_path:
        return {"attempted": False, "available": False, "reason": "cmd.exe unavailable from WSL"}
    try:
        completed = subprocess.run(
            [cmd_path, "/c", "tasklist", "/FI", "IMAGENAME eq Antigravity.exe", "/FO", "CSV", "/NH"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {"attempted": True, "available": False, "timeout": True}
    except Exception as exc:  # noqa: BLE001
        return {"attempted": True, "available": False, "error": str(exc)}

    candidates: list[dict[str, str]] = []
    for row in csv.reader(io.StringIO(completed.stdout)):
        if len(row) >= 2 and row[0].strip().lower() == "antigravity.exe":
            candidates.append({"image_name": row[0].strip(), "pid": row[1].strip()})
    return {
        "attempted": True,
        "available": completed.returncode == 0,
        "returncode": completed.returncode,
        "candidate_count": len(candidates),
        "candidates": candidates[:20],
        "stderr_tail": completed.stderr[-500:],
    }


def antigravity_cli_version() -> dict[str, Any]:
    """Probe the official Antigravity CLI path without reading secrets.

    `cmd.exe`/`powershell.exe` may be absent from this WSL environment, but WSL
    can still invoke the Windows Antigravity executable directly. Use the
    Electron Node CLI `--version` path as the smallest non-invasive transport
    proof; do not call `--status` here because it can include local process
    tokens in command lines.
    """
    if not EXE.exists():
        return {"attempted": False, "available": False, "reason": "Antigravity.exe missing"}
    env = os.environ.copy()
    env["ELECTRON_RUN_AS_NODE"] = "1"
    env["WSLENV"] = "ELECTRON_RUN_AS_NODE/w" if not env.get("WSLENV") else f"ELECTRON_RUN_AS_NODE/w:{env['WSLENV']}"
    try:
        completed = subprocess.run(
            [str(EXE), CLI_JS_WIN, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"attempted": True, "available": False, "timeout": True}
    except Exception as exc:  # noqa: BLE001
        return {"attempted": True, "available": False, "error": str(exc)}
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {
        "attempted": True,
        "available": completed.returncode == 0 and bool(lines),
        "returncode": completed.returncode,
        "version": lines[0] if len(lines) >= 1 else None,
        "commit": lines[1] if len(lines) >= 2 else None,
        "arch": lines[2] if len(lines) >= 3 else None,
        "stderr_tail": completed.stderr[-500:],
    }


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

    cmd_path = cmd_exe_path()
    transport = {
        "cmd.exe": cmd_path is not None,
        "powershell.exe": shutil.which("powershell.exe") is not None,
        "wslpath": shutil.which("wslpath") is not None,
    }
    windows_process_probe = windows_tasklist_antigravity(cmd_path)
    if not windows_process_probe.get("available"):
        artifact_probe = read_windows_probe_artifact()
        if artifact_probe.get("available"):
            windows_process_probe = artifact_probe
    direct_cli = antigravity_cli_version()
    if transport["cmd.exe"] or transport["powershell.exe"]:
        evidence.append(f"Windows command transport visible: {transport}")
    else:
        evidence.append("cmd.exe/powershell.exe not visible from current WSL shell")
    if direct_cli.get("available"):
        evidence.append(
            "Direct Antigravity.exe CLI transport works: "
            f"{direct_cli.get('version')} {direct_cli.get('commit')} {direct_cli.get('arch')}"
        )
    else:
        blockers.append("Direct Antigravity.exe CLI transport did not return a usable version")

    processes = pgrep("[Aa]ntigravity\\.exe|language_server_windows_x64|Electron")
    ports = listen_ports()
    if processes:
        evidence.append(f"process candidates visible: {len(processes)}")
    else:
        evidence.append("no Antigravity process visible from WSL ps")
    if ports:
        evidence.append(f"Antigravity/language-server related listening ports visible: {len(ports)}")
    else:
        evidence.append("no Antigravity/language-server listening port visible from WSL ss")
    if windows_process_probe.get("available"):
        evidence.append(
            "Windows tasklist Antigravity process candidates: "
            f"{windows_process_probe.get('candidate_count')}"
        )
    else:
        evidence.append("Windows tasklist Antigravity process probe unavailable from current WSL shell")

    if not files["install_dir"]:
        adapter_status = "adapter_missing"
    elif direct_cli.get("available"):
        adapter_status = "direct_cli_available_roundtrip_unverified"
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
    if direct_cli.get("available"):
        safe_next_actions.append("use antigravity_adapter.py register-mcp and chat-smoke, then verify a queued run_id comment")
    elif not (transport["cmd.exe"] or transport["powershell.exe"]):
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
            "direct_antigravity_exe_cli": bool(direct_cli.get("available")),
            "running_process_visible": bool(processes),
            "local_ports_visible": bool(ports),
            "windows_tasklist_visible": bool(windows_process_probe.get("available")),
            "windows_antigravity_process_visible": bool(windows_process_probe.get("candidate_count")),
            "windows_antigravity_process_count": int(windows_process_probe.get("candidate_count") or 0),
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
        "direct_cli": direct_cli,
        "windows_process_probe": windows_process_probe,
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
