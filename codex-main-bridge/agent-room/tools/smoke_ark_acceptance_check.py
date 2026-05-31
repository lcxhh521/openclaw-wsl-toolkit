#!/usr/bin/env python3
"""Read-only Ark acceptance verification for the architecture-intake-to-execution-loop agenda item.

Checks watcher state and run logs for evidence of a real Ark provider success
event for openclaw-main after GPT quota depletion.

Exit 0 if at least one acceptance criterion is met; exit 1 otherwise.
Prints a JSON summary of findings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ROOT / ".openclaw_main_watcher_state.json"
RUN_LOG_DIR = ROOT / "watch-runs"
WATCHER_LOG = ROOT / "openclaw-main-mailbox-watch.log"


def check_watcher_state() -> dict:
    """Check .openclaw_main_watcher_state.json for Ark success evidence."""
    findings: dict = {"path": str(STATE_FILE), "exists": False, "accepted": False, "evidence": []}

    if not STATE_FILE.exists():
        findings["evidence"].append("state file not found")
        return findings

    findings["exists"] = True
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings["evidence"].append(f"state file unreadable: {exc}")
        return findings

    # Criterion 2: state records a success timestamp and model
    success_at = state.get("main_ark_fallback_last_success_at")
    success_model = state.get("main_ark_fallback_last_success_model")
    if success_at and success_model:
        findings["accepted"] = True
        findings["evidence"].append(f"state success_at={success_at} model={success_model}")

    # Also check last_post_trigger_status for ark_fallback_advanced
    last_status = state.get("last_post_trigger_status", "")
    if "ark_fallback_advanced" in last_status and "exhausted" not in last_status and "deferred" not in last_status:
        if "retry_exhausted" not in last_status:
            findings["accepted"] = True
            findings["evidence"].append(f"last_post_trigger_status={last_status}")

    # Check fallback detail for success pattern (actual return is ark_fallback_ok)
    fallback_detail = state.get("main_ark_fallback_last_detail", "")
    if "ark_fallback_ok" in fallback_detail.lower() or "ark_fallback_success" in fallback_detail.lower():
        findings["accepted"] = True
        findings["evidence"].append(f"fallback_detail contains success: {fallback_detail[:120]}")

    if not findings["evidence"]:
        findings["evidence"].append(
            f"no ark success in state: quota={state.get('main_quota_state')}, "
            f"last_detail={fallback_detail[:100]}"
        )

    return findings


def check_run_logs() -> dict:
    """Check watch-runs logs and watcher log for ark_fallback_advanced events."""
    findings: dict = {"path": str(RUN_LOG_DIR), "exists": False, "accepted": False, "evidence": []}

    if not RUN_LOG_DIR.exists():
        findings["evidence"].append("run log directory not found")
        return findings

    findings["exists"] = True
    # The actual log writes "ark_fallback_success seq=N model=M reply_chars=N"
    # (openclaw-main-mailbox-watch.py line 1219), NOT "ark_fallback_advanced ... detail=ark_fallback_success"
    ark_success_pattern = re.compile(r"ark_fallback_success\s+seq=\d+\s+model=\S+")
    ark_advanced_pattern = re.compile(r"ark_fallback_advanced\s+seq=\d+\s+detail=ark_fallback_ok:\S+")
    completed_pattern = re.compile(r"completed_ark_fallback_advanced")

    # Check active-run.json first
    active_run = RUN_LOG_DIR / "active-run.json"
    if active_run.exists():
        try:
            ar = json.loads(active_run.read_text(encoding="utf-8"))
            if ar.get("status") == "completed_ark_fallback_advanced":
                findings["accepted"] = True
                findings["evidence"].append(
                    f"active-run.json status=completed_ark_fallback_advanced "
                    f"seq={ar.get('seq')} detail={ar.get('ark_fallback_detail','')[:80]}"
                )
        except (json.JSONDecodeError, OSError):
            pass

    # Scan recent log files (last 20)
    log_files = sorted(RUN_LOG_DIR.glob("seq-*.log"), reverse=True)[:20]
    for log_path in log_files:
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in ark_success_pattern.finditer(content):
            findings["accepted"] = True
            findings["evidence"].append(f"{log_path.name}: {match.group()[:120]}")
        for match in ark_advanced_pattern.finditer(content):
            findings["accepted"] = True
            findings["evidence"].append(f"{log_path.name}: {match.group()[:120]}")
        if completed_pattern.search(content):
            for line in content.splitlines():
                if "completed_ark_fallback_advanced" in line:
                    findings["accepted"] = True
                    findings["evidence"].append(f"{log_path.name}: {line[:120]}")
                    break

    # Also check the watcher log (where ark_fallback_success is actually written)
    if WATCHER_LOG.exists():
        try:
            # Read last 5000 lines to keep bounded
            watcher_lines = WATCHER_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in watcher_lines[-5000:]:
                if ark_success_pattern.search(line):
                    findings["accepted"] = True
                    findings["evidence"].append(f"watcher_log: {line.strip()[:120]}")
                elif ark_advanced_pattern.search(line):
                    findings["accepted"] = True
                    findings["evidence"].append(f"watcher_log: {line.strip()[:120]}")
        except OSError:
            pass

    if not findings["evidence"]:
        findings["evidence"].append(f"no ark_fallback_success/advanced in last {len(log_files)} logs or watcher log")

    return findings


def main() -> int:
    state_result = check_watcher_state()
    log_result = check_run_logs()

    accepted = state_result["accepted"] or log_result["accepted"]
    summary = {
        "schema": "openclaw.agent_room.ark_acceptance_check.v0",
        "accepted": accepted,
        "watcher_state": state_result,
        "run_logs": log_result,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
