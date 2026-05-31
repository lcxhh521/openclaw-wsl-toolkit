#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))
from mailbox_paths import MAILBOX_ROOT

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(CODE_ROOT)))
STATE_FILE = ROOT / "collaboration_ledger.json"
ARCHIVE_FILE = ROOT / "archive" / "collaboration_ledger.jsonl"
TURN_FILE = MAILBOX_ROOT / "turn.json"
LEDGER_SCHEMA = "openclaw.agent_room.collaboration_ledger.v0"
EVENT_SCHEMA = "openclaw.agent_room.collaboration_event.v0"

CLAIM_LEASE_SECONDS = int(os.environ.get("OPENCLAW_CLAIM_LEASE_SECONDS", "300"))
ACTIVE_CLAIM_STATUSES = {"active", "claimed", "running"}
TERMINAL_WORK_ITEM_STATUSES = {"completed", "blocked", "cancelled"}
RETRYABLE_WORK_ITEM_STATUSES = {"retryable"}
ACCEPTANCE_STATUSES = {"pending", "accepted", "rejected", "superseded"}
GOVERNANCE_KEYS = ("mainline_id", "dedupe_key", "problem_statement", "expected_user_value",
                    "definition_of_done", "approval_gate", "next_action", "owner")

POINT_KINDS = {
    "claim",
    "proposal",
    "risk",
    "evidence",
    "question",
    "decision",
    "summary",
    "integrated_summary",
    "synthesis",
    "closure_summary",
}
UPTAKE_STATUSES = {"noticed", "accepted", "challenged", "incorporated", "rejected", "superseded", "pending"}
SYSTEM_PARTICIPANTS = {"openclaw-main"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-format datetime string, falling back to UTC if tzinfo is missing."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def is_claim_expired(item: dict[str, Any], at: datetime | None = None) -> bool:
    """Return True if a claimed work item's lease has expired."""
    status = str(item.get("status") or "").strip()
    if status not in ACTIVE_CLAIM_STATUSES | {"handoff"}:
        return False
    lease_expiry_raw = item.get("lease_expiry")
    if not lease_expiry_raw:
        return False
    try:
        lease_expiry = parse_iso(str(lease_expiry_raw))
    except Exception:
        return False
    now = at or datetime.now().astimezone()
    return now > lease_expiry


def claim_matches_filter(item: dict[str, Any], *, work_item_id: str | None, agent_id: str | None) -> bool:
    if work_item_id and str(item.get("id") or item.get("work_item_id") or "") != work_item_id:
        return False
    if agent_id:
        owner = str(item.get("agent_id") or item.get("claimed_by") or item.get("assigned_to") or "").strip()
        if owner != agent_id:
            return False
    return True


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def output(value: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return code


def load_turn_seq(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        turn = load_json(path)
    except Exception:
        return None
    seq = turn.get("seq") if isinstance(turn, dict) else None
    return seq if isinstance(seq, int) else None


def list_field(value: Any) -> list[Any]:
    return copy.deepcopy(value) if isinstance(value, list) else []


def _extract_governance(task: dict[str, Any]) -> dict[str, Any]:
    """Extract governance fields from task manifest (mainline-governance-contract-20260528).

    Borrows the concept of mainline binding from Kanban/Scrum epic linking.
    Does NOT copy the full Kanban state pipeline — the existing ledger states
    already cover the lifecycle.  Governance fields enable programmatic dedup
    and drift detection without adding ceremony.
    """
    governance: dict[str, Any] = {}
    raw = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    for key in GOVERNANCE_KEYS:
        value = raw.get(key) or task.get(key)
        if value:
            governance[key] = value
    # Fallback: infer mainline_id from standing_mainline metadata
    if "mainline_id" not in governance:
        standing = task.get("standing_mainline") if isinstance(task.get("standing_mainline"), dict) else {}
        mainline_item_id = standing.get("linked_mainline_item_id") or standing.get("item_id")
        if mainline_item_id:
            governance["mainline_id"] = str(mainline_item_id)
        elif task.get("lane") in ("standing_mainline_discussion", "agent_to_agent_mention"):
            governance["mainline_id"] = "agent_room_infrastructure"
    return governance


def dedup_check(governance: dict[str, Any], ledger_dir: Path, current_task_id: str | None = None) -> list[dict[str, Any]]:
    """Find open ledgers with matching mainline_id + dedupe_key (warning-only, not a block).

    Returns a list of dicts with task_id and matching fields so callers can
    decide whether to merge or proceed.  Does not enforce a hard WIP cap.
    """
    mainline_id = governance.get("mainline_id")
    dedupe_key = governance.get("dedupe_key")
    if not mainline_id:
        return []
    matches: list[dict[str, Any]] = []
    if not ledger_dir.is_dir():
        return matches
    for ledger_file in ledger_dir.glob("*.json"):
        try:
            state = load_json(ledger_file)
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        if state.get("status") in ("completed", "cancelled", "merged"):
            continue
        if current_task_id and state.get("task_id") == current_task_id:
            continue
        lg = state.get("governance") if isinstance(state.get("governance"), dict) else {}
        if lg.get("mainline_id") != mainline_id:
            continue
        if dedupe_key and lg.get("dedupe_key") and lg.get("dedupe_key") == dedupe_key:
            matches.append({"task_id": state.get("task_id"), "mainline_id": mainline_id, "dedupe_key": dedupe_key, "status": state.get("status")})
        elif not dedupe_key or not lg.get("dedupe_key"):
            matches.append({"task_id": state.get("task_id"), "mainline_id": mainline_id, "dedupe_key": None, "status": state.get("status")})
    return matches


def state_from_task(task: dict[str, Any], turn_seq: int | None, at: str) -> dict[str, Any]:
    collaboration = task.get("collaboration") if isinstance(task.get("collaboration"), dict) else {}
    governance = _extract_governance(task)
    return {
        "schema": LEDGER_SCHEMA,
        "room_id": task.get("room_id"),
        "task_id": task.get("task_id"),
        "run_id": task.get("run_id") or task.get("task_id"),
        "turn_seq": turn_seq,
        "status": collaboration.get("status") or "open",
        "mode": collaboration.get("mode"),
        "participants": list_field(collaboration.get("participants")),
        "role_policy": copy.deepcopy(collaboration.get("role_policy")) if isinstance(collaboration.get("role_policy"), dict) else {},
        "roles": list_field(collaboration.get("roles")),
        "work_items": list_field(collaboration.get("work_items")),
        "claims": list_field(collaboration.get("claims")),
        "artifacts": list_field(collaboration.get("artifacts")),
        "blockers": list_field(collaboration.get("blockers")),
        "handoffs": list_field(collaboration.get("handoffs")),
        "points": list_field(collaboration.get("points")),
        "uptakes": list_field(collaboration.get("uptakes")),
        "governance": governance,
        "created_at": collaboration.get("created_at") or task.get("created_at") or at,
        "updated_at": at,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"ledger state missing: {path}")
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("ledger state root is not an object")
    if value.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"unsupported ledger schema: {value.get('schema')!r}")
    return value


def find_work_item(state: dict[str, Any], work_item_id: str) -> dict[str, Any]:
    work_items = state.get("work_items")
    if not isinstance(work_items, list):
        raise ValueError("ledger state has no work_items list")
    for item in work_items:
        if isinstance(item, dict) and item.get("id") == work_item_id:
            return item
    raise KeyError(f"unknown work_item_id: {work_item_id}")


def require_nonempty(value: str | None, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def require_participant(state: dict[str, Any], agent_id: str, field: str = "agent_id") -> None:
    participants = state.get("participants")
    if isinstance(participants, list) and participants and agent_id not in participants:
        raise ValueError(f"{field} {agent_id!r} is not in collaboration participants")


def require_collaboration_actor(state: dict[str, Any], agent_id: str, field: str = "agent_id") -> None:
    """Require a local participant or the main room orchestrator.

    Main is not always listed in task-local `participants`, but Alex's desired
    discussion model is tri-agent: openclaw-main + Codex + Claude Code.  Points
    and uptakes therefore allow openclaw-main as an explicit system participant
    while keeping arbitrary outsiders out of the ledger.
    """
    agent_id = require_nonempty(agent_id, field)
    if agent_id in SYSTEM_PARTICIPANTS:
        return
    work_items = state.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("source_agent_id") or "").strip() == agent_id:
                return
    require_participant(state, agent_id, field)


def find_point(state: dict[str, Any], point_id: str) -> dict[str, Any]:
    points = state.get("points")
    if not isinstance(points, list):
        raise ValueError("ledger state has no points list")
    for point in points:
        if isinstance(point, dict) and point.get("id") == point_id:
            return point
    raise KeyError(f"unknown point_id: {point_id}")


def require_current_owner(state: dict[str, Any], item: dict[str, Any], agent_id: str, field: str = "agent_id") -> str:
    agent_id = require_nonempty(agent_id, field)
    require_participant(state, agent_id, field)
    current_owner = str(item.get("claimed_by") or "").strip()
    if not current_owner:
        raise ValueError(f"work item {item.get('id')!r} is not claimed")
    if current_owner != agent_id:
        raise ValueError(f"work item is claimed by {current_owner}, not {agent_id}")
    return agent_id


def claim_conflict(item: dict[str, Any], agent_id: str) -> str | None:
    current_owner = str(item.get("claimed_by") or "").strip()
    status = str(item.get("status") or "").strip()
    handoff_to = str(item.get("handoff_to") or "").strip()
    if current_owner and current_owner != agent_id:
        if status == "handoff" and handoff_to == agent_id:
            return None
        if status in {"completed", "cancelled"}:
            return f"work item is already {status} by {current_owner}"
        # Lease expired → no conflict, allow re-claim
        if is_claim_expired(item):
            return None
        return f"work item already claimed by {current_owner}"
    return None


def make_event(state: dict[str, Any], event_type: str, at: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": EVENT_SCHEMA,
        "event_type": event_type,
        "at": at,
        "room_id": state.get("room_id"),
        "task_id": state.get("task_id"),
        "run_id": state.get("run_id"),
        "turn_seq": state.get("turn_seq"),
        "payload": payload,
    }


def upsert_claim(state: dict[str, Any], claim: dict[str, Any]) -> None:
    claims = state.setdefault("claims", [])
    if not isinstance(claims, list):
        raise ValueError("ledger state claims field is not a list")
    for existing in claims:
        if (
            isinstance(existing, dict)
            and existing.get("work_item_id") == claim.get("work_item_id")
            and existing.get("agent_id") == claim.get("agent_id")
        ):
            existing.update(claim)
            return
    claims.append(claim)


def update_common(state: dict[str, Any], at: str) -> None:
    refresh_state_status(state)
    state["updated_at"] = at


def refresh_state_status(state: dict[str, Any]) -> None:
    work_items = state.get("work_items")
    if not isinstance(work_items, list) or not work_items:
        return
    statuses = [
        str(item.get("status") or "").strip()
        for item in work_items
        if isinstance(item, dict)
    ]
    if not statuses:
        return
    if all(status == "completed" for status in statuses):
        state["status"] = "completed"
    elif all(status in TERMINAL_WORK_ITEM_STATUSES for status in statuses):
        state["status"] = "blocked" if "blocked" in statuses else "completed"
    else:
        state["status"] = "open"


def update_active_claim_status(
    state: dict[str, Any],
    *,
    work_item_id: str,
    agent_id: str,
    status: str,
    at: str,
) -> None:
    if status not in TERMINAL_WORK_ITEM_STATUSES | RETRYABLE_WORK_ITEM_STATUSES:
        return
    claims = state.get("claims")
    if not isinstance(claims, list):
        return
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if claim.get("work_item_id") != work_item_id or claim.get("agent_id") != agent_id:
            continue
        if str(claim.get("status") or "").strip() not in ACTIVE_CLAIM_STATUSES | {"handoff"}:
            continue
        claim["status"] = status
        claim["updated_at"] = at
        if status == "completed":
            claim["completed_at"] = at
        elif status == "blocked":
            claim["blocked_at"] = at
        elif status == "cancelled":
            claim["cancelled_at"] = at
        elif status == "retryable":
            claim["retryable_at"] = at


def run_with_lock(args: argparse.Namespace, handler: Any) -> int:
    state_path = Path(args.state_file).expanduser().resolve()
    archive_path = Path(args.archive_file).expanduser().resolve()
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return handler(args, state_path, archive_path)


def cmd_init(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    task_path = Path(args.task_file).expanduser().resolve()
    task = load_json(task_path)
    if not isinstance(task, dict):
        return output({"ok": False, "error": "task manifest root is not an object", "file": str(task_path)}, 2)
    if args.if_needed and state_path.exists():
        try:
            existing = load_state(state_path)
        except Exception:
            existing = {}
        if (
            isinstance(existing, dict)
            and existing.get("task_id") == task.get("task_id")
            and existing.get("run_id") == (task.get("run_id") or task.get("task_id"))
        ):
            return output({
                "ok": True,
                "event_type": "init",
                "status": "already_current",
                "state": str(state_path),
                "archive": str(archive_path),
                "task_id": existing.get("task_id"),
            })
    at = now_iso()
    turn_seq = args.turn_seq if args.turn_seq is not None else load_turn_seq(Path(args.turn_file).expanduser())
    state = state_from_task(task, turn_seq, at)
    # Dedup check: warn if open ledgers exist for the same mainline_id + dedupe_key
    governance = state.get("governance", {})
    ledger_dir = state_path.parent / "collaboration-ledgers" if (state_path.parent / "collaboration-ledgers").is_dir() else state_path.parent
    dedup_warnings = dedup_check(governance, ledger_dir, current_task_id=task.get("task_id"))
    write_json_atomic(state_path, state)
    event = make_event(state, "init", at, {"task_file": str(task_path)})
    append_jsonl(archive_path, event)
    result: dict[str, Any] = {"ok": True, "event_type": "init", "state": str(state_path), "archive": str(archive_path), "task_id": state.get("task_id")}
    if governance:
        result["governance"] = governance
    if dedup_warnings:
        result["dedup_warnings"] = dedup_warnings
    return output(result)


def cmd_claim(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    at = now_iso()
    state = load_state(state_path)
    agent_id = require_nonempty(args.agent_id, "agent_id")
    require_participant(state, agent_id)
    item = find_work_item(state, args.work_item_id)
    previous_owner = str(item.get("claimed_by") or "").strip()
    expired_takeover = False
    conflict = claim_conflict(item, agent_id)
    if conflict:
        raise ValueError(conflict)
    # Detect expired-claim takeover: previous owner different but lease expired
    if previous_owner and previous_owner != agent_id and is_claim_expired(item):
        expired_takeover = True
    item["status"] = "claimed"
    item["claimed_by"] = agent_id
    item["claimed_at"] = item.get("claimed_at") if previous_owner == agent_id else at
    # Set lease expiry
    now = datetime.now().astimezone()
    lease_seconds_arg = getattr(args, "lease_seconds", None)
    lease_seconds = lease_seconds_arg if lease_seconds_arg is not None else CLAIM_LEASE_SECONDS
    from datetime import timedelta
    lease_expiry = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    item["lease_expiry"] = lease_expiry
    item.pop("handoff_to", None)
    # Clear stale claim info on expired takeover
    if expired_takeover:
        item.pop("lease_expiry_previous", None)
    handoffs = state.get("handoffs")
    if isinstance(handoffs, list):
        for handoff in handoffs:
            if (
                isinstance(handoff, dict)
                and handoff.get("work_item_id") == args.work_item_id
                and handoff.get("to_agent") == agent_id
                and handoff.get("status") == "open"
            ):
                handoff["status"] = "accepted"
                handoff["accepted_at"] = at
    claim = {
        "work_item_id": args.work_item_id,
        "agent_id": agent_id,
        "status": args.status,
        "claimed_at": at,
        "lease_expiry": lease_expiry,
    }
    if expired_takeover:
        claim["expired_takeover_from"] = previous_owner
    if args.note:
        claim["note"] = args.note
    upsert_claim(state, claim)
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "claim", at, claim))
    result = {"ok": True, "event_type": "claim", "work_item_id": args.work_item_id, "agent_id": agent_id, "lease_expiry": lease_expiry}
    if expired_takeover:
        result["expired_takeover_from"] = previous_owner
    return output(result)


def cmd_status(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    at = now_iso()
    state = load_state(state_path)
    item = find_work_item(state, args.work_item_id)
    agent_id = None
    if args.agent_id:
        agent_id = require_current_owner(state, item, args.agent_id)
    item["status"] = args.status
    item["updated_at"] = at
    payload = {"work_item_id": args.work_item_id, "status": args.status}
    if agent_id:
        payload["agent_id"] = agent_id
        update_active_claim_status(
            state,
            work_item_id=args.work_item_id,
            agent_id=agent_id,
            status=args.status,
            at=at,
        )
    if args.note:
        payload["note"] = args.note
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "status", at, payload))
    return output({"ok": True, "event_type": "status", "work_item_id": args.work_item_id, "status": args.status})


def cmd_artifact(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    at = now_iso()
    state = load_state(state_path)
    item = find_work_item(state, args.work_item_id)
    agent_id = require_current_owner(state, item, args.agent_id)
    artifacts = state.setdefault("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("ledger state artifacts field is not a list")
    artifact = {
        "id": f"art-{len(artifacts) + 1:03d}",
        "work_item_id": args.work_item_id,
        "type": args.type,
        "title": args.title,
        "path": args.path,
        "agent_id": agent_id,
        "produced_by": agent_id,
        "produced_at": at,
    }
    artifacts.append(artifact)
    if args.status:
        item["status"] = args.status
        item["updated_at"] = at
        update_active_claim_status(
            state,
            work_item_id=args.work_item_id,
            agent_id=agent_id,
            status=args.status,
            at=at,
        )
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "artifact", at, artifact))
    return output({"ok": True, "event_type": "artifact", "artifact_id": artifact["id"], "path": args.path})


def cmd_blocker(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    at = now_iso()
    state = load_state(state_path)
    item = find_work_item(state, args.work_item_id)
    agent_id = require_current_owner(state, item, args.agent_id)
    blockers = state.setdefault("blockers", [])
    if not isinstance(blockers, list):
        raise ValueError("ledger state blockers field is not a list")
    blocker = {
        "id": f"blk-{len(blockers) + 1:03d}",
        "work_item_id": args.work_item_id,
        "agent_id": agent_id,
        "reason": args.reason,
        "detail": args.detail,
        "blocked_at": at,
        "status": "open",
    }
    blockers.append(blocker)
    item["status"] = "blocked"
    item["updated_at"] = at
    update_active_claim_status(
        state,
        work_item_id=args.work_item_id,
        agent_id=agent_id,
        status="blocked",
        at=at,
    )
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "blocker", at, blocker))
    return output({"ok": True, "event_type": "blocker", "blocker_id": blocker["id"], "reason": args.reason})


