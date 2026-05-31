#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


watcher = load_module(ROOT / "openclaw-main-mailbox-watch.py", "openclaw_main_mailbox_watch_acceptance_smoke")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_fake_provider_smoke(watcher, dry_root: Path) -> tuple[dict, Path]:
    old_attrs = {
        name: getattr(watcher, name)
        for name in (
            "TURN_FILE",
            "CODEX_FILE",
            "MAIN_FILE",
            "LOG_FILE",
            "AGENT_ROOM_STATUS_FILE",
            "MAIN_ARK_MODEL",
            "MAIN_ARK_FALLBACK_MODELS_RAW",
        )
    }
    old_direct_provider = sys.modules.get("direct_provider_lane")

    fake_provider = types.ModuleType("direct_provider_lane")

    class DirectProviderError(Exception):
        kind = "fake_provider_error"
        detail = {}
        status = None

    calls: list[dict] = []

    def run_direct_provider_text_prompt(**kwargs):
        calls.append(dict(kwargs))
        return {"text": "Ark fallback smoke reply from fake provider."}

    fake_provider.DirectProviderError = DirectProviderError
    fake_provider.run_direct_provider_text_prompt = run_direct_provider_text_prompt
    sys.modules["direct_provider_lane"] = fake_provider

    try:
        run_dir = dry_root / "run_fake"
        run_dir.mkdir(parents=True, exist_ok=True)
        watcher.TURN_FILE = run_dir / "turn.json"
        watcher.CODEX_FILE = run_dir / "codex_to_main.md"
        watcher.MAIN_FILE = run_dir / "main_to_codex.md"
        watcher.LOG_FILE = run_dir / "openclaw-main-mailbox-watch.log"
        watcher.AGENT_ROOM_STATUS_FILE = run_dir / "agent_room_status.json"
        watcher.MAIN_ARK_MODEL = "minimax-m2.7"
        watcher.MAIN_ARK_FALLBACK_MODELS_RAW = "minimax-m2.7,deepseek-v4-pro,glm-5.1,kimi-k2.6"

        write_json(
            watcher.TURN_FILE,
            {
                "seq": 42,
                "last_writer": "codex",
                "needs_reply": "main",
                "updated_at": "2026-05-29T00:00:00+08:00",
            },
        )
        watcher.CODEX_FILE.write_text(
            "Codex smoke asks main to answer through the Ark fallback path.\n",
            encoding="utf-8",
        )

        state: dict = {}
        ok, detail = watcher.run_main_via_ark_fallback("smoke instruction", "42", state)
        turn_after = json.loads(watcher.TURN_FILE.read_text(encoding="utf-8"))
        reply_text = watcher.MAIN_FILE.read_text(encoding="utf-8")

        checks = {
            "provider_called_once": len(calls) == 1,
            "success_returned": ok is True and detail == "ark_fallback_ok:model=minimax-m2.7",
            "reply_written": reply_text.strip() == "Ark fallback smoke reply from fake provider.",
            "turn_advanced": turn_after.get("seq") == 43 and turn_after.get("needs_reply") == "codex",
            "turn_records_backend": turn_after.get("main_backend") == "ark_fallback",
            "turn_records_model": turn_after.get("main_ark_model") == "minimax-m2.7",
            "state_records_success_time": bool(state.get("main_ark_fallback_last_success_at")),
            "state_records_success_model": state.get("main_ark_fallback_last_success_model") == "minimax-m2.7",
            "state_records_success_seq": state.get("main_ark_fallback_last_success_seq") == "42",
            "state_records_reply_chars": state.get("main_ark_fallback_last_success_reply_chars") == len(reply_text.strip()),
            "no_no_tool_queue_on_success": not state.get("main_local_no_tool_fallback_queue"),
        }
        checks["fake_all_models_failure_marker"] = (
            ok is True
            or "ark_fallback_all_models" in str(state.get("main_ark_fallback_last_detail") or "")
        )

        state_queue = state.get("main_local_no_tool_fallback_queue")

        log_text = watcher.LOG_FILE.read_text(encoding="utf-8")
        return (
            {
                "schema": "openclaw.main_ark_fallback_acceptance_smoke.v0",
                "ok": all(checks.values()),
                "mode": "fake",
                "classified_as": "local_fake_provider_success" if all(checks.values()) else "fake_provider_smoke_failed",
                "run_dir": str(run_dir),
                "detail": detail,
                "checks": checks,
                "provider_model": calls[0].get("model") if calls else "",
                "turn_after": turn_after,
                "state_success_fields": {
                    key: state.get(key)
                    for key in (
                        "main_quota_state",
                        "main_ark_fallback_last_used_at",
                        "main_ark_fallback_last_success_at",
                        "main_ark_fallback_last_success_seq",
                        "main_ark_fallback_last_success_model",
                        "main_ark_fallback_last_success_reply_chars",
                        "main_ark_fallback_last_seq",
                        "main_ark_fallback_last_model",
                    )
                },
                "state_all_lane_fields": {
                    key: state.get(key)
                    for key in (
                        "main_ark_fallback_last_detail",
                        "main_local_no_tool_fallback_active",
                        "main_local_no_tool_fallback_last_cleared_status",
                        "main_local_no_tool_fallback_last_cleared_seq",
                        "main_local_no_tool_fallback_last_cleared_at",
                        "main_local_no_tool_fallback_next_retry_epoch",
                        "main_local_no_tool_fallback_next_retry_seq",
                    )
                },
                "state_queue": state_queue,
                "provider_call_count": len(calls),
                "provider_payload_model": calls[0].get("model") if calls else "",
                "tail_log": log_text.splitlines()[-20:],
            },
            run_dir,
        )
    finally:
        for name, value in old_attrs.items():
            setattr(watcher, name, value)
        if old_direct_provider is None:
            sys.modules.pop("direct_provider_lane", None)
        else:
            sys.modules["direct_provider_lane"] = old_direct_provider


