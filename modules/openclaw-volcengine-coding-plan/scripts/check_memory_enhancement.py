#!/usr/bin/env python3
"""Check Ark Coding Plan memory-enhancement / embedding wiring for OpenClaw.

This script is intentionally local-first and secret-safe:
- It reads OpenClaw config and validates `agents.defaults.memorySearch`.
- It never prints API keys or embedding vector values.
- Optional `--live` sends only a benign smoke string to the configured embedding
  endpoint and reports HTTP status + vector dimension.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

EXPECTED_MODEL = "doubao-embedding-vision"
EXPECTED_BASE_SUFFIX = "/api/coding/v3"
SMOKE_TEXT = "OpenClaw Ark Coding Plan memory enhancement smoke probe. No private data."
SECRET_FILES = [Path.home() / ".openclaw" / "secrets" / "volcengine.env"]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(token in key.lower() for token in ("key", "token", "secret", "password", "auth")):
                out[key] = "<redacted>" if item else item
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(f"Config not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"Config root must be an object: {path}")
    return data


def get_memory_search(config: dict[str, Any]) -> dict[str, Any]:
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    memory_search = defaults.get("memorySearch") if isinstance(defaults.get("memorySearch"), dict) else {}
    return memory_search


def load_secret_env_value(name: str) -> str:
    """Resolve a secret by env name without printing or exporting it."""
    if os.environ.get(name):
        return str(os.environ[name])
    for path in SECRET_FILES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != name:
                continue
            value = value.strip().strip('"').strip("'")
            return value
    return ""


def resolve_api_key(api_key_config: Any) -> tuple[str, dict[str, Any]]:
    """Resolve OpenClaw secret-ref or direct API key config.

    OpenClaw config may store secrets as objects like
    `{ "source": "env", "provider": "default", "id": "VOLCANO_ENGINE_API_KEY" }`.
    The live probe needs the concrete key, but diagnostics must only expose
    metadata, never the value.
    """
    if isinstance(api_key_config, dict):
        source = str(api_key_config.get("source") or "")
        key_id = str(api_key_config.get("id") or "")
        provider = str(api_key_config.get("provider") or "")
        resolved = load_secret_env_value(key_id) if source == "env" and key_id else ""
        return resolved, {
            "kind": "secret_ref",
            "source": source,
            "provider": provider,
            "id": key_id,
            "resolved": bool(resolved),
        }
    if isinstance(api_key_config, str):
        value = api_key_config.strip()
        if value.startswith("${") and value.endswith("}"):
            key_id = value[2:-1]
            resolved = load_secret_env_value(key_id)
            return resolved, {"kind": "env_placeholder", "id": key_id, "resolved": bool(resolved)}
        if value.startswith("$"):
            key_id = value[1:]
            resolved = load_secret_env_value(key_id)
            return resolved, {"kind": "env_placeholder", "id": key_id, "resolved": bool(resolved)}
        return value, {"kind": "literal", "resolved": bool(value)}
    return "", {"kind": type(api_key_config).__name__, "resolved": False}


def validate_memory_search(memory_search: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    remote = memory_search.get("remote") if isinstance(memory_search.get("remote"), dict) else {}
    base_url = str(remote.get("baseUrl") or "").rstrip("/")
    api_key, api_key_meta = resolve_api_key(remote.get("apiKey"))
    provider = str(memory_search.get("provider") or "")
    model = str(memory_search.get("model") or "")
    enabled = bool(memory_search.get("enabled"))
    checks = [
        {
            "id": "memorySearch_present",
            "ok": bool(memory_search),
            "detail": "agents.defaults.memorySearch exists",
        },
        {
            "id": "enabled_true",
            "ok": enabled,
            "detail": "memorySearch.enabled is true",
            "actual": enabled,
        },
        {
            "id": "provider_openai_compat",
            "ok": provider == "openai",
            "detail": "OpenClaw uses OpenAI-compatible provider for Ark embedding endpoint",
            "actual": provider,
        },
        {
            "id": "model_doubao_embedding_vision",
            "ok": model == EXPECTED_MODEL,
            "detail": "memorySearch.model is the Coding Plan memory-enhancement embedding model",
            "actual": model,
        },
        {
            "id": "base_url_coding_plan",
            "ok": base_url.endswith(EXPECTED_BASE_SUFFIX),
            "detail": "memorySearch.remote.baseUrl points at the Coding Plan endpoint",
            "actual": base_url,
        },
        {
            "id": "api_key_present",
            "ok": bool(api_key) and api_key not in {"***", "<ARK_API_KEY>", "<redacted>"},
            "detail": "memorySearch.remote.apiKey is present (not printed)",
            "source": api_key_meta,
        },
    ]
    summary = {
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "baseUrl": base_url,
        "fallback": memory_search.get("fallback"),
        "apiKeyPresent": bool(api_key) and api_key not in {"***", "<ARK_API_KEY>", "<redacted>"},
        "apiKeySource": api_key_meta,
    }
    return checks, summary


def call_embedding(base_url: str, api_key: str, model: str, timeout: int) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/embeddings"
    body = {
        "model": model,
        "input": SMOKE_TEXT,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            elapsed = round(time.time() - started, 3)
            data = json.loads(raw)
            embedding = (((data.get("data") or [{}])[0] or {}).get("embedding") or [])
            return {
                "ok": True,
                "httpStatus": resp.status,
                "elapsedSec": elapsed,
                "endpoint": endpoint,
                "model": data.get("model") or model,
                "vectorDimension": len(embedding) if isinstance(embedding, list) else None,
                "usage": data.get("usage"),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            error = json.loads(raw)
        except Exception:
            error = raw[:1000]
        return {
            "ok": False,
            "httpStatus": exc.code,
            "elapsedSec": round(time.time() - started, 3),
            "endpoint": endpoint,
            "error": error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "httpStatus": None,
            "elapsedSec": round(time.time() - started, 3),
            "endpoint": endpoint,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OpenClaw Ark Coding Plan memorySearch embedding wiring")
    parser.add_argument("--config", default=str(Path.home() / ".openclaw" / "openclaw.json"))
    parser.add_argument("--live", action="store_true", help="Run a benign live embedding probe against the configured endpoint")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    config_path = Path(os.path.expanduser(args.config))
    config = load_json(config_path)
    memory_search = get_memory_search(config)
    checks, summary = validate_memory_search(memory_search)
    remote = memory_search.get("remote") if isinstance(memory_search.get("remote"), dict) else {}
    resolved_api_key, _api_key_meta = resolve_api_key(remote.get("apiKey"))
    live_result = None
    if args.live:
        if not summary.get("apiKeyPresent"):
            live_result = {"ok": False, "skipped": True, "reason": "missing_or_placeholder_api_key"}
        elif not summary.get("baseUrl") or not summary.get("model"):
            live_result = {"ok": False, "skipped": True, "reason": "missing_base_url_or_model"}
        else:
            live_result = call_embedding(
                str(remote.get("baseUrl") or ""),
                resolved_api_key,
                str(memory_search.get("model") or EXPECTED_MODEL),
                args.timeout,
            )

    result = {
        "schema": "openclaw.volcengine_coding_plan.memory_enhancement_check.v0",
        "configPath": str(config_path),
        "summary": summary,
        "checks": checks,
        "configExcerpt": redact({"agents": {"defaults": {"memorySearch": memory_search}}}),
        "liveEmbeddingProbe": live_result,
        "overallOk": all(check.get("ok") for check in checks) and (live_result is None or bool(live_result.get("ok"))),
        "notes": [
            "This script does not print API keys or embedding vector values.",
            "A failed live probe means the endpoint/auth/model needs investigation; a passed config probe only verifies wiring.",
        ],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"config: {result['configPath']}")
        print(f"memorySearch: enabled={summary.get('enabled')} provider={summary.get('provider')} model={summary.get('model')} baseUrl={summary.get('baseUrl')}")
        for check in checks:
            print(("OK" if check.get("ok") else "FAIL") + f" {check['id']}: {check['detail']}")
        if live_result is not None:
            print("liveEmbeddingProbe:", json.dumps(redact(live_result), ensure_ascii=False))
        print("overallOk:", result["overallOk"])
    return 0 if result["overallOk"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
