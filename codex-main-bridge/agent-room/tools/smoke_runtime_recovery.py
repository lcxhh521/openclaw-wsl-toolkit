#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import argparse
import inspect
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent

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


watcher = load_module(ROOT / "openclaw-main-mailbox-watch.py", "openclaw_main_mailbox_watch")
agent_room_status = load_module(ROOT / "agent_room_status.py", "agent_room_status")
resident = load_module(TOOLS / "agent_room_resident_bridge.py", "agent_room_resident_bridge")
agent_room_bridge_daemon = load_module(TOOLS / "agent_room_bridge_daemon.py", "agent_room_bridge_daemon")
agent_task_runner = load_module(TOOLS / "agent_task_runner.py", "agent_task_runner")
quota_ledger = load_module(TOOLS / "quota_ledger.py", "quota_ledger")
telegram_agent_bridge = load_module(TOOLS / "telegram_agent_bridge.py", "telegram_agent_bridge")
telegram_agent_reply = load_module(TOOLS / "telegram_agent_reply.py", "telegram_agent_reply")
agent_room_inject_message = load_module(TOOLS / "agent_room_inject_message.py", "agent_room_inject_message")
standing_agenda_tick = load_module(TOOLS / "standing_agenda_tick.py", "standing_agenda_tick")
foreground_notify = load_module(ROOT / "foreground_notify.py", "foreground_notify")
claude_ark_runner = load_module(ROOT.parent / "tools" / "claude_code_ark_runner.py", "claude_code_ark_runner")
coding_dispatcher = load_module(ROOT.parent / "tools" / "coding_dispatcher.py", "coding_dispatcher")
ark_coding_plan = load_module(ROOT.parent / "tools" / "ark_coding_plan.py", "ark_coding_plan")
claude_code_model_smoke = load_module(TOOLS / "claude_code_model_smoke.py", "claude_code_model_smoke")


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok {name}")


def load_latest_live_smoke_artifact(live_smoke: dict, model: str) -> tuple[dict, dict]:
    artifact_root_raw = str(live_smoke.get("artifact_root") or "").strip()
    if not artifact_root_raw:
        return {}, {}
    artifact_root = Path(artifact_root_raw)
    if not artifact_root.is_absolute():
        artifact_root = ROOT.parent / artifact_root
    run_dirs = sorted(artifact_root.glob(f"{model}-coding-plan-smoke-*"))
    if not run_dirs:
        return {}, {}
    run_dir = run_dirs[-1]
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        verification = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    return (
        manifest if isinstance(manifest, dict) else {},
        verification if isinstance(verification, dict) else {},
    )


