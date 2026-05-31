#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
SCHEMAS = ROOM / "schemas"
TASK_LEDGER = ROOM / "tasks.jsonl"
TASK_ROOT = ROOM / "tasks"
MCP_BRIEFS = ROOT / "room-mcp" / "briefs"
AG_ADAPTER_TASKS = ROOT / "antigravity-adapter" / "tasks"
AG_ADAPTER_OUTBOX = ROOT / "antigravity-adapter" / "outbox"
AG_COMMENT = ROOT / "agent-comments" / "antigravity.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_id(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    out = "-".join(part for part in out.split("-") if part)
    return out[:120] or "task"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl_once(path: Path, record: dict[str, Any], key: str = "run_id") -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(record.get(key) or "")
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        for line in handle:
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except Exception:
                continue
            if str(existing.get(key) or "") == value:
                return False
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return True


def validate_value(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {value!r}")
    typ = schema.get("type")
    if typ:
        allowed = typ if isinstance(typ, list) else [typ]
        if not any(type_matches(value, t) for t in allowed):
            errors.append(f"{path}: expected type {allowed!r}, got {type(value).__name__}")
            return errors
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required {req!r}")
        props = schema.get("properties") or {}
        for key, subschema in props.items():
            if key in value:
                errors.extend(validate_value(value[key], subschema, f"{path}.{key}"))
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                errors.extend(validate_value(item, item_schema, f"{path}[{idx}]"))
    return errors


def type_matches(value: Any, typ: str) -> bool:
    if typ == "object":
        return isinstance(value, dict)
    if typ == "array":
        return isinstance(value, list)
    if typ == "string":
        return isinstance(value, str)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "null":
        return value is None
    return True


def validate_file(schema_name: str, file_path: Path) -> dict[str, Any]:
    schema_path = SCHEMAS / schema_name
    schema = read_json(schema_path)
    value = read_json(file_path)
    errors = validate_value(value, schema)
    return {
        "schema": str(schema_path),
        "file": str(file_path),
        "ok": not errors,
        "errors": errors,
    }


def build_antigravity_task(run_id: str) -> dict[str, Any]:
    run_id = safe_id(run_id)
    created = now_iso()
    task_id = run_id
    task_dir = TASK_ROOT / run_id
    task_dir.mkdir(parents=True, exist_ok=True)
    MCP_BRIEFS.mkdir(parents=True, exist_ok=True)
    AG_ADAPTER_TASKS.mkdir(parents=True, exist_ok=True)
    AG_ADAPTER_OUTBOX.mkdir(parents=True, exist_ok=True)

    brief = f"""# Antigravity same-run-id queued roundtrip

run_id: `{run_id}`
room_id: `mainline-architecture`

You are Antigravity participating in Alex's OpenClaw agent room.

Task:

1. Use MCP server `openclaw-room`.
2. Call `get_room_status`.
3. Call `read_bounded_brief` with `brief_id={run_id}` if you need to re-read this brief.
4. Write exactly one room comment through `write_agent_comment`:
   - agent_id: `antigravity`
   - run_id: `{run_id}`
   - kind: `review_comment` or `status`
   - confidence: `high` if the tool call succeeds, otherwise `medium`
   - seq_observed: the seq returned by `get_room_status`
   - body: say whether Antigravity can complete this same-run-id queued roundtrip, and list any blocker.

Restrictions:

- Do not edit source files.
- Do not send Telegram.
- Do not read secrets or tokens.
- Do not change global workflow state.
- Do not claim success unless the comment is written with the exact same run_id.

Success criterion:

`agent-comments/antigravity.jsonl` contains a valid record with `run_id={run_id}` and `antigravity_adapter.py read --run-id {run_id}` reports `roundtrip_verified=true`.
"""

    brief_path = task_dir / "brief.md"
    manifest_path = task_dir / "manifest.json"
    mcp_brief_path = MCP_BRIEFS / f"{run_id}.md"
    ag_prompt_path = AG_ADAPTER_OUTBOX / f"{run_id}.prompt.md"
    ag_task_path = AG_ADAPTER_TASKS / f"{run_id}.json"

    brief_path.write_text(brief, encoding="utf-8")
    mcp_brief_path.write_text(brief, encoding="utf-8")
    (MCP_BRIEFS / "latest.md").write_text(brief, encoding="utf-8")
    ag_prompt_path.write_text(brief, encoding="utf-8")

    manifest = {
        "schema": "openclaw.agent_room.task.v0",
        "task_id": task_id,
        "run_id": run_id,
        "room_id": "mainline-architecture",
        "requested_by": "codex",
        "target_agents": ["antigravity"],
        "lane": "validation",
        "brief_path": str(brief_path),
        "context_paths": [
            str(mcp_brief_path),
            str(ROOT / "agent-room" / "room_contract_p0.md"),
            str(ROOT / "agent-room" / "schemas" / "task.schema.json"),
        ],
        "permissions": {
            "source_edit": False,
            "telegram_send": False,
            "notion_publish": False,
            "github_push": False,
            "secrets_access": False,
            "global_state_change": False,
            "gui_automation": False,
        },
        "expected_outputs": [
            {
                "type": "comment_jsonl",
                "path": str(AG_COMMENT),
                "run_id_must_match": True,
                "agent_id": "antigravity",
            }
        ],
        "status": "queued",
        "review_status": "requested",
        "blocked_reason": None,
        "result_paths": [],
        "canonical_imported": False,
        "created_at": created,
        "updated_at": created,
        "lease": {
            "owner": None,
            "heartbeat_at": None,
            "expires_at": None,
        },
        "heartbeat": {
            "last_seen_at": None,
        },
        "retry_budget": {
            "max_attempts": 1,
            "attempt": 0,
        },
        "manual_boundary": True,
        "quality_gate_status": "not_applicable",
        "side_effect_gate_status": "closed",
        "telegram_projection_status": "suppressed",
        "adapter_refs": {
            "antigravity_task_path": str(ag_task_path),
            "antigravity_prompt_path": str(ag_prompt_path),
            "mcp_brief_path": str(mcp_brief_path),
        },
    }
    write_json(manifest_path, manifest)
    validation = validate_file("task.schema.json", manifest_path)
    ag_task = {
        "schema": "openclaw.antigravity_adapter.task.v0",
        "run_id": run_id,
        "created_at": created,
        "status": "queued_for_mcp_room",
        "source": "agent_room_task_manifest",
        "prompt_artifact": str(ag_prompt_path),
        "mcp_brief_artifact": str(mcp_brief_path),
        "latest_mcp_brief": str(MCP_BRIEFS / "latest.md"),
        "task_artifact": str(ag_task_path),
        "room_task_manifest": str(manifest_path),
        "expected_comment_lane": str(AG_COMMENT),
        "policy": {
            "telegram_sent": False,
            "source_edits_allowed": False,
            "secrets_allowed": False,
            "canonical_mailbox_write_allowed": False,
            "gui_automation_allowed": False,
        },
    }
    write_json(ag_task_path, ag_task)
    ledger_record = dict(manifest)
    ledger_record["manifest_path"] = str(manifest_path)
    appended = append_jsonl_once(TASK_LEDGER, ledger_record)
    return {
        "ok": validation["ok"],
        "run_id": run_id,
        "appended_to_ledger": appended,
        "manifest_path": str(manifest_path),
        "brief_path": str(brief_path),
        "mcp_brief_path": str(mcp_brief_path),
        "antigravity_task_path": str(ag_task_path),
        "validation": validation,
    }


def validate_all() -> dict[str, Any]:
    schema_files = sorted(SCHEMAS.glob("*.schema.json"))
    schema_parse = []
    for path in schema_files:
        try:
            read_json(path)
            schema_parse.append({"file": str(path), "ok": True})
        except Exception as exc:
            schema_parse.append({"file": str(path), "ok": False, "error": str(exc)})
    task_validations = []
    for path in sorted(TASK_ROOT.glob("*/manifest.json")):
        task_validations.append(validate_file("task.schema.json", path))
    return {
        "ok": all(item.get("ok") for item in schema_parse) and all(item.get("ok") for item in task_validations),
        "schemas": schema_parse,
        "tasks": task_validations,
        "task_ledger": str(TASK_LEDGER),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-antigravity-roundtrip")
    create.add_argument("--run-id", default=f"antigravity-same-runid-queued-{datetime.now().astimezone().strftime('%Y%m%d-%H%M')}")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.cmd == "create-antigravity-roundtrip":
        print(json.dumps(build_antigravity_task(args.run_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "validate":
        result = validate_all()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
