#!/usr/bin/env python3
"""Compatibility layer for background scripts that still build `openclaw agent` commands.

The public shape mirrors subprocess.CompletedProcess so existing workflow code can
keep parsing OpenClaw-like JSON while the actual model call bypasses the gateway.
"""
from __future__ import annotations

import json
import hashlib
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from direct_provider_lane import DirectProviderError, run_direct_provider_text_prompt
from gateway_model_lane import run_openclaw_model_call as run_native_openclaw_model_call

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
PROMPT_TRANSPORT_DIR = WORKSPACE / "model-inputs" / "native-prompt-transport"
PROMPT_TRANSPORT_THRESHOLD = int(os.environ.get("OPENCLAW_NATIVE_PROMPT_FILE_THRESHOLD_CHARS") or "60000")
NATIVE_BUSY_MAX_ATTEMPTS = int(os.environ.get("OPENCLAW_NATIVE_MODEL_CALL_BUSY_MAX_ATTEMPTS") or "5")
NATIVE_BUSY_BACKOFF_SECONDS = float(os.environ.get("OPENCLAW_NATIVE_MODEL_CALL_BUSY_BACKOFF_SECONDS") or "2")
NATIVE_BUSY_BACKOFF_MAX_SECONDS = float(os.environ.get("OPENCLAW_NATIVE_MODEL_CALL_BUSY_BACKOFF_MAX_SECONDS") or "30")
FORMAL_FALLBACK_ENV = "OPENCLAW_ALLOW_FORMAL_OPENAI_COMPAT_FALLBACK"
FORMAL_FALLBACK_MODEL_ENV = "OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODEL"
FORMAL_FALLBACK_MODELS_ENV = "OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MODELS"
FORMAL_FALLBACK_MAX_TOKENS_ENV = "OPENCLAW_FORMAL_OPENAI_COMPAT_FALLBACK_MAX_TOKENS"
FORMAL_FALLBACK_MARKER = "[FALLBACK]"


def _formal_fallback_max_tokens() -> int:
    raw = os.environ.get(FORMAL_FALLBACK_MAX_TOKENS_ENV, "8192")
    try:
        value = int(raw)
    except ValueError:
        return 8192
    return 8192 if value <= 0 else value


def _formal_fallback_enabled() -> bool:
    return os.environ.get(FORMAL_FALLBACK_ENV) == "1"


def _formal_fallback_model_candidates() -> list[str]:
    def pro_tier(candidate: str) -> str:
        value = candidate.strip()
        normalized = value.lower()
        if normalized == "deepseek-v3.2":
            return "deepseek-v4-pro"
        if normalized.startswith("deepseek-") and normalized.endswith("-pro"):
            return value
        return ""

    def unique_pro_tier(candidates: list[str]) -> list[str]:
        models: list[str] = []
        for candidate in candidates:
            model = pro_tier(candidate)
            if model and model not in models:
                models.append(model)
        return models

    env_models = os.environ.get(FORMAL_FALLBACK_MODELS_ENV)
    if env_models:
        models = unique_pro_tier([item for item in env_models.split(",") if item.strip()])
        if models:
            return models
    single = os.environ.get(FORMAL_FALLBACK_MODEL_ENV, "").strip()
    if single:
        models = unique_pro_tier([single])
        if models:
            return models
    return ["deepseek-v4-pro"]


def _is_quota_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 0:
        return False
    if completed.returncode == 75:
        return False
    text = "\n".join(str(part or "") for part in [completed.stderr, completed.stdout]).lower()
    markers = (
        "usage_limit",
        "usage limit",
        "skipped_cooldown",
        "cooldown_until",
        "all ark cooldown",
        "all_ark_cooldown",
        "all candidate models have already emitted depletion notice",
        "accountquotaexceeded",
        "account quota",
        "quota exceeded",
        "too many requests",
        "429",
        "rate limit",
        "insufficient_quota",
    )
    return any(marker in text for marker in markers)


def _is_formal_usage_limit_cooldown_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 0:
        return False
    if completed.returncode == 75:
        return False
    text = "\n".join(str(part or "") for part in [completed.stderr, completed.stdout]).lower()
    cooldown_markers = (
        "skipped_cooldown",
        "cooldown_until",
        "all ark cooldown",
        "all_ark_cooldown",
        "all candidate models have already emitted depletion notice",
    )
    usage_limit_markers = (
        "usage_limit",
        "usage limit",
        "accountquotaexceeded",
        "account quota",
        "quota exceeded",
        "insufficient_quota",
        "depletion notice",
    )
    return any(marker in text for marker in cooldown_markers) and any(
        marker in text for marker in usage_limit_markers
    )


