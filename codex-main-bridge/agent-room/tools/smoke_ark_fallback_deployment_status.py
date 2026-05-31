#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / "agent-room"
ARTIFACT_DIR = ROOM / "artifacts" / "ark-fallback-deployment-status-20260529"
WATCHER = ROOT / "openclaw-main-mailbox-watch.py"
WATCHER_STATE = ROOT / ".openclaw_main_watcher_state.json"
WATCHER_LOG = ROOT / "openclaw-main-mailbox-watch.log"
TURN_FILE = ROOT / "turn.json"
LOCAL_ACCEPTANCE_SMOKE = ROOM / "tools" / "smoke_main_ark_fallback_acceptance.py"
LOCAL_ACCEPTANCE_RESULT = ROOT / "dry-runs" / "main_ark_fallback_acceptance" / "latest.json"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_FILE = SYSTEMD_USER_DIR / "openclaw-main-mailbox-watch.service"
TIMER_FILE = SYSTEMD_USER_DIR / "openclaw-main-mailbox-watch.timer"
TIMER_WANTS = SYSTEMD_USER_DIR / "timers.target.wants" / "openclaw-main-mailbox-watch.timer"


def now_dt() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def stamp() -> str:
    return now_dt().strftime("%Y%m%d-%H%M%S")


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text) > limit:
        return text[-limit:]
    return text


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_systemctl_show() -> dict[str, Any]:
    command = [
        "systemctl",
        "--user",
        "show",
        "openclaw-main-mailbox-watch.service",
        "--property=ActiveState,SubState,MainPID",
        "--no-pager",
    ]
    try:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {
            "attempted": True,
            "ok": False,
            "command": command,
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }
    return {
        "attempted": True,
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:500],
        "stderr": (proc.stderr or "").strip()[:500],
    }


def systemd_deployment() -> dict[str, Any]:
    unit_text = read_text(UNIT_FILE, limit=4000)
    timer_text = read_text(TIMER_FILE, limit=4000)
    expected_exec = f"ExecStart=/usr/bin/python3 {WATCHER}"
    timer_target = ""
    try:
        timer_target = str(TIMER_WANTS.resolve(strict=True))
    except OSError:
        timer_target = ""
    return {
        "unit_file": str(UNIT_FILE),
        "unit_exists": UNIT_FILE.exists(),
        "unit_execstart_matches": expected_exec in unit_text,
        "timer_file": str(TIMER_FILE),
        "timer_exists": TIMER_FILE.exists(),
        "timer_has_one_minute_interval": "OnUnitActiveSec=1min" in timer_text,
        "timer_wants_link": str(TIMER_WANTS),
        "timer_wants_link_exists": TIMER_WANTS.is_symlink() or TIMER_WANTS.exists(),
        "timer_wants_target": timer_target,
        "systemctl_user_show": run_systemctl_show(),
    }


def production_state() -> dict[str, Any]:
    state = read_json(WATCHER_STATE)
    queue = state.get("main_local_no_tool_fallback_queue")
    failures = state.get("main_ark_model_failures")
    safe_failures = {}
    if isinstance(failures, dict):
        for model, record in failures.items():
            if isinstance(record, dict):
                safe_failures[str(model)] = {
                    "failed_at": record.get("failed_at"),
                    "cooldown_until": record.get("cooldown_until"),
                    "retryable": record.get("retryable"),
                    "reason": record.get("reason"),
                    "status": record.get("status"),
                }
    safe_state_keys = [
        "main_quota_state",
        "last_status",
        "last_post_trigger_status",
        "last_triggered_seq",
        "last_observed_at",
        "last_retryable_failure_class",
        "main_ark_fallback_last_detail",
        "main_ark_fallback_last_success_at",
        "main_ark_fallback_last_success_seq",
        "main_ark_fallback_last_success_model",
        "main_ark_fallback_last_success_reply_chars",
        "main_local_no_tool_fallback_active",
        "main_local_no_tool_fallback_last_cleared_status",
        "main_local_no_tool_fallback_last_cleared_seq",
        "main_local_no_tool_fallback_last_cleared_at",
        "main_local_no_tool_fallback_last_exhausted_seq",
    ]
    return {
        "state_file": str(WATCHER_STATE),
        "state_exists": WATCHER_STATE.exists(),
        "safe_state": {key: state.get(key) for key in safe_state_keys if key in state},
        "queue_len": len(queue) if isinstance(queue, list) else 0,
        "queue_tail": [
            {
                key: value
                for key, value in item.items()
                if key not in {"detail", "retry_exhausted_detail"}
            }
            for item in (queue[-3:] if isinstance(queue, list) else [])
            if isinstance(item, dict)
        ],
        "ark_model_failures": safe_failures,
    }


