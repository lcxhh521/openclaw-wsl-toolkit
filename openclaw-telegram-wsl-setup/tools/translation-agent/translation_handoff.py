#!/usr/bin/env python3
"""Create a file-based translation-agent handoff package.

This helper is intentionally simple: it preserves Alex's request verbatim,
creates a mechanical handoff brief, and writes a main-owned acceptance plan.
It does not call a model or decide task scope.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-._")
    return text[:80] or "translation-run"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", help="Explicit run id. Defaults to timestamp + slug.")
    ap.add_argument("--title", default="translation-task")
    ap.add_argument("--request-file", required=True, help="File containing Alex's original request verbatim")
    ap.add_argument("--source", action="append", default=[], help="Source path/identifier; repeatable")
    ap.add_argument("--output-root", default="/home/lcxhh/.openclaw/workspace/translation-runs")
    args = ap.parse_args()

    request_path = Path(args.request_file).expanduser().resolve()
    request_text = request_path.read_text(encoding="utf-8")
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"{stamp}-{slugify(args.title)}"
    run_dir = Path(args.output_root).expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    user_request = run_dir / "user_request.md"
    handoff = run_dir / "handoff_brief.md"
    ledger = run_dir / "task_ledger.json"
    acceptance = run_dir / "acceptance_plan.json"

    user_request.write_text(request_text, encoding="utf-8")
    source_lines = "\n".join(f"- {s}" for s in args.source) or "- [not specified in helper args; see user_request.md]"
    handoff.write_text(
        f"""# Translation Agent Handoff Brief\n\nRun id: `{run_id}`\n\n## Non-negotiable isolation rules\n\n- Read `user_request.md` as the authoritative Alex request.\n- Do not narrow, summarize, reinterpret, or downgrade the request.\n- If a source/range/format decision is missing, write `needs_alex_decision` in the final envelope instead of inventing scope.\n- Write artifacts under this run directory.\n- Final chat response must be only the JSON envelope described in `TRANSLATION_AGENT_ISOLATION_PROTOCOL.md`.\n- Your `status: candidate_ready` is not final delivery; main will independently verify artifacts.\n\n## Source identifiers\n\n{source_lines}\n\n## Original user request\n\nSee `user_request.md` in this run directory.\n\n## Required final response envelope\n\n```json\n{{\n  \"status\": \"candidate_ready | blocked | failed\",\n  \"run_id\": \"{run_id}\",\n  \"manifest\": \"{run_dir}/manifest.json\",\n  \"artifacts\": [],\n  \"claims\": [],\n  \"needs_main_verification\": true,\n  \"needs_alex_decision\": []\n}}\n```\n""",
        encoding="utf-8",
    )
    ledger.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "owner": "main",
                "status": "dispatched_pending",
                "translation_worker_status": "not_started",
                "other_active_workstreams": [],
                "notes": ["Main owns acceptance; translation worker claims are candidate only."],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    acceptance.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "required_before_main_success": [
                    "manifest exists and names source/ranges/models/artifacts",
                    "expected artifacts exist and pass byte/marker gate",
                    "scope coverage checked against user_request.md",
                    "source/version boundary checked",
                    "long-document coverage audit and repair complete if applicable",
                    "PDF/layout final verification complete if applicable",
                    "translation worker final envelope parsed; no long narrative accepted as final",
                ],
                "recommended_tool": "tools/translation_artifact_gate.py",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "handoff": str(handoff)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