def _is_native_formal_transport_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 0:
        return False
    text = "\n".join(str(part or "") for part in [completed.stderr, completed.stdout]).lower()
    markers = (
        "codex agent harness failed",
        "not falling back to embedded pi backend",
        "creatediagnostictracecontextfromactivescope",
        "[diagnostic] lane task error",
    )
    return any(marker in text for marker in markers)


def _formal_fallback_reason(completed: subprocess.CompletedProcess[str]) -> str:
    text = "\n".join(str(part or "") for part in [completed.stderr, completed.stdout]).lower()
    transport_markers = (
        "codex agent harness failed",
        "not falling back to embedded pi backend",
        "creatediagnostictracecontextfromactivescope",
        "[diagnostic] lane task error",
    )
    if any(marker in text for marker in transport_markers):
        return "native_formal_transport_failure"
    cooldown_markers = (
        "skipped_cooldown",
        "cooldown_until",
        "all ark cooldown",
        "all_ark_cooldown",
        "all candidate models have already emitted depletion notice",
    )
    if any(marker in text for marker in cooldown_markers):
        return "all_ark_cooldown"
    return "quota_or_rate_limit"


def _fallback_marker(
    model: str,
    session_id: str,
    output_dir: Path,
    requested_model: str = "",
    finish_reason: str = "",
    truncated: bool = False,
    fallback_reason: str = "",
) -> str:
    requested = f" requested={requested_model}" if requested_model else ""
    extras: list[str] = []
    if fallback_reason:
        extras.append(f"reason={fallback_reason}")
    if finish_reason:
        extras.append(f"finish_reason={finish_reason}")
    if truncated:
        extras.append("truncated=1")
    extra_suffix = f" {' '.join(extras)}" if extras else ""
    return (
        f"{FORMAL_FALLBACK_MARKER} ark->openai-compatible model={model} "
        f"session={session_id} output_dir={output_dir}{requested}{extra_suffix}"
    )


def _extract_finish_reason(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    reason = first.get("finish_reason")
    if isinstance(reason, str):
        return reason.strip()
    return ""


def _is_incomplete_finish_reason(reason: str) -> bool:
    if not reason:
        return False
    return reason.lower() not in {"stop", "tool_calls", "function_call"}


def _is_formal_command_for_fallback(cmd: list[str]) -> bool:
    if not is_formal_gpt_required_command(cmd):
        return False
    return True


def _extract_prompt(cmd: list[str]) -> str:
    return _option(cmd, "--message")


def _normalize_task_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value or "")
    return safe[:96] or "formal-fallback"


def _run_openai_compatible_fallback(
    *,
    cmd: list[str],
    timeout: int,
    prompt: str,
    session_id: str,
    fallback_reason: str,
) -> subprocess.CompletedProcess[str] | None:
    prompt = prompt or ""
    if not prompt:
        return None
    candidate_models = _formal_fallback_model_candidates()
    if not candidate_models:
        return None
    models_seen: list[str] = []
    for index, fallback_model in enumerate(candidate_models):
        if fallback_model in models_seen:
            continue
        models_seen.append(fallback_model)
        try:
            result = run_direct_provider_text_prompt(
                prompt=prompt,
                task_id=f"{session_id}-formal-fallback",
                task_type=f"formal_fallback_{_normalize_task_id(session_id)}",
                model=fallback_model,
                provider_profile="openai-compatible",
                timeout=max(30, int(timeout)),
                max_tokens=_formal_fallback_max_tokens(),
                output_dir=None,
                agent_id=f"formal-fallback-{_normalize_task_id(session_id)}",
            )
            response = result.get("response") or {}
            finish_reason = _extract_finish_reason(response)
            truncated = _is_incomplete_finish_reason(finish_reason)
            fallback_text = result.get("text") or ""
            if truncated:
                fallback_text = f"{fallback_text}\n\n[truncated]" if fallback_text else "[truncated]"
            manifest = result.get("manifest") or {}
            stdout_payload: dict[str, Any] = {
                "payloads": [{"text": fallback_text}],
                "meta": {
                    "agentMeta": {
                        "usage": response.get("usage"),
                        "provider": "openai-compatible",
                        "directProvider": manifest,
                        "executionTrace": {
                            "fallbackUsed": True,
                            "fallbackReason": fallback_reason,
                            "fallbackProfile": "openai-compatible",
                            "fallbackModel": fallback_model,
                            "fallbackRequestedModel": _option(cmd, "--model"),
                            "fallbackFinishReason": finish_reason,
                            "fallbackTruncated": truncated,
                        },
                        "winnerModel": fallback_model,
                    }
                },
            }
            marker = _fallback_marker(
                fallback_model,
                session_id,
                WORKSPACE / "worker-runs" / "formal-openai-compatible-fallback" / f"{session_id}",
                _option(cmd, "--model"),
                finish_reason=finish_reason,
                truncated=truncated,
                fallback_reason=fallback_reason,
            )
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(stdout_payload, ensure_ascii=False),
                marker,
            )
        except DirectProviderError:
            continue
    return None