def cmd_handoff(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    at = now_iso()
    state = load_state(state_path)
    from_agent = require_nonempty(args.from_agent, "from_agent")
    to_agent = require_nonempty(args.to_agent, "to_agent")
    if from_agent == to_agent:
        raise ValueError("handoff target must differ from source agent")
    require_participant(state, from_agent, "from_agent")
    require_participant(state, to_agent, "to_agent")
    item = find_work_item(state, args.work_item_id)
    current_owner = str(item.get("claimed_by") or "").strip()
    if not current_owner:
        raise ValueError(f"work item {args.work_item_id!r} is not claimed")
    if current_owner != from_agent:
        raise ValueError(f"work item is claimed by {current_owner}, not {from_agent}")
    claims = state.setdefault("claims", [])
    if not isinstance(claims, list):
        raise ValueError("ledger state claims field is not a list")
    for claim in claims:
        if (
            isinstance(claim, dict)
            and claim.get("work_item_id") == args.work_item_id
            and claim.get("agent_id") == from_agent
            and str(claim.get("status") or "") in {"active", "claimed", "running"}
        ):
            claim["status"] = "handoff"
            claim["handoff_to"] = to_agent
            claim["handed_off_at"] = at
    handoffs = state.setdefault("handoffs", [])
    if not isinstance(handoffs, list):
        raise ValueError("ledger state handoffs field is not a list")
    handoff = {
        "id": f"handoff-{len(handoffs) + 1:03d}",
        "work_item_id": args.work_item_id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "reason": args.reason,
        "created_at": at,
        "status": "open",
    }
    handoffs.append(handoff)
    item["status"] = "handoff"
    item["handoff_to"] = to_agent
    item["updated_at"] = at
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "handoff", at, handoff))
    return output({"ok": True, "event_type": "handoff", "handoff_id": handoff["id"], "to_agent": to_agent})