def production_log_evidence() -> dict[str, Any]:
    text = read_text(WATCHER_LOG)
    return {
        "log_file": str(WATCHER_LOG),
        "log_exists": WATCHER_LOG.exists(),
        "ark_fallback_success_count": text.count("ark_fallback_success"),
        "ark_fallback_advanced_count": text.count("ark_fallback_advanced"),
        "ark_fallback_failed_count": text.count("ark_fallback_failed"),
        "ark_fallback_all_models_failed_count": text.count("ark_fallback_all_models_failed"),
        "ark_fallback_all_models_on_cooldown_count": text.count("ark_fallback_all_models_on_cooldown"),
        "main_no_tool_fallback_retrying_count": text.count("main_no_tool_fallback_retrying"),
        "main_no_tool_fallback_retry_deferred_count": text.count("main_no_tool_fallback_retry_deferred"),
        "latest_ark_lines": [
            line
            for line in text.splitlines()
            if "ark_fallback" in line or "main_no_tool_fallback" in line
        ][-20:],
    }


def local_acceptance_smoke() -> dict[str, Any]:
    result = read_json(LOCAL_ACCEPTANCE_RESULT)
    script_text = read_text(LOCAL_ACCEPTANCE_SMOKE, limit=20000)
    run_dir = str(result.get("run_dir") or "")
    mode = str(result.get("mode") or "unknown").lower()
    fake_markers = [
        mode == "fake",
        "fake_provider" in script_text,
        "types.ModuleType(\"direct_provider_lane\")" in script_text,
        "dry-runs/main_ark_fallback_acceptance" in str(LOCAL_ACCEPTANCE_RESULT),
        "/dry-runs/main_ark_fallback_acceptance/" in run_dir,
    ]
    live_markers = [
        mode == "live",
        bool(result.get("state_success_fields") and result["state_success_fields"].get("main_ark_fallback_last_success_seq")),
        isinstance(result.get("tail_log"), list),
    ]
    return {
        "script": str(LOCAL_ACCEPTANCE_SMOKE),
        "latest_result": str(LOCAL_ACCEPTANCE_RESULT),
        "latest_result_exists": LOCAL_ACCEPTANCE_RESULT.exists(),
        "latest_result_ok": bool(result.get("ok")),
        "latest_detail": result.get("detail"),
        "mode": mode,
        "result": result,
        "provider_model": result.get("provider_model"),
        "classified_as": (
            "local_fake_provider_success"
            if mode == "fake"
            else (
                "local_live_provider_attempt"
                if mode == "live"
                else "unknown_or_live_unverified"
            )
        ),
        "fake_provider_markers": fake_markers,
        "live_markers": live_markers,
    }


def turn_snapshot() -> dict[str, Any]:
    turn = read_json(TURN_FILE)
    return {
        "turn_file": str(TURN_FILE),
        "turn_exists": TURN_FILE.exists(),
        "seq": turn.get("seq"),
        "needs_reply": turn.get("needs_reply"),
        "last_writer": turn.get("last_writer"),
        "main_backend": turn.get("main_backend"),
        "main_ark_model": turn.get("main_ark_model"),
        "updated_at": turn.get("updated_at"),
    }