def _option(cmd: list[str], name: str, default: str = "") -> str:
    try:
        index = cmd.index(name)
    except ValueError:
        return default
    if index + 1 >= len(cmd):
        return default
    return str(cmd[index + 1])


def is_openclaw_agent_command(cmd: list[str]) -> bool:
    if len(cmd) < 2:
        return False
    executable = Path(str(cmd[0])).name
    return executable == "openclaw" and str(cmd[1]) == "agent"


def is_formal_gpt_required_command(cmd: list[str]) -> bool:
    """Return True for publishable formal writing that must not fall back to Ark.

    Alex's standing contract: formal report bodies are GPT-primary unless a
    human explicitly changes the strategy.  OpenClaw-style Codex/GPT aliases are
    native gateway models, not Ark direct-provider ids.  The compatibility layer
    must therefore pass them through to native OpenClaw or fail closed via the
    gateway lane; it must never normalize them into Kimi/GLM.
    """
    if os.environ.get("OPENCLAW_FORMAL_WRITING_GPT_REQUIRED") == "1":
        return True
    model = _option(cmd, "--model")
    agent = _option(cmd, "--agent")
    session_id = _option(cmd, "--session-id")
    if model.startswith("openai-codex/") or model.startswith("gpt-") or model.startswith("openai/"):
        markers = ("daily-writer", "people-daily", "market-immersion-summary", "market-digest-gpt")
        return agent == "daily-writer" or any(marker in session_id for marker in markers)
    return False


def with_prompt_artifact_transport(cmd: list[str]) -> list[str]:
    """Move very large formal prompts out of argv without changing bytes.

    Native OpenClaw currently exposes `--message <text>` in CLI help. Large
    formal report prompts can exceed the OS argv limit before the model sees
    them. For formal GPT-required calls, write the exact original prompt bytes
    to a workspace artifact and replace argv with a short instruction containing
    the artifact path and sha256. The agent must read the artifact and treat its
    contents as the user prompt.
    """
    try:
        index = cmd.index("--message")
    except ValueError:
        return cmd
    if index + 1 >= len(cmd):
        return cmd
    prompt = str(cmd[index + 1])
    if len(prompt) < PROMPT_TRANSPORT_THRESHOLD:
        return cmd
    session_id = _option(cmd, "--session-id", "native-prompt") or "native-prompt"
    safe_session = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session_id)[:100]
    sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = PROMPT_TRANSPORT_DIR / safe_session
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = out_dir / f"{stamp}-{sha[:12]}.prompt.txt"
    meta_path = out_dir / f"{stamp}-{sha[:12]}.manifest.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "session_id": session_id,
                "prompt_path": str(prompt_path),
                "sha256": sha,
                "chars": len(prompt),
                "transport": "workspace_prompt_artifact_v1",
                "argv_replacement": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    replacement = (
        "This is a transport wrapper for a formal GPT-required writing task. "
        "Read the exact user prompt from the local workspace artifact below, "
        "verify its sha256 if possible, and then execute that prompt exactly as the user request.\n\n"
        f"Prompt artifact: {prompt_path}\n"
        f"sha256: {sha}\n"
        f"chars: {len(prompt)}\n\n"
        "Do not summarize this wrapper. The artifact contents are the actual prompt."
    )
    new_cmd = list(cmd)
    new_cmd[index + 1] = replacement
    return new_cmd


