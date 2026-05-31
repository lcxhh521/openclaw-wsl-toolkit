#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
SCHEMAS = ROOM / "schemas"
EXAMPLES = ROOM / "examples"
TASK_LEDGER = ROOM / "tasks.jsonl"
COLLAB_LEDGER = ROOT / "collaboration_ledger.json"
COLLAB_LEDGER_ARCHIVE = ROOT / "archive" / "collaboration_ledger.jsonl"
COLLAB_LEDGER_TOOL = ROOM / "tools" / "collaboration_ledger.py"
TELEGRAM_BRIDGE_TOOL = ROOM / "tools" / "telegram_agent_bridge.py"
COLLAB_LEDGER_SCHEMA = "openclaw.agent_room.collaboration_ledger.v0"
COLLAB_EVENT_SCHEMA = "openclaw.agent_room.collaboration_event.v0"
REQUIRED_SCHEMAS = [
    "room.schema.json",
    "task.schema.json",
    "participant.schema.json",
    "comment.schema.json",
    "projection.schema.json",
    "telegram_group_ingress.schema.json",
]


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


def parse_json_file(path: Path) -> dict[str, Any]:
    try:
        load_json(path)
        return {"file": str(path), "ok": True}
    except Exception as exc:
        return {"file": str(path), "ok": False, "error": str(exc)}


def validate_against(schema_name: str, path: Path) -> dict[str, Any]:
    try:
        schema = load_json(SCHEMAS / schema_name)
        value = load_json(path)
        errors = validate_value(value, schema)
        return {"file": str(path), "schema": schema_name, "ok": not errors, "errors": errors}
    except Exception as exc:
        return {"file": str(path), "schema": schema_name, "ok": False, "errors": [str(exc)]}


def validate_examples() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name
        if "antigravity_same_run_id_queued_task" in name:
            out.append(validate_against("task.schema.json", path))
        elif "claude_proactive" in name:
            # This example intentionally contains both comment and projection objects.
            parsed = parse_json_file(path)
            if parsed["ok"]:
                value = load_json(path)
                comment_path = Path("/tmp/openclaw-room-comment-example.json")
                projection_path = Path("/tmp/openclaw-room-projection-example.json")
                comment_path.write_text(json.dumps(value.get("comment"), ensure_ascii=False), encoding="utf-8")
                projection_path.write_text(json.dumps(value.get("projection"), ensure_ascii=False), encoding="utf-8")
                c = validate_against("comment.schema.json", comment_path)
                p = validate_against("projection.schema.json", projection_path)
                out.append({"file": str(path), "ok": c["ok"] and p["ok"], "parts": [c, p]})
            else:
                out.append(parsed)
        elif "telegram_group" in name or "autoroom" in name:
            out.append(validate_against("telegram_group_ingress.schema.json", path))
        else:
            out.append(parse_json_file(path))
    return out