def build_report() -> dict[str, Any]:
    deployment = systemd_deployment()
    state = production_state()
    logs = production_log_evidence()
    local_smoke = local_acceptance_smoke()
    turn = turn_snapshot()
    safe_state = state.get("safe_state") or {}
    local_mode = str(local_smoke.get("mode") or "")
    local_result = local_smoke.get("result") if isinstance(local_smoke.get("result"), dict) else {}
    local_state = (
        local_result.get("state_success_fields")
        if isinstance(local_result.get("state_success_fields"), dict)
        else {}
    )
    local_success_seq = str(local_state.get("main_ark_fallback_last_success_seq") or "")
    local_success_model = str(local_state.get("main_ark_fallback_last_success_model") or "")
    local_success_reply_chars = str(local_state.get("main_ark_fallback_last_success_reply_chars") or "")
    live_mode_ok = local_mode == "live" and bool(local_result.get("ok"))
    live_success_observed = bool(
        live_mode_ok
        and safe_state.get("main_ark_fallback_last_success_at")
        and safe_state.get("main_ark_fallback_last_success_seq")
        and logs.get("ark_fallback_success_count", 0) > 0
        and (
            not local_success_seq
            or str(safe_state.get("main_ark_fallback_last_success_seq")) == local_success_seq
        )
        and (
            not local_success_model
            or safe_state.get("main_ark_fallback_last_success_model") == local_success_model
        )
    )
    live_state_fields_match = bool(
        live_mode_ok
        and safe_state.get("main_ark_fallback_last_success_seq")
        and str(safe_state.get("main_ark_fallback_last_success_seq", "")) == local_success_seq
        and str(safe_state.get("main_ark_fallback_last_success_model", "")) == local_success_model
        and str(safe_state.get("main_ark_fallback_last_success_reply_chars", "")) == local_success_reply_chars
    )
    all_lane_cooldown_failure_observed = bool(
        logs.get("ark_fallback_all_models_failed_count", 0) > 0
        or logs.get("ark_fallback_all_models_on_cooldown_count", 0) > 0
        or str(safe_state.get("main_ark_fallback_last_detail") or "").startswith("ark_fallback_all_models")
    )
    all_lane_cooldown_e2e_observed = bool(
        all_lane_cooldown_failure_observed
        and (
            logs.get("main_no_tool_fallback_retrying_count", 0) > 0
            or logs.get("main_no_tool_fallback_retry_deferred_count", 0) > 0
            or state.get("queue_len", 0) > 0
            or safe_state.get("main_local_no_tool_fallback_last_exhausted_seq")
        )
        and (
            live_success_observed
            or safe_state.get("main_local_no_tool_fallback_last_cleared_status") == "ark_fallback_advanced"
        )
    )
    structural_ok = bool(
        deployment.get("unit_exists")
        and deployment.get("unit_execstart_matches")
        and deployment.get("timer_exists")
        and deployment.get("timer_wants_link_exists")
        and (local_smoke.get("mode") in {"fake", "live"})
        and local_smoke.get("latest_result_ok")
    )
    verdict = (
        "complete"
        if live_success_observed and all_lane_cooldown_e2e_observed
        else "blocked_missing_live_ark_acceptance"
    )
    return {
        "schema": "openclaw.agent_room.ark_fallback_deployment_status_smoke.v0",
        "generated_at": now_iso(),
        "ok": True,
        "external_side_effects": False,
        "secrets_read": False,
        "systemd_deployment": deployment,
        "production_state": state,
        "production_log_evidence": logs,
        "turn_snapshot": turn,
        "local_fake_provider_smoke": local_smoke,
        "acceptance": {
            "structural_success_path_smoked": structural_ok,
            "live_smoke_path": local_result.get("run_dir") if local_mode == "live" else "",
            "production_real_ark_success_observed": live_success_observed,
            "live_state_fields_match": live_state_fields_match,
            "live_fallback_observation_mode": local_mode,
            "all_lane_cooldown_failure_observed": all_lane_cooldown_failure_observed,
            "all_lane_cooldown_e2e_observed": all_lane_cooldown_e2e_observed,
            "verdict": verdict,
            "local_acceptance_tail_trace": local_result.get("tail_log")[-5:] if isinstance(local_result.get("tail_log"), list) else [],
            "blocker_id": None if verdict == "complete" else "ARK-LIVE-PROVIDER-ACCEPTANCE-MISSING",
            "blocker_reason": None
            if verdict == "complete"
            else ("Live Ark smoke未设置或未通过：ARK_SMOKE_MODE=live 的 smoke 结果未形成可验证的 production 级别 success 字段"
                  if local_mode != "live"
                  else "Live smoke 已执行但未形成 production 级别 success 与 state/log 一致证据。"),
        },
    }