def run_live_provider_smoke(watcher, dry_root: Path) -> tuple[dict, Path]:
    forced_model = os.environ.get("ARK_SMOKE_MODEL", "").strip()
    old_attrs = {
        name: getattr(watcher, name)
        for name in (
            "TURN_FILE",
            "CODEX_FILE",
            "MAIN_FILE",
            "LOG_FILE",
            "AGENT_ROOM_STATUS_FILE",
            "MAIN_ARK_MODEL",
            "MAIN_ARK_FALLBACK_MODELS_RAW",
        )
    }
    old_direct_provider = sys.modules.get("direct_provider_lane")

    try:
        run_dir = dry_root / "run_live"
        run_dir.mkdir(parents=True, exist_ok=True)
        watcher.TURN_FILE = run_dir / "turn.json"
        watcher.CODEX_FILE = run_dir / "codex_to_main.md"
        watcher.MAIN_FILE = run_dir / "main_to_codex.md"
        watcher.LOG_FILE = run_dir / "openclaw-main-mailbox-watch.log"
        watcher.AGENT_ROOM_STATUS_FILE = run_dir / "agent_room_status.json"
        if forced_model:
            watcher.MAIN_ARK_MODEL = forced_model
            watcher.MAIN_ARK_FALLBACK_MODELS_RAW = forced_model

        if old_direct_provider is None:
            # no fake injector
            pass
        else:
            sys.modules.pop("direct_provider_lane", None)

        write_json(
            watcher.TURN_FILE,
            {
                "seq": 42,
                "last_writer": "codex",
                "needs_reply": "main",
                "updated_at": "2026-05-29T00:00:00+08:00",
            },
        )
        watcher.CODEX_FILE.write_text(
            "Codex smoke asks main to answer through the real Ark fallback path.\n",
            encoding="utf-8",
        )

        before_log = ""
        if watcher.LOG_FILE.exists():
            before_log = watcher.LOG_FILE.read_text(encoding="utf-8")

        state: dict = {}
        ok, detail = watcher.run_main_via_ark_fallback("smoke instruction", "42", state)
        after_log = watcher.LOG_FILE.read_text(encoding="utf-8") if watcher.LOG_FILE.exists() else ""
        new_lines = after_log[len(before_log) :]
        log_lines = [line for line in new_lines.splitlines() if "ark_fallback" in line]
        turn_after = json.loads(watcher.TURN_FILE.read_text(encoding="utf-8"))
        reply_text = watcher.MAIN_FILE.read_text(encoding="utf-8") if watcher.MAIN_FILE.exists() else ""
        selected_model = state.get("main_ark_fallback_last_success_model") or state.get("main_ark_model", "")
        model_attempts = state.get("main_ark_fallback_model_attempts") or []
        candidate_models = state.get("main_ark_fallback_candidate_models") or []
        model_failures = state.get("main_ark_model_failures") or {}
        if not isinstance(model_attempts, list):
            model_attempts = []
        if not isinstance(candidate_models, list):
            candidate_models = []
        if not isinstance(model_failures, dict):
            model_failures = {}

        attempts_by_model = {
            str(item.get("model", "")).strip(): item
            for item in model_attempts
            if isinstance(item, dict)
        }
        all_retryable_failures_with_cooldown = False
        if candidate_models:
            all_retryable_failures_with_cooldown = all(
                isinstance(model, str)
                and str(model) in attempts_by_model
                and attempts_by_model[str(model)].get("status") == "failed"
                and bool(attempts_by_model[str(model)].get("retryable"))
                and str(model_failures.get(str(model), {}).get("cooldown_until") or "")
                for model in candidate_models
            )

        queue_entry = state.get("main_local_no_tool_fallback_last_entry")
        queue_entry_ok = False
        if isinstance(queue_entry, dict):
            queue_entry_ok = (
                str(queue_entry.get("seq", "") or "")
                == str(state.get("main_ark_fallback_last_success_seq") or "").replace("_", "")
                or str(queue_entry.get("seq", "")) == "42"
                or str(queue_entry.get("status", "")) in {"queued", "retrying"}
            ) and bool(queue_entry.get("reason"))

        checks = {
            "real_success_returned": ok is True,
            "turn_advanced": turn_after.get("seq") == 43 and turn_after.get("needs_reply") == "codex",
            "main_reply_present": bool(reply_text.strip()),
            "live_ark_success_log": any("ark_fallback_success" in line for line in log_lines),
            "state_records_success_time": bool(state.get("main_ark_fallback_last_success_at")),
            "state_records_success_model": bool(state.get("main_ark_fallback_last_success_model")),
            "state_records_success_seq": state.get("main_ark_fallback_last_success_seq") == "42",
            "state_records_reply_chars": state.get("main_ark_fallback_last_success_reply_chars", 0) == len(
                reply_text.strip()
            ),
            "live_all_models_failure_marker": (
                ok is False
                and (
                    "ark_fallback_all_models" in detail
                    or "all_models_on_cooldown" in detail
                    or "all_models_failed" in detail
                )
            ),
            "live_all_models_retryable_failed_with_cooldown": bool(all_retryable_failures_with_cooldown),
            "live_all_models_queue_entry": queue_entry_ok,
        }
        state_queue = state.get("main_local_no_tool_fallback_queue")
        if checks["real_success_returned"]:
            checks["live_queue_marked_after_all_lane_failure"] = True
        elif not checks["live_all_models_failure_marker"]:
            checks["live_queue_marked_after_all_lane_failure"] = True
        else:
            checks["live_queue_marked_after_all_lane_failure"] = (
                isinstance(state_queue, list)
                and any(
                    isinstance(item, dict)
                    and str(item.get("status") or "") in {"queued", "retrying"}
                    for item in state_queue
                )
            )
        return (
            {
                "schema": "openclaw.main_ark_fallback_acceptance_smoke.v0",
                "ok": all(checks.values()),
                "mode": "live",
                "classified_as": (
                    "live_provider_attempt"
                    if all(checks.values())
                    else (
                        "live_all_lane_retryable_blocked"
                        if (
                            checks["live_all_models_failure_marker"]
                            and checks["live_all_models_retryable_failed_with_cooldown"]
                            and checks["live_all_models_queue_entry"]
                        )
                        else "live_provider_blocked"
                    )
                ),
                "run_dir": str(run_dir),
                "detail": detail,
                "checks": checks,
                "provider_model": selected_model,
                "all_lane_retryable_failures_with_cooldown": all_retryable_failures_with_cooldown,
                "all_lane_queue_entry": queue_entry if isinstance(queue_entry, dict) else None,
                "main_ark_model_failures": {
                    model: {
                        "status": record.get("status"),
                        "reason": record.get("reason"),
                        "cooldown_until": record.get("cooldown_until"),
                    }
                    for model, record in model_failures.items()
                    if isinstance(record, dict)
                },
                "turn_after": turn_after,
                "state_success_fields": {
                    key: state.get(key)
                    for key in (
                        "main_quota_state",
                        "main_ark_fallback_last_used_at",
                        "main_ark_fallback_last_success_at",
                        "main_ark_fallback_last_success_seq",
                        "main_ark_fallback_last_success_model",
                        "main_ark_fallback_last_success_reply_chars",
                        "main_ark_fallback_last_seq",
                        "main_ark_fallback_last_model",
                    )
                },
                "state_all_lane_fields": {
                    key: state.get(key)
                    for key in (
                        "main_ark_fallback_last_detail",
                        "main_local_no_tool_fallback_active",
                        "main_local_no_tool_fallback_last_cleared_status",
                        "main_local_no_tool_fallback_last_cleared_seq",
                        "main_local_no_tool_fallback_last_cleared_at",
                        "main_local_no_tool_fallback_next_retry_epoch",
                        "main_local_no_tool_fallback_next_retry_seq",
                        "main_local_no_tool_fallback_last_entry",
                        "main_ark_fallback_candidate_models",
                        "main_ark_fallback_model_attempts",
                        "main_ark_fallback_skipped_models",
                    )
                },
                "state_queue": state_queue,
                "provider_payload_model": selected_model,
                "tail_log": log_lines[-20:],
                "failure_reason": detail if not ok else "",
            },
            run_dir,
        )
    finally:
        for name, value in old_attrs.items():
            setattr(watcher, name, value)
        if old_direct_provider is None:
            sys.modules.pop("direct_provider_lane", None)
        else:
            sys.modules["direct_provider_lane"] = old_direct_provider


def main() -> int:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    dry_root = ROOT / "dry-runs" / "main_ark_fallback_acceptance"
    run_dir = dry_root / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = os.environ.get("ARK_SMOKE_MODE", "fake").strip().lower()
    if mode not in {"fake", "live", ""}:
        print(json.dumps({"schema": "openclaw.main_ark_fallback_acceptance_smoke.v0", "ok": False, "mode": mode, "error": "invalid ARK_SMOKE_MODE"}))
        return 1

    if mode == "live":
        result, _ = run_live_provider_smoke(watcher, dry_root)
    else:
        result, _ = run_fake_provider_smoke(watcher, dry_root)

    write_json(Path(result["run_dir"]) / "result.json", result)
    write_json(dry_root / "latest.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
