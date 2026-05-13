#!/usr/bin/env python3
"""Compatibility layer for background scripts that still build `openclaw agent` commands.

The public shape mirrors subprocess.CompletedProcess so existing workflow code can
keep parsing OpenClaw-like JSON while the actual model call bypasses the gateway.
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from direct_provider_lane import DirectProviderError, run_direct_provider_text_prompt
from gateway_model_lane import run_openclaw_model_call as run_native_openclaw_model_call

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
PROMPT_TRANSPORT_DIR = WORKSPACE / "model-inputs" / "native-prompt-transport"
PROMPT_TRANSPORT_THRESHOLD = int(os.environ.get("OPENCLAW_NATIVE_PROMPT_FILE_THRESHOLD_CHARS") or "60000")


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
        cmd = with_prompt_artifact_transport(cmd)
        completed = run_native_openclaw_model_call(
            cmd,
            timeout=timeout,
            wait_seconds=wait_seconds,
            text=text,
            capture_output=capture_output,
            check=check,
        )
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
