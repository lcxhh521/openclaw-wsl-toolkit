#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[3]
RUNNER = WORKSPACE / "tools" / "claude_code_ark_runner.py"
OUT_DIR = WORKSPACE / "codex-main-bridge" / "agent-room" / "artifacts" / "claude-code-model-smoke"

DEFAULT_MODELS = [
    "glm-5.1",
    "minimax-m2.7",
    "kimi-k2.6",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-reasoner",
]

PROMPT = """你是 OpenClaw Agent Room 的 Claude Code 底层模型 smoke 测试。
不要调用工具，不要读文件，不要输出解释段落。
请只输出一个 JSON 对象：
{
  "ok": true,
  "chinese_reply": "用一句自然中文说明你能作为 Claude Code agent 候选模型参与低风险讨论。",
  "risk_note": "一句话说明你不会越权执行外部写入。"
}
"""

CST = timezone(timedelta(hours=8))


def now_cst() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def parse_last_json_line(text: str) -> dict[str, Any]:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def run_one(model: str, timeout: int, scope_dir: Path, output_root: Path, include_doubao: bool) -> dict[str, Any]:
    lower = model.lower()
    if "doubao" in lower and not include_doubao:
        return {
            "model": model,
            "skipped": True,
            "skip_reason": "doubao_family_excluded_from_regular_candidate_smoke",
            "started_at": now_cst(),
        }
    run_prefix = f"claude-model-smoke-{model.replace('/', '-')}"
    cmd = [
        sys.executable,
        str(RUNNER),
        "--scope-dir",
        str(scope_dir),
        "--brief",
        PROMPT,
        "--run-prefix",
        run_prefix,
        "--output-root",
        str(output_root),
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--expected-format",
        "strict JSON object only",
        "--timeout",
        str(timeout),
        "--bare",
        "--effort",
        "medium",
    ]
    started = time.monotonic()
    record: dict[str, Any] = {
        "model": model,
        "started_at": now_cst(),
        "command_redacted": " ".join(cmd[:cmd.index("--brief") + 1]) + " <prompt omitted> ...",
    }
    try:
        cp = subprocess.run(cmd, cwd=WORKSPACE, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 90)
        elapsed = round(time.monotonic() - started, 3)
        stdout_json = parse_last_json_line(cp.stdout)
        output_dir = Path(str(stdout_json.get("output_dir") or stdout_json.get("run_dir") or ""))
        timing = read_json(output_dir / "artifacts" / "timing.json") if output_dir.is_absolute() else {}
        parsed = read_json(output_dir / "artifacts" / "claude_stdout.parsed.json") if output_dir.is_absolute() else {}
        manifest = read_json(output_dir / "manifest.json") if output_dir.is_absolute() else {}
        manifest_effort = manifest.get("effort")
        manifest_requested_effort = manifest.get("requested_effort")
        record.update({
            "ok": cp.returncode == 0 and stdout_json.get("status") == "completed",
            "exit_code": cp.returncode,
            "elapsed_seconds": elapsed,
            "runner_status": stdout_json.get("status"),
            "output_dir": str(output_dir) if str(output_dir) else "",
            "manifest_model": manifest.get("model"),
            "manifest_provider": manifest.get("provider"),
            "manifest_effort": manifest_effort,
            "manifest_requested_effort": manifest_requested_effort,
            "manifest_effort_policy": manifest.get("effort_policy"),
            "effort_forced_to_max": manifest_effort == "max" and manifest_requested_effort == "medium",
            "runner_timing": timing,
            "parsed_kind": parsed.get("kind"),
            "parsed_confidence": parsed.get("confidence"),
            "parsed_body_preview": str(parsed.get("body") or parsed)[:500],
            "stdout_tail": cp.stdout[-1000:],
            "stderr_tail": cp.stderr[-1000:],
        })
    except subprocess.TimeoutExpired as exc:
        record.update({
            "ok": False,
            "timeout": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        })
    except Exception as exc:
        record.update({
            "ok": False,
            "exception": exc.__class__.__name__,
            "exception_message": str(exc),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    record["finished_at"] = now_cst()
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="Serialized low-cost Claude Code Ark model smoke matrix")
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--scope-dir", default=str(WORKSPACE))
    ap.add_argument("--include-doubao", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    result_path = OUT_DIR / f"matrix-{stamp}.json"
    jsonl_path = OUT_DIR / f"matrix-{stamp}.jsonl"
    output_root = OUT_DIR / "coding-runs"
    output_root.mkdir(parents=True, exist_ok=True)
    scope_dir = Path(args.scope_dir).expanduser().resolve()

    records = []
    for model in args.models:
        rec = run_one(str(model), args.timeout, scope_dir, output_root, bool(args.include_doubao))
        records.append(rec)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "schema": "openclaw.agent_room.claude_code_model_smoke.v0",
        "created_at": now_cst(),
        "scope": "Claude Code agent Ark model selection only; no source edit; no Telegram send; no external write except model API calls and local artifacts.",
        "effort_probe": "Each run intentionally invokes the runner with --effort medium; a passing runner manifest should record effort=max, requested_effort=medium, and the force_max reasoning policy.",
        "models": list(args.models),
        "include_doubao": bool(args.include_doubao),
        "result_jsonl": str(jsonl_path),
        "records": records,
    }
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "result_path": str(result_path), "jsonl_path": str(jsonl_path), "models": len(records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