def cmd_accept(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    """Record an acceptance verdict on a work item's output.

    This closes the production loop: an agent produces an artifact, a peer or
    main reviews it and marks accepted / rejected / superseded, and downstream
    consumers (e.g. standing_agenda_tick) can check whether a work item needs
    re-execution.

    acceptance status values: pending | accepted | rejected | superseded
    """
    at = now_iso()
    state = load_state(state_path)
    reviewer = require_nonempty(args.reviewer, "reviewer")
    require_participant(state, reviewer, "reviewer")
    verdict = require_nonempty(args.verdict, "verdict")
    if verdict not in ACCEPTANCE_STATUSES:
        raise ValueError(f"invalid acceptance verdict {verdict!r}; must be one of {sorted(ACCEPTANCE_STATUSES)}")
    item = find_work_item(state, args.work_item_id)
    current_owner = str(item.get("claimed_by") or "").strip()
    if current_owner and reviewer == current_owner:
        raise ValueError(f"reviewer must differ from work item owner {current_owner!r}")
    acceptance = {
        "verdict": verdict,
        "reviewer": reviewer,
        "reviewed_at": at,
    }
    if args.reason:
        acceptance["reason"] = args.reason
    if args.artifact_path:
        acceptance["artifact_path"] = args.artifact_path
    # Store acceptance history on the work item
    acceptance_history = item.setdefault("acceptance_history", [])
    if not isinstance(acceptance_history, list):
        acceptance_history = []
        item["acceptance_history"] = acceptance_history
    acceptance_history.append(acceptance)
    # Current acceptance verdict (latest wins)
    item["acceptance"] = verdict
    item["acceptance_reviewed_by"] = reviewer
    item["acceptance_reviewed_at"] = at
    if args.reason:
        item["acceptance_reason"] = args.reason
    item["updated_at"] = at
    # If accepted and work item is still claimed, mark it completed
    if verdict == "accepted" and str(item.get("status") or "") in ACTIVE_CLAIM_STATUSES | {"handoff"}:
        item["status"] = "completed"
        item["completed_at"] = at
        update_active_claim_status(
            state,
            work_item_id=args.work_item_id,
            agent_id=str(item.get("claimed_by") or ""),
            status="completed",
            at=at,
        )
    # If rejected, reset item to open so it can be re-claimed
    if verdict == "rejected" and str(item.get("status") or "") in TERMINAL_WORK_ITEM_STATUSES | ACTIVE_CLAIM_STATUSES:
        item["status"] = "open"
        item["claimed_by"] = None
        item.pop("lease_expiry", None)
    # Auto-create uptakes: when a peer reviews a work item, record their uptake
    # status for any points from other agents linked to the same work_item_id.
    # This closes the point→uptake loop without requiring manual cmd_uptake calls.
    auto_uptakes = []
    if verdict in {"accepted", "rejected", "superseded"}:
        points = state.get("points")
        if isinstance(points, list):
            existing_uptakes = state.setdefault("uptakes", [])
            for point in points:
                if not isinstance(point, dict):
                    continue
                if point.get("work_item_id") != args.work_item_id:
                    continue
                pt_agent = str(point.get("agent_id") or "").strip()
                if not pt_agent or pt_agent == reviewer:
                    continue
                uptake_id = f"uptake-{len(existing_uptakes) + len(auto_uptakes) + 1:03d}"
                uptake_reason = (
                    f"auto_uptake_from_{verdict}_verdict"
                    if not args.reason
                    else f"auto_uptake_from_{verdict}_verdict: {args.reason[:200]}"
                )
                uptake = {
                    "id": uptake_id,
                    "point_id": point.get("id"),
                    "point_agent_id": pt_agent,
                    "by_agent": reviewer,
                    "status": verdict,
                    "reason": uptake_reason[:500],
                    "created_at": at,
                }
                if args.artifact_path:
                    uptake["artifact_path"] = args.artifact_path
                existing_uptakes.append(uptake)
                by_agent_status = point.setdefault("uptake_status_by_agent", {})
                by_agent_status[reviewer] = verdict
                point["last_uptake_at"] = at
                auto_uptakes.append({"uptake_id": uptake_id, "point_id": point.get("id"), "status": verdict})
                append_jsonl(archive_path, make_event(state, "uptake", at, uptake))
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "accept", at, {"work_item_id": args.work_item_id, **acceptance}))
    result = {"ok": True, "event_type": "accept", "work_item_id": args.work_item_id, "verdict": verdict, "reviewer": reviewer}
    if auto_uptakes:
        result["auto_uptakes"] = auto_uptakes
    return output(result)


