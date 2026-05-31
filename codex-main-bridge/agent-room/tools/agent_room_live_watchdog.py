#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
APPROVAL = ROOM / "live_approval_epoch.json"
HOLD = ROOM / "live_hold_epoch.json"
STATUS = ROOM / "agent_room_live_watchdog.status.json"
UNIT = "openclaw-agent-room-bridge-daemon.service"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_read_error": type(exc).__name__ + ": " + str(exc)}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def approved() -> tuple[bool, str, dict[str, Any] | None]:
    approval = read_json(APPROVAL)
    if not isinstance(approval, dict):
        return False, "missing_live_approval_epoch", None
    if approval.get("status") not in {"active", "approved"}:
        return False, "live_approval_epoch_not_active", approval
    if not approval.get("live_runtime_enabled", False):
        return False, "live_runtime_not_enabled_in_epoch", approval
    hold = read_json(HOLD)
    if isinstance(hold, dict) and hold.get("status", "active") in {"active", "hold"}:
        hold_at = str(hold.get("created_at") or hold.get("updated_at") or "")
        approved_at = str(approval.get("approved_at") or approval.get("created_at") or "")
        if not approved_at or hold_at >= approved_at:
            return False, "newer_or_active_live_hold_epoch", approval
    return True, "approved", approval


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    ok, reason, approval = approved()
    state_proc = systemctl("show", UNIT, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "Result")
    active = "ActiveState=active" in state_proc.stdout
    action = "noop"
    start_result = None
    if ok and not active:
        start_proc = systemctl("start", UNIT)
        start_result = {"returncode": start_proc.returncode, "stdout": start_proc.stdout[-1200:], "stderr": start_proc.stderr[-1200:]}
        action = "start_daemon" if start_proc.returncode == 0 else "start_failed"
    elif not ok:
        action = "noop_not_live_approved"
    else:
        action = "noop_daemon_active"
    final_proc = systemctl("show", UNIT, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "Result")
    payload = {
        "schema": "openclaw.agent_room.live_watchdog_status.v0",
        "updated_at": now_iso(),
        "approval_ok": ok,
        "approval_reason": reason,
        "approval_epoch_id": approval.get("epoch_id") if isinstance(approval, dict) else None,
        "action": action,
        "before": state_proc.stdout.strip(),
        "after": final_proc.stdout.strip(),
        "start_result": start_result,
        "tokens_printed": False,
    }
    write_json(STATUS, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if action != "start_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
