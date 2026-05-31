#!/usr/bin/env python3
"""Local smoke for formal-writing external fallback on Ark cooldown signals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import direct_provider_openclaw_compat as compat  # noqa: E402
import direct_provider_lane  # noqa: E402


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"ok {name}")


def main() -> int:
    calls: list[dict[str, Any]] = []
    old_native = compat.run_native_openclaw_model_call
    old_direct = compat.run_direct_provider_text_prompt
    old_secret_resolver = direct_provider_lane._resolve_secret_config
    old_env = {
        key: os.environ.get(key)
        for key in (
            "OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK",
            "OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS",
            "OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MAX_TOKENS",
            "OPENCLAW_WORKER_MODEL",
            "OPENCLAW_WORKSPACE",
        )
    }

    def fake_native(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        stderr = json.dumps(
            {
                "model_attempts": [
                    {
                        "model": "glm-5.1",
                        "status": "skipped_cooldown",
                        "reason": "usage_limit",
                        "cooldown_until": "2026-05-31T12:36:00+08:00",
                    },
                    {
                        "model": "deepseek-v4-pro",
                        "status": "skipped_cooldown",
                        "reason": "usage_limit",
                        "cooldown_until": "2026-05-31T12:40:00+08:00",
                    },
                ],
                "summary": "all Ark cooldown",
            },
            ensure_ascii=False,
        )
        return subprocess.CompletedProcess(cmd, 1, "", stderr)

    def fake_direct(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {
            "text": json.dumps({"summary_paragraphs": ["fallback ok"]}, ensure_ascii=False),
            "response": {"choices": [{"finish_reason": "stop"}], "usage": {"total_tokens": 12}},
            "manifest": {
                "provider_profile": "openai-compatible",
                "model": kwargs.get("model"),
                "status": "succeeded",
            },
        }

    try:
        with tempfile.TemporaryDirectory(prefix="openclaw-formal-fallback-smoke-") as raw_tmp:
            os.environ["OPENCLAW_WORKSPACE"] = raw_tmp
            os.environ["OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK"] = "1"
            os.environ["OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS"] = "deepseek-v4-pro"
            os.environ["OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MAX_TOKENS"] = "8192"
            compat.run_native_openclaw_model_call = fake_native
            compat.run_direct_provider_text_prompt = fake_direct

            cmd = [
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "daily-writer",
                "--session-id",
                "market-immersion-summary-smoke-1",
                "--json",
                "--thinking",
                "high",
                "--timeout",
                "30",
                "--model",
                "openai-codex/gpt-5.5",
                "--message",
                "return JSON only",
            ]
            completed = compat.run_openclaw_model_call(cmd, timeout=30, check=False)
            payload = json.loads(completed.stdout)
            trace = ((payload.get("meta") or {}).get("agentMeta") or {}).get("executionTrace") or {}
            check("fallback returned success", completed.returncode == 0)
            check("fake external provider called once", len(calls) == 1)
            check("fallback uses openai-compatible profile", calls[0].get("provider_profile") == "openai-compatible")
            check("fallback model selected", calls[0].get("model") == "deepseek-v4-pro")
            check("cooldown reason propagated to trace", trace.get("fallbackReason") == "all_ark_cooldown")
            check("cooldown reason propagated to marker", "reason=all_ark_cooldown" in completed.stderr)

            type_error_calls = len(calls)

            def fake_type_error_native(type_cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    type_cmd,
                    1,
                    "",
                    "TypeError: createDiagnosticTraceContextFromActiveScope is not a function",
                )

            compat.run_native_openclaw_model_call = fake_type_error_native
            type_error = subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "TypeError: createDiagnosticTraceContextFromActiveScope is not a function",
            )
            completed = compat.run_openclaw_model_call(cmd, timeout=30, check=False)
            check("non-cooldown TypeError predicate stays false", compat._is_formal_usage_limit_cooldown_failure(type_error) is False)
            check("non-cooldown TypeError does not call external fallback", len(calls) == type_error_calls)
            check("non-cooldown TypeError failure is preserved", completed.returncode == 1)

            rate_limit_calls = len(calls)

            def fake_rate_limit_native(rate_cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
                stderr = json.dumps(
                    {
                        "model_attempts": [
                            {
                                "model": "glm-5.1",
                                "status": "skipped_cooldown",
                                "reason": "rate_limit",
                                "cooldown_until": "2026-05-31T12:36:00+08:00",
                            }
                        ],
                        "summary": "rate limit cooldown",
                    },
                    ensure_ascii=False,
                )
                return subprocess.CompletedProcess(rate_cmd, 1, "", stderr)

            compat.run_native_openclaw_model_call = fake_rate_limit_native
            rate_limit = fake_rate_limit_native(cmd)
            completed = compat.run_openclaw_model_call(cmd, timeout=30, check=False)
            check("rate-limit cooldown predicate stays false", compat._is_formal_usage_limit_cooldown_failure(rate_limit) is False)
            check("rate-limit cooldown does not call external fallback", len(calls) == rate_limit_calls)
            check("rate-limit cooldown failure is preserved", completed.returncode == 1)

            os.environ.pop("OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS", None)
            os.environ.pop("OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL", None)
            os.environ["OPENCLAW_WORKER_MODEL"] = "deepseek-v4-flash"
            check(
                "formal fallback default candidates stay pro-only",
                compat._formal_fallback_model_candidates() == ["deepseek-v4-pro"],
            )
            os.environ["OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS"] = "deepseek-v4-pro,deepseek-v4-flash"
            check(
                "formal fallback filters mixed env candidates to pro-only",
                compat._formal_fallback_model_candidates() == ["deepseek-v4-pro"],
            )
            os.environ["OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS"] = "deepseek-v4-flash"
            os.environ["OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL"] = "deepseek-v4-flash"
            check(
                "formal fallback ignores flash-only env candidates",
                compat._formal_fallback_model_candidates() == ["deepseek-v4-pro"],
            )

            direct_provider_lane._resolve_secret_config = lambda _name: ""
            os.environ["OPENCLAW_WORKER_MODEL"] = "deepseek-v4-flash"
            profile = direct_provider_lane.resolve_direct_provider_profile("openai-compatible", "deepseek-v4-pro")
            check("explicit DeepSeek pro is not downgraded to flash", profile.default_model == "deepseek-v4-pro")
            legacy_profile = direct_provider_lane.resolve_direct_provider_profile("openai-compatible", "deepseek-v3.2")
            check("legacy DeepSeek v3.2 maps to pro tier", legacy_profile.default_model == "deepseek-v4-pro")
    finally:
        compat.run_native_openclaw_model_call = old_native
        compat.run_direct_provider_text_prompt = old_direct
        direct_provider_lane._resolve_secret_config = old_secret_resolver
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(json.dumps({"ok": True, "calls": len(calls)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