def main() -> int:
    claude_review_body = """
我检查了本地证据：

1. 系统状态已恢复：turn.json 现在 seq=1271，needs_reply=main，说明之前卡住的 seq=1269 问题已解决。
2. Codex 的修复是最小且正确的：
 - 在 openclaw-main-mailbox-watch.py 中增加了 quota_cooldown 识别，当 gateway 恢复后会自动重置失败次数
 - 在 agent_room_resident_bridge.py 中优化了 takeover 触发条件，避免把“没有额度限制”这种描述性句子当成故障信号

我补充几个后续验证建议：
1. Smoke 测试：模拟 quota_cooldown 状态，确认 gateway 恢复后会重置尝试次数。
2. 边界限制：避免 gateway 反复故障导致无限重试。
总体来说，本轮修复是正确的，系统已恢复正常。
""".strip().lower()

    check(
        "quota cooldown classified from provider cooldown",
        watcher.classify_retryable_startup_failure(
            "Provider openai-codex is in cooldown (all profiles unavailable) (rate_limit)"
        ) == "quota_cooldown",
    )
    check(
        "quota cooldown classified from usage limit",
        watcher.classify_retryable_startup_failure(
            "ERROR: You've hit your usage limit. Try again later."
        ) == "quota_cooldown",
    )
    check(
        "quota cooldown classified from 429",
        watcher.classify_retryable_startup_failure(
            "HTTP 429 Too Many Requests: quota_exhausted"
        ) == "quota_cooldown",
    )
    check(
        "Ark AccountQuotaExceeded is classified as usage_limit",
        agent_task_runner.classify_codex_failure({
            "stderr": "429 {\"error\":{\"code\":\"AccountQuotaExceeded\",\"message\":\"You have exceeded the 5-hour usage quota. It will reset at 2026-05-22 01:52:18 +0800 CST.\"}}"
        }) == "usage_limit",
    )
    check(
        "Codex Ark fallback reuses retired-v3.2-free DeepSeek V4 workspace_write route candidates",
        agent_task_runner.codex_ark_model_candidates("workspace_write")[:4]
        == ["glm-5.1", "deepseek-v4-pro", "deepseek-v4-flash", "minimax-m2.7"],
    )
    check(
        "Ark Coding Plan local catalog registers DeepSeek V4 concrete models",
        ark_coding_plan.resolve_model("deepseek-v4-flash").model_id == "deepseek-v4-flash"
        and ark_coding_plan.resolve_model("deepseek-v4-pro").model_id == "deepseek-v4-pro",
    )
    claude_policy = agent_task_runner.load_claude_model_policy()
    live_smoke = claude_policy.get("last_live_smoke") or {}
    live_smoke_models = list(live_smoke.get("models") or [])
    check(
        "Claude policy registers DeepSeek V4 latest family model",
        (claude_policy.get("latest_family_models") or {}).get("deepseek") == "deepseek-v4-pro",
    )
    check(
        "Claude policy registers DeepSeek V4 flash latest family model",
        (claude_policy.get("latest_family_models") or {}).get("deepseek-flash") == "deepseek-v4-flash",
    )
    check(
        "Claude policy records completed DeepSeek V4 live smoke",
        live_smoke_models == ["deepseek-v4-flash", "deepseek-v4-pro"]
        and live_smoke.get("result") == "both_completed_exit_0_parsed_json_no_tool_calls",
    )
    for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        manifest, verification = load_latest_live_smoke_artifact(live_smoke, model)
        check(
            f"Claude {model} live smoke artifact completed",
            manifest.get("model") == model
            and manifest.get("status") == "completed"
            and manifest.get("exit_code") == 0
            and manifest.get("effort") == "max"
            and manifest.get("effort_policy") == "force_max_reasoning_for_all_claude_code_ark_calls"
            and verification.get("exit_code") == 0
            and verification.get("parsed_json") is True
            and verification.get("dontAsk_tool_attempts_clean") is True,
        )
    check(
        "Claude model policy retires DeepSeek v3.2 after V4 live smoke",
        agent_task_runner.claude_retired_model_replacement("deepseek-v3.2") == "deepseek-v4-pro"
        and "deepseek-v3.2" not in agent_task_runner.claude_candidate_models_for_route("deep_reasoning"),
    )
    check(
        "Claude deep reasoning route uses Ark-supported DeepSeek V4 candidates without v3.2 or unsupported reasoner",
        agent_task_runner.claude_candidate_models_for_route("deep_reasoning")[:4]
        == ["deepseek-v4-pro", "deepseek-v4-flash", "glm-5.1", "minimax-m2.7"],
    )
    check(
        "Claude live smoke defaults exclude retired DeepSeek v3.2 and include V4 candidates",
        "deepseek-v3.2" not in claude_code_model_smoke.DEFAULT_MODELS
        and "deepseek-v4-flash" in claude_code_model_smoke.DEFAULT_MODELS
        and "deepseek-v4-pro" in claude_code_model_smoke.DEFAULT_MODELS
        and "deepseek-reasoner" in claude_code_model_smoke.DEFAULT_MODELS,
    )
    codex_ark_prompt = agent_task_runner.codex_ark_adapt_prompt(
        {"task_id": "smoke-codex-ark", "run_id": "smoke-codex-ark"},
        "请修复这个 runtime 问题",
        "workspace_write",
        {"source_edit": True},
    )
    check(
        "Codex Ark fallback adapts Codex CLI prompt for direct provider",
        "Ark Coding Plan direct-provider fallback" in codex_ark_prompt
        and "do not claim" in codex_ark_prompt.lower()
        and "Original Codex runner prompt" in codex_ark_prompt,
    )
    old_formal_guard = os.environ.get("OPENCLAW_FORMAL_WRITING_GPT_REQUIRED")
    os.environ["OPENCLAW_FORMAL_WRITING_GPT_REQUIRED"] = "1"
    try:
        check(
            "Codex Ark fallback respects formal GPT fail-closed guard",
            agent_task_runner.codex_ark_formal_gpt_required({"run_id": "people-daily-smoke"}, "formal report"),
        )
    finally:
        if old_formal_guard is None:
            os.environ.pop("OPENCLAW_FORMAL_WRITING_GPT_REQUIRED", None)
        else:
            os.environ["OPENCLAW_FORMAL_WRITING_GPT_REQUIRED"] = old_formal_guard
    codex_runner_source = inspect.getsource(agent_task_runner.run_codex_with_fallback)
    check("Codex GPT fallback calls Ark direct-provider adapter", "codex_ark_direct_fallback" in codex_runner_source)
    check("Codex GPT fallback no longer starts Claude runner as Ark fallback", "run_claude_code_ark(ark_task" not in codex_runner_source)
    future_cooldown = (datetime.now(timezone.utc).astimezone() + timedelta(hours=1)).isoformat(timespec="seconds")
    codex_recovery_state = {
        "models": {
            "gpt-5.5": {
                "status": "cooldown",
                "reason": "usage_limit",
                "cooldown_until": future_cooldown,
            }
        }
    }
    check(
        "Codex stale primary cooldown initially blocks gpt-5.5",
        agent_task_runner.cooldown_active_until(codex_recovery_state, "gpt-5.5") is not None,
    )
    agent_task_runner.mark_codex_model_recovered(codex_recovery_state, "gpt-5.5")
    codex_next_dispatch_model = next(
        (
            model
            for model in agent_task_runner.codex_model_candidates({})
            if not agent_task_runner.cooldown_active_until(codex_recovery_state, model)
        ),
        None,
    )
    check(
        "Codex successful primary recovery clears stale cooldown and reselects gpt-5.5",
        codex_next_dispatch_model == "gpt-5.5"
        and (codex_recovery_state.get("models") or {}).get("gpt-5.5", {}).get("status") == "available"
        and "cooldown_until" not in ((codex_recovery_state.get("models") or {}).get("gpt-5.5") or {})
        and "reason" not in ((codex_recovery_state.get("models") or {}).get("gpt-5.5") or {}),
    )
    dry_dir = ROOT / "dry-runs" / "smoke_runtime_recovery"
    dry_dir.mkdir(parents=True, exist_ok=True)
    original_codex_model_state_file = agent_task_runner.CODEX_MODEL_STATE
    original_agent_room_status_file = agent_task_runner.AGENT_ROOM_STATUS_FILE
    original_quota_ledger_file = agent_task_runner.QUOTA_LEDGER_FILE
    original_run_cmd = agent_task_runner.run_cmd
    original_active_codex_catalog_slugs = agent_task_runner.active_codex_catalog_slugs
    old_codex_models_env = os.environ.get("AGENT_ROOM_CODEX_MODELS")
    codex_cooldown_state_file = dry_dir / "codex_cooldown_fallback_state.json"
    codex_status_file = dry_dir / "codex_cooldown_fallback_status.json"
    codex_quota_ledger_file = dry_dir / "codex_cooldown_fallback_quota_ledger.json"
    codex_cooldown_run_dir = dry_dir / "codex_cooldown_fallback_run"
    for path in (codex_status_file, codex_quota_ledger_file):
        if path.exists():
            path.unlink()
    codex_cooldown_state_file.write_text(
        json.dumps(
            {
                "models": {
                    "gpt-5.5": {
                        "status": "cooldown",
                        "reason": "usage_limit",
                        "cooldown_until": future_cooldown,
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    codex_attempted_models: list[str] = []

    def fake_codex_run_cmd(cmd: list[str], **_kwargs: object) -> dict[str, object]:
        model = cmd[cmd.index("--model") + 1]
        out_file = Path(cmd[cmd.index("--output-last-message") + 1])
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(f"fallback body from {model}", encoding="utf-8")
        codex_attempted_models.append(model)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    try:
        agent_task_runner.CODEX_MODEL_STATE = codex_cooldown_state_file
        agent_task_runner.AGENT_ROOM_STATUS_FILE = codex_status_file
        agent_task_runner.QUOTA_LEDGER_FILE = codex_quota_ledger_file
        agent_task_runner.run_cmd = fake_codex_run_cmd
        agent_task_runner.active_codex_catalog_slugs = lambda: set()
        os.environ["AGENT_ROOM_CODEX_MODELS"] = "gpt-5.5,gpt-5.4"
        codex_fallback_result, codex_fallback_body, codex_fallback_fields = agent_task_runner.run_codex_with_fallback(
            {
                "task_id": "smoke-codex-cooldown-aware-fallback",
                "run_id": "smoke-codex-cooldown-aware-fallback",
                "room_id": "openclaw-evolution",
            },
            "smoke prompt",
            codex_cooldown_run_dir,
            {"source_edit": False},
        )
        codex_status = json.loads(codex_status_file.read_text(encoding="utf-8"))
        codex_agent_status = (codex_status.get("agents") or {}).get("codex") or {}
        codex_status_models = codex_agent_status.get("models") or {}
        check(
            "Codex cooldown-aware dispatch skips depleted primary and runs next candidate",
            codex_fallback_result.get("ok") is True
            and codex_attempted_models == ["gpt-5.4"]
            and codex_fallback_body == "fallback body from gpt-5.4"
            and codex_fallback_fields.get("model") == "gpt-5.4"
            and (codex_fallback_fields.get("model_attempts") or [])[0].get("status") == "skipped_cooldown"
            and (codex_fallback_fields.get("model_fallback") or {}).get("from") == "gpt-5.5",
        )
        check(
            "Codex cooldown fallback projects exhausted primary and active fallback status",
            (codex_status_models.get("gpt-5.5") or {}).get("quota_state") == "exhausted"
            and (codex_status_models.get("gpt-5.5") or {}).get("fallback_available") is True
            and (codex_status_models.get("gpt-5.4") or {}).get("quota_state") == "available"
            and codex_agent_status.get("quota_state") == "fallback_active"
            and codex_agent_status.get("active_model") == "gpt-5.4",
        )
    finally:
        agent_task_runner.CODEX_MODEL_STATE = original_codex_model_state_file
        agent_task_runner.AGENT_ROOM_STATUS_FILE = original_agent_room_status_file
        agent_task_runner.QUOTA_LEDGER_FILE = original_quota_ledger_file
        agent_task_runner.run_cmd = original_run_cmd
        agent_task_runner.active_codex_catalog_slugs = original_active_codex_catalog_slugs
        if old_codex_models_env is None:
            os.environ.pop("AGENT_ROOM_CODEX_MODELS", None)
        else:
            os.environ["AGENT_ROOM_CODEX_MODELS"] = old_codex_models_env

    retryable_room_root = dry_dir / f"retryable_quota_failure_{datetime.now().strftime('%H%M%S%f')}"
    retryable_room = retryable_room_root / "agent-room"
    saved_runner_paths = {
        "ROOT": agent_task_runner.ROOT,
        "ROOM": agent_task_runner.ROOM,
        "COMMENT_ROOT": agent_task_runner.COMMENT_ROOT,
        "COLLAB_LEDGER_DIR": agent_task_runner.COLLAB_LEDGER_DIR,
    }
    try:
        shutil.rmtree(retryable_room_root, ignore_errors=True)
        agent_task_runner.ROOT = retryable_room_root
        agent_task_runner.ROOM = retryable_room
        agent_task_runner.COMMENT_ROOT = retryable_room_root / "agent-comments"
        agent_task_runner.COLLAB_LEDGER_DIR = retryable_room / "collaboration-ledgers"
        retry_after = (datetime.now(timezone.utc).astimezone() - timedelta(seconds=5)).isoformat(timespec="seconds")
        retryable_task = {
            "task_id": "smoke-retryable-quota-failure",
            "run_id": "smoke-retryable-quota-failure",
            "room_id": "openclaw-evolution",
            "target_agents": ["codex"],
            "status": "running",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "participants": ["codex"],
                "work_items": [{"id": "retryable_codex", "status": "open", "assigned_to": "codex"}],
            },
        }
        retryable_manifest = retryable_room / "tasks" / retryable_task["task_id"] / "manifest.json"
        agent_task_runner.write_json(retryable_manifest, retryable_task)
        begin = agent_task_runner.collaboration_begin(retryable_task, "codex", retryable_manifest)
        retryable_comment = {
            "agent_id": "codex",
            "task_id": retryable_task["task_id"],
            "run_id": retryable_task["run_id"],
            "room_id": "openclaw-evolution",
            "title": "Codex CLI execution blocked",
            "body": "usage_limit; retry after cooldown",
            "blockers": ["codex_cli_failed", "usage_limit"],
            "retryable_failure": {
                "schema": "openclaw.agent_room.retryable_provider_failure.v0",
                "status": "retryable",
                "agent_id": "codex",
                "reason": "usage_limit",
                "retry_after": retry_after,
                "cooldown_until": retry_after,
            },
        }
        retryable_result = {"ok": False, "exit_code": 1, "stderr": "usage limit"}
        finish = agent_task_runner.collaboration_finish(
            retryable_task,
            "codex",
            begin.get("work_item_id"),
            retryable_comment,
            retryable_result,
        )
        retryable_result_path = retryable_room_root / "runner" / "result.json"
        agent_task_runner.write_json(retryable_result_path, {"ok": True, "results": []})
        agent_task_runner.update_task_manifest_after_results(
            retryable_manifest,
            retryable_task,
            [{
                "agent_id": "codex",
                "executed": True,
                "result": retryable_result,
                "comment": retryable_comment,
                "comment_written": True,
                "retryable": True,
            }],
            retryable_result_path,
        )
        retryable_after = agent_task_runner.read_json(retryable_manifest)
        retryable_ledger = agent_task_runner.read_json(Path(begin["state_file"]))
        retryable_item = (retryable_ledger.get("work_items") or [{}])[0]
        retryable_claim = (retryable_ledger.get("claims") or [{}])[0]
        check(
            "usage_limit runner result marks task and claim retryable instead of failed or blocked",
            finish.get("ok") is True
            and retryable_after.get("status") == "retryable"
            and retryable_after.get("retry_after") == retry_after
            and "codex" in ((retryable_after.get("runner_summary") or {}).get("retryable_agents") or [])
            and retryable_item.get("status") == "retryable"
            and retryable_claim.get("status") == "retryable"
            and not (retryable_ledger.get("blockers") or []),
        )
    finally:
        for name, value in saved_runner_paths.items():
            setattr(agent_task_runner, name, value)

    original_projection_events_file = agent_task_runner.PROJECTION_EVENTS_FILE
    original_main_projection_events_file = agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE
    projection_events_file = dry_dir / "projection_events_smoke.jsonl"
    main_projection_events_file = dry_dir / "projections" / "openclaw-main" / "projection_events_smoke.jsonl"
    for path in (projection_events_file, main_projection_events_file):
        if path.exists():
            path.unlink()
    try:
        agent_task_runner.PROJECTION_EVENTS_FILE = projection_events_file
        agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = main_projection_events_file
        projection_state = {
            "models": {
                "gpt-5.5": {
                    "status": "cooldown",
                    "reason": "usage_limit",
                    "cooldown_until": future_cooldown,
                }
            }
        }
        recovery_signal = agent_task_runner.mark_codex_model_recovered(
            projection_state,
            "gpt-5.5",
            run_id="smoke-codex-recovery",
            room_id="openclaw-evolution",
        )
        root_events = projection_events_file.read_text(encoding="utf-8").strip().splitlines()
        main_events = main_projection_events_file.read_text(encoding="utf-8").strip().splitlines()
        recovery_event = json.loads(root_events[-1])
        agent_task_runner.mark_codex_model_recovered(
            projection_state,
            "gpt-5.5",
            run_id="smoke-codex-recovery",
            room_id="openclaw-evolution",
        )
        repeated_events = projection_events_file.read_text(encoding="utf-8").strip().splitlines()
        check(
            "Codex model recovery writes one local OpenClaw-main projection event",
            recovery_signal.get("projection_event_written") is True
            and len(root_events) == 1
            and len(main_events) == 1
            and len(repeated_events) == 1
            and recovery_event.get("row_type") == "model_quota_recovered"
            and ((recovery_event.get("model_quota_recovery") or {}).get("remaining_known") is False),
        )
    finally:
        agent_task_runner.PROJECTION_EVENTS_FILE = original_projection_events_file
        agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = original_main_projection_events_file
    check(
        "main Ark fallback has ordered candidate models",
        watcher.main_ark_candidate_models()[:4] == ["minimax-m2.7", "deepseek-v4-pro", "glm-5.1", "kimi-k2.6"],
    )
    old_main_ark_model = watcher.MAIN_ARK_MODEL
    old_main_ark_models_raw = watcher.MAIN_ARK_FALLBACK_MODELS_RAW
    watcher.MAIN_ARK_MODEL = "deepseek-v3.2"
    watcher.MAIN_ARK_FALLBACK_MODELS_RAW = "deepseek-v3.2,deepseek-v4-pro,glm-5.1,minimax-m2.7,kimi-k2.6"
    try:
        deepseek_first_candidates = watcher.main_ark_candidate_models()
        check(
            "main Ark fallback normalizes retired DeepSeek without duplicate candidates",
            deepseek_first_candidates[:4] == ["deepseek-v4-pro", "glm-5.1", "minimax-m2.7", "kimi-k2.6"],
        )
    finally:
        watcher.MAIN_ARK_MODEL = old_main_ark_model
        watcher.MAIN_ARK_FALLBACK_MODELS_RAW = old_main_ark_models_raw

    class ProviderHttp429(Exception):
        kind = "provider_http_error"
        detail = {"status": 429, "detail": "rate limit"}

    check(
        "main Ark fallback treats 429 as retryable per-model failure",
        watcher.main_ark_error_retryable(ProviderHttp429("Provider HTTP 429")),
    )
    main_ark_state = {
        watcher.MAIN_ARK_MODEL_FAILURES_KEY: {
            "minimax-m2.7": {
                "failed_at": watcher.now_iso(),
                "retryable": True,
                "kind": "provider_http_error",
            }
        }
    }
    check(
        "main Ark fallback skips recently failed model by cooldown",
        watcher.main_ark_model_on_cooldown(main_ark_state, "minimax-m2.7")[0],
    )
    check(
        "startup transport classified",
        watcher.classify_retryable_startup_failure("GatewayTransportError: gateway closed")
        == "startup_transport",
    )
    check(
        "resettable quota attempts can reset when gateway is ready",
        watcher.should_reset_attempts_after_retryable_failure(3, False, "quota_cooldown"),
    )
    check(
        "non retryable failures do not reset",
        not watcher.should_reset_attempts_after_retryable_failure(3, False, "syntax_error"),
    )
    check(
        "plain no-quota sentence does not trigger takeover",
        not resident.body_indicates_runtime_takeover("问题是现在没有额度限制了"),
    )
    check(
        "review advice with quota words does not trigger takeover",
        not resident.body_indicates_runtime_takeover(claude_review_body),
    )
    check(
        "body text alone never triggers takeover",
        not resident.body_indicates_runtime_takeover(
            "Provider openai-codex is in cooldown (all profiles unavailable) (rate_limit)"
        ),
    )
    check(
        "structured provider cooldown blocker triggers takeover",
        resident.is_runtime_takeover_trigger_comment({"blockers": ["rate_limit"]}),
    )
    check(
        "runner timeout still triggers takeover",
        resident.is_runtime_takeover_trigger_comment({"blockers": ["runner_timeout"]}),
    )

    dry_dir = ROOT / "dry-runs" / "smoke_runtime_recovery"
    dry_dir.mkdir(parents=True, exist_ok=True)
    claude_attempt_a = agent_task_runner.claude_ark_attempt_run_id(
        "tg-openclaw-evolution-4fe67182fb403c50",
        "glm-5.1",
        dry_dir / "resident-runs" / "20260525-190841" / "runner" / "tg-openclaw-evolution-4fe67182fb403c50" / "claude-code",
    )
    claude_attempt_b = agent_task_runner.claude_ark_attempt_run_id(
        "tg-openclaw-evolution-4fe67182fb403c50",
        "glm-5.1",
        dry_dir / "resident-runs" / "20260525-191409" / "runner" / "tg-openclaw-evolution-4fe67182fb403c50" / "claude-code",
    )
    check(
        "Claude Ark repeated room dispatches get distinct coding run ids",
        claude_attempt_a != claude_attempt_b
        and "glm-5.1" in claude_attempt_a
        and len(claude_attempt_a) <= 96
        and len(claude_attempt_b) <= 96,
    )
    duplicate_runner_dir = dry_dir / "claude-duplicate-runner"
    duplicate_existing_dir = dry_dir / "coding-runs" / "smoke-claude-duplicate"
    duplicate_artifacts = duplicate_existing_dir / "artifacts"
    duplicate_artifacts.mkdir(parents=True, exist_ok=True)
    (duplicate_existing_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "smoke-claude-duplicate",
                "status": "completed",
                "exit_code": 0,
                "output_dir": str(duplicate_existing_dir),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (duplicate_existing_dir / "status.json").write_text(
        json.dumps({"status": "completed", "exit_code": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    (duplicate_artifacts / "claude_stdout.txt").write_text(
        json.dumps(
            {
                "agent_id": "claude-code",
                "run_id": "smoke-claude-duplicate",
                "kind": "status",
                "confidence": "high",
                "title": "duplicate completed run reused",
                "body": "复用已完成的 Claude Code Ark 运行结果。",
                "blockers": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original_run_cmd = agent_task_runner.run_cmd
    try:
        agent_task_runner.run_cmd = lambda *_args, **_kwargs: {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Run directory already exists: {duplicate_existing_dir}",
        }
        duplicate_result, duplicate_body, duplicate_fields = agent_task_runner.run_claude_code_ark_once(
            {
                "task_id": "smoke-claude-duplicate",
                "run_id": "smoke-claude-duplicate",
                "room_id": "openclaw-evolution",
                "source_scope_dir": str(ROOT.parent),
            },
            "请给出一个简短状态。",
            duplicate_runner_dir,
            {"source_edit": False},
        )
        check(
            "Claude Ark duplicate completed run reuses existing artifact",
            duplicate_result.get("ok") is True
            and duplicate_result.get("duplicate_recovered") is True
            and duplicate_fields.get("coding_run_dir") == str(duplicate_existing_dir)
            and "复用已完成" in duplicate_body,
        )
    finally:
        agent_task_runner.run_cmd = original_run_cmd
    class FakeProviderExc(Exception):
        kind = "provider_http_error"
        status = 429
        detail = {"status": 429, "error": {"code": "AccountQuotaExceeded", "message": "You have exceeded the 5-hour usage quota. It will reset at 2099-05-22 01:52:18 +0800 CST."}}

    fake_exc = FakeProviderExc("Provider HTTP 429")
    check("main Ark fallback classifies quota as retryable", watcher.main_ark_error_retryable(fake_exc))
    check("main Ark fallback records usage-limit reason", watcher.main_ark_failure_reason(fake_exc) == "usage_limit")
    check("main Ark fallback parses provider reset time", watcher.parse_main_ark_retry_time(fake_exc).startswith("2099-05-22T01:52:18"))
    main_model_state: dict = {}
    watcher.record_main_ark_model_failure(main_model_state, "minimax-m2.7", fake_exc, True)
    check("main Ark fallback stores per-model cooldown", watcher.main_ark_model_on_cooldown(main_model_state, "minimax-m2.7")[0])
    watcher.record_main_ark_model_success(main_model_state, "minimax-m2.7")
    check("main Ark fallback clears per-model cooldown after success", not watcher.main_ark_model_on_cooldown(main_model_state, "minimax-m2.7")[0])
    original_watcher_status_file = watcher.AGENT_ROOM_STATUS_FILE
    original_status_bridge = agent_room_status.BRIDGE
    watcher_status_file = dry_dir / "main_agent_room_status_smoke.json"
    try:
        watcher.AGENT_ROOM_STATUS_FILE = watcher_status_file
        watcher.update_agent_room_quota_status("openclaw-main", "openai-codex/gpt-5.5", "exhausted", reason="quota_cooldown", fallback_available=True, active_model="minimax-m2.7", run_id="smoke-main")
        main_status_bridge = dry_dir / "main_status_bridge"
        (main_status_bridge / "agent-room").mkdir(parents=True, exist_ok=True)
        (main_status_bridge / ".openclaw_main_watcher_state.json").write_text(json.dumps({"main_quota_state": "depleted_ark_active", "main_ark_fallback_last_model": "minimax-m2.7"}, ensure_ascii=False), encoding="utf-8")
        (main_status_bridge / "agent-room" / "agent_room_status.json").write_text(watcher_status_file.read_text(encoding="utf-8"), encoding="utf-8")
        main_runtime = agent_room_status.main_runtime_summary(main_status_bridge)
        check("main runtime status exposes fallback_active", main_runtime.get("fallback_active") is True and main_runtime.get("active_model") == "minimax-m2.7")
    finally:
        watcher.AGENT_ROOM_STATUS_FILE = original_watcher_status_file
        agent_room_status.BRIDGE = original_status_bridge
    original_quota_state_file = agent_task_runner.AGENT_QUOTA_STATE_FILE
    original_agent_room_status_file = agent_task_runner.AGENT_ROOM_STATUS_FILE
    original_projection_events_file = agent_task_runner.PROJECTION_EVENTS_FILE
    original_main_projection_events_file = agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE
    original_model_quota_signal_file = agent_task_runner.MODEL_QUOTA_SIGNAL_FILE
    original_quota_ledger_file = agent_task_runner.QUOTA_LEDGER_FILE
    original_quota_ledger_module_file = quota_ledger.QUOTA_LEDGER_FILE
    original_quota_ledger_module_signal_file = quota_ledger.MODEL_QUOTA_SIGNAL_FILE
    original_quota_header_observations_file = quota_ledger.QUOTA_HEADER_OBSERVATIONS_FILE
    quota_state_file = dry_dir / "agent_quota_state_smoke.json"
    agent_room_status_file = dry_dir / "agent_room_status_smoke.json"
    model_quota_signal_file = dry_dir / "model_quota_signal_smoke.json"
    quota_ledger_file = dry_dir / "quota_ledger_smoke.json"
    quota_header_observations_file = dry_dir / "quota_header_observations_smoke.jsonl"
    projection_events_file = dry_dir / "agent_quota_projection_events_smoke.jsonl"
    main_projection_events_file = dry_dir / "projections" / "openclaw-main" / "agent_quota_projection_events_smoke.jsonl"
    if quota_state_file.exists():
        quota_state_file.unlink()
    if agent_room_status_file.exists():
        agent_room_status_file.unlink()
    if model_quota_signal_file.exists():
        model_quota_signal_file.unlink()
    if quota_ledger_file.exists():
        quota_ledger_file.unlink()
    if quota_header_observations_file.exists():
        quota_header_observations_file.unlink()
    for path in (projection_events_file, main_projection_events_file):
        if path.exists():
            path.unlink()
    try:
        agent_task_runner.AGENT_QUOTA_STATE_FILE = quota_state_file
        agent_task_runner.AGENT_ROOM_STATUS_FILE = agent_room_status_file
        agent_task_runner.PROJECTION_EVENTS_FILE = projection_events_file
        agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = main_projection_events_file
        agent_task_runner.MODEL_QUOTA_SIGNAL_FILE = model_quota_signal_file
        agent_task_runner.QUOTA_LEDGER_FILE = quota_ledger_file
        quota_ledger.QUOTA_LEDGER_FILE = quota_ledger_file
        quota_ledger.MODEL_QUOTA_SIGNAL_FILE = model_quota_signal_file
        quota_ledger.QUOTA_HEADER_OBSERVATIONS_FILE = quota_header_observations_file
        quota_ledger.update_quota_ledger_from_headers(
            "minimax-m2.7",
            {
                "date": "Tue, 26 May 2026 00:00:00 GMT",
                "content-type": "application/json",
            },
            agent_id="claude-code",
        )
        observation_events = [
            json.loads(line)
            for line in quota_header_observations_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        check(
            "quota header observation records absence of provider remaining headers",
            len(observation_events) == 1
            and observation_events[0].get("quota_headers_present") is False
            and observation_events[0].get("known_quota_header_names") == [],
        )
        quota_ledger.update_quota_ledger_from_headers(
            "minimax-m2.7",
            {
                "x-ratelimit-remaining-requests": "9",
                "x-ratelimit-limit-requests": "12",
                "x-ratelimit-remaining-tokens": "3456",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-reset-requests": "60",
            },
            agent_id="claude-code",
        )
        ledger_signal = agent_task_runner.trusted_remaining_quota_signal("claude-code", "minimax-m2.7")
        check(
            "quota ledger response headers flow into trusted remaining quota signal",
            ledger_signal is not None
            and ledger_signal.get("remaining_known") is True
            and ledger_signal.get("remaining_requests") == 9
            and ledger_signal.get("remaining_tokens") == 3456
            and (ledger_signal.get("per_model_remaining_units") or {}).get("requests") == 9
            and ledger_signal.get("quota_signal_contract_version") == "2-provider-header"
            and ledger_signal.get("blocking_missing") == [],
        )
        observation_events = [
            json.loads(line)
            for line in quota_header_observations_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        check(
            "quota header observation records known quota header names without values",
            len(observation_events) == 2
            and observation_events[-1].get("quota_headers_present") is True
            and "x-ratelimit-remaining-requests" in (observation_events[-1].get("known_quota_header_names") or [])
            and "requests_remaining" not in observation_events[-1],
        )
        model_quota_signal_file.unlink()
        ledger_fallback_signal = agent_task_runner.trusted_remaining_quota_signal("claude-code", "minimax-m2.7")
        check(
            "trusted remaining quota signal falls back to quota_ledger when signal file is missing",
            ledger_fallback_signal is not None
            and ledger_fallback_signal.get("remaining_known") is True
            and ledger_fallback_signal.get("remaining_requests") == 9
            and ledger_fallback_signal.get("signal_file") == str(quota_ledger_file),
        )
        model_quota_signal_file.write_text(json.dumps({
            "schema": "openclaw.agent_room.model_quota_signal.v0",
            "source": "smoke-session-status",
            "signals": {
                "claude-code": {
                    "models": {
                        "minimax-m2.7": {
                            "remaining_known": True,
                            "remaining_percent": 0.42,
                            "remaining_requests": 7,
                            "proactive_switching_ready": True,
                            "observed_at": "2099-01-01T00:00:00+00:00",
                        }
                    }
                }
            },
        }, ensure_ascii=False), encoding="utf-8")
        first_depletion = agent_task_runner.mark_agent_quota_depleted("claude-code", "rate_limit", "minimax-m2.7")
        check("first quota depletion requires one visible notification", first_depletion.get("notification_required") is True)
        check("quota state is scoped to the depleted model", agent_task_runner.agent_quota_is_depleted("claude-code", "minimax-m2.7")[0] and not agent_task_runner.agent_quota_is_depleted("claude-code", "glm-5.1")[0])
        projected_status = agent_task_runner.load_agent_room_status()
        check("agent room status projects per-model quota exhaustion", (((projected_status.get("agents") or {}).get("claude-code") or {}).get("models") or {}).get("minimax-m2.7", {}).get("quota_state") == "exhausted")
        claude_quota_signal = (((projected_status.get("agents") or {}).get("claude-code") or {}).get("quota_signal") or {})
        claude_model_quota_signal = (((((projected_status.get("agents") or {}).get("claude-code") or {}).get("models") or {}).get("minimax-m2.7") or {}).get("quota_signal") or {})
        check(
            "agent room quota status uses trusted remaining quota sentinel when present",
            claude_quota_signal.get("remaining_known") is True
            and claude_model_quota_signal.get("remaining_known") is True
            and claude_model_quota_signal.get("remaining_percent") == 0.42
            and claude_model_quota_signal.get("remaining_requests") == 7
            and claude_quota_signal.get("proactive_switching_ready") is True,
        )
        timeout_until = (datetime.now(timezone.utc).astimezone() + timedelta(minutes=10)).isoformat(timespec="seconds")
        agent_task_runner.update_agent_room_quota_status("claude-code", "deepseek-v4-pro", "cooldown", reason="timeout", cooldown_until=timeout_until, fallback_available=True, run_id="smoke-timeout")
        projected_status = agent_task_runner.load_agent_room_status()
        claude_status = (projected_status.get("agents") or {}).get("claude-code") or {}
        check(
            "Claude timeout cooldown is not projected as quota exhaustion",
            (((claude_status.get("models") or {}).get("deepseek-v4-pro") or {}).get("quota_state") == "cooldown")
            and "deepseek-v4-pro" not in (claude_status.get("active_exhausted_models") or []),
        )
        agent_task_runner.update_agent_room_quota_status("claude-code", "deepseek-v4-pro", "available", active_model="deepseek-v4-pro", run_id="smoke-quota")
        projected_status = agent_task_runner.load_agent_room_status()
        check("agent room status shows fallback_active when one model is depleted and another is available", ((projected_status.get("agents") or {}).get("claude-code") or {}).get("quota_state") == "fallback_active")
        external_status_model = agent_task_runner.external_deepseek_status_model("deepseek-v4-flash")
        agent_task_runner.update_agent_room_quota_status(
            "claude-code",
            external_status_model,
            "available",
            reason="external_deepseek_direct_provider_fallback",
            active_model=external_status_model,
            run_id="smoke-external-deepseek-status",
        )
        projected_status = agent_task_runner.load_agent_room_status()
        claude_status = (projected_status.get("agents") or {}).get("claude-code") or {}
        check(
            "external DeepSeek fallback is visible as provider-qualified available fallback",
            claude_status.get("fallback_active") is True
            and claude_status.get("active_model") == external_status_model
            and external_status_model in (claude_status.get("available_models") or []),
        )
        agent_task_runner.update_agent_room_quota_status("claude-code", "deepseek-v3.2", "available", active_model="deepseek-v3.2", run_id="smoke-retired-available")
        projected_status = agent_task_runner.load_agent_room_status()
        check(
            "Claude retired models do not count as available fallback",
            "deepseek-v3.2" not in (((projected_status.get("agents") or {}).get("claude-code") or {}).get("available_models") or []),
        )
        agent_task_runner.mark_agent_quota_depleted("smoke-recovery-agent", "rate_limit", "probe-model")
        agent_task_runner.mark_agent_quota_recovered(
            "smoke-recovery-agent",
            "probe-model",
            run_id="smoke-quota-recovered",
            room_id="openclaw-evolution",
        )
        quota_recovery_events = projection_events_file.read_text(encoding="utf-8").strip().splitlines()
        quota_recovery_event = json.loads(quota_recovery_events[-1])
        check(
            "agent quota recovery writes projection event without claiming numeric remaining quota",
            len(quota_recovery_events) == 1
            and main_projection_events_file.exists()
            and quota_recovery_event.get("row_type") == "model_quota_recovered"
            and ((quota_recovery_event.get("model_quota_recovery") or {}).get("agent_id") == "smoke-recovery-agent")
            and ((quota_recovery_event.get("model_quota_recovery") or {}).get("remaining_known") is False),
        )
        chain_records = agent_task_runner.mark_agent_quota_depleted_for_attempts("smoke-claude", [
            {"model": "glm-5.1", "status": "failed", "reason": "usage_limit"},
            {"model": "deepseek-v4-pro", "status": "skipped_cooldown", "reason": "timeout"},
            {"model": "kimi-k2.6", "status": "failed", "reason": "usage_limit"},
        ])
        chain_notice = agent_task_runner.make_quota_notice_fields({}, "smoke-claude", chain_records[0])
        check(
            "Claude quota chain records all quota-failed models and excludes timeout cooldowns",
            len(chain_records) == 2
            and all(record.get("notification_required") for record in chain_records)
            and "`glm-5.1`" in chain_notice.get("body", "")
            and "`kimi-k2.6`" in chain_notice.get("body", "")
            and "`deepseek-v4-pro`" not in chain_notice.get("body", ""),
        )
        for record in chain_records:
            agent_task_runner.mark_agent_quota_notification_sent("smoke-claude", str(record.get("model") or ""), "smoke-quota-chain")
        repeated_chain_records = agent_task_runner.mark_agent_quota_depleted_for_attempts("smoke-claude", [
            {"model": "glm-5.1", "status": "failed", "reason": "usage_limit"},
            {"model": "kimi-k2.6", "status": "failed", "reason": "usage_limit"},
        ])
        check("Claude quota chain respects once-per-model notification after aggregate notice", all(not record.get("notification_required") for record in repeated_chain_records))
        agent_task_runner.mark_agent_quota_notification_sent("claude-code", "minimax-m2.7", "smoke-quota")
        repeated_depletion = agent_task_runner.mark_agent_quota_depleted("claude-code", "rate_limit", "minimax-m2.7")
        check("repeated same bot/model quota depletion stays quiet", repeated_depletion.get("notification_required") is False)
        other_model_depletion = agent_task_runner.mark_agent_quota_depleted("claude-code", "rate_limit", "glm-5.1")
        check("different model gets its own quota notification", other_model_depletion.get("notification_required") is True)
        selected_model, _candidates, skipped_models = agent_task_runner.claude_select_model_for_route("plain_chat")
        check("Claude model selector skips quota-depleted candidate", selected_model != "minimax-m2.7" and any(item.get("reason") == "quota_depleted" for item in skipped_models))
    finally:
        agent_task_runner.AGENT_QUOTA_STATE_FILE = original_quota_state_file
        agent_task_runner.AGENT_ROOM_STATUS_FILE = original_agent_room_status_file
        agent_task_runner.PROJECTION_EVENTS_FILE = original_projection_events_file
        agent_task_runner.OPENCLAW_MAIN_PROJECTION_EVENTS_FILE = original_main_projection_events_file
        agent_task_runner.MODEL_QUOTA_SIGNAL_FILE = original_model_quota_signal_file
        agent_task_runner.QUOTA_LEDGER_FILE = original_quota_ledger_file
        quota_ledger.QUOTA_LEDGER_FILE = original_quota_ledger_module_file
        quota_ledger.MODEL_QUOTA_SIGNAL_FILE = original_quota_ledger_module_signal_file
        quota_ledger.QUOTA_HEADER_OBSERVATIONS_FILE = original_quota_header_observations_file

    execution_brief = dry_dir / "execution_task_brief.md"
    execution_brief.write_text(
        "## User message\n\n请修改脚本并验证这个 Agent Room runtime 卡死问题。\n",
        encoding="utf-8",
    )
    internal_exec_task = {
        "task_id": "smoke-internal-peer-exec",
        "room_id": "openclaw-evolution",
        "requested_by": "agent-room-collab-followup",
        "lane": "peer_collaboration_followup",
        "target_agents": ["claude-code"],
        "brief_path": str(execution_brief),
        "permissions": {
            "source_edit": True,
            "global_state_change": True,
            "telegram_send": True,
            "github_push": True,
            "secrets_access": True,
        },
        "source": {"transport": "agent-room-collab-followup"},
    }
    perms = agent_task_runner.effective_permissions(internal_exec_task, "claude-code")
    check("internal peer task keeps approved source_edit", perms.get("source_edit"))
    check("internal peer task keeps approved global_state_change", perms.get("global_state_change"))
    check("internal peer task still closes external sends/secrets", not perms.get("telegram_send") and not perms.get("github_push") and not perms.get("secrets_access"))
    inject_perms = agent_room_inject_message.base_permissions()
    check(
        "agent-originated injected tasks close direct Telegram send permission",
        inject_perms.get("telegram_send") is False,
    )
    check(
        "agent-originated injected tasks keep scoped local maintenance permissions",
        inject_perms.get("source_edit") is True and inject_perms.get("global_state_change") is True,
    )
    profile = agent_task_runner.claude_room_run_spec(internal_exec_task, perms, dry_dir)
    check("internal peer task gets scoped source_edit grant", profile.get("source_edit") is True)
    check("source_edit maps to native Claude acceptEdits", profile.get("claude_cli_permission_mode") == "acceptEdits")

    keyword_lure_brief = dry_dir / "keyword_lure_claude_room_turn.md"
    keyword_lure_brief.write_text(
        "## User message\n\n这只是只读讨论，不要改代码，readonly investigation。\n",
        encoding="utf-8",
    )
    keyword_lure_task = dict(internal_exec_task)
    keyword_lure_task["task_id"] = "smoke-keyword-lure-no-downgrade"
    keyword_lure_task["brief_path"] = str(keyword_lure_brief)
    keyword_lure_profile = agent_task_runner.claude_room_run_spec(keyword_lure_task, perms, dry_dir)
    check("natural-language readonly keywords do not downgrade Claude source_edit", keyword_lure_profile.get("source_edit") is True)
    check("keyword-lure source_edit maps to native Claude acceptEdits", keyword_lure_profile.get("claude_cli_permission_mode") == "acceptEdits")

    accept_args = argparse.Namespace(
        permission_mode="acceptEdits",
        tools=None,
        disallowed_tools=None,
        claude_bin="claude",
        bare=True,
        output_format="text",
        effort="max",
        run_id="smoke-native-accept-edits",
        run_prefix="smoke",
        model="kimi-k2.6",
    )
    accept_cmd = claude_ark_runner.build_command(accept_args, dry_dir, "prompt", dry_dir / "accept.debug")
    check("Claude runner passes native acceptEdits", "--permission-mode" in accept_cmd and accept_cmd[accept_cmd.index("--permission-mode") + 1] == "acceptEdits")
    check("Claude acceptEdits default tools include edit and local validation", "--tools" in accept_cmd and "Edit" in accept_cmd[accept_cmd.index("--tools") + 1] and "Bash" in accept_cmd[accept_cmd.index("--tools") + 1])
    check("Claude acceptEdits default disallow keeps web tools blocked", "--disallowed-tools" in accept_cmd and "WebFetch" in accept_cmd[accept_cmd.index("--disallowed-tools") + 1] and "WebSearch" in accept_cmd[accept_cmd.index("--disallowed-tools") + 1])

    dont_ask_args = argparse.Namespace(**{**vars(accept_args), "permission_mode": "dontAsk"})
    dont_ask_cmd = claude_ark_runner.build_command(dont_ask_args, dry_dir, "prompt", dry_dir / "dontask.debug")
    check("Claude runner passes native dontAsk", "--permission-mode" in dont_ask_cmd and dont_ask_cmd[dont_ask_cmd.index("--permission-mode") + 1] == "dontAsk")
    check("Claude dontAsk blocks edit and bash tools", "--disallowed-tools" in dont_ask_cmd and "Edit" in dont_ask_cmd[dont_ask_cmd.index("--disallowed-tools") + 1] and "Bash" in dont_ask_cmd[dont_ask_cmd.index("--disallowed-tools") + 1])

    external_unsupported_task = dict(internal_exec_task)
    external_unsupported_task["task_id"] = "smoke-external-clean-run-spec"
    external_unsupported_task["requested_by"] = "telegram-user"
    external_unsupported_task.pop("lane", None)
    external_unsupported_task["source"] = {"transport": "telegram"}
    external_unsupported_profile = agent_task_runner.claude_room_run_spec(external_unsupported_task, perms, dry_dir)
    check("clean external run spec keeps granted source_edit", external_unsupported_profile.get("source_edit") is True)

    plain_brief = dry_dir / "plain_claude_room_turn.md"
    plain_brief.write_text(
        "## User message\n\n你对这个说法怎么看？\n",
        encoding="utf-8",
    )
    plain_task = dict(external_unsupported_task)
    plain_task["task_id"] = "smoke-plain-claude-room-turn"
    plain_task["brief_path"] = str(plain_brief)
    plain_profile = agent_task_runner.claude_room_run_spec(plain_task, perms, dry_dir)
    check("plain Claude room turn gets granted scoped source_edit instead of keyword read lane", plain_profile.get("source_edit") is True and plain_profile.get("claude_cli_permission_mode") == "acceptEdits" and not plain_profile.get("tools"))
    plain_routed_profile = agent_task_runner.with_claude_model_override(plain_task, plain_profile)
    check("plain Claude run spec does not carry auto model routing", "auto_model_routing" not in plain_routed_profile)
    check("plain Claude room turn uses catalog model without profile routing", plain_routed_profile.get("model") == agent_task_runner.claude_select_model_for_route("workspace_write")[0])
    check("plain Claude room turn does not record an automatic model source", plain_routed_profile.get("model_override_source") is None)
    plain_model_advisory = agent_task_runner.claude_model_routing_advisory(plain_task, plain_routed_profile)
    check("Claude model routing advisory is scoped to Claude Code model selection", plain_model_advisory.get("mode") == "enabled" and plain_model_advisory.get("scope") == "claude_code_agent_model_selection_only")
    check("source_edit Claude advisory proposes workspace-write catalog model", plain_model_advisory.get("route_key") == "workspace_write" and plain_model_advisory.get("resolved_model") == agent_task_runner.claude_select_model_for_route("workspace_write")[0])
    check("Claude advisory records enabled catalog selection", plain_model_advisory.get("mode") == "enabled" and plain_model_advisory.get("selected_model_source") == "model_policy_catalog.workspace_write")
    plain_effort_profile = agent_task_runner.enforce_claude_effort_policy(
        agent_task_runner.with_claude_effort_override(plain_task, plain_routed_profile)
    )
    check("plain Claude room turn uses max Ark effort", plain_effort_profile.get("effort") == "max")
    check("plain Claude room turn records max effort policy", plain_effort_profile.get("effort_policy") == agent_task_runner.CLAUDE_ARK_EFFORT_POLICY)

    review_brief = dry_dir / "review_claude_room_turn.md"
    review_brief.write_text(
        "## User message\n\n你们互相审查这个方案，给出风险和反例。\n",
        encoding="utf-8",
    )
    review_task = dict(plain_task)
    review_task["task_id"] = "smoke-review-claude-room-turn"
    review_task["brief_path"] = str(review_brief)
    review_profile = agent_task_runner.claude_room_run_spec(review_task, perms, dry_dir)
    review_routed_profile = agent_task_runner.with_claude_model_override(review_task, review_profile)
    check("Claude review room turn does not switch model by keyword", review_routed_profile.get("model") == agent_task_runner.claude_select_model_for_route("workspace_write")[0])
    check("Claude review room turn has no permission-based automatic model source", review_routed_profile.get("model_override_source") is None)
    check("review keyword cannot create auto model routing metadata", "auto_model_routing" not in review_routed_profile)
    review_model_advisory = agent_task_runner.claude_model_routing_advisory(review_task, review_routed_profile)
    check("review text alone does not trigger peer-review model route", review_model_advisory.get("route_key") == "workspace_write")

    mainline_brief = dry_dir / "mainline_acceleration_brief.md"
    mainline_brief.write_text(
        "## User message\n\n你们加速推进协作体系和主线吧好吧\n",
        encoding="utf-8",
    )
    mainline_task = dict(internal_exec_task)
    mainline_task["task_id"] = "smoke-agent-room-mainline-acceleration"
    mainline_task["brief_path"] = str(mainline_brief)
    mainline_profile = agent_task_runner.claude_room_run_spec(mainline_task, perms, dry_dir)
    check("mainline acceleration request keeps granted scoped source_edit", mainline_profile.get("source_edit") is True)
    default_mainline_profile = agent_task_runner.with_claude_model_override(mainline_task, mainline_profile)
    check("Claude auto routing is absent from default run spec", "auto_model_routing" not in default_mainline_profile)
    check("source_edit Claude room task uses catalog model", default_mainline_profile.get("model") == agent_task_runner.claude_select_model_for_route("peer_review")[0])
    check("source_edit Claude room task does not record auto source", default_mainline_profile.get("model_override_source") is None)
    peer_model_advisory = agent_task_runner.claude_model_routing_advisory(mainline_task, default_mainline_profile)
    check("peer follow-up Claude advisory proposes review catalog model", peer_model_advisory.get("route_key") == "peer_review" and peer_model_advisory.get("resolved_model") == agent_task_runner.claude_select_model_for_route("peer_review")[0])
    readonly_perms = dict(perms)
    readonly_perms["source_edit"] = False
    readonly_profile = agent_task_runner.claude_room_run_spec(plain_task, readonly_perms, dry_dir)
    readonly_advisory = agent_task_runner.claude_model_routing_advisory(plain_task, readonly_profile)
    check("readonly plain Claude advisory proposes plain-chat catalog model", readonly_advisory.get("route_key") == "plain_chat" and readonly_advisory.get("resolved_model") == agent_task_runner.claude_select_model_for_route("plain_chat")[0])
    mainline_effort_profile = agent_task_runner.enforce_claude_effort_policy(
        agent_task_runner.with_claude_effort_override(mainline_task, default_mainline_profile)
    )
    check("source_edit Claude room task uses max Ark effort", mainline_effort_profile.get("effort") == "max")
    low_effort_task = dict(mainline_task)
    low_effort_task["claude_code_effort"] = "low"
    forced_effort_profile = agent_task_runner.enforce_claude_effort_policy(
        agent_task_runner.with_claude_effort_override(low_effort_task, default_mainline_profile)
    )
    check("per-task lower Claude effort is forced back to max", forced_effort_profile.get("effort") == "max")
    check("forced Claude effort records previous value", forced_effort_profile.get("effort_policy_previous_effort") == "low")
    override_task = dict(mainline_task)
    override_task["claude_code_models"] = {"acceptEdits": "minimax-m2.7", "dontAsk": "kimi-k2.6"}
    overridden_profile = agent_task_runner.with_claude_model_override(override_task, mainline_profile)
    check("per-task Claude model override can select source_edit model", overridden_profile.get("model") == "minimax-m2.7")
    check("per-task Claude model override records source", overridden_profile.get("model_override_source") == "task.claude_code_models.acceptEdits")
    override_advisory = agent_task_runner.claude_model_routing_advisory(override_task, overridden_profile)
    check("explicit Claude model override wins over advisory routing", override_advisory.get("decision") == "explicit_task_override_wins" and override_advisory.get("selected_model") == "minimax-m2.7")
    retired_override_task = dict(mainline_task)
    retired_override_task["claude_code_model"] = "glm-4.7"
    retired_override_profile = agent_task_runner.with_claude_model_override(retired_override_task, mainline_profile)
    check("retired GLM Claude override is replaced with latest local GLM", retired_override_profile.get("model") == "glm-5.1")
    check("retired GLM Claude override records blocked model", retired_override_profile.get("model_policy_blocked_model") == "glm-4.7")
    blocked_doubao_task = dict(mainline_task)
    blocked_doubao_task["claude_code_model"] = "doubao-seed-2.0-code"
    blocked_doubao_profile = agent_task_runner.with_claude_model_override(blocked_doubao_task, mainline_profile)
    check("blocked Doubao Claude override falls back cross-vendor to GLM", blocked_doubao_profile.get("model") == "glm-5.1")
    check("Claude default and policy fallback are cross-vendor", agent_task_runner.claude_policy_default_model() != agent_task_runner.claude_policy_fallback_model("workspace_write"))
    execute_task = dict(mainline_task)
    execute_task["claude_auto_model_routing_mode"] = "execute"
    execute_task["permissions"] = {**(execute_task.get("permissions") or {}), "quality_surface_change": True}
    execute_profile = agent_task_runner.with_claude_model_override(execute_task, mainline_profile)
    check("stale auto routing execute request is ignored", "auto_model_routing" not in execute_profile and execute_profile.get("model_override_source") is None)
    mainline_prompt = agent_task_runner.task_prompt(mainline_task, "codex", perms)
    check("runner prompt does not force mainline layer preamble", "first state the layer" not in mainline_prompt and "Alex product/goal" not in mainline_prompt)
    check("runner prompt blocks unsupported config/model claims", "Do not invent automatic behavior" in mainline_prompt and "model-routing rules" in mainline_prompt)
    check("runner prompt states Claude runtime tool boundary", "native Claude --permission-mode plus allowed/disallowed tools" in mainline_prompt)
    check("runner prompt embeds translation active mainline", "Translation Agent is an active mainline workflow" in mainline_prompt)
    check("runner prompt embeds coding lane backup boundary", "backup/audit harness" in mainline_prompt)
    check("runner prompt embeds degraded quorum rule", "degraded-quorum" in mainline_prompt)
    check("runner prompt embeds Antigravity bounded unblocker rule", "Antigravity remains a bounded unblocker" in mainline_prompt)
    check("runner prompt requires peer-anchored collaboration replies", "visible peer claim" in mainline_prompt and "first-pass parallel turn" in mainline_prompt)
    check("runner prompt requires proactive mainline contribution before NO_COMMENT", "advance the OpenClaw mainline" in mainline_prompt and "Use NO_COMMENT only after that check" in mainline_prompt)
    check("runner prompt does not ask Alex to confirm boundary corrections", "Do not ask Alex to confirm workflow-boundary corrections from Alex" in mainline_prompt)
    check("Claude Ark runner no longer references stale profile variable", "profile.get" not in inspect.getsource(agent_task_runner.run_claude_code_ark))
    check("runner prompt does not block Claude process-intent style", "inspect/check later" not in mainline_prompt and "process-intent" not in mainline_prompt)
    bridge_boundaries = telegram_agent_bridge.ROOM_TASK_BOUNDARIES
    broadcast_targets = telegram_agent_bridge.group_broadcast_targets("supergroup", ["codex", "claude-code", "openclaw-main"], "现在 Claude Code 在做什么")
    check("runtime status question is not hard-blocked from peer agents", "codex" in broadcast_targets and "claude-code" in broadcast_targets)
    check("private chat still has no group broadcast targets", telegram_agent_bridge.group_broadcast_targets("private", ["codex", "claude-code"], "现在 Claude Code 在做什么") == [])
    check("telegram ingress single spokesperson is not fixed main", "not a rule that openclaw-main always speaks" in bridge_boundaries and "Codex, Claude Code, or openclaw-main" in bridge_boundaries)
    group_surface_policy = json.loads((ROOT / "agent-room" / "config" / "group-surface-policy.json").read_text(encoding="utf-8"))
    single_spokesperson_policy = str((group_surface_policy.get("multi_agent_rules") or {}).get("single_spokesperson") or "")
    check("group surface single spokesperson can rotate", "not a permanent main-only channel" in single_spokesperson_policy and "may rotate among openclaw-main, codex, and claude-code" in single_spokesperson_policy)
    main_role_policy = json.loads((ROOT / "agent-room" / "rooms" / "openclaw-evolution" / "main_role_policy.json").read_text(encoding="utf-8"))
    check("main role policy rejects permanent main spokesperson", "current_turn_owner_speaks" in str(main_role_policy.get("reply_mode") or {}))
    check("telegram ingress does not force mainline layer preamble", "first state the layer" not in bridge_boundaries and "Alex product/goal" not in bridge_boundaries)
    check("telegram ingress blocks unsupported config/model claims", "Do not invent automatic behavior" in bridge_boundaries and "model-routing rules" in bridge_boundaries)
    check("telegram ingress states Claude runtime tool boundary", "native Claude --permission-mode plus allowed/disallowed tools" in bridge_boundaries)
    check("telegram ingress brief embeds translation/coding boundary", "Translation Agent is an active mainline workflow" in bridge_boundaries and "backup/audit harness" in bridge_boundaries)
    check("telegram ingress brief embeds degraded quorum and Antigravity rules", "degraded-quorum" in bridge_boundaries and "Antigravity remains a bounded unblocker" in bridge_boundaries)
    check("telegram ingress requires proactive mainline contribution before NO_COMMENT", "non-duplicative mainline contribution" in bridge_boundaries and "Use NO_COMMENT only when that check" in bridge_boundaries)
    check("telegram ingress does not ask Alex to confirm boundary corrections", "Do not ask Alex to confirm workflow-boundary corrections from Alex" in bridge_boundaries)
    check("telegram ingress brief does not block Claude process-intent style", "inspect/check later" not in bridge_boundaries and "process-intent" not in bridge_boundaries)
    bot_by_username, _bot_by_agent = telegram_agent_bridge.load_bot_index()
    room_participants = ["openclaw-main", "codex", "claude-code"]
    check(
        "spaced Claude mention routes only to Claude Code",
        telegram_agent_bridge.mention_targets("@ lchclaudecode_bot 你觉得如何呢", room_participants, bot_by_username) == ["claude-code"],
    )
    foreground_room = {
        "room_id": "openclaw-evolution",
        "telegram_chat_id": "-1009000000001",
        "policies": {
            "foreground_notify": {
                "target_surface": "telegram_group",
                "target_from_room_chat_id": True,
            }
        },
    }
    foreground_policy = foreground_notify.load_policy(foreground_room)
    target, target_source = foreground_notify.resolve_target(
        argparse.Namespace(target=""),
        foreground_policy,
        foreground_room,
    )
    check("foreground notify can resolve Agent Room group chat target", target == "-1009000000001" and target_source == "room.telegram_chat_id")
    no_implicit_group_policy = foreground_notify.load_policy({"room_id": "openclaw-evolution", "telegram_chat_id": "-1009000000001"})
    target, target_source = foreground_notify.resolve_target(
        argparse.Namespace(target=""),
        no_implicit_group_policy,
        {"room_id": "openclaw-evolution", "telegram_chat_id": "-1009000000001"},
    )
    check("foreground notify does not infer group target without room policy opt-in", target == "" and target_source == "missing")
    check(
        "foreground notify room-id resolves canonical Agent Room room file",
        foreground_notify.resolve_room_file(argparse.Namespace(room_file="", room_id="openclaw-evolution"))
        == ROOT / "agent-room" / "rooms" / "openclaw-evolution" / "room.json",
    )
    import_preserve_dir = dry_dir / "canonical_room_policy_preserve"
    canonical_room = import_preserve_dir / "agent-room"
    poll_room = import_preserve_dir / "poll"
    resident.write_json(canonical_room / "rooms" / "openclaw-evolution" / "room.json", foreground_room)
    resident.write_json(
        poll_room / "rooms" / "openclaw-evolution" / "room.json",
        {
            "room_id": "openclaw-evolution",
            "telegram_chat_id": "-1009000000001",
            "title": "openclaw进化",
        },
    )
    original_room = resident.ROOM
    try:
        resident.ROOM = canonical_room
        resident.import_canonical_artifacts(poll_room)
        imported_room = resident.read_json(canonical_room / "rooms" / "openclaw-evolution" / "room.json", {})
    finally:
        resident.ROOM = original_room
    imported_policy = ((imported_room.get("policies") or {}).get("foreground_notify") or {})
    check(
        "canonical room import preserves foreground notify runtime policy",
        imported_policy.get("target_from_room_chat_id") is True and imported_policy.get("target_surface") == "telegram_group",
    )
    group_policy_dir = dry_dir / f"group_chat_policy_routes_{datetime.now().strftime('%H%M%S%f')}"
    telegram_agent_bridge.normalize_updates(
        [
            {
                "update_id": 9101,
                "message": {
                    "message_id": 9101,
                    "chat": {"id": -1009000000001, "type": "supergroup", "title": "openclaw进化"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370001,
                    "text": "你们自己接着讨论吧，有了可以落地的决策后就自动进行",
                },
            },
            {
                "update_id": 99101,
                "receiver_agent_id": "claude-code",
                "message": {
                    "message_id": 9101,
                    "chat": {"id": -1009000000001, "type": "supergroup", "title": "openclaw进化"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370001,
                    "text": "你们自己接着讨论吧，有了可以落地的决策后就自动进行",
                },
            },
            {
                "update_id": 9102,
                "message": {
                    "message_id": 9102,
                    "chat": {"id": -1009000000001, "type": "supergroup", "title": "openclaw进化"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370002,
                    "text": "@ lchclaudecode_bot 你觉得如何呢",
                },
            },
            {
                "update_id": 9103,
                "receiver_agent_id": "codex",
                "message": {
                    "message_id": 9103,
                    "chat": {"id": 424242, "type": "private", "username": "alex"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370003,
                    "text": "私聊里只问 Codex 这个问题",
                },
            },
            {
                "update_id": 9104,
                "message": {
                    "message_id": 9104,
                    "chat": {"id": -1009000000001, "type": "supergroup", "title": "openclaw进化"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370004,
                    "text": "/status",
                },
            },
            {
                "update_id": 9105,
                "message": {
                    "message_id": 9105,
                    "chat": {"id": -1009000000001, "type": "supergroup", "title": "openclaw进化"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Alex"},
                    "date": 1779370005,
                    "text": "这个又是怎么回事",
                },
            },
        ],
        group_policy_dir,
    )
    generated_room = resident.read_json(group_policy_dir / "rooms" / "openclaw-evolution" / "room.json", {})
    generated_foreground_policy = ((generated_room.get("policies") or {}).get("foreground_notify") or {})
    check(
        "telegram bridge emits dry-run foreground notify policy for group rooms",
        generated_foreground_policy.get("enabled") is False
        and generated_foreground_policy.get("target_surface") == "telegram_group"
        and generated_foreground_policy.get("target_from_room_chat_id") is True,
    )
    group_policy_tasks = resident.read_jsonl(group_policy_dir / "tasks.jsonl")
    discussion_task = next(task for task in group_policy_tasks if task.get("source", {}).get("update_id") == "group-message:-1009000000001:9101")
    single_mention_task = next(task for task in group_policy_tasks if task.get("source", {}).get("update_id") == "group-message:-1009000000001:9102")
    dm_task = next(task for task in group_policy_tasks if task.get("source", {}).get("update_id") == "update:codex:9103")
    status_command_tasks = [task for task in group_policy_tasks if task.get("source", {}).get("update_id") == "group-message:-1009000000001:9104"]
    natural_status_tasks = [task for task in group_policy_tasks if task.get("source", {}).get("update_id") == "group-message:-1009000000001:9105"]
    group_policy_messages = resident.read_jsonl(group_policy_dir / "messages.jsonl")
    discussion_message = next(message for message in group_policy_messages if message.get("stable_message_id") == "group-message:-1009000000001:9101")
    single_mention_message = next(message for message in group_policy_messages if message.get("stable_message_id") == "group-message:-1009000000001:9102")
    dm_message = next(message for message in group_policy_messages if message.get("stable_message_id") == "update:codex:9103")
    status_command_message = next(message for message in group_policy_messages if message.get("stable_message_id") == "group-message:-1009000000001:9104")
    natural_status_message = next(message for message in group_policy_messages if message.get("stable_message_id") == "group-message:-1009000000001:9105")
    status_intent_paths = sorted((group_policy_dir / "status-fast-path").glob("status-*.json"))
    status_intents = [resident.read_json(path, {}) for path in status_intent_paths]
    status_intents_by_update = {
        str(intent.get("source_update_id") or ""): intent
        for intent in status_intents
    }
    status_intent = status_intents_by_update.get("group-message:-1009000000001:9104", {})
    natural_status_intent = status_intents_by_update.get("group-message:-1009000000001:9105", {})
    check("no-mention group discussion routes to local peers", discussion_task.get("target_agents") == ["codex", "claude-code"])
    check(
        "/status group command uses daemon fast-path instead of agent task queue",
        not status_command_tasks
        and len(status_intents) == 2
        and status_intent.get("agent_id") == "openclaw-main"
        and status_intent.get("chat_id") == "-1009000000001"
        and status_intent.get("trigger") == "status_command"
        and status_command_message.get("command_targets") == []
        and not status_command_message.get("broadcast_targets"),
    )
    check(
        "/status group command is classified as non-interrupting status probe",
        (status_command_message.get("incoming_message_triage") or {}).get("mode") == "non_interrupting_status_probe"
        and (status_command_message.get("incoming_message_triage") or {}).get("active_runner_default") == "continue_existing_runners",
    )
    check(
        "natural status probe uses daemon fast-path instead of agent task queue",
        not natural_status_tasks
        and natural_status_intent.get("agent_id") == "openclaw-main"
        and natural_status_intent.get("chat_id") == "-1009000000001"
        and natural_status_intent.get("trigger") == "natural_status_probe"
        and natural_status_message.get("command_targets") == []
        and not natural_status_message.get("broadcast_targets")
        and not natural_status_message.get("target_agents"),
    )
    check(
        "natural status probe is classified as non-interrupting status probe",
        (natural_status_message.get("incoming_message_triage") or {}).get("mode") == "non_interrupting_status_probe"
        and (natural_status_message.get("incoming_message_triage") or {}).get("active_runner_default") == "continue_existing_runners",
    )
    status_import_room = dry_dir / f"status_fast_path_import_{datetime.now().strftime('%H%M%S%f')}" / "agent-room"
    original_room = resident.ROOM
    try:
        resident.ROOM = status_import_room
        status_import_result = resident.import_canonical_artifacts(group_policy_dir, allow_send=False)
    finally:
        resident.ROOM = original_room
    status_reply_paths = status_import_result.get("status_reply_files_written") or []
    status_replies = [resident.read_json(Path(path), {}) for path in status_reply_paths]
    status_replies_by_run = {
        str(reply.get("run_id") or ""): reply
        for reply in status_replies
    }
    status_reply = status_replies_by_run.get(str(status_intent.get("run_id") or ""), {})
    natural_status_reply = status_replies_by_run.get(str(natural_status_intent.get("run_id") or ""), {})
    check(
        "/status daemon fast-path writes local reply artifact without Telegram send",
        len(status_reply_paths) == 2
        and status_reply.get("agent_id") == "openclaw-main"
        and status_reply.get("run_id") == status_intent.get("run_id")
        and status_reply.get("would_send") is True
        and status_reply.get("sent") is False
        and status_reply.get("projection_mode") == "direct-text",
    )
    check(
        "natural status probe writes local reply artifact without Telegram send",
        natural_status_reply.get("agent_id") == "openclaw-main"
        and natural_status_reply.get("run_id") == natural_status_intent.get("run_id")
        and natural_status_reply.get("would_send") is True
        and natural_status_reply.get("sent") is False
        and natural_status_reply.get("projection_mode") == "direct-text",
    )
    check(
        "duplicate group receiver updates produce one task",
        sum(1 for task in group_policy_tasks if task.get("source", {}).get("update_id") == "group-message:-1009000000001:9101") == 1,
    )
    check(
        "duplicate group receiver updates produce one room message",
        sum(1 for message in group_policy_messages if message.get("stable_message_id") == "group-message:-1009000000001:9101") == 1,
    )
    check(
        "no-mention group discussion is broadcast scoped",
        discussion_message.get("broadcast_targets") == ["codex", "claude-code"]
        and not discussion_message.get("command_targets")
        and not discussion_message.get("mentioned_targets")
        and discussion_task.get("delivery_policy") == "broadcast_all_agents_decide",
    )
    check("no-mention group discussion creates bounded work items", len((discussion_task.get("collaboration") or {}).get("work_items") or []) == 2)
    one_off_single_speaker_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        "具体详细的给我说一下，派一个人出来说就行了，其余人继续干活",
        ["codex", "claude-code"],
        "telegram-user",
        "smoke-single-visible-one-off",
    )
    single_speaker_correction_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        "也不是这个意思，我刚刚那一瞬间担心其余人被干扰所以选择让一个人来说，这是一次性行为",
        ["codex", "claude-code"],
        "telegram-user",
        "smoke-single-visible-correction",
    )
    check(
        "single visible speaker request is scoped to one task",
        one_off_single_speaker_task.get("single_visible_speaker_requested") is True
        and (one_off_single_speaker_task.get("single_visible_speaker_scope") or {}).get("scope") == "current_message_task_only"
        and (one_off_single_speaker_task.get("single_visible_speaker_scope") or {}).get("non_persistent") is True,
    )
    check(
        "single speaker correction does not create sticky single-speaker policy",
        single_speaker_correction_task.get("single_visible_speaker_requested") is not True
        and not single_speaker_correction_task.get("single_visible_speaker_scope"),
    )
    systemization_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        "问题在于你们对于每一个所暴露的问题都要尝试给出系统性的解决方案，这个方案是通过你们讨论决定的。我记得之前已经强调了一些原则了，不知道为什么你们就是记不住",
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9104",
    )
    systemization_items = (systemization_task.get("collaboration") or {}).get("work_items") or []
    check(
        "systemic problem messages create systemic work items",
        bool(systemization_items)
        and all(item.get("systemic_solution_required") is True for item in systemization_items if isinstance(item, dict))
        and any("systemic root cause" in str(item.get("description") or "") for item in systemization_items if isinstance(item, dict)),
    )
    recurring_patch_message = "问题是这种问题不是第一次出现了 之前也修补过，为什么还是没有从根本上解决呢"
    recurring_patch_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        recurring_patch_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9104b",
    )
    recurring_patch_items = (recurring_patch_task.get("collaboration") or {}).get("work_items") or []
    check(
        "recurring patch failure asks for systemic root-cause work",
        recurring_patch_task.get("collab_tick_enabled") is True
        and bool(recurring_patch_items)
        and all(item.get("systemic_solution_required") is True for item in recurring_patch_items if isinstance(item, dict))
        and any("systemic root cause" in str(item.get("description") or "") for item in recurring_patch_items if isinstance(item, dict)),
    )
    recurring_patch_budget = resident.build_task_budget({
        "task_id": "smoke-recurring-patch-failure-budget",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram"},
        "delivery_policy": "broadcast_all_agents_decide",
        "user_message": recurring_patch_message,
    })
    check(
        "recurring patch failure uses runtime design budget",
        recurring_patch_budget.get("interaction_class") == "design_discussion"
        and int(recurring_patch_budget.get("hard_seconds") or 0) >= 1200,
    )
    idle_agent_message = "为什么codex都闲着  不是应该干活的吗  我们不是说了自己没活干的时候要找活干吗"
    idle_agent_repeated_message = "怎么codex和claude code又闲下来了"
    idle_agent_negative_message = "我今天没活干，先整理一下自己的事情"
    check(
        "idle-agent contribution detector is consistent across runtime entrypoints",
        telegram_agent_bridge.idle_agent_contribution_problem_requested(idle_agent_message)
        and telegram_agent_bridge.idle_agent_contribution_problem_requested(idle_agent_repeated_message)
        and agent_room_inject_message.idle_agent_contribution_problem_requested(idle_agent_message)
        and agent_room_inject_message.idle_agent_contribution_problem_requested(idle_agent_repeated_message)
        and resident.idle_agent_contribution_problem_requested(idle_agent_message)
        and resident.idle_agent_contribution_problem_requested(idle_agent_repeated_message)
        and not telegram_agent_bridge.idle_agent_contribution_problem_requested(idle_agent_negative_message)
        and not agent_room_inject_message.idle_agent_contribution_problem_requested(idle_agent_negative_message)
        and not resident.idle_agent_contribution_problem_requested(idle_agent_negative_message),
    )
    shared_detector_source = Path(
        inspect.getsourcefile(telegram_agent_bridge.idle_agent_contribution_problem_requested) or ""
    ).name
    check(
        "idle-agent contribution detector uses one shared implementation",
        shared_detector_source == "agent_room_detection_shared.py"
        and telegram_agent_bridge.idle_agent_contribution_problem_requested
        is agent_room_inject_message.idle_agent_contribution_problem_requested
        and telegram_agent_bridge.idle_agent_contribution_problem_requested
        is resident.idle_agent_contribution_problem_requested,
    )
    idle_agent_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        idle_agent_repeated_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9104c-idle-agent",
    )
    idle_agent_collab = idle_agent_task.get("collaboration") or {}
    idle_agent_items = idle_agent_collab.get("work_items") or []
    check(
        "idle agent criticism creates systemic work-seeking peer loop",
        telegram_agent_bridge.idle_agent_contribution_problem_requested(idle_agent_message)
        and agent_room_inject_message.task_requests_agent_collaboration_loop(idle_agent_message)
        and idle_agent_task.get("collab_tick_enabled") is True
        and bool(idle_agent_items)
        and all(item.get("systemic_solution_required") is True for item in idle_agent_items if isinstance(item, dict))
        and any("systemic root cause" in str(item.get("description") or "") for item in idle_agent_items if isinstance(item, dict)),
    )
    idle_agent_budget = resident.build_task_budget({
        "task_id": "smoke-idle-agent-work-seeking-budget",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram"},
        "delivery_policy": "broadcast_all_agents_decide",
        "user_message": idle_agent_message,
    })
    check(
        "idle agent criticism uses runtime design budget",
        idle_agent_budget.get("interaction_class") == "design_discussion"
        and int(idle_agent_budget.get("hard_seconds") or 0) >= 1200,
    )
    # Regression gate: systemic messages must NOT be routed as room_broadcast.
    # After the routing fix, group_broadcast_targets returns [] for messages
    # that also trigger task_requests_agent_collaboration_loop, so the event_type
    # will be "room_message_ignored" rather than "room_broadcast", and the
    # manifest will still be written with collab_tick_enabled=True for the
    # collaboration daemon to pick up.
    check(
        "systemic recurring-patch message suppresses room_broadcast routing",
        telegram_agent_bridge.task_requests_agent_collaboration_loop(recurring_patch_message) is True
        and telegram_agent_bridge.group_broadcast_targets("group", ["openclaw-main", "codex", "claude-code"], recurring_patch_message) == []
    )
    discussion_review_principle_message = "@lchclaudecode_bot  @lchcodex_bot  你们两个怎么看这个问题  我们之前的原则不就是要你们讨论何审查吗"
    discussion_review_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        discussion_review_principle_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9104c",
    )
    discussion_review_items = (discussion_review_task.get("collaboration") or {}).get("work_items") or []
    discussion_review_triage = discussion_review_task.get("incoming_message_triage") or {}
    check(
        "discussion/review principle message keeps collaboration loop active",
        discussion_review_task.get("collab_tick_enabled") is True
        and discussion_review_triage.get("active_runner_default") == "continue_existing_runners"
        and discussion_review_triage.get("visible_reply_expected") is True
        and bool(discussion_review_items)
        and all(item.get("systemic_solution_required") is True for item in discussion_review_items if isinstance(item, dict))
        and any(str(item.get("role") or "") == "lead" for item in discussion_review_items if isinstance(item, dict))
        and any(str(item.get("role") or "") == "co_producer" for item in discussion_review_items if isinstance(item, dict)),
    )
    context_lag_message = "你没有理解偏，额度就是要看的  我们现在在讨论新的话题  只是你反应太慢了没跟上，这反映出来你的上下文理解还是一个问题  这种问题也是要解决的"
    context_lag_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        context_lag_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9105",
    )
    context_lag_items = (context_lag_task.get("collaboration") or {}).get("work_items") or []
    check(
        "context tracking criticism creates systemic collaboration work items",
        context_lag_task.get("collab_tick_enabled") is True
        and bool(context_lag_items)
        and all(item.get("systemic_solution_required") is True for item in context_lag_items if isinstance(item, dict)),
    )
    bot_to_bot_gap_message = "这不就暴露出bot-to-bot的协作问题来了"
    bot_to_bot_gap_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        bot_to_bot_gap_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9106",
    )
    bot_to_bot_gap_collab = bot_to_bot_gap_task.get("collaboration") or {}
    bot_to_bot_gap_items = bot_to_bot_gap_collab.get("work_items") or []
    check(
        "bot-to-bot collaboration gap creates systemic peer loop",
        bot_to_bot_gap_task.get("collab_tick_enabled") is True
        and bot_to_bot_gap_collab.get("max_rounds") == bot_to_bot_gap_task.get("collab_tick_max_rounds")
        and bool(bot_to_bot_gap_items)
        and all(item.get("systemic_solution_required") is True for item in bot_to_bot_gap_items if isinstance(item, dict)),
    )
    spaced_bot_to_bot_gap_message = "怎么感觉你们的bot to bot的协作还是有问题"
    spaced_bot_to_bot_gap_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        spaced_bot_to_bot_gap_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9106b",
    )
    spaced_bot_to_bot_gap_collab = spaced_bot_to_bot_gap_task.get("collaboration") or {}
    spaced_bot_to_bot_gap_items = spaced_bot_to_bot_gap_collab.get("work_items") or []
    check(
        "spaced bot to bot collaboration gap creates systemic peer loop",
        spaced_bot_to_bot_gap_task.get("collab_tick_enabled") is True
        and bool(spaced_bot_to_bot_gap_items)
        and all(item.get("systemic_solution_required") is True for item in spaced_bot_to_bot_gap_items if isinstance(item, dict))
        and any("systemic root cause" in str(item.get("description") or "") for item in spaced_bot_to_bot_gap_items if isinstance(item, dict)),
    )
    proposal_uptake_message = "不仅是main提方案 你你们每个人提出来的东西都应该被接住 你们能理解我的需求吗，就跟一群人在讨论一样，你可以对别人的观点直接回复，也可以虽然没回复，但是默默记下来，有帮助的甚至有可能会影响自己的行为"
    proposal_uptake_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        proposal_uptake_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9107",
    )
    proposal_uptake_collab = proposal_uptake_task.get("collaboration") or {}
    proposal_uptake_items = proposal_uptake_collab.get("work_items") or []
    check(
        "peer proposal uptake request creates bounded collaboration loop",
        telegram_agent_bridge.peer_proposal_uptake_requested(proposal_uptake_message)
        and agent_room_inject_message.task_requests_agent_collaboration_loop(proposal_uptake_message)
        and proposal_uptake_task.get("collab_tick_enabled") is True
        and "record_uptake" in str((proposal_uptake_task.get("collaboration_tick") or {}).get("acceptance") or "")
        and bool(proposal_uptake_items)
        and all(item.get("systemic_solution_required") is True for item in proposal_uptake_items if isinstance(item, dict)),
    )
    misread_execution_message = "我让你们讨论不就是为了避免某个人理解错误 然后错误的执行吗 你还是落实不了"
    misread_execution_task = telegram_agent_bridge.build_task(
        "openclaw-evolution",
        "-1009000000001",
        misread_execution_message,
        ["codex", "claude-code"],
        "telegram-user",
        "group-message:-1009000000001:9108",
    )
    misread_execution_collab = misread_execution_task.get("collaboration") or {}
    misread_execution_items = misread_execution_collab.get("work_items") or []
    check(
        "collaboration misread/execution correction creates systemic peer loop",
        telegram_agent_bridge.systemic_solution_requested(misread_execution_message)
        and telegram_agent_bridge.collaboration_quality_problem_requested(misread_execution_message)
        and agent_room_inject_message.task_requests_agent_collaboration_loop(misread_execution_message)
        and misread_execution_task.get("collab_tick_enabled") is True
        and bool(misread_execution_items)
        and all(item.get("systemic_solution_required") is True for item in misread_execution_items if isinstance(item, dict)),
    )
    original_runner_room = agent_task_runner.ROOM
    try:
        focus_room = dry_dir / "current_turn_focus_guard_room"
        agent_task_runner.ROOM = focus_room
        focus_messages = focus_room / "rooms" / "openclaw-evolution" / "messages.jsonl"
        focus_messages.parent.mkdir(parents=True, exist_ok=True)
        focus_messages.write_text(
            json.dumps(
                {
                    "created_at": "2026-05-26T00:40:00+08:00",
                    "target_agents": ["codex", "claude-code"],
                    "text": "额度窗口到底怎么显示，继续核查 quota ledger。",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        focus_brief = dry_dir / "current_turn_focus_guard_brief.md"
        focus_brief.write_text(
            "# Telegram Agent Room Task\n\n"
            "## User message\n\n"
            f"{context_lag_message}\n\n"
            "## Boundaries\n\n- smoke\n",
            encoding="utf-8",
        )
        focus_prompt = agent_task_runner.task_prompt(
            {
                "task_id": "smoke-current-turn-focus",
                "run_id": "smoke-current-turn-focus",
                "room_id": "openclaw-evolution",
                "target_agents": ["codex", "claude-code"],
                "brief_path": str(focus_brief),
            },
            "codex",
            {"source_edit": True, "telegram_send": False, "secrets_access": False},
        )
        focus_idx = focus_prompt.find("# Current Turn Focus Guard")
        recent_idx = focus_prompt.find("# Recent Room Context")
        task_idx = focus_prompt.find("Task brief:")
        check(
            "runner prompt puts current-turn focus guard before recent context",
            focus_idx >= 0 and recent_idx > focus_idx and task_idx > recent_idx,
        )
        check(
            "runner prompt does not depend on correction keywords",
            "Detected user correction/topic-shift language" not in focus_prompt
            and "Do this on every turn from message ordering" in focus_prompt,
        )
        check(
            "runner prompt classifies supplements before changing direction",
            "correction, supplement, clarification, status request, or new task" in focus_prompt
            and "Do not abandon existing mainline work unless the current message actually supersedes it" in focus_prompt,
        )
        check(
            "runner prompt treats one-turn chatter reduction as non-standing",
            "one-turn request for reduced chatter" in focus_prompt
            and "not a standing policy" in focus_prompt,
        )
        dm_focus_prompt = agent_task_runner.task_prompt(
            {
                "task_id": "smoke-current-turn-focus-dm",
                "run_id": "smoke-current-turn-focus-dm",
                "room_id": "dm-codex-100000001",
                "target_agents": ["codex"],
                "brief_path": str(focus_brief),
            },
            "codex",
            {"source_edit": True, "telegram_send": False, "secrets_access": False},
        )
        check(
            "private DM focus guard keeps peer status as background",
            "In private DM rooms, treat peer/other-agent status from recent context as background only" in dm_focus_prompt
            and "unless the current User message asks for it or directly depends on it" in dm_focus_prompt,
        )
        external_prompt = agent_task_runner.external_deepseek_fallback_prompt(
            {
                "task_id": "smoke-external-deepseek-compact",
                "run_id": "smoke-external-deepseek-compact",
                "room_id": "openclaw-evolution",
                "target_agents": ["claude-code"],
                "brief_path": str(focus_brief),
            },
            focus_prompt,
            [
                {
                    "model": "glm-5.1",
                    "status": "skipped_cooldown",
                    "reason": "usage_limit",
                    "cooldown_until": "2099-01-01T00:00:00+08:00",
                }
            ],
        )
        check(
            "external DeepSeek fallback prompt is compact but keeps current user message and cooldown evidence",
            len(external_prompt) < len(focus_prompt)
            and context_lag_message in external_prompt
            and "glm-5.1" in external_prompt
            and "skipped_cooldown" in external_prompt
            and "Do not claim tool use" in external_prompt,
        )
        check(
            "external DeepSeek status model is provider-qualified",
            agent_task_runner.external_deepseek_status_model("deepseek-v4-flash")
            == f"{agent_task_runner.EXTERNAL_DEEPSEEK_BACKEND}/deepseek-v4-flash",
        )
        check(
            "external DeepSeek fallback selects V4-pro for source-edit/runtime routes",
            agent_task_runner.external_deepseek_fallback_model(
                {"room_id": "openclaw-evolution"},
                {"source_edit": True, "model_route_key": "workspace_write"},
            )
            == ("deepseek-v4-pro", "route.workspace_write"),
        )
        ark_cooldown_attempts = [
            {
                "model": "deepseek-v4-pro",
                "status": "skipped_cooldown",
                "reason": "usage_limit",
                "cooldown_until": "2099-01-01T00:00:00+08:00",
            }
        ]
        check(
            "external DeepSeek fallback promotes V4-pro when Ark V4-pro is cooling down",
            agent_task_runner.external_deepseek_fallback_model(
                {"room_id": "openclaw-evolution"},
                {"source_edit": False, "model_route_key": "plain_chat"},
                ark_cooldown_attempts,
            )
            == ("deepseek-v4-pro", "ark_cooldown.deepseek-v4-pro"),
        )
        check(
            "external DeepSeek fallback keeps V4-flash as secondary after promoted V4-pro",
            agent_task_runner.external_deepseek_fallback_model_candidates(
                {"room_id": "openclaw-evolution"},
                {"source_edit": False, "model_route_key": "plain_chat"},
                ark_cooldown_attempts,
            )
            == [
                ("deepseek-v4-pro", "ark_cooldown.deepseek-v4-pro"),
                ("deepseek-v4-flash", "secondary_after_v4_pro"),
            ],
        )
        check(
            "external DeepSeek fallback selects V4-flash for ordinary fast text fallback",
            agent_task_runner.external_deepseek_fallback_model(
                {"room_id": "openclaw-evolution"},
                {"source_edit": False, "model_route_key": "plain_chat"},
            )
            == ("deepseek-v4-flash", "route.plain_chat"),
        )
        check(
            "external DeepSeek fallback metadata is copied into room comments",
            'comment["external_deepseek_fallback"] = comment_fields.get("external_deepseek_fallback")'
            in inspect.getsource(agent_task_runner.execute_agent),
        )
    finally:
        agent_task_runner.ROOM = original_runner_room
    check(
        "collaboration work items have visible titles",
        all(
            item.get("title")
            for item in ((discussion_task.get("collaboration") or {}).get("work_items") or [])
            if isinstance(item, dict)
        ),
    )
    discussion_roles = {
        role.get("agent_id"): role.get("role")
        for role in ((discussion_task.get("collaboration") or {}).get("roles") or [])
        if isinstance(role, dict)
    }
    discussion_work_item_roles = {
        item.get("assigned_to"): item.get("role")
        for item in ((discussion_task.get("collaboration") or {}).get("work_items") or [])
        if isinstance(item, dict)
    }
    check(
        "collaboration roles use lead/co-producer peer language",
        set(discussion_roles.values()) <= {"lead", "co_producer"}
        and set(discussion_work_item_roles.values()) <= {"lead", "co_producer"}
        and "lead" in set(discussion_roles.values()),
    )
    discussion_assignments = resident.collaboration_assignments(discussion_task, discussion_task.get("target_agents") or [])
    manifest_lead = next((agent_id for agent_id, role in discussion_roles.items() if role == "lead"), "")
    check(
        "runtime collaboration assignment follows manifest lead role",
        bool(manifest_lead)
        and discussion_assignments.get(manifest_lead, {}).get("turn_position") == "lead"
        and all(
            assignment.get("turn_position") in {"lead", "co_producer"}
            for assignment in discussion_assignments.values()
        ),
    )
    check("single group mention keeps first owner target only", single_mention_task.get("target_agents") == ["claude-code"])
    check("single group mention records first response owner", single_mention_message.get("first_response_owner") == "claude-code")
    check("single group mention is targeted reply", single_mention_task.get("delivery_policy") == "targeted_reply" and not single_mention_task.get("broadcast_targets"))
    check("private DM remains separate from group room", str(dm_task.get("room_id") or "").startswith("dm-codex-") and dm_task.get("target_agents") == ["codex"])
    check("private DM records direct receiver only", dm_message.get("receiver_agent_id") == "codex" and dm_message.get("chat_type") == "private")
    private_followup_parent = {
        "task_id": "smoke-private-dm-peer-followup-parent",
        "run_id": "smoke-private-dm-peer-followup-parent",
        "room_id": "dm-claude-code-100000001",
        "requested_by": "telegram-user",
        "delivery_policy": "targeted_reply",
        "target_agents": ["claude-code"],
        "source": {"transport": "telegram", "chat_id": "100000001", "update_id": "update:claude-code:private"},
    }
    check("private DM peer follow-up stays local-only", not resident.peer_followup_may_project_to_telegram(private_followup_parent))
    private_followup_task = {
        "task_id": "smoke-private-dm-peer-followup",
        "requested_by": "agent-room-collab-followup",
        "delivery_policy": None,
        "target_agents": ["codex"],
        "room_id": "dm-claude-code-100000001",
        "peer_followup_visible_allowed": False,
        "source": {"transport": "agent-room-collab-followup", "chat_id": "100000001"},
    }
    private_followup_material_comment = {
        "agent_id": "codex",
        "run_id": "smoke-private-dm-peer-followup",
        "title": "peer evidence",
        "body": "我引用 Claude Code 的主张并补充本地证据。",
        "blockers": [],
    }
    may_project, projection_mode = resident.telegram_projection_decision(private_followup_task, [private_followup_material_comment])
    check("private DM peer follow-up cannot project through another bot DM", not may_project and projection_mode == "private_dm_agent_mismatch")
    private_dm_mismatch_task = {
        "task_id": "smoke-private-dm-mismatched-agent",
        "requested_by": "telegram-user",
        "delivery_policy": "targeted_reply",
        "target_agents": ["codex"],
        "room_id": "dm-claude-code-100000001",
        "source": {"transport": "telegram", "chat_id": "100000001"},
    }
    may_project, projection_mode = resident.telegram_projection_decision(
        private_dm_mismatch_task,
        [{"agent_id": "codex", "run_id": "smoke-private-dm-mismatched-agent", "body": "本地证据。"}],
    )
    check("private DM cannot project a different agent bot reply", not may_project and projection_mode == "private_dm_agent_mismatch")
    may_project, projection_mode = resident.telegram_projection_decision(
        private_dm_mismatch_task,
        [{
            "agent_id": "codex",
            "run_id": "smoke-private-dm-mismatched-quota-notice",
            "body": "Codex quota status.",
            "telegram_projection_status": "user_visible_quota_notification",
        }],
    )
    check("private DM mismatch suppresses even user-visible status notices", not may_project and projection_mode == "private_dm_agent_mismatch")
    markdown_sample = "\x1b[32m**重点** `cmd --flag` *args path/*.py\n```python\nprint('ok')\n```"
    normalized_runner_text = agent_task_runner.normalize_claude_code_visible_text(markdown_sample)
    normalized_reply_text = telegram_agent_reply.normalize_claude_code_visible_text(markdown_sample)
    for label, normalized_text in (
        ("runner", normalized_runner_text),
        ("telegram reply", normalized_reply_text),
    ):
        check(f"{label} normalizer strips ANSI transport noise", "\x1b[" not in normalized_text)
        check(
            f"{label} normalizer preserves Claude markdown and code tokens",
            all(token in normalized_text for token in ("**重点**", "`cmd --flag`", "*args", "path/*.py", "```python")),
        )
    projected_reply_text = telegram_agent_reply.telegram_plain_text_projection(normalized_reply_text)
    check("telegram projection hides paired markdown bold markers", "**重点**" not in projected_reply_text and "重点" in projected_reply_text)
    check("telegram projection preserves code-ish stars", all(token in projected_reply_text for token in ("`cmd --flag`", "*args", "path/*.py", "```python")))
    code_star_sample = "`x ** y` **标题**"
    projected_code_star = telegram_agent_reply.telegram_plain_text_projection(code_star_sample)
    check("telegram projection preserves bold-like stars inside code", "`x ** y`" in projected_code_star and "**标题**" not in projected_code_star)
    code_block_sample = "**改法**\n\n```python\ndef f(x):\n    return x ** 2 < 5\n```\n\n`cmd --flag` 和 path/*.py 保持可读。"
    code_block_projection = telegram_agent_reply.build_telegram_projection(code_block_sample)
    check("telegram code block projection uses HTML parse mode", code_block_projection.get("parse_mode") == "HTML")
    check(
        "telegram code block projection renders fenced code as pre/code",
        '<pre><code class="language-python">' in code_block_projection.get("text", "")
        and "</code></pre>" in code_block_projection.get("text", ""),
    )
    check(
        "telegram code block projection escapes code without eating operators",
        "return x ** 2 &lt; 5" in code_block_projection.get("text", "")
        and "path/*.py" in code_block_projection.get("text", ""),
    )
    bold_code_projection = telegram_agent_reply.telegram_html_projection("**`task_router_core.py` 的关键词已经清理完毕**")
    check(
        "telegram bold projection can wrap inline code",
        bold_code_projection == "<b><code>task_router_core.py</code> 的关键词已经清理完毕</b>",
    )
    check(
        "telegram code block fallback keeps original fences",
        "```python" in code_block_projection.get("plain_fallback_text", "")
        and "return x ** 2 < 5" in code_block_projection.get("plain_fallback_text", ""),
    )
    check(
        "telegram reply suppresses routine approval request for safe local work",
        telegram_agent_reply.routine_approval_request_reason("批准的话我直接开始补本地 smoke。") == "routine_approval_request_to_alex",
    )
    check(
        "telegram reply suppresses routine optional execution tail for safe local work",
        telegram_agent_reply.routine_approval_request_reason(
            "已 inspection 配置并给出方案。\n\n我可以在权限内修改 standing-agenda.json 或补 smoke。"
        ) == "routine_optional_execution_to_alex",
    )
    check(
        "telegram reply keeps high-risk approval request visible",
        telegram_agent_reply.routine_approval_request_reason("需要你确认是否发布到外部生产环境。") is None,
    )
    check(
        "telegram reply keeps non-retrievable preference confirmation visible",
        telegram_agent_reply.routine_approval_request_reason("需要你确认这篇日报的口吻偏好。") is None,
    )
    long_code_sample = "```python\n" + "print('x')\n" * 800 + "```"
    long_projection = telegram_agent_reply.telegram_html_projection_limited(long_code_sample, 3500)
    check(
        "long Telegram projection stays HTML and bounded",
        len(long_projection) <= 3500 and "<pre><code" in long_projection and "</code></pre>" in long_projection,
    )

    close_task_path = dry_dir / "task_manifest_close.json"
    close_task = {
        "task_id": "smoke-task-manifest-close",
        "run_id": "smoke-task-manifest-close",
        "target_agents": ["codex"],
        "status": "queued",
        "result_paths": [],
    }
    resident.write_json(close_task_path, close_task)
    close_result_path = dry_dir / "task_manifest_close_result.json"
    close_result_path.write_text("{}\n", encoding="utf-8")
    agent_task_runner.update_task_manifest_after_results(
        close_task_path,
        close_task,
        [{"agent_id": "codex", "comment_written": True, "executed": True, "result": {"ok": True}}],
        close_result_path,
    )
    closed_task = resident.read_json(close_task_path, {})
    check("task manifest closes after comment result", closed_task.get("status") == "completed")
    check("task manifest records runner result path", bool(closed_task.get("runner_result_path")))

    canonical_task_path = dry_dir / "task_manifest_close_canonical.json"
    local_task_path = dry_dir / "task_manifest_close_local.json"
    canonical_task = {
        "task_id": "smoke-task-manifest-close-canonical",
        "run_id": "smoke-task-manifest-close-canonical",
        "target_agents": ["codex", "claude-code"],
        "status": "queued",
        "result_paths": [],
    }
    local_task = dict(canonical_task)
    local_task["target_agents"] = ["codex"]
    local_task["_canonical_manifest_path"] = str(canonical_task_path)
    resident.write_json(canonical_task_path, canonical_task)
    resident.write_json(local_task_path, local_task)
    local_result_path = dry_dir / "task_manifest_close_local_result.json"
    local_result_path.write_text("{}\n", encoding="utf-8")
    agent_task_runner.update_task_manifest_after_results(
        local_task_path,
        local_task,
        [{"agent_id": "codex", "comment_written": True, "executed": True, "result": {"ok": True}}],
        local_result_path,
    )
    mirrored_task = resident.read_json(canonical_task_path, {})
    check("local async runner mirrors partial status to canonical manifest", mirrored_task.get("status") == "partial")
    check("canonical manifest records completed async agent", "codex" in ((mirrored_task.get("runner_summary") or {}).get("completed_agents") or []))

    original_ledger_dir = agent_task_runner.COLLAB_LEDGER_DIR
    try:
        agent_task_runner.COLLAB_LEDGER_DIR = dry_dir / "manifest_sync_collaboration_ledgers"
        collab_sync_task_path = dry_dir / "task_manifest_collab_sync.json"
        collab_sync_result_path = dry_dir / "task_manifest_collab_sync_result.json"
        collab_sync_result_path.write_text("{}\n", encoding="utf-8")
        collab_sync_task = {
            "task_id": "smoke-task-manifest-collab-sync",
            "run_id": "smoke-task-manifest-collab-sync",
            "target_agents": ["codex"],
            "status": "queued",
            "result_paths": [],
            "collaboration": {
                "status": "open",
                "participants": ["codex"],
                "work_items": [
                    {
                        "id": "room_response_codex",
                        "status": "open",
                        "assigned_to": "codex",
                    }
                ],
                "claims": [],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
            },
        }
        resident.write_json(collab_sync_task_path, collab_sync_task)
        state_file, _archive_file = agent_task_runner.collaboration_ledger_paths(collab_sync_task)
        resident.write_json(
            state_file,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": "smoke-task-manifest-collab-sync",
                "run_id": "smoke-task-manifest-collab-sync",
                "status": "completed",
                "mode": "dynamic_claims",
                "participants": ["codex"],
                "roles": [],
                "work_items": [
                    {
                        "id": "room_response_codex",
                        "status": "completed",
                        "assigned_to": "codex",
                        "claimed_by": "codex",
                    }
                ],
                "claims": [
                    {
                        "work_item_id": "room_response_codex",
                        "agent_id": "codex",
                        "status": "completed",
                    }
                ],
                "artifacts": [
                    {
                        "id": "art-001",
                        "work_item_id": "room_response_codex",
                        "path": "agent-comments/codex.jsonl",
                    }
                ],
                "blockers": [],
                "handoffs": [],
                "updated_at": "2026-05-25T21:16:00+08:00",
            },
        )
        agent_task_runner.update_task_manifest_after_results(
            collab_sync_task_path,
            collab_sync_task,
            [{"agent_id": "codex", "comment_written": True, "executed": True, "result": {"ok": True}}],
            collab_sync_result_path,
        )
        synced_task = resident.read_json(collab_sync_task_path, {})
        synced_collab = synced_task.get("collaboration") if isinstance(synced_task.get("collaboration"), dict) else {}
        synced_items = synced_collab.get("work_items") if isinstance(synced_collab.get("work_items"), list) else []
        check(
            "task manifest syncs completed collaboration ledger snapshot",
            synced_collab.get("status") == "completed"
            and synced_items
            and synced_items[0].get("status") == "completed"
            and bool(synced_collab.get("artifacts")),
        )
        multi_collab_task_path = dry_dir / "task_manifest_multi_collab_quality.json"
        multi_collab_result_path = dry_dir / "task_manifest_multi_collab_quality_result.json"
        multi_collab_result_path.write_text("{}\n", encoding="utf-8")
        multi_collab_task = {
            "task_id": "smoke-task-manifest-multi-collab-quality",
            "run_id": "smoke-task-manifest-multi-collab-quality",
            "target_agents": ["codex", "claude-code"],
            "status": "queued",
            "result_paths": [],
            "collaboration": {
                "status": "open",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
            },
        }
        resident.write_json(multi_collab_task_path, multi_collab_task)
        multi_state_file, _multi_archive_file = agent_task_runner.collaboration_ledger_paths(multi_collab_task)
        resident.write_json(
            multi_state_file,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": "smoke-task-manifest-multi-collab-quality",
                "run_id": "smoke-task-manifest-multi-collab-quality",
                "status": "completed",
                "mode": "dynamic_claims",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [
                    {"id": "art-codex", "work_item_id": "room_response_codex", "agent_id": "codex", "path": "agent-comments/codex.jsonl"},
                    {"id": "art-claude", "work_item_id": "room_response_claude-code", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"},
                ],
                "blockers": [],
                "handoffs": [],
                "updated_at": "2026-05-26T23:05:00+08:00",
            },
        )
        agent_task_runner.update_task_manifest_after_results(
            multi_collab_task_path,
            multi_collab_task,
            [
                {"agent_id": "codex", "comment_written": True, "executed": True, "result": {"ok": True}},
                {"agent_id": "claude-code", "comment_written": True, "executed": True, "result": {"ok": True}},
            ],
            multi_collab_result_path,
        )
        multi_synced_task = resident.read_json(multi_collab_task_path, {})
        multi_gate = (multi_synced_task.get("runner_summary") or {}).get("collaboration_quality_gate") or {}
        check(
            "multi-agent manifest records collaboration quality review debt",
            multi_synced_task.get("status") == "completed"
            and multi_synced_task.get("quality_gate_status") == "needs_collaboration_review"
            and multi_synced_task.get("review_status") == "needs_collaboration_review"
            and multi_gate.get("reason") == "parallel_artifacts_without_integration",
        )
        uptake_task_path = dry_dir / "task_manifest_multi_collab_uptake.json"
        uptake_result_path = dry_dir / "task_manifest_multi_collab_uptake_result.json"
        uptake_result_path.write_text("{}\n", encoding="utf-8")
        uptake_task = {
            "task_id": "smoke-task-manifest-multi-collab-uptake",
            "run_id": "smoke-task-manifest-multi-collab-uptake",
            "target_agents": ["codex", "claude-code"],
            "status": "queued",
            "result_paths": [],
            "collaboration": {
                "status": "open",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [],
                "blockers": [],
                "handoffs": [],
                "points": [],
                "uptakes": [],
            },
        }
        resident.write_json(uptake_task_path, uptake_task)
        uptake_state_file, _uptake_archive_file = agent_task_runner.collaboration_ledger_paths(uptake_task)
        resident.write_json(
            uptake_state_file,
            {
                "schema": "openclaw.agent_room.collaboration_ledger.v0",
                "room_id": "openclaw-evolution",
                "task_id": "smoke-task-manifest-multi-collab-uptake",
                "run_id": "smoke-task-manifest-multi-collab-uptake",
                "status": "completed",
                "mode": "dynamic_claims",
                "participants": ["codex", "claude-code"],
                "work_items": [],
                "claims": [],
                "artifacts": [
                    {"id": "art-codex", "work_item_id": "room_response_codex", "agent_id": "codex", "path": "agent-comments/codex.jsonl"},
                    {"id": "art-claude", "work_item_id": "room_response_claude-code", "agent_id": "claude-code", "path": "agent-comments/claude.jsonl"},
                ],
                "blockers": [],
                "handoffs": [],
                "points": [
                    {"id": "pt-001", "agent_id": "codex", "kind": "proposal", "text": "status surface should track per-agent uptake", "status": "incorporated"}
                ],
                "uptakes": [
                    {"id": "uptake-001", "point_id": "pt-001", "point_agent_id": "codex", "by_agent": "claude-code", "status": "incorporated", "reason": "silent uptake changed status-card design"}
                ],
                "updated_at": "2026-05-26T23:55:00+08:00",
            },
        )
        agent_task_runner.update_task_manifest_after_results(
            uptake_task_path,
            uptake_task,
            [
                {"agent_id": "codex", "comment_written": True, "executed": True, "result": {"ok": True}},
                {"agent_id": "claude-code", "comment_written": True, "executed": True, "result": {"ok": True}},
            ],
            uptake_result_path,
        )
        uptake_synced_task = resident.read_json(uptake_task_path, {})
        uptake_gate = (uptake_synced_task.get("runner_summary") or {}).get("collaboration_quality_gate") or {}
        check(
            "multi-agent point uptake satisfies collaboration quality gate",
            uptake_synced_task.get("quality_gate_status") == "peer_reviewed"
            and uptake_gate.get("reason") == "point_uptake_recorded"
            and bool((uptake_synced_task.get("collaboration") or {}).get("points")),
        )

        original_runner_room = agent_task_runner.ROOM
        original_comment_root = agent_task_runner.COMMENT_ROOT
        try:
            agent_task_runner.ROOM = dry_dir / "parent_followup_uptake_room"
            agent_task_runner.COMMENT_ROOT = dry_dir / "parent_followup_uptake_comments"
            shutil.rmtree(agent_task_runner.ROOM, ignore_errors=True)
            shutil.rmtree(agent_task_runner.COMMENT_ROOT, ignore_errors=True)
            parent_id = "smoke-parent-followup-uptake"
            parent_task_path = agent_task_runner.ROOM / "tasks" / parent_id / "manifest.json"
            parent_task = {
                "task_id": parent_id,
                "run_id": parent_id,
                "target_agents": ["codex", "claude-code"],
                "status": "completed",
                "runner_summary": {"completed_agents": ["codex", "claude-code"]},
                "collaboration": {
                    "status": "completed",
                    "participants": ["codex", "claude-code"],
                    "work_items": [
                        {"id": "room_response_codex", "status": "completed", "assigned_to": "codex"},
                        {"id": "room_response_claude-code", "status": "completed", "assigned_to": "claude-code"},
                    ],
                    "claims": [],
                    "artifacts": [],
                    "blockers": [],
                    "handoffs": [],
                    "points": [],
                    "uptakes": [],
                },
            }
            resident.write_json(parent_task_path, parent_task)
            agent_task_runner.append_jsonl(
                agent_task_runner.comment_path("codex"),
                {
                    "agent_id": "codex",
                    "task_id": parent_id,
                    "run_id": parent_id,
                    "kind": "evidence",
                    "title": "Codex evidence point",
                    "body": "Codex says the mainline has local evidence but lacks uptake closure.",
                },
            )
            parent_state_file, parent_archive_file = agent_task_runner.collaboration_ledger_paths(parent_task)
            for stale_path in (
                parent_state_file,
                parent_archive_file,
                parent_state_file.with_name(f".{parent_state_file.name}.lock"),
            ):
                if stale_path.exists():
                    stale_path.unlink()
            init_parent = agent_task_runner.run_collaboration_ledger(
                ["init", "--task-file", str(parent_task_path), "--if-needed"],
                state_file=parent_state_file,
                archive_file=parent_archive_file,
            )
            check("parent follow-up uptake smoke initializes parent ledger", init_parent.get("ok"))
            source_point = agent_task_runner.run_collaboration_ledger(
                [
                    "point",
                    "--agent-id", "codex",
                    "--kind", "evidence",
                    "--text", "Codex source point that the follow-up was created for.",
                    "--source-message-id", "agent-comment:codex:source-point",
                ],
                state_file=parent_state_file,
                archive_file=parent_archive_file,
            )
            later_point = agent_task_runner.run_collaboration_ledger(
                [
                    "point",
                    "--agent-id", "codex",
                    "--kind", "proposal",
                    "--text", "Later Codex point that must not steal the follow-up uptake.",
                    "--source-message-id", "agent-comment:codex:later-point",
                ],
                state_file=parent_state_file,
                archive_file=parent_archive_file,
            )
            check("parent follow-up uptake smoke seeds distinct source point", source_point.get("ok") and later_point.get("ok"))
            followup_task = {
                "task_id": "smoke-peer-followup-completion",
                "run_id": "smoke-peer-followup-completion",
                "requested_by": "agent-room-collab-followup",
                "lane": "peer_collaboration_followup",
                "target_agents": ["claude-code"],
                "collab_parent_task_id": parent_id,
                "collab_parent_agent_id": "codex",
                "collab_source_point_id": source_point.get("point_id"),
                "collab_intent": "delegate_work",
                "collaboration_action": {
                    "source_agent_id": "codex",
                    "source_point_id": source_point.get("point_id"),
                    "expected_outputs": ["smoke", "artifact", "evidence"],
                },
            }
            hook = agent_task_runner.record_parent_peer_followup_uptake(
                followup_task,
                "claude-code",
                {
                    "agent_id": "claude-code",
                    "task_id": "smoke-peer-followup-completion",
                    "run_id": "smoke-peer-followup-completion",
                    "title": "Claude uptake",
                    "body": "同意 Codex 的证据，并补了 smoke/artifact 验证。",
                },
                {"ok": True},
            )
            parent_ledger = resident.read_json(parent_state_file, {})
            refreshed_parent = resident.read_json(parent_task_path, {})
            check(
                "peer follow-up completion records parent point uptake",
                hook.get("ok")
                and hook.get("status") == "recorded"
                and len(parent_ledger.get("points") or []) == 2
                and any(
                    item.get("point_id") == source_point.get("point_id")
                    and item.get("point_agent_id") == "codex"
                    and item.get("by_agent") == "claude-code"
                    and item.get("status") in {"accepted", "challenged", "incorporated", "rejected", "superseded"}
                    for item in (parent_ledger.get("uptakes") or [])
                    if isinstance(item, dict)
                ),
            )
            check("parent manifest quality gate refreshes from auto uptake", refreshed_parent.get("quality_gate_status") == "peer_reviewed")
        finally:
            agent_task_runner.ROOM = original_runner_room
            agent_task_runner.COMMENT_ROOT = original_comment_root
    finally:
        agent_task_runner.COLLAB_LEDGER_DIR = original_ledger_dir

    participant_guard_task = {
        "task_id": "smoke-collab-participant-guard",
        "run_id": "smoke-collab-participant-guard",
        "target_agents": ["codex"],
        "collaboration": {
            "participants": ["codex"],
            "work_items": [
                {
                    "id": "peer_followup_response",
                    "status": "open",
                    "source_agent_id": "claude-code",
                }
            ],
        },
    }
    check(
        "collaboration work item lookup respects participants",
        agent_task_runner.collaboration_work_item_id(participant_guard_task, "codex") == "peer_followup_response"
        and agent_task_runner.collaboration_work_item_id(participant_guard_task, "claude-code") is None,
    )
    original_agent_status = agent_task_runner.agent_status
    try:
        agent_task_runner.agent_status = lambda agent_id: {"agent_id": agent_id, "ready": True}
        skipped_nonparticipant = agent_task_runner.execute_agent(
            participant_guard_task,
            "claude-code",
            dry_dir / "participant_guard_runner",
            allow_exec=False,
        )
    finally:
        agent_task_runner.agent_status = original_agent_status
    check(
        "nonparticipant collaboration dispatch is skipped without room comment",
        skipped_nonparticipant.get("skipped") is True
        and skipped_nonparticipant.get("skip_reason") == "agent_not_in_collaboration_participants"
        and skipped_nonparticipant.get("comment_written") is False,
    )
    co_producer_task = {
        "task_id": "smoke-collab-soft-coproducer",
        "run_id": "smoke-collab-soft-coproducer",
        "room_id": "openclaw-evolution",
        "target_agents": ["codex"],
        "permissions": {"source_edit": False, "global_state_change": False},
        "collaboration_assignment": {
            "turn_position": "co_producer",
            "role": "runtime_patch_evidence_or_boundary_producer",
        },
        "collaboration": {
            "participants": ["codex", "claude-code"],
            "roles": [
                {"agent_id": "claude-code", "role": "lead"},
                {"agent_id": "codex", "role": "co_producer"},
            ],
            "work_items": [{"id": "shared_response", "status": "open"}],
        },
    }
    claim_conflict_ledger = {
        "enabled": True,
        "work_item_id": "shared_response",
        "init": {"ok": True},
        "claim": {"ok": False, "error": "work item already claimed by claude-code"},
    }
    lead_task = dict(co_producer_task)
    lead_task["collaboration_assignment"] = {"turn_position": "lead"}
    lead_task["collaboration"] = {
        **co_producer_task["collaboration"],
        "roles": [{"agent_id": "codex", "role": "lead"}],
    }
    check(
        "co-producer claim conflict is eligible for soft material contribution",
        agent_task_runner.collaboration_soft_contribution_allowed(co_producer_task, "codex", claim_conflict_ledger)
        and not agent_task_runner.collaboration_soft_contribution_allowed(lead_task, "codex", claim_conflict_ledger),
    )
    soft_brief = dry_dir / "soft_coproducer_claim_conflict.md"
    soft_brief.write_text("## User message\n\n请补一个不同角度的 smoke 和证据。\n", encoding="utf-8")
    co_producer_task["brief_path"] = str(soft_brief)
    original_comment_root = agent_task_runner.COMMENT_ROOT
    original_collaboration_begin = agent_task_runner.collaboration_begin
    original_agent_status = agent_task_runner.agent_status
    original_run_codex_with_fallback = agent_task_runner.run_codex_with_fallback
    try:
        agent_task_runner.COMMENT_ROOT = dry_dir / "soft_coproducer_comments"
        agent_task_runner.collaboration_begin = lambda *_args, **_kwargs: dict(claim_conflict_ledger)
        agent_task_runner.agent_status = lambda agent_id: {"agent_id": agent_id, "ready": True}
        agent_task_runner.run_codex_with_fallback = lambda *_args, **_kwargs: (
            {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
            "我补了 co-producer 的 smoke 证据，不复述 lead 结论。",
            {"title": "soft co-producer material follow-up", "kind": "status", "confidence": "high"},
        )
        soft_result = agent_task_runner.execute_agent(
            co_producer_task,
            "codex",
            dry_dir / "soft_coproducer_runner",
            allow_exec=True,
        )
    finally:
        agent_task_runner.COMMENT_ROOT = original_comment_root
        agent_task_runner.collaboration_begin = original_collaboration_begin
        agent_task_runner.agent_status = original_agent_status
        agent_task_runner.run_codex_with_fallback = original_run_codex_with_fallback
    soft_comment = soft_result.get("comment") if isinstance(soft_result.get("comment"), dict) else {}
    soft_ledger = soft_result.get("collaboration_ledger") if isinstance(soft_result.get("collaboration_ledger"), dict) else {}
    check(
        "co-producer claim conflict runs without collaboration-ownership noise",
        soft_result.get("executed") is True
        and soft_result.get("comment_written") is True
        and soft_ledger.get("soft_unclaimed_contribution") is True
        and soft_ledger.get("work_item_id") is None
        and "协作账本没有授予" not in str(soft_comment.get("body") or ""),
    )

    def dispatch_task_path(task_id: str, message: str) -> Path:
        brief_path = dry_dir / f"{task_id}.md"
        brief_path.write_text(f"## User message\n\n{message}\n", encoding="utf-8")
        task_path = dry_dir / f"{task_id}.json"
        resident.write_json(task_path, {
            "task_id": task_id,
            "run_id": task_id,
            "brief_path": str(brief_path),
            "target_agents": ["codex", "claude-code"],
            "source": {"transport": "telegram"},
        })
        return task_path

    accelerated = dispatch_task_path("smoke-dispatch-accelerated", "antigravity 这个任务太慢了，尽可能快。")
    ordinary_before = dispatch_task_path("smoke-dispatch-ordinary-before", "继续保留普通协作任务。")
    ordinary_after = dispatch_task_path("smoke-dispatch-ordinary-after", "另一个协作任务也不要停掉。")
    default_dispatch = resident.select_new_task_paths([accelerated, ordinary_before, ordinary_after], 2)
    selected_dispatch = resident.select_new_task_paths([accelerated, ordinary_before, ordinary_after], 2, prioritize_acceleration=True)
    check("acceleration marker is detected", resident.task_requests_acceleration(resident.read_json(accelerated, {})))
    check("acceleration priority is not default dispatch policy", accelerated not in default_dispatch and ordinary_before in default_dispatch and ordinary_after in default_dispatch)
    check("accelerated task is selected in same tick", accelerated in selected_dispatch)
    check("acceleration keeps one ordinary new-task slot", ordinary_after in selected_dispatch)
    check("acceleration dispatch respects tick budget", len(selected_dispatch) == 2)

    mechanism_brief = dry_dir / "collaboration_mechanism_brief.md"
    mechanism_brief.write_text(
        "## User message\n\n协作机制应该自动沉淀为系统经验，不要让我手动裁决。\n",
        encoding="utf-8",
    )
    mechanism_task = {
        "task_id": "smoke-collaboration-mechanism-system-experience",
        "run_id": "smoke-collaboration-mechanism-system-experience",
        "brief_path": str(mechanism_brief),
        "target_agents": ["codex", "claude-code"],
    }
    mechanism_assignments = resident.collaboration_assignments(mechanism_task, ["codex", "claude-code"])
    codex_mechanism_assignment = mechanism_assignments.get("codex") or {}
    mechanism_protocol = codex_mechanism_assignment.get("mechanism_change_protocol") or []
    peer_protocol = codex_mechanism_assignment.get("peer_interaction_protocol") or []
    check("collaboration assignments embed system principle", "系统责任" in str(codex_mechanism_assignment.get("collaboration_system_principle") or ""))
    check("collaboration assignments embed first-principles resolution", "目标、不变量和边界" in str(codex_mechanism_assignment.get("first_principles_resolution") or ""))
    check("collaboration assignments reject band-aid approval loop", "一次性止血" in str(codex_mechanism_assignment.get("first_principles_resolution") or "") and "交回 Alex" in str(codex_mechanism_assignment.get("first_principles_resolution") or ""))
    check("collaboration assignments embed production principle", "生产贡献者" in str(codex_mechanism_assignment.get("production_principle") or ""))
    check("collaboration assignments require proactive mainline work before NO_COMMENT", "主动寻找不重复的主线推进点" in str(codex_mechanism_assignment.get("production_principle") or ""))
    check("collaboration assignments protect existing task flows", "不能自动套用或改写已有生产流程" in str(codex_mechanism_assignment.get("production_principle") or ""))
    check("collaboration mechanism protocol avoids user arbitration", any("不要求 Alex" in str(item) or "默认裁决者" in str(item) for item in mechanism_protocol))
    check("collaboration mechanism corrections do not need Alex reconfirmation", any("二次确认" in str(item) and "Alex" in str(item) for item in mechanism_protocol))
    check("collaboration mechanism protocol requires peer challenge", any("反例" in str(item) or "挑战" in str(item) for item in mechanism_protocol))
    check("collaboration mechanism protocol uses invariant framing", any("系统不变量" in str(item) for item in mechanism_protocol))
    check("collaboration mechanism protocol avoids minimal work-item framing", not any("最小 work_item" in str(item) for item in mechanism_protocol))
    check("collaboration assignments require concrete peer alignment", any("具体" in str(item) and "peer" in str(item) for item in peer_protocol))
    check("collaboration mechanism protocol handles first-pass handoff", any("first-pass" in str(item) and "handoff" in str(item) for item in mechanism_protocol))
    idle_agent_brief = dry_dir / "idle_agent_work_seeking_brief.md"
    idle_agent_brief.write_text(
        "## User message\n\n为什么codex都闲着  不是应该干活的吗  我们不是说了自己没活干的时候要找活干吗\n",
        encoding="utf-8",
    )
    idle_assignment_task = dict(mechanism_task)
    idle_assignment_task["task_id"] = "smoke-idle-agent-work-seeking-assignment"
    idle_assignment_task["brief_path"] = str(idle_agent_brief)
    idle_assignments = resident.collaboration_assignments(idle_assignment_task, ["codex", "claude-code"])
    idle_assignment = idle_assignments.get("codex") or {}
    check(
        "idle agent criticism maps to collaboration mechanism assignment",
        idle_assignment.get("topic") == "collaboration_mechanism"
        and "主动寻找不重复的主线推进点" in str(idle_assignment.get("production_principle") or ""),
    )
    alex_systemization_brief = dry_dir / "alex_systemization_brief.md"
    alex_systemization_brief.write_text(
        "## User message\n\n问题在于你们对于每一个所暴露的问题都要尝试给出系统性的解决方案，这个方案是通过你们讨论决定的。我记得之前已经强调了一些原则了，不知道为什么你们就是记不住。\n",
        encoding="utf-8",
    )
    alex_systemization_task = dict(mechanism_task)
    alex_systemization_task["task_id"] = "smoke-alex-systemic-problem-protocol"
    alex_systemization_task["brief_path"] = str(alex_systemization_brief)
    alex_assignments = resident.collaboration_assignments(alex_systemization_task, ["codex", "claude-code"])
    alex_assignment = alex_assignments.get("codex") or {}
    systemic_protocol = alex_assignment.get("systemic_problem_protocol") or []
    check("Alex systemic correction is classified as collaboration mechanism", alex_assignment.get("topic") == "collaboration_mechanism")
    check("systemic problem protocol requires root-cause classification", any("runner/ledger" in str(item) and "提示协议" in str(item) for item in systemic_protocol))
    check("systemic problem protocol requires first-principles layer selection", any("系统目标、不变量和权限边界" in str(item) for item in systemic_protocol))
    check("systemic problem protocol preserves Alex's prior constraints", any("方向性原则" in str(item) and "设计约束" in str(item) for item in systemic_protocol))
    check("systemic problem protocol rejects local band-aid", any("不能只做局部止血" in str(item) and "不能把下一步执行交回 Alex" in str(item) for item in systemic_protocol))
    check("systemic problem protocol requires verifiable output", any("可验收物" in str(item) for item in systemic_protocol))
    check("systemic problem protocol preserves existing workflow gates", any("既有流程入口" in str(item) and "质量门" in str(item) for item in systemic_protocol))
    misread_execution_brief = dry_dir / "alex_misread_execution_brief.md"
    misread_execution_brief.write_text(
        "## User message\n\n我让你们讨论不就是为了避免某个人理解错误 然后错误的执行吗 你还是落实不了\n",
        encoding="utf-8",
    )
    misread_execution_assignment_task = dict(mechanism_task)
    misread_execution_assignment_task["task_id"] = "smoke-misread-execution-policy"
    misread_execution_assignment_task["brief_path"] = str(misread_execution_brief)
    misread_execution_assignments = resident.collaboration_assignments(misread_execution_assignment_task, ["codex", "claude-code"])
    misread_execution_assignment = misread_execution_assignments.get("codex") or {}
    check("misread/execution correction is classified as collaboration mechanism", misread_execution_assignment.get("topic") == "collaboration_mechanism")
    check("misread/execution correction receives systemic problem protocol", bool(misread_execution_assignment.get("systemic_problem_protocol")))

    review_repair_brief = dry_dir / "review_repair_brief.md"
    review_repair_brief.write_text(
        "## User message\n\n你们互相审查力度要加大，发现问题自动解决，不能等我纠错。\n",
        encoding="utf-8",
    )
    review_repair_task = dict(mechanism_task)
    review_repair_task["task_id"] = "smoke-review-repair-policy"
    review_repair_task["brief_path"] = str(review_repair_brief)
    review_repair_assignments = resident.collaboration_assignments(review_repair_task, ["codex", "claude-code"])
    review_repair_assignment = review_repair_assignments.get("codex") or {}
    review_repair_protocol = review_repair_assignment.get("review_repair_protocol") or []
    check("review repair topic is selected from user request", review_repair_assignment.get("topic") == "review_repair")
    check("review repair protocol requires inspected evidence", any("本地文件" in str(item) or "artifact" in str(item) for item in review_repair_protocol))
    check("review repair protocol avoids Alex as reviewer of last resort", any("不等 Alex" in str(item) for item in review_repair_protocol))
    check("review repair task marker is detected", resident.task_requests_auto_review_repair(review_repair_task))
    report_brief = dry_dir / "parallel_report_brief.md"
    report_brief.write_text("## User message\n\n这是一个大任务，研究报告可以拆成不同部分同时写，最后互相审查、合并、质检。\n", encoding="utf-8")
    report_task = dict(mechanism_task)
    report_task["task_id"] = "smoke-parallel-report-production"
    report_task["brief_path"] = str(report_brief)
    report_assignments = resident.collaboration_assignments(report_task, ["codex", "claude-code"])
    report_assignment = report_assignments.get("codex") or {}
    check("non-code large task does not auto-change existing flows", report_assignment.get("topic") != "parallel_production")
    report_task["parallel_production"] = True
    report_assignments = resident.collaboration_assignments(report_task, ["codex", "claude-code"])
    report_assignment = report_assignments.get("codex") or {}
    check("explicit opt-in non-code task gets parallel production topic", report_assignment.get("topic") == "parallel_production")
    check("explicit opt-in non-code task gets parallel production protocol", bool(report_assignment.get("parallel_production_protocol")))
    check("runner prompt embeds opt-in parallel production boundary", "Use parallel production only when the task explicitly opts" in mainline_prompt and "Do not reinterpret, replace, or modify existing production/task workflows" in mainline_prompt)
    check("telegram ingress embeds opt-in parallel production boundary", "Use parallel production only when a task explicitly opts" in bridge_boundaries and "Do not reinterpret, replace, or modify existing production/task workflows" in bridge_boundaries)

    parent_task = {
        "task_id": "smoke-parent-single-mention",
        "room_id": "openclaw-evolution",
        "requested_by": "telegram-user",
        "target_agents": ["claude-code"],
        "delivery_policy": "targeted_reply",
        "permissions": {"source_edit": True, "global_state_change": True},
        "source": {"transport": "telegram"},
    }
    material_comment = {
        "agent": "claude-code",
        "agent_id": "claude-code",
        "run_id": "smoke-parent-single-mention",
        "title": "runtime patch proposal",
        "body": "我发现 runner 卡住的根因，需要 patch、smoke 验证，并调整协作路由。",
        "blockers": [],
    }
    check("single mention can create material peer follow-up", resident.should_create_collab_followup(parent_task, material_comment, {"claude-code"}))
    check("material peer follow-up can inherit edit permission", resident.peer_followup_can_edit(parent_task, material_comment))
    check("direct targeted peer follow-up may project", resident.peer_followup_may_project_to_telegram(parent_task))

    may_project, projection_mode = resident.telegram_projection_decision(parent_task, [material_comment])
    check("direct targeted telegram reply remains visible", may_project and projection_mode == "normal")

    original_room = resident.ROOM
    try:
        collab_action_room = dry_dir / "collab_action_room"
        shutil.rmtree(collab_action_room, ignore_errors=True)
        resident.ROOM = collab_action_room
        collab_parent = dict(parent_task)
        collab_parent["source"] = {"transport": "telegram", "chat_id": "-1009000000001"}
        collab_parent["collab_tick_enabled"] = True
        collab_parent["collab_tick_max_rounds"] = 2
        collab_parent["collaboration"] = {
            "status": "open",
            "participants": ["claude-code", "codex"],
            "work_items": [
                {"id": "room_response_claude-code", "status": "completed", "assigned_to": "claude-code"},
                {"id": "room_response_codex", "status": "open", "assigned_to": "codex"},
            ],
            "claims": [],
            "artifacts": [],
            "blockers": [],
            "handoffs": [],
            "points": [],
            "uptakes": [],
        }
        resident.write_json(
            collab_action_room / "tasks" / str(collab_parent.get("task_id")) / "manifest.json",
            collab_parent,
        )
        first_followup = resident.create_collab_followup_task(collab_parent, material_comment)
        first_manifest = resident.read_json(
            collab_action_room / "tasks" / str((first_followup or {}).get("task_id") or "") / "manifest.json",
            {},
        )
        parent_ledger = resident.read_json(resident.collaboration_ledger_state_path(str(collab_parent.get("task_id"))), {})
        source_points = parent_ledger.get("points") if isinstance(parent_ledger.get("points"), list) else []
        first_brief = Path(str(first_manifest.get("brief_path") or "")).read_text(encoding="utf-8") if first_manifest.get("brief_path") else ""
        check(
            "peer follow-up manifest declares collaboration action",
            bool(first_followup)
            and first_manifest.get("collab_intent") == "delegate_work"
            and (first_manifest.get("collaboration_action") or {}).get("action") == "delegate_work"
            and "patch" in ((first_manifest.get("collaboration_action") or {}).get("expected_outputs") or []),
        )
        check(
            "peer follow-up collaboration participants include source and target",
            set(((first_manifest.get("collaboration") or {}).get("participants") or [])) == {"claude-code", "codex"},
        )
        check(
            "peer follow-up brief declares expected output",
            "## Collaboration contract" in first_brief and "expected_output" in first_brief and "patch" in first_brief,
        )
        check(
            "peer follow-up creation records source comment as parent point",
            len(source_points) == 1
            and source_points[0].get("agent_id") == "claude-code"
            and source_points[0].get("id") == first_manifest.get("collab_source_point_id")
            and (first_manifest.get("collaboration_action") or {}).get("source_point_id") == source_points[0].get("id"),
        )
        disabled_internal_parent = dict(first_manifest)
        disabled_internal_parent.pop("collab_tick_enabled", None)
        disabled_internal_parent.pop("collab_tick_max_rounds", None)
        disabled_second = resident.create_collab_followup_task(disabled_internal_parent, {
            "agent_id": "codex",
            "run_id": "smoke-disabled-second",
            "title": "second round blocked by default",
            "body": "我补了 smoke 验证，但默认不应该继续自动协作 tick。",
            "blockers": [],
        })
        check("internal peer follow-up second round is opt-in", disabled_second is None)
        second_followup = resident.create_collab_followup_task(first_manifest, {
            "agent_id": "codex",
            "run_id": str(first_manifest.get("run_id") or ""),
            "title": "second round evidence",
            "body": "我补了 smoke 验证，但还需要 peer 检查 runner 边界和 blocker。",
            "blockers": [],
        })
        check(
            "collab tick creates bounded second-round follow-up when enabled",
            bool(second_followup)
            and second_followup.get("collab_round") == 2
            and second_followup.get("target_agents") == ["claude-code"],
        )
        third_followup = resident.create_collab_followup_task(second_followup or {}, {
            "agent_id": "claude-code",
            "run_id": str((second_followup or {}).get("run_id") or ""),
            "title": "third round should stop",
            "body": "继续补 patch 和 smoke。",
            "blockers": [],
        })
        check("collab tick stops at max rounds", third_followup is None)
    finally:
        resident.ROOM = original_room

    broadcast_task = dict(parent_task)
    broadcast_task["task_id"] = "smoke-parent-broadcast"
    broadcast_task["target_agents"] = ["codex", "claude-code"]
    broadcast_task["delivery_policy"] = "broadcast_all_agents_decide"
    broadcast_task["broadcast_targets"] = ["codex", "claude-code"]
    check("broadcast peer follow-up stays local", not resident.peer_followup_may_project_to_telegram(broadcast_task))
    visible_broadcast_followup_task = dict(broadcast_task)
    visible_broadcast_followup_task["broadcast_peer_followup_visible"] = True
    check("broadcast peer follow-up visibility requires explicit opt-in", resident.peer_followup_may_project_to_telegram(visible_broadcast_followup_task))
    check("broadcast material collaboration can target peer follow-up", resident.peer_followup_targets(broadcast_task, material_comment, {"codex", "claude-code"}) == ["codex"])
    check("broadcast material collaboration creates bounded peer follow-up", resident.should_create_collab_followup(broadcast_task, material_comment, {"codex", "claude-code"}))

    may_project, projection_mode = resident.telegram_projection_decision(broadcast_task, [material_comment])
    check("broadcast telegram peer replies may project when material", may_project and projection_mode == "normal")

    duplicate_filter_task = dict(broadcast_task)
    duplicate_filter_task["problem_statement"] = "减少重复讨论"
    duplicate_filter_task["collaboration"] = {"acceptance": "non-duplicative peer contribution required"}
    structured_delta_comment = {
        "agent_id": "codex",
        "run_id": "smoke-structured-delta",
        "kind": "status",
        "title": "structured delta without label words",
        "body": "我核对了现有投影门，补充一个不同的验收事实。",
        "blockers": [],
        "collaboration_assignment": {"turn_position": "co_producer"},
        "evidence": {"inspected": True, "result": "schema-backed contribution"},
    }
    check("structured evidence makes co-producer delta concrete", resident.comment_has_concrete_visible_delta(structured_delta_comment))
    may_project, projection_mode = resident.telegram_projection_decision(duplicate_filter_task, [structured_delta_comment])
    check("structured co-producer delta is not suppressed by label absence", may_project and projection_mode == "normal")

    label_only_comment = dict(structured_delta_comment)
    label_only_comment.pop("evidence", None)
    label_only_comment["title"] = "label-only contribution"
    label_only_comment["body"] = "Patch: Smoke: Artifact: Blocker: 我后面再整理。"
    check("label-only contribution is not a concrete co-producer delta", not resident.comment_has_concrete_visible_delta(label_only_comment))
    may_project, suppress_reason = resident.telegram_projection_decision(duplicate_filter_task, [label_only_comment])
    check("label-only co-producer contribution is suppressed", not may_project and suppress_reason == "coproducer_no_concrete_delta")

    extension_only_comment = dict(structured_delta_comment)
    extension_only_comment.pop("evidence", None)
    extension_only_comment["body"] = "Patch: .py / Artifact: .json / Smoke: 稍后。"
    check("extension-only fallback is not a concrete co-producer delta", not resident.comment_has_concrete_visible_delta(extension_only_comment))

    placeholder_structured_comment = dict(structured_delta_comment)
    placeholder_structured_comment.pop("evidence", None)
    placeholder_structured_comment["patches"] = ["无"]
    placeholder_structured_comment["artifacts"] = [{"path": ""}]
    placeholder_structured_comment["smoke_results"] = ["todo"]
    check("placeholder structured fields are not concrete", not resident.comment_has_concrete_visible_delta(placeholder_structured_comment))

    line_referenced_file_comment = dict(structured_delta_comment)
    line_referenced_file_comment.pop("evidence", None)
    line_referenced_file_comment["body"] = "已核 `agent_room_resident_bridge.py:3100`，兜底仍保留可检查的文件行号锚点。"
    check("line-referenced file fallback remains concrete", resident.comment_has_concrete_visible_delta(line_referenced_file_comment))

    failed_verification_comment = dict(structured_delta_comment)
    failed_verification_comment.pop("evidence", None)
    failed_verification_comment["verification"] = {"ok": False, "reason": "regression_detected"}
    check("structured failed verification is still concrete", resident.comment_has_concrete_visible_delta(failed_verification_comment))

    path_only_comment = {
        "agent_id": "codex",
        "run_id": "smoke-path-marker-fallback",
        "kind": "status",
        "title": "path-only contribution without structured fields",
        "body": "检查了文件 agent_room_resident_bridge.py:3100",
        "blockers": [],
    }
    check("path marker fallback passes without structured fields", resident.comment_has_concrete_visible_delta(path_only_comment))
    may_project, projection_mode = resident.telegram_projection_decision(duplicate_filter_task, [path_only_comment])
    check("path-only co-producer delta may project", may_project and projection_mode == "normal")

    review_parent = dict(broadcast_task)
    review_parent["task_id"] = "smoke-parent-review-repair-broadcast"
    review_parent["brief_path"] = str(review_repair_brief)
    review_comment = dict(material_comment)
    review_comment["agent_id"] = "codex"
    review_comment["body"] = "我核查了本地文件并给出修正，发现前一条存在事实错误，需要自动解决。"
    check("review repair expands targets even when both peers were initial targets", resident.peer_followup_targets(review_parent, review_comment, {"codex", "claude-code"}) == ["claude-code"])
    ordinary_broadcast = dict(broadcast_task)
    ordinary_broadcast_brief = dry_dir / "ordinary_broadcast_brief.md"
    ordinary_broadcast_brief.write_text("## User message\n\n请各自补充一个状态观察。\n", encoding="utf-8")
    ordinary_broadcast["brief_path"] = str(ordinary_broadcast_brief)
    ordinary_comment = dict(review_comment)
    ordinary_comment["title"] = "status observation"
    ordinary_comment["body"] = "我补充一个本地观察，当前没有新增事项。"
    check("ordinary broadcast does not create peer follow-up", not resident.should_create_collab_followup(ordinary_broadcast, ordinary_comment, {"codex", "claude-code"}))

    internal_followup = {
        "task_id": "smoke-internal-followup",
        "requested_by": "agent-room-collab-followup",
        "delivery_policy": "targeted_reply",
        "source": {"transport": "agent-room-collab-followup"},
        "peer_followup_visible_allowed": False,
    }
    may_project, suppress_reason = resident.telegram_projection_decision(internal_followup, [material_comment])
    check("internal peer follow-up without explicit visibility is suppressed", not may_project and suppress_reason == "peer_followup_projection_not_explicit")

    internal_followup["peer_followup_visible_allowed"] = True
    may_project, projection_mode = resident.telegram_projection_decision(internal_followup, [material_comment])
    check("explicit direct peer follow-up uses internal summary", may_project and projection_mode == "internal-summary")

    diagnostic_quote_comment = dict(material_comment)
    diagnostic_quote_comment["title"] = "Claude diagnostic with quoted runner failure"
    diagnostic_quote_comment["body"] = "我查到上一轮 codex runner_timeout，日志里写了没有形成可发布正文；这只是被引用的证据，不是本轮 Claude 输出失败。"
    check("diagnostic quoting runner failure phrase remains material", resident.is_material_peer_comment(diagnostic_quote_comment))
    may_project, projection_mode = resident.telegram_projection_decision(parent_task, [diagnostic_quote_comment])
    check("diagnostic quoting runner failure phrase may project", may_project and projection_mode == "normal")

    direct_mention_failure_task = {
        "task_id": "smoke-direct-mention-claude-runner-failure",
        "requested_by": "telegram-user",
        "delivery_policy": "targeted_reply",
        "target_agents": ["claude-code"],
        "source": {"transport": "telegram", "update_id": "group-message:-1009000000001:1138"},
    }
    suppressed_direct_runner_failure = {
        "agent_id": "claude-code",
        "run_id": "smoke-direct-mention-claude-runner-failure",
        "title": "claude-code runner did not produce a publishable reply",
        "body": "claude-code 本轮 runner 进程已经不存在，且没有留下可发布正文。已转为 blocker，避免把内部状态或乱码发到群里。",
        "blockers": ["runner_process_missing"],
        "telegram_projection_status": "suppressed_runner_failure",
    }
    may_project, projection_mode = resident.telegram_projection_decision(direct_mention_failure_task, [suppressed_direct_runner_failure])
    check("direct @ Claude runner failure is suppressed by default", not may_project and projection_mode == "runner_lifecycle_failure_local_only")
    visible_runner_failure = dict(suppressed_direct_runner_failure, telegram_projection_status="user_visible_runner_failure")
    check("explicit Claude runner failure remains material only when explicitly visible", resident.is_material_peer_comment(visible_runner_failure))
    explicit_visible_failure_task = dict(direct_mention_failure_task)
    explicit_visible_failure_task["visible_runner_failure_allowed"] = True
    explicit_comments, explicit_promoted = resident.promote_runner_failures_for_visible_silence(
        explicit_visible_failure_task,
        [dict(suppressed_direct_runner_failure, run_id="smoke-direct-explicit-runner-failure")],
    )
    check(
        "explicit runtime-status task may promote runner failure",
        bool(explicit_promoted) and explicit_comments[0].get("telegram_projection_status") == "user_visible_runner_failure",
    )
    ordinary_telegram_failure_task = {
        "task_id": "smoke-ordinary-telegram-runner-failure",
        "requested_by": "telegram-user",
        "delivery_policy": "broadcast_all_agents_decide",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram", "update_id": "group-message:-1009000000001:1200"},
    }
    suppressed_runner_failure = {
        "agent_id": "codex",
        "run_id": "smoke-ordinary-telegram-runner-failure",
        "title": "codex runner did not produce a publishable reply",
        "body": "codex 本轮 runner 超时，没有形成可发布正文。已转为 blocker。",
        "blockers": ["runner_timeout"],
        "telegram_projection_status": "suppressed_runner_failure",
    }
    check(
        "ordinary Telegram runner failure stays local by default",
        not resident.runner_failure_should_be_user_visible(ordinary_telegram_failure_task),
    )
    promoted_comments, promoted_failures = resident.promote_runner_failures_for_visible_silence(
        ordinary_telegram_failure_task,
        [suppressed_runner_failure],
    )
    check(
        "ordinary Telegram runner failure is not promoted into agent speech",
        not promoted_failures
        and promoted_comments[0].get("telegram_projection_status") == "suppressed_runner_failure",
    )
    may_project, projection_mode = resident.telegram_projection_decision(ordinary_telegram_failure_task, promoted_comments)
    check("ordinary Telegram runner failure remains local-only", not may_project and projection_mode == "runner_lifecycle_failure_local_only")

    original_room = resident.ROOM
    try:
        silent_failure_room = dry_dir / "silent_failure_handoff_room"
        shutil.rmtree(silent_failure_room, ignore_errors=True)
        resident.ROOM = silent_failure_room
        silent_failure_run_id = f"smoke-group-targeted-silent-failure-{uuid.uuid4().hex[:8]}"
        group_targeted_failure_task = dict(direct_mention_failure_task)
        group_targeted_failure_task.update({
            "room_id": "openclaw-evolution",
            "run_id": silent_failure_run_id,
            "task_id": silent_failure_run_id,
            "first_response_owner": "claude-code",
            "source": {"transport": "telegram", "chat_id": "-1009000000001"},
        })
        check(
            "group targeted mention allows silent-failure handoff projection",
            resident.group_targeted_task_allows_silent_failure_projection(group_targeted_failure_task),
        )
        dm_targeted_failure_task = dict(group_targeted_failure_task)
        dm_targeted_failure_task["room_id"] = "dm-claude-code-123"
        dm_targeted_failure_task["source"] = {"transport": "telegram", "chat_id": "100000001"}
        check(
            "direct DM targeted runner failure does not use collaboration silent-failure projection",
            not resident.group_targeted_task_allows_silent_failure_projection(dm_targeted_failure_task),
        )
        broadcast_failure_task = dict(group_targeted_failure_task)
        broadcast_failure_task["delivery_policy"] = "broadcast_all_agents_decide"
        broadcast_failure_task["target_agents"] = ["codex", "claude-code"]
        check(
            "broadcast/no-first-owner runner failure does not use targeted silent-failure projection",
            not resident.group_targeted_task_allows_silent_failure_projection(broadcast_failure_task),
        )
        diagnostic = resident.runner_silent_failure_diagnostic(group_targeted_failure_task, "claude-code")
        check("missing targeted owner runner is diagnosed without log contents", (diagnostic or {}).get("reason") == "runner_missing")
        projection = resident.maybe_emit_silent_failure_handoff_projection(
            group_targeted_failure_task,
            "claude-code",
            "codex",
            allow_send=False,
        )
        status_run_id = resident.silent_failure_projection_run_id(
            silent_failure_run_id,
            "claude-code",
            "codex",
        )
        check(
            "silent-failure handoff projection records a separate status artifact",
            bool(projection)
            and projection.get("status_run_id") == status_run_id
            and resident.reply_artifact_exists("codex", status_run_id)
            and not resident.reply_artifact_exists("codex", silent_failure_run_id),
        )
        room_messages = resident.read_jsonl(silent_failure_room / "rooms" / "openclaw-evolution" / "messages.jsonl")
        check(
            "silent-failure handoff writes a visible room status message",
            any(
                row.get("source") == "silent_failure_handoff"
                and "degraded-quorum" in str(row.get("text") or "")
                for row in room_messages
            ),
        )
        duplicate_projection = resident.maybe_emit_silent_failure_handoff_projection(
            group_targeted_failure_task,
            "claude-code",
            "codex",
            allow_send=False,
        )
        check("silent-failure handoff projection is idempotent", duplicate_projection is None)
    finally:
        resident.ROOM = original_room

    check(
        "runtime takeover from user Telegram may return visible semantic recovery",
        resident.runtime_takeover_reply_visible_allowed(direct_mention_failure_task),
    )
    takeover_visible_task = {
        "task_id": "smoke-runtime-takeover-visible-semantic-result",
        "requested_by": "agent-room-runtime-takeover",
        "delivery_policy": "targeted_reply",
        "target_agents": ["codex"],
        "runtime_takeover_visible_allowed": True,
        "source": {"transport": "agent-room-runtime-takeover"},
    }
    may_project, projection_mode = resident.telegram_projection_decision(takeover_visible_task, [material_comment])
    check("runtime takeover semantic recovery may project as internal summary", may_project and projection_mode == "internal-summary")

    internal_runner_failure_task = {
        "task_id": "smoke-internal-runner-failure-local-only",
        "requested_by": "agent-room-collab-followup",
        "delivery_policy": "targeted_reply",
        "target_agents": ["codex"],
        "source": {"transport": "agent-room-collab-followup"},
    }
    internal_comments, internal_promoted = resident.promote_runner_failures_for_visible_silence(
        internal_runner_failure_task,
        [suppressed_runner_failure],
    )
    check(
        "runtime takeover from internal task stays local",
        not resident.runtime_takeover_reply_visible_allowed(internal_runner_failure_task),
    )
    check(
        "internal runner failure is not promoted by Telegram liveness contract",
        not resident.runner_failure_should_be_user_visible(internal_runner_failure_task)
        and not internal_promoted
        and internal_comments[0].get("telegram_projection_status") == "suppressed_runner_failure",
    )
    visible_quota_notice = {
        "agent_id": "claude-code",
        "run_id": "smoke-visible-quota-notice",
        "title": "claude-code model quota depleted",
        "body": "claude-code 当前使用的模型 `minimax-m2.7` 额度已耗尽。这条是该机器人/模型本轮耗尽后的唯一提示。",
        "blockers": ["agent_model_quota_depleted", "rate_limit"],
        "telegram_projection_status": "user_visible_quota_exhausted",
    }
    check("first quota notice remains material", resident.is_material_peer_comment(visible_quota_notice))
    may_project, projection_mode = resident.telegram_projection_decision(parent_task, [visible_quota_notice])
    check("first quota notice may project visibly", may_project and projection_mode == "normal")
    check(
        "first quota notice is not suppressed as internal plumbing",
        not telegram_agent_reply.is_internal_runner_failure_comment(
            visible_quota_notice,
            str(visible_quota_notice["title"]),
            str(visible_quota_notice["body"]),
            visible_quota_notice["blockers"],
        ),
    )
    local_quota_silence = dict(visible_quota_notice)
    local_quota_silence["telegram_projection_status"] = "local_only_quota_silenced"
    local_quota_silence["body"] = "claude-code/minimax-m2.7 仍处于额度耗尽静默期，用户提示已在首次耗尽时发送。"
    check("repeated quota silence is not material", not resident.is_material_peer_comment(local_quota_silence))
    may_project, suppress_reason = resident.telegram_projection_decision(parent_task, [local_quota_silence])
    check("repeated quota silence is suppressed with quota reason", not may_project and suppress_reason == "quota_silenced_already_notified")
    check(
        "repeated quota silence is suppressed by reply gate",
        telegram_agent_reply.is_internal_runner_failure_comment(
            local_quota_silence,
            str(local_quota_silence["title"]),
            str(local_quota_silence["body"]),
            local_quota_silence["blockers"],
        ),
    )

    class BrokenPattern:
        def sub(self, *_args, **_kwargs):
            raise NameError("simulated missing projection regex")

    original_single_star = telegram_agent_reply.PAIRED_SINGLE_STAR_RE
    telegram_agent_reply.PAIRED_SINGLE_STAR_RE = BrokenPattern()
    try:
        projection = telegram_agent_reply.build_telegram_projection("**Claude Code** 本轮需要可见失败，而不是沉默。")
    finally:
        telegram_agent_reply.PAIRED_SINGLE_STAR_RE = original_single_star
    check("Telegram projection crash falls back to plain text", projection.get("parse_mode") is None)
    check("Telegram projection crash records root cause", (projection.get("projection_error") or {}).get("type") == "NameError")
    check("Telegram projection crash still keeps visible text", "Claude Code" in projection.get("text", ""))

    failed_reply_result = {
        "ok": False,
        "stdout": '{"would_send": true, "sent": false, "telegram_error": {"type": "Forbidden"}}',
        "stderr": "",
    }
    check("reply stdout JSON is parsed for delivery accounting", (resident.reply_result_payload(failed_reply_result).get("telegram_error") or {}).get("type") == "Forbidden")
    failed_delivery_state = resident.classify_reply_delivery_state(failed_reply_result)
    check("failed Telegram send is classified as visible response layer failure", failed_delivery_state == "telegram_send_failed")
    check("failed reply result is not treated as healthy pending state", resident.reply_delivery_failed(failed_reply_result, failed_delivery_state))
    nested_sent_reply_result = {
        "agent_id": "claude-code",
        "ok": True,
        "result": {
            "ok": True,
            "stdout": '{"would_send": true, "sent": true, "telegram_message_id": 1830}',
            "stderr": "",
        },
    }
    check("nested reply stdout JSON is parsed for sent delivery accounting", resident.classify_reply_delivery_state(nested_sent_reply_result) == "sent")
    check(
        "failed attempted send still routes bot mentions",
        telegram_agent_reply.should_route_bot_mentions_after_reply(
            {"would_send": True, "sent": False},
            allow_send=True,
            projection_mode="normal",
        ),
    )
    check(
        "dry-run no-send does not route bot mentions",
        not telegram_agent_reply.should_route_bot_mentions_after_reply(
            {"would_send": True, "sent": False},
            allow_send=False,
            projection_mode="normal",
        ),
    )
    check(
        "duplicate-suppressed reply can still repair bot mention routing",
        telegram_agent_reply.should_route_bot_mentions_after_reply(
            {"would_send": False, "sent": False},
            allow_send=True,
            projection_mode="normal",
            has_routeable_mentions=True,
            duplicate_suppressed=True,
        ),
    )
    targets, trigger = telegram_agent_reply.bot_mention_targets_for_text(
        "@lchopenclaw_bot 请接一下这个协作链路",
        "codex",
        "openclaw-evolution",
    )
    check("bot mention targets are computed before Telegram projection", targets == ["claude-code"] and trigger)
    check(
        "projection/truncation can hide a source mention but routing keeps intent",
        telegram_agent_reply.source_visible_mention_status(
            "前文 @lchopenclaw_bot 需要处理",
            "前文 ...[truncated]",
        ) == "mentions_not_visible_after_projection_or_truncation",
    )
    bot_mention_room = dry_dir / f"bot_mention_failed_{datetime.now().strftime('%H%M%S%f')}" / "agent-room"
    original_reply_paths = (
        telegram_agent_reply.ROOM,
        telegram_agent_reply.ROOM_BINDINGS,
        telegram_agent_reply.ROOT_BINDINGS,
    )
    try:
        telegram_agent_reply.ROOM = bot_mention_room
        telegram_agent_reply.ROOM_BINDINGS = bot_mention_room / "telegram-room-bindings.json"
        telegram_agent_reply.ROOT_BINDINGS = bot_mention_room.parent / "telegram-room-bindings.json"
        failed_mention_tasks = telegram_agent_reply.create_bot_mention_tasks(
            {"room_id": "openclaw-evolution"},
            "codex",
            "-1009000000001",
            "smoke-failed-bot-mention",
            None,
            "@lchopenclaw_bot 请看这个失败后仍需协作的问题",
            "failed",
            "projected_truncated",
            "mentions_not_visible_after_projection_or_truncation",
            "...[truncated]",
        )
    finally:
        (
            telegram_agent_reply.ROOM,
            telegram_agent_reply.ROOM_BINDINGS,
            telegram_agent_reply.ROOT_BINDINGS,
        ) = original_reply_paths
    failed_mention_task = failed_mention_tasks[0] if failed_mention_tasks else {}
    check(
        "failed bot mention task records source send status",
        (failed_mention_task.get("source") or {}).get("source_send_status") == "failed"
        and failed_mention_task.get("target_agents") == ["claude-code"],
    )
    check(
        "failed bot mention task records projection and visible mention status",
        (failed_mention_task.get("source") or {}).get("source_projection_status") == "projected_truncated"
        and (failed_mention_task.get("source") or {}).get("source_visible_mention_status") == "mentions_not_visible_after_projection_or_truncation",
    )

    collab_scan_room = dry_dir / f"collab_pending_scan_{datetime.now().strftime('%H%M%S%f')}" / "agent-room"
    saved_resident_globals = {
        "ROOM": resident.ROOM,
        "ACTIVE_RUNNERS": resident.ACTIVE_RUNNERS,
        "FINISHED_RUNNERS": resident.FINISHED_RUNNERS,
    }
    try:
        shutil.rmtree(collab_scan_room.parent, ignore_errors=True)
        resident.ROOM = collab_scan_room
        resident.ACTIVE_RUNNERS = collab_scan_room / "active-runners"
        resident.FINISHED_RUNNERS = collab_scan_room / "finished-runners"
        created_now = datetime.now(timezone.utc).astimezone()
        hidden_followup = {
            "task_id": "smoke-hidden-collab-followup",
            "run_id": "smoke-hidden-collab-followup",
            "room_id": "openclaw-evolution",
            "requested_by": "agent-room-collab-followup",
            "target_agents": ["claude-code"],
            "status": "queued",
            "created_at": created_now.isoformat(timespec="seconds"),
            "updated_at": created_now.isoformat(timespec="seconds"),
            "lease": {"owner": None},
            "peer_followup_visible_allowed": False,
            "source": {
                "transport": "agent-room-collab-followup",
                "chat_id": "-1009000000001",
            },
        }
        hidden_manifest = collab_scan_room / "tasks" / hidden_followup["task_id"] / "manifest.json"
        resident.write_json(hidden_manifest, hidden_followup)
        resident.append_jsonl(collab_scan_room / "tasks.jsonl", [hidden_followup])
        for idx in range(60):
            resident.append_jsonl(collab_scan_room / "tasks.jsonl", [{
                "task_id": f"smoke-newer-telegram-{idx}",
                "run_id": f"smoke-newer-telegram-{idx}",
                "status": "queued",
                "created_at": created_now.isoformat(timespec="seconds"),
                "source": {"transport": "telegram", "chat_id": "-1009000000001"},
                "target_agents": ["codex"],
            }])
        selected_collab = resident.recent_pending_task_paths(
            source_transports={"agent-room-collab-followup"},
            scan_rows=50,
            include_manifest_scan=True,
        )
        check("dedicated collab lane scans manifests beyond recent task rows", hidden_manifest in selected_collab)
        retry_due = (created_now - timedelta(seconds=5)).isoformat(timespec="seconds")
        retry_future = (created_now + timedelta(minutes=30)).isoformat(timespec="seconds")
        retryable_due = {
            "task_id": "smoke-retryable-due",
            "run_id": "smoke-retryable-due",
            "room_id": "openclaw-evolution",
            "target_agents": ["codex"],
            "status": "retryable",
            "retry_after": retry_due,
            "created_at": created_now.isoformat(timespec="seconds"),
            "updated_at": created_now.isoformat(timespec="seconds"),
            "runner_summary": {
                "retryable_agents": ["codex"],
                "retry_after": retry_due,
            },
            "source": {"transport": "telegram", "chat_id": "-1009000000001"},
        }
        retryable_future = {
            **retryable_due,
            "task_id": "smoke-retryable-future",
            "run_id": "smoke-retryable-future",
            "retry_after": retry_future,
            "runner_summary": {
                "retryable_agents": ["codex"],
                "retry_after": retry_future,
            },
        }
        retry_due_manifest = collab_scan_room / "tasks" / retryable_due["task_id"] / "manifest.json"
        retry_future_manifest = collab_scan_room / "tasks" / retryable_future["task_id"] / "manifest.json"
        resident.write_json(retry_due_manifest, retryable_due)
        resident.write_json(retry_future_manifest, retryable_future)
        resident.append_jsonl(collab_scan_room / "tasks.jsonl", [retryable_due, retryable_future])
        resident.write_json(
            collab_scan_room / "telegram-agent-reply" / f"codex-{retryable_due['run_id']}.json",
            {
                "schema": "openclaw.agent_room.telegram_agent_reply.v0",
                "agent_id": "codex",
                "run_id": retryable_due["run_id"],
                "sent": True,
                "suppressed_reason": None,
            },
        )
        selected_retryable = resident.recent_pending_task_paths(
            source_transports={"telegram"},
            scan_rows=0,
            include_manifest_scan=True,
        )
        check(
            "retryable quota task is dispatchable after cooldown despite prior quota reply artifact",
            retry_due_manifest in selected_retryable
            and retry_future_manifest not in selected_retryable,
        )
        old_botmention = {
            **hidden_followup,
            "task_id": "smoke-stale-botmention",
            "run_id": "smoke-stale-botmention",
            "requested_by": "agent-room-bot-mention",
            "created_at": (created_now - timedelta(days=2)).isoformat(timespec="seconds"),
            "updated_at": (created_now - timedelta(days=2)).isoformat(timespec="seconds"),
            "peer_followup_visible_allowed": None,
            "source": {
                "transport": "agent-room-bot-mention",
                "chat_id": "-1009000000001",
            },
        }
        old_manifest = collab_scan_room / "tasks" / old_botmention["task_id"] / "manifest.json"
        resident.write_json(old_manifest, old_botmention)
        marked_stale = resident.mark_stale_internal_followup_tasks(3600, limit=10)
        old_after = resident.read_json(old_manifest, {})
        check(
            "old botmention queue entries are marked stale",
            old_after.get("status") == "stale"
            and old_after.get("blocked_reason") == "internal_followup_queue_stale"
            and any(item.get("task_id") == "smoke-stale-botmention" for item in marked_stale),
        )
    finally:
        for name, value in saved_resident_globals.items():
            setattr(resident, name, value)

    budget_task = {
        "task_id": "smoke-task-budget-v0",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram"},
        "delivery_policy": "targeted_reply",
        "user_message": "请修复这个 agent room 脚本问题",
    }
    task_budget = resident.build_task_budget(budget_task)
    check("TaskBudget V0 classifies implementation tasks", task_budget.get("interaction_class") == "implementation")
    mechanism_budget = resident.build_task_budget({
        "task_id": "smoke-collaboration-mechanism-budget",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram"},
        "user_message": "每个人都应该干活，如果当前任务没他什么事，就主动探索怎么推进主线和协作机制",
    })
    check("TaskBudget V0 gives collaboration-mechanism turns design budget", mechanism_budget.get("interaction_class") == "design_discussion")
    check("TaskBudget V0 gives collaboration-mechanism turns a longer hard budget", int(mechanism_budget.get("hard_seconds") or 0) >= 1200)
    runner_limit_question_budget = resident.build_task_budget({
        "task_id": "smoke-runner-limit-question",
        "target_agents": ["codex", "claude-code"],
        "source": {"transport": "telegram"},
        "delivery_policy": "broadcast_all_agents_decide",
        "user_message": "是不是这个runners上限可以更多一些呢",
    })
    check("runner limit questions use runtime design budget", runner_limit_question_budget.get("interaction_class") == "design_discussion")
    check("runtime design runner hard budget is 30 minutes", int(runner_limit_question_budget.get("hard_seconds") or 0) >= 1800)
    codex_budget = resident.runner_budget_for_agent(task_budget, "codex")
    check("TaskBudget V0 keeps Codex outer hard budget above inner CLI timeout", int(codex_budget.get("hard_seconds") or 0) >= 720)
    now = datetime.now(timezone.utc).astimezone()
    soft_only_record = {
        "agent_id": "codex",
        "pid": os.getpid(),
        "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
        "soft_deadline_at": (now - timedelta(seconds=10)).isoformat(timespec="seconds"),
        "hard_deadline_at": (now + timedelta(seconds=600)).isoformat(timespec="seconds"),
        "runner_budget": codex_budget,
    }
    check("TaskBudget V0 soft deadline does not kill active runner", not resident.active_runner_stale(soft_only_record))
    check("TaskBudget V0 soft deadline is visible in deadline state", resident.classify_runner_deadline_state(soft_only_record) == "soft_deadline_exceeded")
    hard_expired_record = dict(soft_only_record)
    hard_expired_record["started_at"] = (now - timedelta(seconds=int(codex_budget.get("hard_seconds") or 720) + 5)).isoformat(timespec="seconds")
    hard_expired_record["hard_deadline_at"] = (now - timedelta(seconds=5)).isoformat(timespec="seconds")
    check("TaskBudget V0 hard deadline marks runner stale", resident.active_runner_stale(hard_expired_record))
    check("TaskBudget V0 hard deadline is classified", resident.classify_runner_deadline_state(hard_expired_record) == "hard_deadline_exceeded")
    old_single_owner_task = {
        "task_id": "smoke-soft-handoff",
        "run_id": "smoke-soft-handoff",
        "target_agents": ["claude-code"],
        "first_response_owner": "claude-code",
        "room_id": "openclaw-evolution",
        "created_at": (now - timedelta(seconds=400)).isoformat(timespec="seconds"),
    }
    check("first-response owner soft deadline hands off to another local peer", resident.soft_deadline_handoff_targets(old_single_owner_task, ["claude-code"]) == ["codex"])
    private_dm_old_single_owner_task = dict(old_single_owner_task)
    private_dm_old_single_owner_task["run_id"] = "smoke-soft-handoff-private-dm"
    private_dm_old_single_owner_task["room_id"] = "dm-claude-code-100000001"
    private_dm_old_single_owner_task["source"] = {"transport": "telegram", "chat_id": "100000001"}
    check("private DM soft deadline does not hand off to another bot", resident.soft_deadline_handoff_targets(private_dm_old_single_owner_task, ["claude-code"]) == [])
    check("private DM runtime takeover recovery stays local", not resident.runtime_takeover_reply_visible_allowed(private_dm_old_single_owner_task))
    fresh_single_owner_task = dict(old_single_owner_task)
    fresh_single_owner_task["run_id"] = "smoke-soft-handoff-fresh"
    fresh_single_owner_task["created_at"] = now.isoformat(timespec="seconds")
    check("first-response owner does not hand off before soft deadline", resident.soft_deadline_handoff_targets(fresh_single_owner_task, ["claude-code"]) == [])
    multi_target_budget = resident.build_task_budget({"task_id": "smoke-multi-owner", "target_agents": ["codex", "claude-code"], "created_at": now.isoformat(timespec="seconds")})
    check("multi-target turns do not invent a first-response owner", not multi_target_budget.get("first_response_owner"))
    resident_main_source = inspect.getsource(resident.main)
    resident_source = Path(resident.__file__).read_text(encoding="utf-8")
    check("Agent Room dispatch uses a global active-runner cap", "deferred_global_active_runner_limit" in resident_source)
    check("per-agent active-runner cap is explicit opt-in", "AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT" in resident_source)
    check("observer lane no longer depends on len(targets) == 1", "len(targets) == 1 and is_group_context and primary_comments" not in resident_source)
    check("already-recorded agent replies are not re-run", "reply_already_recorded" in resident_source)
    check("Claude Ark wrapper has one active definition", inspect.getsource(agent_task_runner).count("def run_claude_code_ark(") == 1)
    check(
        "per-agent active-runner cap defaults off",
        'AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT", "0"' in resident_source,
    )
    check(
        "global active-runner cap leaves room for multi-agent fanout",
        'runner_admission_limit(task)' in resident_source
        and resident.DEFAULT_GLOBAL_ACTIVE_RUNNER_LIMIT >= 10
        and resident.DEFAULT_USER_MAIN_RESERVED_RUNNER_SLOTS >= 1,
    )
    old_global_runner_limit = os.environ.pop("AGENT_ROOM_GLOBAL_ACTIVE_RUNNER_LIMIT", None)
    old_user_main_reserved_slots = os.environ.pop("AGENT_ROOM_USER_MAIN_RESERVED_RUNNER_SLOTS", None)
    old_fresh_user_reserved_slots = os.environ.pop("AGENT_ROOM_FRESH_USER_RESERVED_RUNNER_SLOTS", None)
    old_new_task_limit = os.environ.pop("AGENT_ROOM_NEW_TASK_LIMIT_PER_TICK", None)
    old_acceleration_policy = os.environ.pop("AGENT_ROOM_ACCELERATION_POLICY", None)
    try:
        generic_backlog_task = {
            "task_id": "smoke-generic-backlog",
            "created_at": now.isoformat(timespec="seconds"),
            "source": {"transport": "agent-room-standing-mainline"},
        }
        fresh_telegram_task = {
            "task_id": "smoke-fresh-telegram",
            "created_at": now.isoformat(timespec="seconds"),
            "source": {"transport": "telegram"},
        }
        openclaw_main_task = {
            "task_id": "smoke-main-control",
            "created_at": now.isoformat(timespec="seconds"),
            "requested_by": "openclaw-main",
            "source": {"transport": "agent-room-bot-mention"},
        }
        old_telegram_task = {
            "task_id": "smoke-old-telegram",
            "created_at": (now - timedelta(seconds=1200)).isoformat(timespec="seconds"),
            "source": {"transport": "telegram"},
        }
        generic_limit = resident.runner_admission_limit(generic_backlog_task)
        fresh_limit = resident.runner_admission_limit(fresh_telegram_task)
        main_limit = resident.runner_admission_limit(openclaw_main_task)
        old_limit = resident.runner_admission_limit(old_telegram_task)
        check(
            "runner admission defaults to eight ordinary slots plus two user/main reserved slots",
            generic_limit.get("global_active_runner_limit") == 10
            and generic_limit.get("user_main_reserved_runner_slots") == 2
            and generic_limit.get("effective_active_runner_limit") == 8,
        )
        check(
            "fresh Telegram task can use reserved runner lane",
            fresh_limit.get("reserved_runner_lane") is True
            and fresh_limit.get("reserved_lane_reason") == "fresh_telegram_user_task"
            and fresh_limit.get("effective_active_runner_limit") == 10,
        )
        check(
            "openclaw-main control task can use reserved runner lane",
            main_limit.get("reserved_runner_lane") is True
            and main_limit.get("reserved_lane_reason") == "openclaw_main_control_task"
            and main_limit.get("effective_active_runner_limit") == 10,
        )
        check(
            "old Telegram backlog does not permanently occupy reserved runner lane",
            old_limit.get("reserved_runner_lane") is False
            and old_limit.get("effective_active_runner_limit") == 8,
        )
        check(
            "fresh Telegram ticks default to two new task slots",
            resident.configured_new_task_limit_per_tick() == 2,
        )
        check(
            "acceleration priority defaults to nonexclusive on",
            resident.configured_acceleration_policy() == "nonexclusive"
            and resident.acceleration_priority_enabled(),
        )
        acceleration_dir = dry_dir / "acceleration-priority-smoke"
        acceleration_dir.mkdir(parents=True, exist_ok=True)
        ordinary_a = acceleration_dir / "ordinary-a.json"
        accelerated = acceleration_dir / "accelerated.json"
        ordinary_b = acceleration_dir / "ordinary-b.json"
        resident.write_json(ordinary_a, {"task_id": "ordinary-a", "user_message": "普通状态确认"})
        resident.write_json(accelerated, {"task_id": "accelerated", "user_message": "请更快处理这个状态"})
        resident.write_json(ordinary_b, {"task_id": "ordinary-b", "user_message": "普通跟进"})
        selected_acceleration = resident.select_new_task_paths(
            [ordinary_a, accelerated, ordinary_b],
            resident.configured_new_task_limit_per_tick(),
            resident.acceleration_priority_enabled(),
        )
        check(
            "default nonexclusive acceleration keeps accelerated task inside fresh tick budget",
            accelerated in selected_acceleration and len(selected_acceleration) == 2,
        )
    finally:
        if old_global_runner_limit is not None:
            os.environ["AGENT_ROOM_GLOBAL_ACTIVE_RUNNER_LIMIT"] = old_global_runner_limit
        if old_user_main_reserved_slots is not None:
            os.environ["AGENT_ROOM_USER_MAIN_RESERVED_RUNNER_SLOTS"] = old_user_main_reserved_slots
        if old_fresh_user_reserved_slots is not None:
            os.environ["AGENT_ROOM_FRESH_USER_RESERVED_RUNNER_SLOTS"] = old_fresh_user_reserved_slots
        if old_new_task_limit is not None:
            os.environ["AGENT_ROOM_NEW_TASK_LIMIT_PER_TICK"] = old_new_task_limit
        if old_acceleration_policy is not None:
            os.environ["AGENT_ROOM_ACCELERATION_POLICY"] = old_acceleration_policy
    original_active_runners = resident.ACTIVE_RUNNERS
    original_finished_runners = resident.FINISHED_RUNNERS
    active_smoke_dir = dry_dir / "active-runners-smoke"
    orphan_active_smoke_dir = dry_dir / "active-runners-orphan-smoke"
    orphan_finished_smoke_dir = dry_dir / "finished-runners-orphan-smoke"
    active_smoke_dir.mkdir(parents=True, exist_ok=True)
    orphan_active_smoke_dir.mkdir(parents=True, exist_ok=True)
    orphan_finished_smoke_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in active_smoke_dir.glob("*.json"):
        stale_path.unlink()
    for stale_path in orphan_active_smoke_dir.glob("*.json"):
        stale_path.unlink()
    for stale_path in orphan_finished_smoke_dir.glob("*.json"):
        stale_path.unlink()
    try:
        resident.ACTIVE_RUNNERS = active_smoke_dir
        resident.write_json(
            resident.active_runner_path("codex", "smoke-previous-turn"),
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "smoke-previous-turn",
                "pid": os.getpid(),
                "started_at": now.isoformat(timespec="seconds"),
                "runner_budget": codex_budget,
            },
        )
        check("active runner count sees prior same-agent work", resident.active_runner_count("codex") == 1)
        check("active runner count is global rather than per-agent only", resident.active_runner_count() == 1)
        old_enforce_per_agent = os.environ.get("AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT")
        old_per_agent_limit = os.environ.get("AGENT_ROOM_ACTIVE_RUNNERS_PER_AGENT")
        try:
            os.environ["AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT"] = "1"
            os.environ["AGENT_ROOM_ACTIVE_RUNNERS_PER_AGENT"] = "1"
            ordinary_decision = resident.runner_per_agent_limit_decision("codex", reserved_runner_lane=False)
            reserved_decision = resident.runner_per_agent_limit_decision("codex", reserved_runner_lane=True)
            check(
                "per-agent cap still throttles ordinary same-agent backlog",
                ordinary_decision.get("blocked") is True
                and ordinary_decision.get("active_runner_count") == 1
                and ordinary_decision.get("active_runner_limit") == 1,
            )
            check(
                "reserved lane is not starved by same-agent backlog cap",
                reserved_decision.get("blocked") is False
                and reserved_decision.get("active_runner_count") == 1
                and reserved_decision.get("active_runner_limit") == 1,
            )
        finally:
            if old_enforce_per_agent is None:
                os.environ.pop("AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT", None)
            else:
                os.environ["AGENT_ROOM_ENFORCE_PER_AGENT_ACTIVE_LIMIT"] = old_enforce_per_agent
            if old_per_agent_limit is None:
                os.environ.pop("AGENT_ROOM_ACTIVE_RUNNERS_PER_AGENT", None)
            else:
                os.environ["AGENT_ROOM_ACTIVE_RUNNERS_PER_AGENT"] = old_per_agent_limit
        resident.ACTIVE_RUNNERS = orphan_active_smoke_dir
        resident.FINISHED_RUNNERS = orphan_finished_smoke_dir
        orphan_path = resident.active_runner_path("codex", "smoke-dead-orphan-runner")
        resident.write_json(
            orphan_path,
            {
                "schema": "openclaw.agent_room.active_runner.v0",
                "status": "running",
                "agent_id": "codex",
                "run_id": "smoke-dead-orphan-runner",
                "pid": 999999999,
                "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "runner_dir": str(dry_dir / "missing-result-runner"),
            },
        )
        harvested_orphans = resident.harvest_active_runners(allow_send=False)
        finished_orphan = resident.read_json(orphan_finished_smoke_dir / orphan_path.name, {})
        check(
            "harvest removes dead active-runner orphan without result.json",
            not orphan_path.exists()
            and any(
                item.get("run_id") == "smoke-dead-orphan-runner"
                and item.get("orphan_harvest") is True
                and item.get("missing_result_json") is True
                for item in harvested_orphans
            )
            and finished_orphan.get("status") == "finished"
            and finished_orphan.get("orphan_harvest") is True
            and finished_orphan.get("missing_process") is True
            and finished_orphan.get("missing_result_json") is True,
        )
    finally:
        resident.ACTIVE_RUNNERS = original_active_runners
        resident.FINISHED_RUNNERS = original_finished_runners
        for stale_path in active_smoke_dir.glob("*.json"):
            stale_path.unlink()
        for stale_path in orphan_active_smoke_dir.glob("*.json"):
            stale_path.unlink()
        for stale_path in orphan_finished_smoke_dir.glob("*.json"):
            stale_path.unlink()

    original_resident_room = resident.ROOM
    context_smoke_root = dry_dir / "context-freshness-smoke" / "agent-room"
    try:
        resident.ROOM = context_smoke_root
        room_messages = context_smoke_root / "rooms" / "openclaw-evolution" / "messages.jsonl"
        old_message_at = (now - timedelta(seconds=120)).isoformat(timespec="seconds")
        runner_started_at = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
        newer_message_at = (now - timedelta(seconds=10)).isoformat(timespec="seconds")
        resident.append_jsonl(room_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-old-human:room-message",
            "stable_message_id": "smoke-old-human",
            "room_id": "openclaw-evolution",
            "actor_user_id": "alex",
            "text": "old request",
            "created_at": old_message_at,
        }])
        context_snapshot = resident.room_context_snapshot("openclaw-evolution")
        resident.append_jsonl(room_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-new-human:room-message",
            "stable_message_id": "smoke-new-human",
            "room_id": "openclaw-evolution",
            "actor_user_id": "alex",
            "text": "改成先暂停旧方向，立刻按新问题处理",
            "created_at": newer_message_at,
        }])
        record = {
            "room_id": "openclaw-evolution",
            "started_at": runner_started_at,
            "context_snapshot": context_snapshot,
        }
        freshness = resident.runner_context_freshness(record)
        check(
            "runner context freshness detects newer human room message",
            freshness.get("status") == "stale_context"
            and freshness.get("newer_human_message", {}).get("stable_message_id") == "smoke-new-human",
        )
        check(
            "interrupting newer human message blocks stale projection",
            freshness.get("projection_should_continue") is False
            and (freshness.get("newer_message_policy") or {}).get("mode") == "interrupting_context_change",
        )
        check(
            "room message refs omit raw text while preserving correlation hash",
            "text" not in (freshness.get("newer_human_message") or {})
            and bool((freshness.get("newer_human_message") or {}).get("text_sha256")),
        )
        stale_comment = resident.stale_context_comment(
            {"task_id": "smoke-context-freshness", "run_id": "smoke-context-freshness", "room_id": "openclaw-evolution"},
            "codex",
            freshness,
        )
        check("stale context comments are local-only", stale_comment.get("telegram_projection_status") == "local_only_stale_context")
        may_project, suppress_reason = resident.telegram_projection_decision(
            {"task_id": "smoke-context-freshness", "run_id": "smoke-context-freshness"},
            [stale_comment],
        )
        check(
            "stale context projection is suppressed before Telegram send",
            may_project is False and suppress_reason == "stale_context_superseded_by_room_state_update",
        )
        check(
            "stale context diagnostics do not blame the user",
            freshness.get("user_fault") is False
            and "不是用户连续发送消息的责任" in str(stale_comment.get("body") or ""),
        )
        before_room_message_count = len(resident.read_jsonl(room_messages))
        resident.append_agent_comments_to_room("openclaw-evolution", [stale_comment], source="primary_agent_reply")
        check(
            "local-only stale context comments do not enter shared room transcript",
            len(resident.read_jsonl(room_messages)) == before_room_message_count,
        )

        supplement_room = "openclaw-evolution-supplement"
        supplement_messages = context_smoke_root / "rooms" / supplement_room / "messages.jsonl"
        resident.append_jsonl(supplement_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-supplement-old:room-message",
            "stable_message_id": "smoke-supplement-old",
            "room_id": supplement_room,
            "actor_user_id": "alex",
            "text": "old request",
            "created_at": old_message_at,
        }])
        supplement_snapshot = resident.room_context_snapshot(supplement_room)
        resident.append_jsonl(supplement_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-supplement-new:room-message",
            "stable_message_id": "smoke-supplement-new",
            "room_id": supplement_room,
            "actor_user_id": "alex",
            "text": "补充一下，这是对主线任务的补充，不需要停下当前工作",
            "created_at": newer_message_at,
        }])
        supplement_freshness = resident.runner_context_freshness({
            "room_id": supplement_room,
            "started_at": runner_started_at,
            "context_snapshot": supplement_snapshot,
        })
        supplement_policy = supplement_freshness.get("newer_message_policy") or {}
        check(
            "supplemental newer human message does not stale current runner and expects prompt ack",
            supplement_freshness.get("status") == "context_update_available"
            and supplement_freshness.get("projection_should_continue") is True
            and supplement_policy.get("mode") == "non_interrupting_supplement"
            and supplement_policy.get("visible_reply_expected") is True,
        )

        status_probe_room = "openclaw-evolution-status-probe"
        status_probe_messages = context_smoke_root / "rooms" / status_probe_room / "messages.jsonl"
        resident.append_jsonl(status_probe_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-status-old:room-message",
            "stable_message_id": "smoke-status-old",
            "room_id": status_probe_room,
            "actor_user_id": "alex",
            "text": "old request",
            "created_at": old_message_at,
        }])
        status_probe_snapshot = resident.room_context_snapshot(status_probe_room)
        resident.append_jsonl(status_probe_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-status-new:room-message",
            "stable_message_id": "smoke-status-new",
            "room_id": status_probe_room,
            "actor_user_id": "alex",
            "text": "现在状态怎么样",
            "created_at": newer_message_at,
        }])
        status_probe_freshness = resident.runner_context_freshness({
            "room_id": status_probe_room,
            "started_at": runner_started_at,
            "context_snapshot": status_probe_snapshot,
        })
        status_probe_policy = status_probe_freshness.get("newer_message_policy") or {}
        check(
            "status probe expects immediate visible reply without staling runner",
            status_probe_freshness.get("status") == "context_update_available"
            and status_probe_freshness.get("projection_should_continue") is True
            and status_probe_policy.get("mode") == "non_interrupting_status_probe"
            and status_probe_policy.get("runtime_action") == "continue_runner_and_answer_status_immediately"
            and status_probe_policy.get("visible_reply_expected") is True,
        )

        immediate_reply_room = "openclaw-evolution-nonimpact-reply"
        immediate_reply_messages = context_smoke_root / "rooms" / immediate_reply_room / "messages.jsonl"
        resident.append_jsonl(immediate_reply_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-nonimpact-old:room-message",
            "stable_message_id": "smoke-nonimpact-old",
            "room_id": immediate_reply_room,
            "actor_user_id": "alex",
            "text": "old request",
            "created_at": old_message_at,
        }])
        immediate_reply_snapshot = resident.room_context_snapshot(immediate_reply_room)
        resident.append_jsonl(immediate_reply_messages, [{
            "schema": "openclaw.agent_room.message.v0",
            "message_event_id": "smoke-nonimpact-new:room-message",
            "stable_message_id": "smoke-nonimpact-new",
            "room_id": immediate_reply_room,
            "actor_user_id": "alex",
            "text": "如果不影响当前任务就应该立刻回复我",
            "created_at": newer_message_at,
        }])
        immediate_reply_freshness = resident.runner_context_freshness({
            "room_id": immediate_reply_room,
            "started_at": runner_started_at,
            "context_snapshot": immediate_reply_snapshot,
        })
        immediate_reply_policy = immediate_reply_freshness.get("newer_message_policy") or {}
        check(
            "non-impact newer human message expects immediate visible reply without staling runner",
            immediate_reply_freshness.get("status") == "context_update_available"
            and immediate_reply_freshness.get("projection_should_continue") is True
            and immediate_reply_policy.get("mode") == "non_interrupting_supplement"
            and immediate_reply_policy.get("runtime_action") == "continue_runner_and_answer_visible_message_immediately"
            and immediate_reply_policy.get("visible_reply_expected") is True,
        )
    finally:
        resident.ROOM = original_resident_room

    env_names = ["AGENT_ROOM_STANDING_AGENDA_ENABLED", "AGENT_ROOM_STANDING_MAINLINE_DISCUSSION"]
    saved_env = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ.pop(name, None)
        check("standing agenda honors config when env override is absent", not standing_agenda_tick.env_disabled())
        os.environ["AGENT_ROOM_STANDING_AGENDA_ENABLED"] = "0"
        check("standing agenda explicit env off disables scheduler", standing_agenda_tick.env_disabled())
        os.environ["AGENT_ROOM_STANDING_AGENDA_ENABLED"] = "1"
        check("standing agenda explicit env on enables scheduler", not standing_agenda_tick.env_disabled())
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    standing_smoke_root = dry_dir / "standing-agenda-smoke"
    standing_smoke_room = standing_smoke_root / "agent-room"
    standing_saved_env = {name: os.environ.get(name) for name in env_names}
    saved_standing_globals = {
        "ROOT": standing_agenda_tick.ROOT,
        "ROOM": standing_agenda_tick.ROOM,
        "CONFIG": standing_agenda_tick.CONFIG,
        "STATE": standing_agenda_tick.STATE,
        "TASKS_JSONL": standing_agenda_tick.TASKS_JSONL,
        "ACTIVE_RUNNERS": standing_agenda_tick.ACTIVE_RUNNERS,
    }
    try:
        shutil.rmtree(standing_smoke_root, ignore_errors=True)
        for name in env_names:
            os.environ.pop(name, None)
        standing_agenda_tick.ROOT = standing_smoke_root
        standing_agenda_tick.ROOM = standing_smoke_room
        standing_agenda_tick.CONFIG = standing_smoke_room / "config" / "standing-agenda.json"
        standing_agenda_tick.STATE = standing_smoke_room / "standing-agenda-state.json"
        standing_agenda_tick.TASKS_JSONL = standing_smoke_room / "tasks.jsonl"
        standing_agenda_tick.ACTIVE_RUNNERS = standing_smoke_room / "active-runners"
        standing_agenda_tick.write_json(
            standing_agenda_tick.CONFIG,
            {
                "schema": "openclaw.agent_room.standing_agenda.v0",
                "enabled": False,
                "proactive_tick_interval_seconds": 300,
                "items": [
                    {
                        "id": "smoke-proactive-discussion",
                        "title": "Smoke proactive discussion",
                        "description": "Create a quiet-period standing task.",
                        "status": "open",
                        "priority": 10,
                        "target_agents": ["codex", "claude-code"],
                        "max_silence_seconds": 0,
                    }
                ],
            },
        )
        disabled_tick = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=False,
        ))
        check(
            "standing agenda disabled flag leaves daemon injection unchanged",
            disabled_tick.get("status") == "disabled"
            and disabled_tick.get("created") is False
            and not standing_agenda_tick.TASKS_JSONL.exists(),
        )
        standing_agenda_tick.write_json(
            standing_agenda_tick.CONFIG,
            {
                "schema": "openclaw.agent_room.standing_agenda.v0",
                "enabled": True,
                "proactive_tick_interval_seconds": 300,
                "standing_dead_runner_grace_seconds": 0,
                "max_rounds": 1,
                "items": [
                    {
                        "id": "smoke-proactive-discussion",
                        "mainline_item_id": "smoke-mainline-lane",
                        "title": "Smoke proactive discussion",
                        "description": "Create a quiet-period standing task.",
                        "status": "open",
                        "priority": 10,
                        "target_agents": ["codex", "claude-code"],
                        "max_silence_seconds": 0,
                        "max_rounds": 1,
                    }
                ],
            },
        )
        standing_agenda_tick.write_json(
            standing_smoke_room / "rooms" / "openclaw-evolution" / "room.json",
            {"room_id": "openclaw-evolution", "telegram_chat_id": "-1009000000001"},
        )
        standing_agenda_tick.write_json(
            standing_smoke_room / "rooms" / "openclaw-evolution" / "mainline_agenda.json",
            {
                "schema": "openclaw.agent_room.mainline_agenda.v0",
                "room_id": "openclaw-evolution",
                "active_items": [
                    {
                        "id": "smoke-mainline-lane",
                        "status": "open",
                        "work_item": "Use linked mainline work item in generated standing brief.",
                        "acceptance_evidence": ["linked mainline acceptance is present"],
                        "must_not_displace": ["existing production workflows"],
                    }
                ],
            },
        )
        stale_user_task = {
            "task_id": "smoke-stale-telegram-task",
            "run_id": "smoke-stale-telegram-task",
            "created_at": (now - timedelta(seconds=600)).isoformat(timespec="seconds"),
            "source": {"transport": "telegram"},
            "target_agents": ["codex"],
        }
        recent_user_task = {
            **stale_user_task,
            "task_id": "smoke-recent-telegram-task",
            "run_id": "smoke-recent-telegram-task",
            "created_at": now.isoformat(timespec="seconds"),
        }
        pending_dir = standing_smoke_room / "pending-tasks"
        standing_agenda_tick.write_json(pending_dir / "stale.json", stale_user_task)
        check("standing agenda ignores stale pending user tasks", standing_agenda_tick.fresh_user_task_count(300) == 0)
        standing_agenda_tick.write_json(pending_dir / "recent.json", recent_user_task)
        check("standing agenda still suppresses fresh pending user tasks", standing_agenda_tick.fresh_user_task_count(300) == 1)
        self_scanned_suppressed = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=None,
            active_runner_count=0,
            dry_run=False,
        ))
        check(
            "standing agenda self-scan suppresses fresh Telegram backlog",
            self_scanned_suppressed.get("status") == "suppressed_fresh_user_task"
            and self_scanned_suppressed.get("created") is False,
        )
        suppressed = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=1,
            active_runner_count=0,
            dry_run=False,
        ))
        check(
            "standing agenda fresh Telegram tasks suppress injection",
            suppressed.get("status") == "suppressed_fresh_user_task"
            and suppressed.get("created") is False,
        )
        for pending_path in pending_dir.glob("*.json"):
            pending_path.unlink()
        would_create = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=True,
        ))
        check(
            "standing agenda would create due quiet-period task",
            would_create.get("status") == "would_create"
            and would_create.get("item_id") == "smoke-proactive-discussion",
        )
        created = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=False,
        ))
        manifest_path = Path(str(created.get("manifest_path") or ""))
        created_task = standing_agenda_tick.read_json(manifest_path, {})
        created_brief = manifest_path.with_name("brief.md").read_text(encoding="utf-8") if manifest_path.exists() else ""
        created_rows = standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)
        check(
            "standing agenda creates local standing-mainline task",
            created.get("created") is True
            and manifest_path.exists()
            and ((created_task.get("source") or {}).get("transport") == "agent-room-standing-mainline"),
        )
        check(
            "standing agenda creates exactly one internal task after quiet period",
            len(created_rows) == 1
            and created_rows[0].get("task_id") == created.get("task_id"),
        )
        check(
            "standing agenda links config item to mainline acceptance evidence",
            "Use linked mainline work item in generated standing brief." in created_brief
            and "linked mainline acceptance is present" in created_brief
            and ((created_task.get("standing_mainline") or {}).get("linked_mainline_item_id") == "smoke-mainline-lane"),
        )
        check(
            "standing agenda manifest is bounded by max_rounds and one attempt",
            ((created_task.get("standing_agenda") or {}).get("max_rounds") == 1)
            and ((created_task.get("collaboration") or {}).get("max_rounds") == 1)
            and ((created_task.get("retry_budget") or {}).get("max_attempts") == 1)
            and isinstance(created_task.get("lease"), dict),
        )
        material_comment = {
            "agent_id": "codex",
            "body": "smoke material progress with a bounded verification result",
            "telegram_projection_status": "ready",
        }
        may_project, projection_mode = resident.telegram_projection_decision(created_task, [material_comment])
        gated_task = dict(created_task)
        gated_task["standing_visible_allowed"] = False
        gated_project, gated_reason = resident.telegram_projection_decision(gated_task, [material_comment])
        check(
            "standing agenda Telegram projection uses concise internal summary when allowed",
            may_project and projection_mode == "internal-summary",
        )
        check(
            "standing agenda Telegram projection remains explicitly gated",
            (not gated_project) and gated_reason == "standing_mainline_projection_not_explicit",
        )
        pending_state = standing_agenda_tick.read_json(standing_agenda_tick.STATE, {})
        check("standing agenda tracks pending standing task", standing_agenda_tick.pending_standing_task(pending_state)[0])
        for agent_id in ("codex", "claude-code"):
            standing_agenda_tick.write_json(
                standing_smoke_room / "telegram-agent-reply" / f"{agent_id}-{created.get('run_id')}.json",
                {"agent_id": agent_id, "run_id": created.get("run_id"), "suppressed_reason": "smoke_local_only"},
            )
        check("standing agenda releases pending task after reply artifacts", not standing_agenda_tick.pending_standing_task(pending_state)[0])
        duplicate_tick = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=False,
        ))
        check(
            "standing agenda cooldown prevents duplicate quiet-period tasks",
            duplicate_tick.get("status") == "suppressed_cooldown"
            and duplicate_tick.get("created") is False
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 1,
        )
        completed_old = dict(created_task)
        completed_old["status"] = "completed"
        completed_old["created_at"] = (now - timedelta(seconds=900)).isoformat(timespec="seconds")
        completed_old["updated_at"] = (now - timedelta(seconds=900)).isoformat(timespec="seconds")
        standing_agenda_tick.write_json(manifest_path, completed_old)
        continuation_state = standing_agenda_tick.read_json(standing_agenda_tick.STATE, {})
        continuation_state.pop("pending_task", None)
        continuation_state["last_injected_at"] = (now - timedelta(seconds=600)).isoformat(timespec="seconds")
        continuation_state.setdefault("items", {}).setdefault("smoke-proactive-discussion", {})[
            "last_discussed_at"
        ] = (now - timedelta(seconds=600)).isoformat(timespec="seconds")
        standing_agenda_tick.write_json(standing_agenda_tick.STATE, continuation_state)
        next_epoch = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=True,
        ))
        check(
            "standing agenda max_rounds is a burst fuse, not permanent mainline suppression",
            next_epoch.get("status") == "would_create"
            and next_epoch.get("item_id") == "smoke-proactive-discussion",
        )
        dead_task_id = "standing-openclaw-evolution-smoke-dead-active-runner"
        dead_task_dir = standing_smoke_room / "tasks" / dead_task_id
        dead_task = {
            "schema": "openclaw.agent_room.task.v0",
            "task_id": dead_task_id,
            "run_id": dead_task_id,
            "room_id": "openclaw-evolution",
            "target_agents": ["codex", "claude-code"],
            "status": "running",
            "created_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
            "updated_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
            "source": {"transport": "agent-room-standing-mainline"},
            "standing_agenda": {"item_id": "smoke-proactive-discussion"},
            "collaboration": {
                "schema": "openclaw.agent_room.collaboration.v0",
                "status": "open",
                "work_items": [
                    {"id": "smoke-proactive-discussion_codex", "status": "open", "assigned_to": "codex"},
                    {"id": "smoke-proactive-discussion_claude-code", "status": "open", "assigned_to": "claude-code"},
                ],
                "blockers": [],
            },
        }
        standing_agenda_tick.write_json(dead_task_dir / "manifest.json", dead_task)
        standing_agenda_tick.append_jsonl(standing_agenda_tick.TASKS_JSONL, [dead_task])
        state = standing_agenda_tick.read_json(standing_agenda_tick.STATE, {})
        state["pending_task"] = {
            "task_id": dead_task_id,
            "run_id": dead_task_id,
            "item_id": "smoke-proactive-discussion",
            "target_agents": ["codex", "claude-code"],
            "created_at": dead_task["created_at"],
        }
        standing_agenda_tick.write_json(standing_agenda_tick.STATE, state)
        for agent_id in ("codex", "claude-code"):
            standing_agenda_tick.write_json(
                standing_agenda_tick.active_runner_path(agent_id, dead_task_id),
                {
                    "schema": "openclaw.agent_room.active_runner.v0",
                    "status": "running",
                    "agent_id": agent_id,
                    "run_id": dead_task_id,
                    "pid": 999999999,
                    "started_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                    "runner_dir": str(standing_smoke_root / "missing-runner-result" / agent_id),
                },
            )
        reconcile_only = standing_agenda_tick.tick(argparse.Namespace(
            room_id="openclaw-evolution",
            fresh_task_count=0,
            active_runner_count=0,
            dry_run=False,
            reconcile_only=True,
        ))
        dead_after = standing_agenda_tick.read_json(dead_task_dir / "manifest.json", {})
        dead_summary = dead_after.get("runner_summary") if isinstance(dead_after.get("runner_summary"), dict) else {}
        check(
            "standing agenda reconcile-only closes dead active-runner pending tasks without creating new work",
            reconcile_only.get("status") == "reconciled_only"
            and reconcile_only.get("created") is False
            and len(standing_agenda_tick.read_jsonl(standing_agenda_tick.TASKS_JSONL)) == 2
            and dead_after.get("status") == "failed"
            and ((dead_after.get("standing_closure") or {}).get("reason") == "dead_active_runner_evidence")
            and set(dead_summary.get("failed_agents") or []) == {"codex", "claude-code"},
        )
    finally:
        for name, value in standing_saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in saved_standing_globals.items():
            setattr(standing_agenda_tick, name, value)
    standing_agenda_create_source = inspect.getsource(standing_agenda_tick.create_task)
    resident_standing_mainline_source = inspect.getsource(resident.maybe_create_standing_mainline_task)
    check(
        "standing agenda creates per-agent owned work items",
        '"assigned_to": agent_id' in standing_agenda_create_source
        and "for agent_id in targets" in standing_agenda_create_source
        and "compact_slug(agent_id)" in standing_agenda_create_source,
    )
    check(
        "resident standing mainline creates per-agent owned work items",
        '"assigned_to": agent_id' in resident_standing_mainline_source
        and "for agent_id in targets" in resident_standing_mainline_source
        and "compact_slug(agent_id)" in resident_standing_mainline_source,
    )
    resident_main_source = inspect.getsource(resident.main)
    check(
        "resident bridge accepts room-id for standing agenda",
        'parser.add_argument("--room-id"' in resident_main_source
        and '"--room-id", args.room_id' in resident_main_source,
    )
    check(
        "resident standing agenda lets normal scheduler scan fresh user backlog",
        resident_main_source.count('"--fresh-task-count"') == 1
        and '"--fresh-task-count", "0"' in resident_main_source,
    )
    check(
        "resident post-harvest agenda may bypass fresh user gate for autonomous continuation",
        'post_harvest_run = run_cmd([' in resident_main_source
        and '"--fresh-task-count", "0"' in resident_main_source,
    )
    harvest_idx = resident_main_source.find("harvest_active_runners")
    standing_idx = resident_main_source.find("standing_agenda_tick.py")
    dispatch_idx = resident_main_source.find("task_paths_to_process")
    check(
        "resident standing agenda runs after harvest and before task dispatch",
        0 <= harvest_idx < standing_idx < dispatch_idx,
    )
    check(
        "resident standing agenda uses post-harvest active-runner count",
        "active_runner_blocking_count_for_standing_agenda()" in resident_main_source,
    )
    daemon_main_source = inspect.getsource(agent_room_bridge_daemon.main)
    daemon_run_tick_source = inspect.getsource(agent_room_bridge_daemon.run_tick)
    check(
        "daemon passes room-id through resident bridge and standing agenda",
        'parser.add_argument("--room-id"' in daemon_main_source
        and '"--room-id", room_id' in daemon_run_tick_source
        and '["python3", str(STANDING_AGENDA_TICK), "--room-id", args.room_id]' in daemon_main_source,
    )

    deferred_comment = resident.deferred_agent_comment(parent_task, "codex", "already_running")
    check("deferred liveness signal is local-only", deferred_comment.get("telegram_projection_status") == "local_only_deferred_liveness_signal")
    check("deferred liveness signal is not material peer content", not resident.is_material_peer_comment(deferred_comment))
    check(
        "deferred liveness signal is suppressed by projection gate",
        resident.telegram_projection_decision(parent_task, [deferred_comment]) == (False, "local_only_deferred_liveness_signal"),
    )
    check(
        "telegram reply suppresses deferred liveness signal",
        telegram_agent_reply.is_internal_runner_failure_comment(
            deferred_comment,
            str(deferred_comment.get("title") or ""),
            str(deferred_comment.get("body") or ""),
            deferred_comment.get("blockers") or [],
        ),
    )
    legacy_deferred_comment = dict(deferred_comment)
    legacy_deferred_comment["telegram_projection_status"] = "deferred_liveness_signal"
    check("legacy deferred liveness signal is not material", not resident.is_material_peer_comment(legacy_deferred_comment))
    check(
        "legacy deferred liveness signal is suppressed by projection gate",
        resident.telegram_projection_decision(parent_task, [legacy_deferred_comment]) == (False, "local_only_deferred_liveness_signal"),
    )
    check(
        "telegram reply suppresses legacy deferred liveness signal",
        telegram_agent_reply.is_internal_runner_failure_comment(
            legacy_deferred_comment,
            str(legacy_deferred_comment.get("title") or ""),
            str(legacy_deferred_comment.get("body") or ""),
            legacy_deferred_comment.get("blockers") or [],
        ),
    )
    original_comment_path = resident.comment_path
    persisted_deferred_path = dry_dir / "persisted_deferred_comments.jsonl"
    try:
        if persisted_deferred_path.exists():
            persisted_deferred_path.unlink()
        resident.comment_path = lambda _agent_id: persisted_deferred_path  # type: ignore[assignment]
        resident.DEFERRED_COMMENT_TRACKER.clear()
        resident.append_jsonl(persisted_deferred_path, [legacy_deferred_comment | {"run_id": "smoke-persisted-deferred"}])
        check(
            "deferred liveness dedupe reads durable comment ledger",
            resident.deferred_comment_already_emitted("codex", "smoke-persisted-deferred", "already_running"),
        )
    finally:
        resident.comment_path = original_comment_path  # type: ignore[assignment]
        resident.DEFERRED_COMMENT_TRACKER.clear()

    routine_comment = dict(material_comment)
    routine_comment["title"] = "ack"
    routine_comment["body"] = "收到，我同意。"
    check("routine peer ack does not create follow-up", not resident.should_create_collab_followup(parent_task, routine_comment, {"claude-code"}))
    proposal_comment = dict(material_comment)
    proposal_comment["title"] = "peer proposal uptake"
    proposal_comment["body"] = "我建议把 Claude 的验收目标记下来，后续行为要受这个观点影响；Codex 可以接住并说明采纳或不采纳。"
    proposal_comment["blockers"] = []
    check(
        "peer proposal comment creates uptake follow-up",
        resident.should_create_collab_followup(parent_task, proposal_comment, {"claude-code"})
        and "uptake_decision" in resident.collaboration_followup_expected_outputs(parent_task, proposal_comment),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
