#!/usr/bin/env python3
"""Direct provider worker for heavy background tasks.

This helper intentionally bypasses the OpenClaw gateway. Use it for bounded
worker jobs such as translation chunks, summaries, format conversion checks, or
other artifact-producing tasks where the main OpenClaw agent only needs to
review the final manifest/result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(WORKSPACE / "codex-main-bridge")))
QUOTA_LEDGER_TOOL = ROOT / "agent-room" / "tools" / "quota_ledger.py"
if QUOTA_LEDGER_TOOL.exists():
    sys.path.insert(0, str(QUOTA_LEDGER_TOOL.parent))
    try:
        from quota_ledger import update_quota_ledger_from_headers, update_quota_ledger_from_usage
    except Exception:
        update_quota_ledger_from_headers = None
        update_quota_ledger_from_usage = None
else:
    update_quota_ledger_from_headers = None
    update_quota_ledger_from_usage = None
DEFAULT_OUTPUT_ROOT = WORKSPACE / "worker-runs"
SECRET_FILES = [
    Path.home() / ".openclaw" / "secrets" / "agent-room-deepseek.env",
    Path.home() / ".openclaw" / "secrets" / "agent-room-openai.env",
    Path.home() / ".openclaw" / "secrets" / "volcengine.env",
    Path.home() / ".openclaw" / ".env",
]


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    api_key_env: str
    default_model: str
    endpoint_path: str = "/chat/completions"


PROFILES: dict[str, ProviderProfile] = {
    "ark-coding-plan": ProviderProfile(
        name="ark-coding-plan",
        base_url=os.environ.get("ARK_CODING_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
        api_key_env="VOLCANO_ENGINE_API_KEY",
        default_model=os.environ.get("OPENCLAW_WORKER_MODEL", "kimi-k2.6"),
    ),
    "openai-compatible": ProviderProfile(
        name="openai-compatible",
        base_url=os.environ.get("OPENCLAW_WORKER_BASE_URL", ""),
        api_key_env=os.environ.get("OPENCLAW_WORKER_API_KEY_ENV", "OPENCLAW_WORKER_API_KEY"),
        default_model=os.environ.get("OPENCLAW_WORKER_MODEL", ""),
    ),
}


class WorkerError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text_file(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def build_prompt(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.contract_file:
        parts.append("# Task Contract\n" + read_text_file(args.contract_file).strip())
    if args.prompt:
        parts.append(str(args.prompt).strip())
    if args.input_file:
        parts.append("# Input\n" + read_text_file(args.input_file).strip())
    if not parts and not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            parts.append(stdin_text)
    prompt = "\n\n".join(part for part in parts if part)
    if not prompt:
        raise WorkerError("empty_prompt", "No prompt, input file, contract file, or stdin was provided")
    return prompt


def resolve_profile(args: argparse.Namespace) -> ProviderProfile:
    if args.profile not in PROFILES:
        raise WorkerError("unknown_provider_profile", f"Unknown provider profile: {args.profile}")
    profile = PROFILES[args.profile]
    if profile.name == "openai-compatible":
        base_url = (args.base_url or profile.base_url or _resolve_secret_config("OPENCLAW_WORKER_BASE_URL") or "").rstrip("/")
        api_key_env = args.api_key_env or _resolve_secret_config("OPENCLAW_WORKER_API_KEY_ENV") or profile.api_key_env
        model = args.model or profile.default_model or _resolve_secret_config("OPENCLAW_WORKER_MODEL")
    else:
        base_url = (args.base_url or profile.base_url or "").rstrip("/")
        api_key_env = args.api_key_env or profile.api_key_env
        model = args.model or profile.default_model
    if not base_url:
        raise WorkerError("missing_base_url", "Provider base URL is not configured")
    if not model:
        raise WorkerError("missing_model", "Provider model is not configured")
    return ProviderProfile(profile.name, base_url, api_key_env, model, profile.endpoint_path)


def _resolve_secret_config(env_name: str) -> str:
    """Resolve non-secret provider metadata from local secret env files.

    The external DeepSeek fallback is intentionally installed outside the repo
    in ~/.openclaw/secrets/agent-room-deepseek.env. Loading base URL/model/env
    metadata from that file lets operators paste only the key once and then run
    the generic openai-compatible worker without manually exporting variables.
    """
    value = os.environ.get(env_name)
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


def redact_error_detail(detail: Any) -> Any:
    if isinstance(detail, str):
        return detail.replace(os.environ.get("OPENCLAW_WORKER_API_KEY", "__NO_KEY__"), "[REDACTED]")
    if isinstance(detail, dict):
        redacted: dict[str, Any] = {}
        for key, value in detail.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("key", "token", "secret", "authorization", "password")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_error_detail(value)
        return redacted
    if isinstance(detail, list):
        return [redact_error_detail(item) for item in detail]
    return detail


def _resolve_api_key(env_name: str) -> str:
    """Resolve API key from env var, falling back to secret files.

    Mirrors the key-resolution logic in
    modules/openclaw-volcengine-coding-plan/scripts/check_memory_enhancement.py
    so that the worker works when the key is in
    ~/.openclaw/secrets/volcengine.env but not in the process environment.
    """
    value = os.environ.get(env_name)
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
            key, val = line.split("=", 1)
            if key.strip() == env_name:
                return val.strip().strip('"').strip("'")
    return ""


def call_chat_completions(
    profile: ProviderProfile,
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
    extra_body: dict[str, Any] | None = None,
    quota_agent_id: str | None = None,
) -> tuple[dict[str, Any], float]:
    api_key = _resolve_api_key(profile.api_key_env)
    if not api_key:
        raise WorkerError("missing_api_key", f"Missing provider API key env: {profile.api_key_env} (checked env var and secret files)")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict[str, Any] = {
        "model": profile.default_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens and max_tokens > 0:
        body["max_tokens"] = max_tokens
    if extra_body:
        body.update(extra_body)
    request = urllib.request.Request(
        profile.base_url + profile.endpoint_path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            data = json.loads(raw)
            elapsed = time.time() - start
            data.setdefault("_worker", {})
            # Capture HTTP response headers for quota/rate-limit tracking
            response_headers = {}
            if hasattr(response, "headers") and response.headers:
                for header_key in response.headers:
                    response_headers[header_key.lower()] = response.headers[header_key]
            elif hasattr(response, "info"):
                info = response.info()
                if hasattr(info, "items"):
                    for k, v in info.items():
                        response_headers[k.lower()] = v
            data["_worker"].update({
                "ok": True,
                "elapsed_sec": round(elapsed, 3),
                "http_status": response.status,
                "response_headers": response_headers,
            })
            return data, elapsed
    except urllib.error.HTTPError as exc:
        if update_quota_ledger_from_headers is not None and exc.headers:
            try:
                update_quota_ledger_from_headers(profile.default_model, exc.headers, agent_id=quota_agent_id)
            except Exception:
                pass
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail: Any = json.loads(raw)
        except Exception:
            detail = raw
        raise WorkerError("provider_http_error", f"Provider HTTP {exc.code}", status=exc.code, detail=detail) from exc
    except TimeoutError as exc:
        raise WorkerError("provider_timeout", f"Provider call timed out after {timeout}s") from exc
    except urllib.error.URLError as exc:
        raise WorkerError("provider_network_error", str(exc.reason)) from exc


def extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(output_dir / "manifest.json", manifest)


def run(args: argparse.Namespace) -> int:
    prompt = build_prompt(args)
    system = args.system or read_text_file(args.system_file).strip()
    profile = resolve_profile(args)
    task_id = args.task_id or f"direct-provider-{int(time.time())}"
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "task_id": task_id,
        "task_type": args.task_type,
        "status": "dry_run" if args.dry_run else "running",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "gateway_used": False,
        "openclaw_gateway_used": False,
        "provider_profile": profile.name,
        "base_url_host": profile.base_url.split("//")[-1].split("/")[0],
        "model": profile.default_model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "system_chars": len(system),
        "max_tokens_requested": args.max_tokens if args.max_tokens and args.max_tokens > 0 else None,
        "input_file": args.input_file or "",
        "contract_file": args.contract_file or "",
        "outputs": {
            "manifest": str(output_dir / "manifest.json"),
            "result": str(output_dir / "result.md"),
            "response": str(output_dir / "response.json"),
            "error": str(output_dir / "error.json"),
        },
    }

    if args.dry_run:
        write_manifest(output_dir, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    write_manifest(output_dir, manifest)
    try:
        response, elapsed = call_chat_completions(
            profile,
            prompt,
            system=system,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
            quota_agent_id=getattr(args, "agent_id", "direct-provider"),
        )
        # Wire quota headers to quota_ledger for remaining-quota visibility
        if update_quota_ledger_from_headers is not None:
            worker_data = response.get("_worker", {})
            headers = worker_data.get("response_headers") or worker_data.get("headers") or {}
            if headers:
                try:
                    update_quota_ledger_from_headers(profile.default_model, headers, agent_id=getattr(args, "agent_id", "direct-provider"))
                except Exception:
                    pass  # Non-fatal: quota tracking must not break worker execution
        if update_quota_ledger_from_usage is not None:
            try:
                update_quota_ledger_from_usage(profile.default_model, response.get("usage") or {})
            except Exception:
                pass  # Non-fatal: quota tracking must not break worker execution

        result_text = extract_text(response)
        (output_dir / "result.md").write_text(result_text + ("\n" if result_text else ""), encoding="utf-8")
        write_json(output_dir / "response.json", response)
        manifest.update(
            {
                "status": "succeeded",
                "updated_at": utc_now(),
                "elapsed_sec": round(elapsed, 3),
                "result_chars": len(result_text),
            }
        )
        write_manifest(output_dir, manifest)
        if args.print_result:
            print(result_text)
        else:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except WorkerError as exc:
        error = {
            "kind": exc.kind,
            "message": str(exc),
            "status": exc.status,
            "detail": redact_error_detail(exc.detail),
            "at": utc_now(),
        }
        write_json(output_dir / "error.json", error)
        manifest.update({"status": "failed", "updated_at": utc_now(), "error_kind": exc.kind, "error_summary": str(exc)})
        write_manifest(output_dir, manifest)
        print(json.dumps({"manifest": manifest, "error": error}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 75 if exc.kind in {"provider_timeout", "provider_network_error", "missing_api_key"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a background worker directly against an OpenAI-compatible provider")
    parser.add_argument("--profile", default="ark-coding-plan", choices=sorted(PROFILES))
    parser.add_argument("--base-url", help="Override provider base URL")
    parser.add_argument("--api-key-env", help="Override env var name used for provider API key")
    parser.add_argument("--model", help="Model id; defaults to profile/env model")
    parser.add_argument("--task-id")
    parser.add_argument("--task-type", default="generic_worker")
    parser.add_argument("--agent-id", default="direct-provider", help="Agent id used when projecting quota headers into model_quota_signal.json")
    parser.add_argument("--contract-file")
    parser.add_argument("--input-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--prompt")
    parser.add_argument("--system")
    parser.add_argument("--system-file")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true", help="Write manifest only; do not call provider")
    parser.add_argument("--print-result", action="store_true")
    args = parser.parse_args()
    try:
        return run(args)
    except WorkerError as exc:
        print(json.dumps({"error_kind": exc.kind, "error_summary": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