def completed_from_direct_provider(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    prompt = _option(cmd, "--message")
    if not prompt:
        return subprocess.CompletedProcess(cmd, 2, "", "missing --message for direct provider compatibility call")
    model = _option(cmd, "--model")
    session_id = _option(cmd, "--session-id", "direct-provider-worker")
    agent = _option(cmd, "--agent", "worker")
    task_id = session_id or f"direct-provider-{agent}"
    task_type = f"openclaw_agent_compat_{agent}"
    try:
        result = run_direct_provider_text_prompt(
            prompt=prompt,
            task_id=task_id,
            task_type=task_type,
            model=model,
            timeout=max(30, int(timeout)),
            max_tokens=8192,
            output_dir=None,
            agent_id=agent,
        )
        response = result.get("response") or {}
        manifest = result.get("manifest") or {}
        stdout_payload: dict[str, Any] = {
            "payloads": [{"text": result.get("text") or ""}],
            "meta": {
                "agentMeta": {
                    "usage": response.get("usage"),
                    "directProvider": manifest,
                }
            },
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(stdout_payload, ensure_ascii=False), "")
    except DirectProviderError as exc:
        error = {
            "error_kind": exc.kind,
            "error_summary": str(exc),
            "output_dir": exc.output_dir,
            "detail": exc.detail,
        }
        return subprocess.CompletedProcess(cmd, 75, "", json.dumps(error, ensure_ascii=False))


def is_retryable_native_lane_busy(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode != 75:
        return False
    stderr = str(completed.stderr or "").lower()
    return "gateway_model_lane" in stderr or "lane_not_acquired" in stderr or "gateway_unreachable_before_model_call" in stderr


def run_formal_native_with_busy_retry(
    cmd: list[str],
    *,
    timeout: int,
    wait_seconds: int | None,
    text: bool,
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    attempts = max(1, NATIVE_BUSY_MAX_ATTEMPTS)
    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        completed = run_native_openclaw_model_call(
            cmd,
            timeout=timeout,
            wait_seconds=wait_seconds,
            text=text,
            capture_output=capture_output,
            check=False,
        )
        if not is_retryable_native_lane_busy(completed) or attempt >= attempts:
            return completed
        delay = min(NATIVE_BUSY_BACKOFF_MAX_SECONDS, NATIVE_BUSY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        delay += random.uniform(0, min(1.0, delay / 2))
        time.sleep(delay)
    assert completed is not None
    return completed


def run_openclaw_model_call(
    cmd: list[str],
    *,
    timeout: int,
    text: bool = True,
    capture_output: bool = True,
    check: bool = False,
    wait_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if is_openclaw_agent_command(cmd) and is_formal_gpt_required_command(cmd):
        request_prompt = _extract_prompt(cmd)
        cmd = with_prompt_artifact_transport(cmd)
        completed = run_formal_native_with_busy_retry(
            cmd,
            timeout=timeout,
            wait_seconds=wait_seconds,
            text=text,
            capture_output=capture_output,
        )
        if (
            _formal_fallback_enabled()
            and _is_formal_command_for_fallback(cmd)
            and _is_formal_usage_limit_cooldown_failure(completed)
            and request_prompt
        ):
            fallback_reason = _formal_fallback_reason(completed)
            fallback = _run_openai_compatible_fallback(
                cmd=cmd,
                timeout=timeout,
                prompt=request_prompt,
                session_id=_option(cmd, "--session-id", "formal-openai-compatible-fallback"),
                fallback_reason=fallback_reason,
            )
            if fallback is not None:
                if check and fallback.returncode != 0:
                    raise subprocess.CalledProcessError(fallback.returncode, cmd, output=fallback.stdout, stderr=fallback.stderr)
                return fallback
        if check and completed.returncode != 0:
            raise subprocess.CalledProcessError(completed.returncode, cmd, output=completed.stdout, stderr=completed.stderr)
        return completed
    completed = completed_from_direct_provider(cmd, timeout=timeout)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, output=completed.stdout, stderr=completed.stderr)
    return completed


def maybe_run_openclaw_agent_direct(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
    if is_openclaw_agent_command(cmd):
        if is_formal_gpt_required_command(cmd):
            return None
        return completed_from_direct_provider(cmd, timeout=timeout)
    return None
