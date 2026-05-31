#!/usr/bin/env python3
"""Main-facing Translation Agent entry V0.

Creates a durable `translation-runs/<run-id>/` package from Alex's original
request without invoking a model. This is the real entry/handoff boundary main
should use before dispatching a dedicated translation worker:

- preserve Alex's request verbatim in `user_request.md`;
- write a mechanical `handoff_brief.md` and `dispatch_prompt.md`;
- write main-owned ledger/acceptance/delivery/model-selection records;
- emit compact review/Telegram-summary artifacts for the foreground assistant.

The helper intentionally does **not** translate, summarize, narrow, or choose a
model for the worker. It prepares an auditable handoff and marks the run as
`entry_created_pending_translation_agent` until a worker produces artifacts and
main runs the artifact gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = WORKSPACE / "translation-runs"
CONTRACT_DOC = WORKSPACE / "openclaw-telegram-wsl-setup/docs/translation-agent-contract.md"
ISOLATION_DOC = WORKSPACE / "openclaw-telegram-wsl-setup/docs/translation-agent-isolation-protocol.md"
ARTIFACT_GATE = WORKSPACE / "openclaw-telegram-wsl-setup/tools/translation-agent/translation_artifact_gate.py"
ACCEPTANCE_GATE = WORKSPACE / "openclaw-telegram-wsl-setup/tools/translation-agent/translation_acceptance_gate.py"
SCHEMA = "openclaw.translation_task_entry.v0"


def now_cst() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def stamp_cst() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")


def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-._")
    return text[:80] or "translation-run"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_request(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.request_file:
        parts.append(Path(args.request_file).expanduser().resolve().read_text(encoding="utf-8", errors="replace"))
    if args.request:
        parts.append(args.request)
    if args.stdin_request:
        parts.append(sys.stdin.read())
    request = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if not request:
        raise SystemExit("Missing request: provide --request, --request-file, or --stdin-request")
    return request.rstrip() + "\n"


def source_lines(sources: list[str]) -> str:
    return "\n".join(f"- {s}" for s in sources) if sources else "- [not specified; see user_request.md]"


def create_run(args: argparse.Namespace) -> dict[str, Any]:
    request = read_request(args)
    run_id = args.run_id or f"{stamp_cst()}-{slugify(args.title)}"
    output_root = Path(args.output_root).expanduser().resolve()
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    created_at = now_cst()
    sources = [str(s) for s in args.source]

    (run_dir / "user_request.md").write_text(request, encoding="utf-8")

    handoff = f"""# Translation Agent Handoff Brief

Run id: `{run_id}`
Created at: {created_at}

## Authoritative request

Read `user_request.md`. It is Alex's original request and must be preserved as the task authority.

## Source identifiers

{source_lines(sources)}

## Non-negotiable rules

- Do not narrow, summarize, reinterpret, crop, or downgrade Alex's request.
- Do not change source/version/scope/style/format unless Alex explicitly approves.
- If a required source/range/format decision is missing, set `needs_alex_decision` in the final envelope instead of inventing scope.
- Write all worker artifacts under this run directory.
- Final chat response must be exactly the JSON envelope described in `translation-agent-isolation-protocol.md`.
- `candidate_ready` means ready for main verification, not final delivery.

## Protocol references

- Contract: `{CONTRACT_DOC}`
- Isolation protocol: `{ISOLATION_DOC}`
- Main artifact gate: `{ARTIFACT_GATE}`

## Required final response envelope

```json
{{
  "status": "candidate_ready | blocked | failed",
  "run_id": "{run_id}",
  "manifest": "{run_dir}/manifest.json",
  "artifacts": [],
  "claims": [],
  "needs_main_verification": true,
  "needs_alex_decision": []
}}
```
"""
    (run_dir / "handoff_brief.md").write_text(handoff, encoding="utf-8")

    dispatch_prompt = f"""You are the isolated translation agent for OpenClaw.

Read the handoff brief at:
{run_dir / 'handoff_brief.md'}

Then read `user_request.md` in the same run directory as the authoritative Alex request.

