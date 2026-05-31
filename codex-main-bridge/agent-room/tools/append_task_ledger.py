#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
SCHEMAS = ROOM / "schemas"
TASK_LEDGER = ROOM / "tasks.jsonl"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key!r}")
        for key, subschema in (schema.get("properties") or {}).items():
            if key in value:
                errors.extend(validate_value(value[key], subschema, f"{path}.{key}"))
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                errors.extend(validate_value(item, item_schema, f"{path}[{idx}]"))
    return errors


def validate_task(record: dict[str, Any]) -> list[str]:
    schema = load_json(SCHEMAS / "task.schema.json")
    return validate_value(record, schema)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a validated room task to agent-room/tasks.jsonl.")
    parser.add_argument("--task-file", required=True, help="Path to a task manifest JSON file.")
    parser.add_argument("--ledger", default=str(TASK_LEDGER), help="Task ledger path.")
    parser.add_argument("--allow-event-for-existing-task", action="store_true", help="Allow appending an event for an existing task_id when event_type is present.")
    args = parser.parse_args()

    task_path = Path(args.task_file).expanduser().resolve()
    ledger_path = Path(args.ledger).expanduser().resolve()
    record = load_json(task_path)
    if not isinstance(record, dict):
        print(json.dumps({"ok": False, "error": "task root is not object", "file": str(task_path)}, ensure_ascii=False, indent=2))
        return 2

    errors = validate_task(record)
    if errors:
        print(json.dumps({"ok": False, "stage": "validate", "file": str(task_path), "errors": errors}, ensure_ascii=False, indent=2))
        return 2

    task_id = str(record.get("task_id") or "")
    event_type = record.get("event_type")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    appended = False
    duplicate = False

    with ledger_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        for line in handle:
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except Exception:
                continue
            if str(existing.get("task_id") or "") == task_id:
                duplicate = True
                break
        if duplicate and not (args.allow_event_for_existing_task and event_type):
            print(json.dumps({
                "ok": False,
                "stage": "dedupe",
                "reason": "duplicate_task_id",
                "task_id": task_id,
                "ledger": str(ledger_path),
            }, ensure_ascii=False, indent=2))
            return 3
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        appended = True

    print(json.dumps({
        "ok": True,
        "appended": appended,
        "duplicate_allowed_as_event": bool(duplicate and event_type),
        "task_id": task_id,
        "run_id": record.get("run_id"),
        "ledger": str(ledger_path),
        "source": str(task_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
