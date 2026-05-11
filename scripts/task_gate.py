#!/usr/bin/env python3
"""Task execution gate for local OpenClaw workflows.

This wrapper makes Task Router V0 an actual admission gate. It is meant for
systemd services, manual retry scripts, and supervisor catch-up starts. It does
not call the OpenClaw gateway. It can either print a decision or exec the target
command only when the decision is allowed.
"""
from __future__ import annotations

import argparse
import shutil
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE / "scripts") not in sys.path:
    sys.path.insert(0, str(WORKSPACE / "scripts"))

from task_router_core import route_task  # noqa: E402

DEFAULT_ALLOWED = {"inline", "worker", "review-required"}
DEFER_CODE = 75


def emit_event(decision: dict, *, allowed: bool, reason: str) -> None:
    path = WORKSPACE / "memory" / "task-router" / "gate-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": decision.get("generated_at"),
        "allowed": allowed,
        "reason": reason,
        "task_type": decision.get("task_type"),
        "route": decision.get("route"),
        "signals": decision.get("signals"),
        "constraints": decision.get("constraints"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate a local task before starting it.")
    ap.add_argument("--task-type", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--expected-seconds", type=int)
    ap.add_argument("--chunk-count", type=int)
    ap.add_argument("--model-calls", type=int)
    ap.add_argument("--external-side-effect", action="store_true")
    ap.add_argument("--needs-openclaw-native", action="store_true")
    ap.add_argument("--allow-routes", default="inline,worker,review-required")
    ap.add_argument("--defer-when-gateway-hot", action="store_true")
    ap.add_argument("--low-priority", action="store_true")
    ap.add_argument("--nice", type=int, default=10)
    ap.add_argument("--print-decision", action="store_true")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    allowed_routes = {x.strip() for x in args.allow_routes.split(",") if x.strip()} or DEFAULT_ALLOWED
    decision = route_task(
        text=args.text or args.task_type,
        task_type=args.task_type,
        expected_seconds=args.expected_seconds,
        chunk_count=args.chunk_count,
        model_calls=args.model_calls,
        external_side_effect=True if args.external_side_effect else None,
        needs_openclaw_native=True if args.needs_openclaw_native else None,
    )
    gateway_hot = bool((decision.get("signals") or {}).get("gateway_hot"))
    route = str(decision.get("route") or "")
    reason = "allowed"
    allowed = True
    if args.defer_when_gateway_hot and gateway_hot and args.needs_openclaw_native:
        allowed = False
        reason = "gateway_hot_defer_native_runtime"
    elif route not in allowed_routes:
        allowed = False
        reason = f"route_not_allowed:{route}"
    if args.print_decision or not command:
        print(json.dumps({"allowed": allowed, "reason": reason, "decision": decision}, ensure_ascii=False, indent=2))
    emit_event(decision, allowed=allowed, reason=reason)
    if not allowed:
        if not args.print_decision and command:
            print(json.dumps({"allowed": False, "reason": reason, "decision": decision}, ensure_ascii=False), file=sys.stderr)
        return DEFER_CODE
    if not command:
        return 0
    if args.low_priority:
        command = ["nice", "-n", str(args.nice), *command]
        if shutil.which("ionice"):
            command = ["ionice", "-c3", *command]
    completed = subprocess.run(command)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