def cmd_point(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    """Record a discussion point/proposal/risk/evidence item.

    A point is the durable version of "someone said something material".  It
    does not require a visible chat reply; later agents can record an uptake to
    show they directly answered it, silently incorporated it, challenged it, or
    left it pending.
    """
    at = now_iso()
    state = load_state(state_path)
    agent_id = require_nonempty(args.agent_id, "agent_id")
    require_collaboration_actor(state, agent_id)
    kind = require_nonempty(args.kind, "kind")
    if kind not in POINT_KINDS:
        raise ValueError(f"invalid point kind {kind!r}; must be one of {sorted(POINT_KINDS)}")
    points = state.setdefault("points", [])
    if not isinstance(points, list):
        raise ValueError("ledger state points field is not a list")
    point_id = args.point_id or f"pt-{len(points) + 1:03d}"
    if any(isinstance(point, dict) and point.get("id") == point_id for point in points):
        raise ValueError(f"point_id already exists: {point_id}")
    point = {
        "id": point_id,
        "agent_id": agent_id,
        "kind": kind,
        "text": require_nonempty(args.text, "text")[:2000],
        "created_at": at,
        "status": "open",
        "uptake_status_by_agent": {},
    }
    if args.work_item_id:
        point["work_item_id"] = args.work_item_id
    if args.source_artifact:
        point["source_artifact"] = args.source_artifact
    if args.source_message_id:
        point["source_message_id"] = args.source_message_id
    points.append(point)
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "point", at, point))
    return output({"ok": True, "event_type": "point", "point_id": point_id, "agent_id": agent_id, "kind": kind})


