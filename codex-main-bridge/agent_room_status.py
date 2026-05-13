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
from datetime import datetime
from pathlib import Path
from typing import Any

BRIDGE = Path(os.environ.get("OPENCLAW_MAILBOX_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
PROBES = {
    "codex": BRIDGE / "adapter-probes" / "codex" / "latest.json",
    "claude-code": BRIDGE / "adapter-probes" / "claude-code" / "latest.json",
    "antigravity": BRIDGE / "adapter-probes" / "antigravity" / "latest.json",
}


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
    turn = read_json(bridge / "turn.json")
    bridge_status = run_bridge_status()

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
            "seq": turn.get("seq") if isinstance(turn, dict) else None,
            "needs_reply": turn.get("needs_reply") if isinstance(turn, dict) else None,
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
            "agent_room_ready": not blocked and not unstable_baselines,
            "why_not_ready": why_not_ready,
        },
        "participants": summaries,
        "baselines": baselines,
    }
    if not args.no_write and args.write:
        write_json_atomic(args.write, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