def validate_ledger() -> dict[str, Any]:
    if not TASK_LEDGER.exists():
        return {"file": str(TASK_LEDGER), "ok": True, "status": "missing_optional_ledger", "records": 0, "errors": []}
    schema = load_json(SCHEMAS / "task.schema.json")
    records = 0
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(TASK_LEDGER.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        records += 1
        try:
            record = json.loads(line)
        except Exception as exc:
            errors.append({"line": line_no, "errors": [f"json parse: {exc}"]})
            continue
        validation_errors = validate_value(record, schema)
        task_id = str(record.get("task_id") or "")
        if task_id in seen:
            validation_errors.append(f"duplicate task_id in ledger: {task_id}")
        seen.add(task_id)
        if validation_errors:
            errors.append({"line": line_no, "task_id": task_id, "errors": validation_errors})
    return {"file": str(TASK_LEDGER), "ok": not errors, "status": "present", "records": records, "errors": errors}


def validate_collaboration_ledger() -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    current_status = "missing_optional_current"
    archive_status = "missing_optional_archive"
    events = 0

    if COLLAB_LEDGER.exists():
        current_status = "present"
        try:
            current = load_json(COLLAB_LEDGER)
            current_errors: list[str] = []
            if not isinstance(current, dict):
                current_errors.append("current ledger root is not object")
            else:
                if current.get("schema") != COLLAB_LEDGER_SCHEMA:
                    current_errors.append(f"unsupported schema: {current.get('schema')!r}")
                for key in ("work_items", "claims", "artifacts", "blockers", "handoffs"):
                    if key in current and not isinstance(current.get(key), list):
                        current_errors.append(f"{key} must be a list")
                participants = current.get("participants") if isinstance(current.get("participants"), list) else []
                participant_set = {str(item) for item in participants if str(item)}
                work_items = current.get("work_items") if isinstance(current.get("work_items"), list) else []
                claims = current.get("claims") if isinstance(current.get("claims"), list) else []
                handoffs = current.get("handoffs") if isinstance(current.get("handoffs"), list) else []
                work_by_id = {
                    str(item.get("id")): item
                    for item in work_items
                    if isinstance(item, dict) and str(item.get("id") or "")
                }
                active_claim_owner: dict[str, str] = {}
                for idx, claim in enumerate(claims):
                    if not isinstance(claim, dict):
                        current_errors.append(f"claims[{idx}] must be object")
                        continue
                    work_item_id = str(claim.get("work_item_id") or "").strip()
                    agent_id = str(claim.get("agent_id") or "").strip()
                    if not work_item_id or work_item_id not in work_by_id:
                        current_errors.append(f"claims[{idx}] references unknown work_item_id {work_item_id!r}")
                    if not agent_id:
                        current_errors.append(f"claims[{idx}].agent_id must not be empty")
                    if participant_set and agent_id and agent_id not in participant_set:
                        current_errors.append(f"claims[{idx}].agent_id {agent_id!r} is not a participant")
                    status = str(claim.get("status") or "").strip()
                    if status in {"active", "claimed", "running"} and work_item_id and agent_id:
                        previous = active_claim_owner.get(work_item_id)
                        if previous and previous != agent_id:
                            current_errors.append(f"work_item {work_item_id!r} has multiple active claim owners: {previous!r}, {agent_id!r}")
                        active_claim_owner[work_item_id] = agent_id
                for idx, handoff in enumerate(handoffs):
                    if not isinstance(handoff, dict):
                        current_errors.append(f"handoffs[{idx}] must be object")
                        continue
                    work_item_id = str(handoff.get("work_item_id") or "").strip()
                    from_agent = str(handoff.get("from_agent") or "").strip()
                    to_agent = str(handoff.get("to_agent") or "").strip()
                    if not work_item_id or work_item_id not in work_by_id:
                        current_errors.append(f"handoffs[{idx}] references unknown work_item_id {work_item_id!r}")
                    if not from_agent:
                        current_errors.append(f"handoffs[{idx}].from_agent must not be empty")
                    if not to_agent:
                        current_errors.append(f"handoffs[{idx}].to_agent must not be empty")
                    if from_agent and to_agent and from_agent == to_agent:
                        current_errors.append(f"handoffs[{idx}] source and target must differ")
                    if participant_set and from_agent and from_agent not in participant_set:
                        current_errors.append(f"handoffs[{idx}].from_agent {from_agent!r} is not a participant")
                    if participant_set and to_agent and to_agent not in participant_set:
                        current_errors.append(f"handoffs[{idx}].to_agent {to_agent!r} is not a participant")
                for work_item_id, item in work_by_id.items():
                    owner = str(item.get("claimed_by") or "").strip()
                    reviewer = str(item.get("acceptance_reviewed_by") or "").strip()
                    if owner and reviewer and owner == reviewer:
                        current_errors.append(f"work_item {work_item_id!r} was self-accepted by owner {owner!r}")
            if current_errors:
                errors.append({"file": str(COLLAB_LEDGER), "errors": current_errors})
        except Exception as exc:
            errors.append({"file": str(COLLAB_LEDGER), "errors": [f"json parse: {exc}"]})

    if COLLAB_LEDGER_ARCHIVE.exists():
        archive_status = "present"
        for line_no, line in enumerate(COLLAB_LEDGER_ARCHIVE.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            events += 1
            try:
                event = json.loads(line)
            except Exception as exc:
                errors.append({"file": str(COLLAB_LEDGER_ARCHIVE), "line": line_no, "errors": [f"json parse: {exc}"]})
                continue
            event_errors: list[str] = []
            if not isinstance(event, dict):
                event_errors.append("event root is not object")
            else:
                if event.get("schema") != COLLAB_EVENT_SCHEMA:
                    event_errors.append(f"unsupported schema: {event.get('schema')!r}")
                if not event.get("event_type"):
                    event_errors.append("missing event_type")
                if not isinstance(event.get("payload"), dict):
                    event_errors.append("payload must be object")
            if event_errors:
                errors.append({"file": str(COLLAB_LEDGER_ARCHIVE), "line": line_no, "errors": event_errors})

    return {
        "file": str(COLLAB_LEDGER),
        "archive": str(COLLAB_LEDGER_ARCHIVE),
        "ok": not errors,
        "current_status": current_status,
        "archive_status": archive_status,
        "events": events,
        "errors": errors,
    }


def run_ledger_smoke_command(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(COLLAB_LEDGER_TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    parsed: Any = None
    try:
        parsed = json.loads(completed.stdout)
    except Exception:
        parsed = None
    return {
        "args": args,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-1000:],
        "json": parsed if isinstance(parsed, dict) else None,
    }


def validate_collaboration_ledger_smoke() -> dict[str, Any]:
    if not COLLAB_LEDGER_TOOL.exists():
        return {"file": str(COLLAB_LEDGER_TOOL), "ok": False, "errors": ["collaboration ledger tool missing"], "checks": []}

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="openclaw-collab-ledger-smoke-") as tmp:
        tmp_path = Path(tmp)
        task_file = tmp_path / "task.json"
        state_file = tmp_path / "collaboration_ledger.json"
        archive_file = tmp_path / "collaboration_ledger.jsonl"
        task_file.write_text(
            json.dumps(
                {
                    "room_id": "smoke-room",
                    "task_id": "smoke-task",
                    "run_id": "smoke-run",
                    "collaboration": {
                        "participants": ["codex", "claude-code"],
                        "role_policy": {
                            "strategy": "deterministic_rotation",
                            "rotation_key_sha256": "smoke-key",
                        },
                        "roles": [
                            {"agent_id": "codex", "role": "lead"},
                            {"agent_id": "claude-code", "role": "reviewer"},
                        ],
                        "work_items": [
                            {"id": "wi-1", "title": "claimed item", "status": "open", "role": "lead"},
                            {"id": "wi-2", "title": "unclaimed item", "status": "open", "role": "reviewer"},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        base = ["--state-file", str(state_file), "--archive-file", str(archive_file)]
        cases: list[tuple[str, list[str], bool, str | None]] = [
            ("init", [*base, "init", "--task-file", str(task_file)], True, None),
            ("claim-owner", [*base, "claim", "--work-item-id", "wi-1", "--agent-id", "codex"], True, None),
            (
                "reject-duplicate-other-owner",
                [*base, "claim", "--work-item-id", "wi-1", "--agent-id", "claude-code"],
                False,
                "already claimed by codex",
            ),
            (
                "reject-artifact-non-owner",
                [*base, "artifact", "--work-item-id", "wi-1", "--agent-id", "claude-code", "--title", "wrong owner", "--path", "nowhere"],
                False,
                "claimed by codex",
            ),
            (
                "reject-blocker-unclaimed",
                [*base, "blocker", "--work-item-id", "wi-2", "--agent-id", "claude-code", "--reason", "unclaimed", "--detail", "unclaimed"],
                False,
                "is not claimed",
            ),
            (
                "reject-handoff-empty-target",
                [*base, "handoff", "--work-item-id", "wi-1", "--from-agent", "codex", "--to-agent", "", "--reason", "empty"],
                False,
                "to_agent must not be empty",
            ),
            (
                "handoff-owner-to-peer",
                [*base, "handoff", "--work-item-id", "wi-1", "--from-agent", "codex", "--to-agent", "claude-code", "--reason", "handoff"],
                True,
                None,
            ),
            (
                "claim-handoff-target",
                [*base, "claim", "--work-item-id", "wi-1", "--agent-id", "claude-code"],
                True,
                None,
            ),
            (
                "artifact-new-owner",
                [*base, "artifact", "--work-item-id", "wi-1", "--agent-id", "claude-code", "--title", "done", "--path", "agent-comments/claude.jsonl", "--status", "completed"],
                True,
                None,
            ),
            ("claim-second-item", [*base, "claim", "--work-item-id", "wi-2", "--agent-id", "codex"], True, None),
            (
                "artifact-second-item",
                [*base, "artifact", "--work-item-id", "wi-2", "--agent-id", "codex", "--title", "done", "--path", "agent-comments/codex.jsonl", "--status", "completed"],
                True,
                None,
            ),
            (
                "reject-self-accept",
                [*base, "accept", "--work-item-id", "wi-2", "--reviewer", "codex", "--verdict", "accepted", "--reason", "self review must not count"],
                False,
                "reviewer must differ from work item owner",
            ),
            (
                "accept-peer-review",
                [*base, "accept", "--work-item-id", "wi-2", "--reviewer", "claude-code", "--verdict", "accepted", "--reason", "peer accepted smoke output"],
                True,
                None,
            ),
        ]

        for name, args, should_succeed, error_fragment in cases:
            result = run_ledger_smoke_command(args)
            ok_json = isinstance(result.get("json"), dict) and result["json"].get("ok") is should_succeed
            ok_exit = (result["exit_code"] == 0) is should_succeed
            output_text = json.dumps(result.get("json") or {}, ensure_ascii=False) + "\n" + str(result.get("stdout") or "") + str(result.get("stderr") or "")
            ok_error = error_fragment is None or error_fragment in output_text
            checks.append({"name": name, "ok": ok_json and ok_exit and ok_error, "exit_code": result["exit_code"]})
            if not checks[-1]["ok"]:
                errors.append(f"{name} failed expectation: {output_text[-500:]}")

        if archive_file.exists():
            event_count = sum(1 for line in archive_file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        else:
            event_count = 0
        checks.append({"name": "archive-success-events-only", "ok": event_count == 8, "events": event_count})
        if event_count != 8:
            errors.append(f"expected 8 successful smoke events in archive, got {event_count}")

        try:
            final_state = load_json(state_file)
            role_policy_preserved = final_state.get("role_policy") == {
                "strategy": "deterministic_rotation",
                "rotation_key_sha256": "smoke-key",
            }
            role_preserved = final_state.get("roles") == [
                {"agent_id": "codex", "role": "lead"},
                {"agent_id": "claude-code", "role": "reviewer"},
            ]
            item_roles = {
                item.get("id"): item.get("role")
                for item in final_state.get("work_items", [])
                if isinstance(item, dict)
            }
            work_item_roles_preserved = item_roles.get("wi-1") == "lead" and item_roles.get("wi-2") == "reviewer"
            active_claims = [
                claim
                for claim in final_state.get("claims", [])
                if isinstance(claim, dict) and str(claim.get("status") or "") in {"active", "claimed", "running"}
            ]
            active_owner_ok = not active_claims
            claim_completed = any(
                isinstance(claim, dict)
                and claim.get("work_item_id") == "wi-1"
                and claim.get("agent_id") == "claude-code"
                and claim.get("status") == "completed"
                for claim in final_state.get("claims", [])
            )
            ledger_completed = final_state.get("status") == "completed"
            handoff_accepted = any(
                isinstance(handoff, dict)
                and handoff.get("work_item_id") == "wi-1"
                and handoff.get("to_agent") == "claude-code"
                and handoff.get("status") == "accepted"
                for handoff in final_state.get("handoffs", [])
            )
            wi_2 = next(
                (
                    item
                    for item in final_state.get("work_items", [])
                    if isinstance(item, dict) and item.get("id") == "wi-2"
                ),
                {},
            )
            peer_acceptance_recorded = any(
                isinstance(entry, dict)
                and entry.get("reviewer") == "claude-code"
                and entry.get("verdict") == "accepted"
                for entry in (wi_2.get("acceptance_history") or [])
            )
            checks.append({"name": "role-policy-preserved", "ok": role_policy_preserved})
            checks.append({"name": "role-metadata-preserved", "ok": role_preserved})
            checks.append({"name": "work-item-role-preserved", "ok": work_item_roles_preserved})
            checks.append({"name": "handoff-terminal-item-has-no-active-claim", "ok": active_owner_ok})
            checks.append({"name": "handoff-claim-marked-completed", "ok": claim_completed})
            checks.append({"name": "ledger-status-refreshed-after-artifact", "ok": ledger_completed})
            checks.append({"name": "handoff-accepted", "ok": handoff_accepted})
            checks.append({"name": "peer-acceptance-recorded", "ok": peer_acceptance_recorded})
            if not role_policy_preserved:
                errors.append("ledger smoke did not preserve collaboration role policy")
            if not role_preserved:
                errors.append("ledger smoke did not preserve collaboration roles")
            if not work_item_roles_preserved:
                errors.append("ledger smoke did not preserve work item roles")
            if not active_owner_ok:
                errors.append("handoff smoke left an active claim on a completed work item")
            if not claim_completed:
                errors.append("handoff smoke did not mark the accepted claim completed")
            if not ledger_completed:
                errors.append("ledger smoke did not refresh top-level status after artifact completion")
            if not handoff_accepted:
                errors.append("handoff smoke did not mark the handoff accepted")
            if not peer_acceptance_recorded:
                errors.append("ledger smoke did not record peer acceptance")
        except Exception as exc:
            checks.append({"name": "handoff-final-state", "ok": False})
            errors.append(f"handoff final state check failed: {exc}")

    return {"file": str(COLLAB_LEDGER_TOOL), "ok": not errors, "checks": checks, "errors": errors}


def validate_telegram_bridge_smoke() -> dict[str, Any]:
    if not TELEGRAM_BRIDGE_TOOL.exists():
        return {"file": str(TELEGRAM_BRIDGE_TOOL), "ok": False, "errors": ["telegram bridge tool missing"], "checks": []}

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    try:
        spec = importlib.util.spec_from_file_location("telegram_agent_bridge_smoke", TELEGRAM_BRIDGE_TOOL)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load telegram_agent_bridge module spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        text = "我觉得bot-to-bot这里是需要持续迭代和完善的，这个做好了能够很好的提升效率"
        plain = "今天市场怎么样"
        improvement_detected = bool(module.collaboration_improvement_requested(text))
        loop_requested = bool(module.task_requests_agent_collaboration_loop(text))
        tick = module.collaboration_tick_for_text(text)
        tick_ok = isinstance(tick, dict) and tick.get("enabled") is True and int(tick.get("max_rounds") or 0) >= 2
        task = module.build_task(
            "openclaw-evolution",
            "-1009000000001",
            text,
            ["codex", "claude-code"],
            "telegram-user",
            "smoke:bot-to-bot-continuous-iteration",
        )
        collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
        work_items = collaboration.get("work_items") if isinstance(collaboration.get("work_items"), list) else []
        material_items = bool(work_items) and all(
            isinstance(item, dict) and item.get("systemic_solution_required") is True
            for item in work_items
        )
        bounded_rounds = int(collaboration.get("max_rounds") or 0) >= 2
        plain_not_triggered = not module.collaboration_improvement_requested(plain) and not module.task_requests_agent_collaboration_loop(plain)

        cases = [
            ("bot-to-bot-improvement-detected", improvement_detected),
            ("collaboration-loop-requested", loop_requested),
            ("bounded-peer-tick-enabled", tick_ok),
            ("material-work-items", material_items),
            ("bounded-rounds-propagated", bounded_rounds),
            ("plain-message-not-triggered", plain_not_triggered),
        ]
        for name, ok in cases:
            checks.append({"name": name, "ok": ok})
            if not ok:
                errors.append(f"{name} failed")
    except Exception as exc:
        checks.append({"name": "telegram-bridge-smoke-exception", "ok": False})
        errors.append(str(exc))

    return {"file": str(TELEGRAM_BRIDGE_TOOL), "ok": not errors, "checks": checks, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Agent Room artifact packet.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    args = parser.parse_args()

    schemas = []
    for name in REQUIRED_SCHEMAS:
        path = SCHEMAS / name
        if not path.exists():
            schemas.append({"file": str(path), "ok": False, "error": "missing_required_schema"})
        else:
            schemas.append(parse_json_file(path))
    examples = validate_examples()
    ledger = validate_ledger()
    collaboration_ledger = validate_collaboration_ledger()
    collaboration_ledger_smoke = validate_collaboration_ledger_smoke()
    telegram_bridge_smoke = validate_telegram_bridge_smoke()
    ok = (
        all(item.get("ok") for item in schemas)
        and all(item.get("ok") for item in examples)
        and ledger.get("ok")
        and collaboration_ledger.get("ok")
        and collaboration_ledger_smoke.get("ok")
        and telegram_bridge_smoke.get("ok")
    )
    result = {
        "ok": ok,
        "room_root": str(ROOM),
        "schemas": schemas,
        "examples": examples,
        "task_ledger": ledger,
        "collaboration_ledger": collaboration_ledger,
        "collaboration_ledger_smoke": collaboration_ledger_smoke,
        "telegram_bridge_smoke": telegram_bridge_smoke,
        "external_side_effects": False,
        "telegram_outbound": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not ok and not args.json:
        print("\nFailing paths:")
        for collection in (schemas, examples, [ledger], [collaboration_ledger], [collaboration_ledger_smoke], [telegram_bridge_smoke]):
            for item in collection:
                if not item.get("ok"):
                    print("-", item.get("file"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