def cmd_uptake(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    """Record how an agent received a point.

    Uptake supports both visible reply semantics (`challenged`, `accepted`) and
    silent-but-durable behavior changes (`incorporated`).  This is the missing
    middle layer between noisy group chat and invisible private reasoning.
    """
    at = now_iso()
    state = load_state(state_path)
    point = find_point(state, args.point_id)
    by_agent = require_nonempty(args.by_agent, "by_agent")
    require_collaboration_actor(state, by_agent, "by_agent")
    status = require_nonempty(args.status, "status")
    if status not in UPTAKE_STATUSES:
        raise ValueError(f"invalid uptake status {status!r}; must be one of {sorted(UPTAKE_STATUSES)}")
    uptakes = state.setdefault("uptakes", [])
    if not isinstance(uptakes, list):
        raise ValueError("ledger state uptakes field is not a list")
    uptake = {
        "id": f"uptake-{len(uptakes) + 1:03d}",
        "point_id": args.point_id,
        "point_agent_id": point.get("agent_id"),
        "by_agent": by_agent,
        "status": status,
        "reason": require_nonempty(args.reason, "reason")[:2000],
        "created_at": at,
    }
    if args.behavior_impact:
        uptake["behavior_impact"] = args.behavior_impact[:2000]
    if args.artifact_path:
        uptake["artifact_path"] = args.artifact_path
    uptakes.append(uptake)
    by_agent_status = point.setdefault("uptake_status_by_agent", {})
    if not isinstance(by_agent_status, dict):
        by_agent_status = {}
        point["uptake_status_by_agent"] = by_agent_status
    by_agent_status[by_agent] = {
        "status": status,
        "reason": uptake["reason"],
        "updated_at": at,
    }
    point["last_uptake_at"] = at
    if status in {"accepted", "incorporated", "challenged", "rejected", "superseded"}:
        point["status"] = status
    update_common(state, at)
    write_json_atomic(state_path, state)
    append_jsonl(archive_path, make_event(state, "uptake", at, uptake))
    return output({"ok": True, "event_type": "uptake", "uptake_id": uptake["id"], "point_id": args.point_id, "by_agent": by_agent, "status": status})


def cmd_show(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    state = load_state(state_path)
    return output({"ok": True, "state": state, "archive": str(archive_path)})


def cmd_release_expired(args: argparse.Namespace, state_path: Path, archive_path: Path) -> int:
    """Release or terminally block work items whose claim lease has expired."""
    at = now_iso()
    now = datetime.now().astimezone()
    state = load_state(state_path)
    work_items = state.get("work_items")
    if not isinstance(work_items, list):
        raise ValueError("ledger state has no work_items list")
    target_work_item_id = str(getattr(args, "work_item_id", None) or "").strip() or None
    target_agent_id = str(getattr(args, "agent_id", None) or "").strip() or None
    mode = str(getattr(args, "mode", "release") or "release").strip()
    reason = str(getattr(args, "reason", None) or "claim_lease_expired").strip()
    detail = str(
        getattr(args, "detail", None)
        or "Claim lease expired before the assigned agent produced an artifact or blocker."
    ).strip()
    released = []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        if not claim_matches_filter(item, work_item_id=target_work_item_id, agent_id=target_agent_id):
            continue
        if is_claim_expired(item, at=now):
            work_item_id = str(item.get("id") or "").strip()
            previous_owner = str(item.get("claimed_by") or "").strip()
            previous_status = str(item.get("status") or "").strip()
            if mode == "block":
                item["status"] = "blocked"
                item["blocked_at"] = at
                item["blocked_reason"] = reason
            else:
                item["status"] = "open"
                item["claimed_by"] = None
            item.pop("lease_expiry", None)
            item.pop("handoff_to", None)
            item["last_expired_claim_owner"] = previous_owner or None
            item["last_expired_claim_released_at"] = at
            item["updated_at"] = at
            released_claims = []
            claims = state.get("claims")
            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue
                    if str(claim.get("work_item_id") or "").strip() != work_item_id:
                        continue
                    if previous_owner and str(claim.get("agent_id") or "").strip() != previous_owner:
                        continue
                    if str(claim.get("status") or "").strip() not in ACTIVE_CLAIM_STATUSES | {"handoff"}:
                        continue
                    claim_previous_status = str(claim.get("status") or "").strip()
                    claim["status"] = "blocked" if mode == "block" else "expired_released"
                    claim["previous_status"] = claim_previous_status
                    claim["released_at"] = at
                    claim["release_reason"] = reason
                    released_claims.append({
                        "agent_id": claim.get("agent_id"),
                        "previous_status": claim_previous_status,
                    })
            released.append({
                "work_item_id": work_item_id or item.get("id"),
                "previous_owner": previous_owner,
                "previous_status": previous_status,
                "released_claims": released_claims,
            })
    if mode == "block" and released:
        blockers = state.setdefault("blockers", [])
        if not isinstance(blockers, list):
            blockers = []
            state["blockers"] = blockers
        existing = {
            (str(blocker.get("work_item_id") or ""), str(blocker.get("reason") or ""))
            for blocker in blockers
            if isinstance(blocker, dict)
        }
        for item in released:
            work_item_id = str(item.get("work_item_id") or "")
            if not work_item_id or (work_item_id, reason) in existing:
                continue
            blockers.append({
                "id": f"blk-{len(blockers) + 1:03d}",
                "work_item_id": work_item_id,
                "agent_id": "agent-room-collaboration-lease",
                "reason": reason,
                "detail": detail,
                "blocked_at": at,
                "status": "closed_by_lease_expiry",
            })
            existing.add((work_item_id, reason))
    if released:
        update_common(state, at)
        write_json_atomic(state_path, state)
        payload = {"released": released, "count": len(released), "mode": mode, "reason": reason}
        append_jsonl(archive_path, make_event(state, "release_expired", at, payload))
    return output({"ok": True, "event_type": "release_expired", "mode": mode, "released_count": len(released), "released": released})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain the current Agent Room collaboration ledger and append-only audit trail.")
    parser.add_argument("--state-file", default=str(STATE_FILE), help="Current collaboration ledger JSON path.")
    parser.add_argument("--archive-file", default=str(ARCHIVE_FILE), help="Append-only collaboration ledger JSONL path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize current ledger state from a task manifest.")
    init.add_argument("--task-file", required=True)
    init.add_argument("--turn-file", default=str(TURN_FILE))
    init.add_argument("--turn-seq", type=int)
    init.add_argument("--if-needed", action="store_true", help="Do not rewrite or append when the current ledger already tracks the same task/run.")
    init.set_defaults(func=cmd_init)

    claim = subparsers.add_parser("claim", help="Claim an existing work item.")
    claim.add_argument("--work-item-id", required=True)
    claim.add_argument("--agent-id", required=True)
    claim.add_argument("--status", default="active")
    claim.add_argument("--note")
    claim.add_argument("--lease-seconds", type=int)
    claim.set_defaults(func=cmd_claim)

    status = subparsers.add_parser("status", help="Update an existing work item's status.")
    status.add_argument("--work-item-id", required=True)
    status.add_argument("--status", required=True)
    status.add_argument("--agent-id")
    status.add_argument("--note")
    status.set_defaults(func=cmd_status)

    artifact = subparsers.add_parser("artifact", help="Record an artifact for a work item.")
    artifact.add_argument("--work-item-id", required=True)
    artifact.add_argument("--agent-id", required=True)
    artifact.add_argument("--type", default="artifact")
    artifact.add_argument("--title", required=True)
    artifact.add_argument("--path", required=True)
    artifact.add_argument("--status")
    artifact.set_defaults(func=cmd_artifact)

    blocker = subparsers.add_parser("blocker", help="Record a blocker for a work item.")
    blocker.add_argument("--work-item-id", required=True)
    blocker.add_argument("--agent-id", required=True)
    blocker.add_argument("--reason", required=True)
    blocker.add_argument("--detail", required=True)
    blocker.set_defaults(func=cmd_blocker)

    handoff = subparsers.add_parser("handoff", help="Record a handoff for a work item.")
    handoff.add_argument("--work-item-id", required=True)
    handoff.add_argument("--from-agent", required=True)
    handoff.add_argument("--to-agent", required=True)
    handoff.add_argument("--reason", required=True)
    handoff.set_defaults(func=cmd_handoff)

    accept = subparsers.add_parser("accept", help="Record an acceptance verdict on a work item's output.")
    accept.add_argument("--work-item-id", required=True)
    accept.add_argument("--reviewer", required=True, help="Agent id of the reviewer (must be a participant).")
    accept.add_argument("--verdict", required=True, choices=sorted(ACCEPTANCE_STATUSES), help="Acceptance verdict: pending | accepted | rejected | superseded.")
    accept.add_argument("--reason", help="Reason for the verdict (required for rejected).")
    accept.add_argument("--artifact-path", help="Optional artifact path that was reviewed.")
    accept.set_defaults(func=cmd_accept)

    point = subparsers.add_parser("point", help="Record a material discussion point/proposal/risk/evidence item.")
    point.add_argument("--agent-id", required=True)
    point.add_argument("--kind", required=True, choices=sorted(POINT_KINDS))
    point.add_argument("--text", required=True)
    point.add_argument("--point-id")
    point.add_argument("--work-item-id")
    point.add_argument("--source-artifact")
    point.add_argument("--source-message-id")
    point.set_defaults(func=cmd_point)

    uptake = subparsers.add_parser("uptake", help="Record that an agent noticed, challenged, incorporated, or rejected a point.")
    uptake.add_argument("--point-id", required=True)
    uptake.add_argument("--by-agent", required=True)
    uptake.add_argument("--status", required=True, choices=sorted(UPTAKE_STATUSES))
    uptake.add_argument("--reason", required=True)
    uptake.add_argument("--behavior-impact")
    uptake.add_argument("--artifact-path")
    uptake.set_defaults(func=cmd_uptake)

    show = subparsers.add_parser("show", help="Print current ledger state.")
    show.set_defaults(func=cmd_show)

    release_expired = subparsers.add_parser(
        "release-expired",
        help="Release or block expired claimed/handoff work items.",
    )
    release_expired.add_argument("--work-item-id", help="Only process this work item.")
    release_expired.add_argument("--agent-id", help="Only process this agent's claim.")
    release_expired.add_argument("--mode", choices=["release", "block"], default="release")
    release_expired.add_argument("--reason", default="claim_lease_expired")
    release_expired.add_argument("--detail", default="Claim lease expired before the assigned agent produced an artifact or blocker.")
    release_expired.set_defaults(func=cmd_release_expired)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_with_lock(args, args.func)
    except Exception as exc:
        return output({"ok": False, "error": str(exc), "command": args.command}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
