#!/usr/bin/env python3
"""Compatibility layer for background scripts that still build `openclaw agent` commands.

The public shape mirrors subprocess.CompletedProcess so existing workflow code can
keep parsing OpenClaw-like JSON while the actual model call bypasses the gateway.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from direct_provider_lane import DirectProviderError, run_direct_provider_text_prompt


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
    completed = completed_from_direct_provider(cmd, timeout=timeout)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, output=completed.stdout, stderr=completed.stderr)
    return completed


def maybe_run_openclaw_agent_direct(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
    if is_openclaw_agent_command(cmd):
        return completed_from_direct_provider(cmd, timeout=timeout)
    return None
