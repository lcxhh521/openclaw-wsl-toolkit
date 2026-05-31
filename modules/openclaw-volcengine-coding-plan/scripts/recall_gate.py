#!/usr/bin/env python3
"""Recall reliability gate for OpenClaw memory-enhancement diagnostics.

This is intentionally conservative:
1. Always run local exact-string fallback first for IDs/paths/error codes/markers.
2. Inspect local SQLite index freshness without remote calls.
3. Optionally run `openclaw memory search` only when `--semantic` is passed.

Default mode is local-only and safe for private memory files.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace"
SCRIPT_DIR = Path(__file__).resolve().parent
EXACT = SCRIPT_DIR / "exact_recall_fallback.py"
LOCAL = SCRIPT_DIR / "recall_smoke_local.py"


def run_json(cmd: list[str], allow_nonzero: bool = True, timeout: int = 60) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    started = time.time()
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    meta = {
        "cmd": [cmd[0], *cmd[1:]],
        "returncode": proc.returncode,
        "elapsedSec": round(time.time() - started, 3),
        "stderrPreview": proc.stderr[-2000:],
    }
    if proc.returncode != 0 and not allow_nonzero:
        return None, meta
    try:
        return json.loads(proc.stdout or "null"), meta
    except Exception as exc:
        meta["jsonError"] = f"{type(exc).__name__}: {exc}"
        meta["stdoutPreview"] = proc.stdout[-2000:]
        return None, meta


def semantic_search(query: str, agent: str | None, max_results: int, min_score: float | None, timeout: int) -> tuple[Any, dict[str, Any]]:
    cmd = ["openclaw", "memory", "search", query, "--json", "--max-results", str(max_results)]
    if agent:
        cmd += ["--agent", agent]
    if min_score is not None:
        cmd += ["--min-score", str(min_score)]
    data, meta = run_json(cmd, allow_nonzero=True, timeout=timeout)
    return data, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-first recall gate for OpenClaw memory diagnostics")
    parser.add_argument("query", help="Exact token or natural-language query")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--semantic", action="store_true", help="Also run openclaw memory search (may call remote embedding provider)")
    parser.add_argument("--semantic-min-score", type=float, default=None)
    parser.add_argument("--semantic-max-results", type=int, default=5)
    parser.add_argument("--target-path-like", default=None)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    exact_data, exact_meta = run_json([str(EXACT), args.query, "--json"], allow_nonzero=True, timeout=args.timeout)
    local_data, local_meta = run_json([
        str(LOCAL), "--term", args.query, "--json",
        *( ["--target-path-like", args.target_path_like] if args.target_path_like else [] ),
    ], allow_nonzero=True, timeout=args.timeout)

    semantic_data = None
    semantic_meta = None
    if args.semantic:
        semantic_data, semantic_meta = semantic_search(
            args.query,
            args.agent,
            args.semantic_max_results,
            args.semantic_min_score,
            args.timeout,
        )

    exact_hits = (exact_data or {}).get("hitCount") or 0
    file_hits = ((local_data or {}).get("summary") or {}).get("fileHitCount") or 0
    db_hits = ((local_data or {}).get("summary") or {}).get("dbChunkHitCount") or 0
    semantic_hits = len(semantic_data) if isinstance(semantic_data, list) else None
    result = {
        "schema": "openclaw.recall_gate.v0",
        "generatedAt": int(time.time()),
        "query": args.query,
        "mode": "local+semantic" if args.semantic else "local-only",
        "summary": {
            "exactFallbackHits": exact_hits,
            "fileHits": file_hits,
            "dbChunkHits": db_hits,
            "semanticHits": semantic_hits,
            "localExactSatisfied": exact_hits > 0 or file_hits > 0,
            "indexLikelyFreshForTerm": db_hits > 0,
            "semanticRan": args.semantic,
        },
        "exactFallback": exact_data,
        "exactFallbackMeta": exact_meta,
        "localIndexProbe": local_data,
        "localIndexProbeMeta": local_meta,
        "semanticSearch": semantic_data,
        "semanticSearchMeta": semantic_meta,
        "decisionHints": [
            "If localExactSatisfied=true but semanticHits=0, do not claim the fact is absent; cite local artifact/file evidence.",
            "If fileHits>0 but dbChunkHits=0, treat as index freshness/coverage gap.",
            "Run with --semantic only when remote embedding calls are acceptable for the query.",
        ],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["localExactSatisfied"] or (semantic_hits or 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
