#!/usr/bin/env python3
"""Shared Task Router V0 admission logic.

Keep this module free of background task imports so it can be reused by the CLI,
task record creation, and direct-provider worker manifests without circular
imports or OpenClaw gateway calls.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

SIDECAR = Path.home() / ".openclaw" / "monitor-cache" / "reliability-sidecar.json"

INLINE = "inline"
WORKER = "worker"
EMBEDDED_LIMITED = "embedded-limited"
REVIEW_REQUIRED = "review-required"
DEFER = "defer"
ROUTES = [INLINE, WORKER, EMBEDDED_LIMITED, REVIEW_REQUIRED, DEFER]

HEAVY_HINTS = {
    "pdf", "ocr", "notion", "publish", "发布", "翻译", "translation",
    "translate", "日报", "简报", "market", "people daily", "人民日报",
    "批量", "chunk", "全量", "终版", "final", "抓取", "长日志",
    "log", "diff", "格式转换",
}
SIDE_EFFECT_HINTS = {
    "publish", "发布", "notion", "delete", "删除", "重启", "restart",
    "配置", "config", "token", "secret", "secrets", "oauth", "commit",
    "push", "github", "发送",
}
NATIVE_HINTS = {
    "openclaw 原生", "原生插件", "binding", "agent binding", "permission",
    "权限", "control", "memory", "记忆", "人格", "长期记忆", "session",
    "会话", "工具", "tool", "插件",
}
SHORT_CHECK_HINTS = {
    "状态", "status", "看看", "查一下", "是否", "能不能", "活着",
    "running", "probe",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_sidecar() -> dict[str, Any]:
    if not SIDECAR.exists():
        return {}
    try:
        return json.loads(SIDECAR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def gateway_hot(sidecar: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    gateway = sidecar.get("gateway") or {}
    logs = sidecar.get("recent_gateway_logs") or {}
    try:
        cpu = float(gateway.get("cpu_percent") or 0)
    except Exception:
        cpu = 0.0
    try:
        rss = float(gateway.get("rss_mb") or 0)
    except Exception:
        rss = 0.0
    if cpu >= 70:
        reasons.append(f"gateway_cpu_high:{cpu:.1f}%")
    elif cpu >= 50:
        reasons.append(f"gateway_cpu_watch:{cpu:.1f}%")
    if rss >= 1600:
        reasons.append(f"gateway_rss_high:{rss:.0f}MB")
    elif rss >= 1000:
        reasons.append(f"gateway_rss_watch:{rss:.0f}MB")
    for reason in logs.get("reasons") or []:
        if "event_loop" in str(reason) or "timeout" in str(reason):
            reasons.append(str(reason))
    counts = logs.get("counts") or {}
    for key in ("telegram_send_failed", "telegram_action_failed", "fetch_timeout", "event_loop_delay"):
        try:
            count = int(counts.get(key) or 0)
        except Exception:
            count = 0
        if count:
            reasons.append(f"{key}:{count}")
    return bool(reasons), reasons


def contains_any(text: str, hints: set[str]) -> list[str]:
    lower = text.lower()
    return sorted([hint for hint in hints if hint.lower() in lower])


def estimate_from_text(text: str) -> dict[str, Any]:
    chars = len(text)
    chunk_count = 1
    model_calls = 1
    expected_seconds = 15
    lower = text.lower()
    if chars > 8000:
        chunk_count = max(chunk_count, min(20, chars // 6000 + 1))
    if any(x in lower for x in ["全书", "整本", "完整pdf", "pdf", "batch", "批量", "全量"]):
        chunk_count = max(chunk_count, 4)
    if any(x in lower for x in ["翻译", "translation", "translate"]):
        model_calls = max(model_calls, chunk_count)
        expected_seconds = max(expected_seconds, 90)
    if any(x in lower for x in ["日报", "简报", "market", "人民日报", "notion", "发布"]):
        model_calls = max(model_calls, 3)
        expected_seconds = max(expected_seconds, 120)
    if chunk_count > 1:
        expected_seconds = max(expected_seconds, chunk_count * 45)
    return {
        "input_chars": chars,
        "chunk_count": chunk_count,
        "model_calls": model_calls,
        "expected_seconds": expected_seconds,
    }


def route_task(
    *,
    text: str,
    task_type: str = "generic",
    expected_seconds: int | None = None,
    chunk_count: int | None = None,
    model_calls: int | None = None,
    external_side_effect: bool | None = None,
    needs_openclaw_native: bool | None = None,
    needs_memory_or_personality: bool | None = None,
    force: str = "",
) -> dict[str, Any]:
    est = estimate_from_text(text)
    if expected_seconds is not None:
        est["expected_seconds"] = expected_seconds
    if chunk_count is not None:
        est["chunk_count"] = chunk_count
    if model_calls is not None:
        est["model_calls"] = model_calls

    sidecar = load_sidecar()
    hot, hot_reasons = gateway_hot(sidecar)
    heavy_hits = contains_any(text, HEAVY_HINTS)
    side_effect_hits = contains_any(text, SIDE_EFFECT_HINTS)
    native_hits = contains_any(text, NATIVE_HINTS)
    short_hits = contains_any(text, SHORT_CHECK_HINTS)

    external = bool(side_effect_hits) if external_side_effect is None else external_side_effect
    native = bool(native_hits) if needs_openclaw_native is None else needs_openclaw_native
    memory = (
        "记忆" in text or "人格" in text or "长期" in text or "memory" in text.lower()
        if needs_memory_or_personality is None
        else needs_memory_or_personality
    )

    reasons: list[str] = []
    constraints: list[str] = []
    route = INLINE

    if force:
        route = force
        reasons.append(f"forced:{force}")
    elif external:
        route = REVIEW_REQUIRED
        reasons.append("external_side_effect_requires_task_record_and_review_gate")
    elif hot and native:
        route = DEFER
        reasons.append("gateway_hot_and_task_needs_openclaw_native_runtime")
    elif est["expected_seconds"] > 60 or est["chunk_count"] > 1 or est["model_calls"] > 1 or heavy_hits:
        route = WORKER
        reasons.append("estimated_long_or_multi_step_work")
    elif native:
        route = EMBEDDED_LIMITED
        reasons.append("needs_openclaw_native_runtime_but_estimated_short")
    elif memory:
        route = WORKER
        reasons.append("main_creates_contract_worker_executes_main_reviews")
    elif short_hits and est["expected_seconds"] <= 30:
        route = INLINE
        reasons.append("short_control_plane_query")
    else:
        route = INLINE
        reasons.append("default_light_inline")

    if hot:
        constraints.append("gateway_hot: avoid embedded agent unless explicitly necessary")
    if route == EMBEDDED_LIMITED:
        constraints.extend([
            "hard_timeout_seconds=60",
            "no_multi_chunk_work",
            "no_external_write_without_review_gate",
            "write_task_record_before_dispatch",
        ])
    if route in {WORKER, REVIEW_REQUIRED, DEFER}:
        constraints.extend([
            "do_not_run_in_telegram_hot_path",
            "worker_writes_manifest_result_error",
            "telegram_receives_short_status_only",
        ])
    if route == REVIEW_REQUIRED:
        constraints.append("main_review_required_before_publish_or_user_completion_claim")
    if route == DEFER:
        constraints.append("retry_or_ask_after_gateway_cools_down")

    confidence = "medium"
    if external or est["expected_seconds"] > 120 or est["chunk_count"] > 2 or hot:
        confidence = "high"

    return {
        "schema": "openclaw.task_router.v0",
        "generated_at": now_iso(),
        "task_type": task_type,
        "route": route,
        "confidence": confidence,
        "reasons": reasons,
        "constraints": constraints,
        "estimates": est,
        "signals": {
            "heavy_hits": heavy_hits,
            "side_effect_hits": side_effect_hits,
            "native_hits": native_hits,
            "short_control_hits": short_hits,
            "needs_openclaw_native": native,
            "needs_memory_or_personality": memory,
            "external_side_effect": external,
            "gateway_hot": hot,
            "gateway_hot_reasons": hot_reasons,
            "runtime_profile": (sidecar.get("runtime_profile") or {}).get("inferred_profile") or "unknown",
        },
        "boundaries": {
            "calls_openclaw_gateway": False,
            "starts_embedded_agent": False,
            "writes_config": False,
            "restarts_or_kills": False,
        },
    }
