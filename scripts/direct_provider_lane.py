#!/usr/bin/env python3
"""Small Python API for direct-provider background model calls.

This module bypasses the OpenClaw gateway. It is for worker tasks that only need
an OpenAI-compatible chat completion and will hand artifacts back to main for
review.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from direct_provider_worker import (  # noqa: E402
    ProviderProfile,
    WorkerError,
    call_chat_completions,
    extract_text,
    write_json,
    utc_now,
)
from task_router_core import route_task

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(WORKSPACE / "codex-main-bridge")))
QUOTA_LEDGER_TOOL = ROOT / "agent-room" / "tools" / "quota_ledger.py"
_quota_func = None
if QUOTA_LEDGER_TOOL.exists():
    sys.path.insert(0, str(QUOTA_LEDGER_TOOL.parent))
    try:
        from quota_ledger import update_quota_ledger_from_headers as _quota_func
    except Exception:
        pass

DEFAULT_OUTPUT_ROOT = WORKSPACE / "worker-runs"
DEFAULT_ARK_MODEL = os.environ.get("OPENCLAW_DIRECT_PROVIDER_DEFAULT_MODEL", "kimi-k2.6")
ARK_BASE_URL = os.environ.get("ARK_CODING_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
OPENAI_COMPATIBLE_BASE_URL = os.environ.get("OPENCLAW_WORKER_BASE_URL", "https://api.deepseek.com/v1")
SECRET_FILES = [
    Path.home() / ".openclaw" / "secrets" / "agent-room-deepseek.env",
    Path.home() / ".openclaw" / "secrets" / "agent-room-openai.env",
    Path.home() / ".openclaw" / ".env",
]


class DirectProviderError(RuntimeError):
    def __init__(self, kind: str, message: str, *, detail: Any = None, output_dir: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.output_dir = output_dir


def normalize_direct_model(model: str | None, *, fallback: str = DEFAULT_ARK_MODEL) -> str:
    value = (model or "").strip()
    if not value:
        return fallback
    for prefix in ("volcengine-plan/", "volcengine/", "ark/"):
        if value.startswith(prefix):
            return value.split("/", 1)[1]
    # OpenClaw's Codex aliases are gateway/provider-adapter names, not Ark ids.
    if value.startswith("openai-codex/"):
        return fallback
    return value


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(raw[start : end + 1])
        if isinstance(value, dict):
            return value
    raise DirectProviderError("invalid_json", "direct provider returned non-JSON output", detail=raw[-1200:])


def _resolve_secret_config(env_name: str) -> str:
    """Resolve API/config values from workspace secret files when env is unset."""
    value = os.environ.get(env_name, "")
    if value:
        return value
    for path in SECRET_FILES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            key, val = line.split("=", 1)
            if key.strip() == env_name:
                return val.strip().strip('"').strip("'")
    return ""


def resolve_direct_provider_profile(profile_name: str, model: str) -> ProviderProfile:
    """Build provider profile for direct worker calls."""
    selected = (profile_name or "ark-coding-plan").strip().lower()
    if selected not in {"ark-coding-plan", "openai-compatible"}:
        raise DirectProviderError("provider_profile", f"Unknown provider profile: {profile_name}")

    if selected == "openai-compatible":
        base_url = (_resolve_secret_config("OPENCLAW_WORKER_BASE_URL") or OPENAI_COMPATIBLE_BASE_URL).rstrip("/")
        api_key_env = os.environ.get("OPENCLAW_WORKER_API_KEY_ENV", "OPENCLAW_WORKER_API_KEY")
        api_key_env = _resolve_secret_config("OPENCLAW_WORKER_API_KEY_ENV") or api_key_env
        direct_model = normalize_direct_model(model)
        normalized = direct_model.lower()
        if normalized == "deepseek-v3.2":
            direct_model = "deepseek-v4-pro"
        return ProviderProfile(
            name="openai-compatible",
            base_url=base_url,
            api_key_env=api_key_env,
            default_model=direct_model,
        )

    return ProviderProfile(
        name="ark-coding-plan",
        base_url=ARK_BASE_URL.rstrip("/"),
        api_key_env="VOLCANO_ENGINE_API_KEY",
        default_model=normalize_direct_model(model),
    )


def run_direct_provider_text_prompt(
    *,
    prompt: str,
    task_id: str,
    task_type: str,
    model: str | None = None,
    provider_profile: str = "ark-coding-plan",
    system: str = "",
    output_dir: str | Path | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    timeout: int = 180,
    agent_id: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOT / task_id
    output.mkdir(parents=True, exist_ok=True)
    direct_model = normalize_direct_model(model, fallback=DEFAULT_ARK_MODEL)
    try:
        router_decision = route_task(
            text=prompt or task_type,
            task_type=task_type,
            expected_seconds=timeout,
            model_calls=1,
            external_side_effect=False,
            needs_openclaw_native=False,
        )
    except Exception as exc:  # noqa: BLE001 - routing metadata must not block worker execution
        router_decision = {"schema": "openclaw.task_router.v0", "error": str(exc)[:300]}
    profile = resolve_direct_provider_profile(provider_profile, direct_model)
    if profile.name == "openai-compatible" and not profile.base_url:
        raise DirectProviderError("missing_base_url", "OpenAI-compatible base URL is not configured")
    manifest: dict[str, Any] = {
        "task_id": task_id,
        "task_type": task_type,
        "status": "running",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "gateway_used": False,
        "openclaw_gateway_used": False,
        "provider_profile": profile.name,
        "base_url_host": profile.base_url.split("//")[-1].split("/")[0],
        "model": direct_model,
        "agent_id": agent_id or None,
        "prompt_chars": len(prompt or ""),
        "system_chars": len(system or ""),
        "max_tokens_requested": max_tokens if max_tokens and max_tokens > 0 else None,
        "router_decision": router_decision,
        "outputs": {
            "manifest": str(output / "manifest.json"),
            "result": str(output / "result.md"),
            "response": str(output / "response.json"),
            "error": str(output / "error.json"),
        },
    }
    write_json(output / "manifest.json", manifest)
    started = time.time()
    try:
        response, elapsed = call_chat_completions(
            profile,
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            quota_agent_id=agent_id or "direct-provider",
        )
        text = extract_text(response)
        # Wire quota headers to quota_ledger for remaining-quota visibility
        if _quota_func is not None:
            worker_data = response.get("_worker", {})
            headers = worker_data.get("response_headers") or worker_data.get("headers") or {}
            if headers:
                try:
                    _quota_func(direct_model, headers, agent_id=agent_id or "direct-provider")
                except Exception:
                    pass  # Non-fatal: quota tracking must not break worker execution
        (output / "result.md").write_text(text + ("\n" if text else ""), encoding="utf-8")
        write_json(output / "response.json", response)
        manifest.update(
            {
                "status": "succeeded",
                "updated_at": utc_now(),
                "elapsed_sec": round(elapsed, 3),
                "result_chars": len(text),
            }
        )
        write_json(output / "manifest.json", manifest)
        return {"text": text, "manifest": manifest, "response": response, "output_dir": str(output)}
    except WorkerError as exc:
        error = {"kind": exc.kind, "message": str(exc), "status": exc.status, "detail": exc.detail, "at": utc_now()}
        write_json(output / "error.json", error)
        manifest.update({"status": "failed", "updated_at": utc_now(), "error_kind": exc.kind, "error_summary": str(exc)})
        write_json(output / "manifest.json", manifest)
        raise DirectProviderError(exc.kind, str(exc), detail=error, output_dir=str(output)) from exc
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - started
        error = {"kind": "direct_provider_error", "message": str(exc), "at": utc_now(), "elapsed_sec": round(elapsed, 3)}
        write_json(output / "error.json", error)
        manifest.update({"status": "failed", "updated_at": utc_now(), "error_kind": error["kind"], "error_summary": str(exc)})
        write_json(output / "manifest.json", manifest)
        raise DirectProviderError("direct_provider_error", str(exc), detail=error, output_dir=str(output)) from exc


def run_direct_provider_json_prompt(
    *,
    prompt: str,
    task_id: str,
    task_type: str,
    model: str | None = None,
    provider_profile: str = "ark-coding-plan",
    system: str = "",
    output_dir: str | Path | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    timeout: int = 180,
    agent_id: str | None = None,
) -> dict[str, Any]:
    result = run_direct_provider_text_prompt(
        prompt=prompt,
        task_id=task_id,
        task_type=task_type,
        model=model,
        provider_profile=provider_profile,
        system=system,
        output_dir=output_dir,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        agent_id=agent_id,
    )
    payload = extract_json_object(result["text"])
    payload["_direct_provider"] = {
        "gateway_used": False,
        "model": (result.get("manifest") or {}).get("model"),
        "output_dir": result.get("output_dir"),
        "usage": (result.get("response") or {}).get("usage"),
    }
    return payload