Follow the translation-agent isolation protocol. Write artifacts under this run directory. Return only the required JSON envelope; do not paste translated content or long narrative into chat.
"""
    (run_dir / "dispatch_prompt.md").write_text(dispatch_prompt, encoding="utf-8")

    ledger = {
        "schema": "openclaw.translation_task_ledger.v0",
        "run_id": run_id,
        "created_at": created_at,
        "owner": "main",
        "status": "entry_created_pending_translation_agent",
        "translation_worker_status": "not_started",
        "mainline_layer": "real_workflow/translation_agent",
        "sources": sources,
        "notes": [
            "Main owns acceptance; translation worker candidate_ready is not final.",
            "Main must verify artifacts before reporting success to Alex.",
        ],
    }
    write_json(run_dir / "task_ledger.json", ledger)

    acceptance = {
        "schema": "openclaw.translation_acceptance_plan.v0",
        "run_id": run_id,
        "status": "planned",
        "required_before_main_success": [
            "translation worker final envelope parses as JSON and names manifest/artifacts",
            "manifest.json exists and names source paths/ranges/model(s)/artifacts",
            "expected artifacts exist and pass byte/marker gate",
            "scope coverage checked against user_request.md",
            "source/version boundary checked",
            "long-document coverage audit and repair complete if applicable",
            "PDF/layout final verification complete if applicable",
            "delivery artifact can be sent or full file path is returned without lossy summary",
        ],
        "recommended_gate_command_template": f"python3 {ARTIFACT_GATE} --cwd {run_dir} --expect <artifact> --min-bytes <n>",
        "recommended_acceptance_command": f"python3 {ACCEPTANCE_GATE} --run-dir {run_dir} --out acceptance_gate_report.json",
    }
    write_json(run_dir / "acceptance_plan.json", acceptance)

    model_selection = {
        "schema": "openclaw.translation_model_selection_brief.v0",
        "run_id": run_id,
        "owner": "translation_agent",
        "status": "not_selected",
        "main_constraint": "Main passes Alex's request faithfully and does not hard-code a single model unless Alex explicitly named one.",
        "must_record_in_manifest": True,
    }
    write_json(run_dir / "model_selection_brief.json", model_selection)

    delivery = {
        "schema": "openclaw.translation_delivery_plan.v0",
        "run_id": run_id,
        "status": "planned_pending_artifacts",
        "preferred_delivery": ["delivery-ready PDF/Markdown/zip when applicable", "otherwise exact artifact path(s)"],
        "telegram_rule": "Do not present a compressed summary as complete delivery when the channel cannot carry the full output.",
    }
    write_json(run_dir / "delivery_plan.json", delivery)

    entry = {
        "schema": SCHEMA,
        "created_at": created_at,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": "entry_created_pending_translation_agent",
        "title": args.title,
        "sources": sources,
        "artifacts": {
            "user_request": str(run_dir / "user_request.md"),
            "handoff_brief": str(run_dir / "handoff_brief.md"),
            "dispatch_prompt": str(run_dir / "dispatch_prompt.md"),
            "task_ledger": str(run_dir / "task_ledger.json"),
            "acceptance_plan": str(run_dir / "acceptance_plan.json"),
            "model_selection_brief": str(run_dir / "model_selection_brief.json"),
            "delivery_plan": str(run_dir / "delivery_plan.json"),
        },
        "next_action": "dispatch translation agent with dispatch_prompt.md, then run artifact gate before reporting success",
    }
    write_json(run_dir / "entry.json", entry)

    main_review = f"""# Main Review

- checked_at: {created_at}
- entry_schema: {SCHEMA}
- run_id: {run_id}
- status: entry_created_pending_translation_agent
- mainline_layer: real_workflow/translation_agent
- user_request_preserved: true
- sources: {sources}

## Current verdict

Entry package created. No translation worker has been dispatched or accepted yet.

## Required next step

Dispatch the translation agent with `dispatch_prompt.md`; after worker output, verify artifacts with `acceptance_plan.json` and `translation_artifact_gate.py`.
"""
    (run_dir / "main_review.md").write_text(main_review, encoding="utf-8")

    summary_obj = {
        "schema": "openclaw.translation_task_entry.telegram_summary.v0",
        "created_at": created_at,
        "run_id": run_id,
        "status": "entry_created_pending_translation_agent",
        "run_dir": str(run_dir),
        "main_review": str(run_dir / "main_review.md"),
        "next_action": entry["next_action"],
    }
    write_json(run_dir / "telegram_summary.json", summary_obj)
    (run_dir / "telegram_summary.md").write_text(
        "\n".join(
            [
                f"- translation entry created: {run_id}",
                f"- status: entry_created_pending_translation_agent",
                f"- run_dir: {run_dir}",
                "- next: dispatch translation agent, then verify artifacts before reporting success",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", help="Explicit run id. Defaults to timestamp + slug.")
    ap.add_argument("--title", default="translation-task")
    ap.add_argument("--request", help="Alex's original request text")
    ap.add_argument("--request-file", help="File containing Alex's original request verbatim")
    ap.add_argument("--stdin-request", action="store_true", help="Read Alex's original request from stdin")
    ap.add_argument("--source", action="append", default=[], help="Source path/identifier; repeatable")
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = ap.parse_args()

    entry = create_run(args)
    print(json.dumps({"ok": True, "run_id": entry["run_id"], "run_dir": entry["run_dir"], "entry": str(Path(entry["run_dir"]) / "entry.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