def markdown(report: dict[str, Any], json_path: Path) -> str:
    acceptance = report["acceptance"]
    deployment = report["systemd_deployment"]
    state = report["production_state"]["safe_state"]
    logs = report["production_log_evidence"]
    local = report["local_fake_provider_smoke"]
    lines = [
        "# Ark fallback deployment status smoke",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- json: {json_path}",
        f"- verdict: {acceptance['verdict']}",
        f"- blocker_id: {acceptance['blocker_id'] or '(none)'}",
        f"- secrets_read: {report['secrets_read']}",
        f"- external_side_effects: {report['external_side_effects']}",
        "",
        "## Evidence",
        "",
        f"- systemd unit exists: {deployment['unit_exists']}",
        f"- systemd ExecStart points to watcher: {deployment['unit_execstart_matches']}",
        f"- timer exists and is linked from timers.target.wants: {deployment['timer_exists']} / {deployment['timer_wants_link_exists']}",
        f"- systemctl user bus verified: {deployment['systemctl_user_show'].get('ok')} ({deployment['systemctl_user_show'].get('stderr') or deployment['systemctl_user_show'].get('stdout') or 'no output'})",
        f"- local success-path smoke: {local['classified_as']} ok={local['latest_result_ok']} mode={local.get('mode')} detail={local['latest_detail']}",
        f"- smoke markers: fake_markers={local.get('fake_provider_markers')} live_markers={local.get('live_markers')}",
        f"- live acceptance path: {acceptance['live_fallback_observation_mode']} live_path={acceptance.get('live_smoke_path')}",
        f"- live state fields match: {acceptance['live_state_fields_match']}",
        f"- production Ark success log count: {logs['ark_fallback_success_count']}",
        f"- production Ark advanced log count: {logs['ark_fallback_advanced_count']}",
        f"- all-models-failed/cooldown counts: {logs['ark_fallback_all_models_failed_count']} / {logs['ark_fallback_all_models_on_cooldown_count']}",
        f"- no-tool retrying/deferred counts: {logs['main_no_tool_fallback_retrying_count']} / {logs['main_no_tool_fallback_retry_deferred_count']}",
        f"- main_quota_state: {state.get('main_quota_state')}",
        f"- last_post_trigger_status: {state.get('last_post_trigger_status')}",
        f"- no-tool queue len: {report['production_state']['queue_len']}",
        f"- last Ark detail: {state.get('main_ark_fallback_last_detail')}",
        "",
        "## Boundary correction",
        "",
        "The acceptance script now records `mode=fake|live` in `dry-runs/main_ark_fallback_acceptance/latest.json`.",
        "Only `live` mode plus production state/log alignment closes `production_real_ark_success_observed`.",
        "",
        "## Next live acceptance gate",
        "",
        "- Run `ARK_SMOKE_MODE=live python3 agent-room/tools/smoke_main_ark_fallback_acceptance.py` in an isolated env.",
        "- Use real `direct_provider_lane` (no fake injection) and require `ok=true` with `mode=live`.",
        "- Verify reporter fields `production_real_ark_success_observed`、`live_state_fields_match` 同时成立。",
        "- For all-lane cooldown e2e, verify cooldown failure marker + retry queue deferral + clear/advance in live production log/state.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ARK_FALLBACK_DEPLOYMENT_STATUS_SMOKE_{stamp()}"
    json_path = ARTIFACT_DIR / f"{name}.json"
    md_path = ARTIFACT_DIR / f"{name}.md"
    write_json(json_path, report)
    md_path.write_text(markdown(report, json_path), encoding="utf-8")
    report["artifact_json"] = str(json_path)
    report["artifact_markdown"] = str(md_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
