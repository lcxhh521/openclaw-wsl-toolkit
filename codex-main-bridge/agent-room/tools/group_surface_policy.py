#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "group-surface-policy.json"

REQUIRED_TOP_KEYS = {
    "schema",
    "scope",
    "surfaces",
    "allow_visible_group_send_when",
    "suppress_or_move_private_when",
    "message_shape",
    "multi_agent_rules",
    "automatic_local_action_allowed",
    "automatic_bounded_runtime_action_allowed",
    "record_blocker_or_handoff_instead_of_asking_alex_when",
    "requires_alex_or_private_confirmation",
    "rate_limits",
}

SENSITIVE_MARKERS = {
    "secret", "secrets", "token", "oauth", "api key", "apikey", "password", "auth", "cookie",
    "私聊", "账号", "密钥", "令牌", "密码", "凭证",
}
RAW_INTERNAL_MARKERS = {
    "raw_internal_json_body", "runner_failed", "traceback", "stderr", "stdout", "{\"status\"", "accepted:",
}
ROUTINE_MARKERS = {"heartbeat", "心跳", "retry ok", "重试成功", "ack", "我同意"}
MATERIAL_MARKERS = {
    "证据", "修复", "验证", "通过", "失败", "blocker", "决策", "落地", "实现", "patch", "smoke",
    "不同意", "反例", "风险", "结论",
}
GROUP_COLLAB_MARKERS = {
    "agent room", "群聊", "peer", "codex", "claude", "模型路由", "surface", "协作", "机制",
}


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("policy root must be object")
    missing = sorted(REQUIRED_TOP_KEYS - set(value))
    if missing:
        raise ValueError(f"policy missing required keys: {missing}")
    return value


def contains_any(text: str, markers: set[str]) -> list[str]:
    lower = text.lower()
    return sorted([m for m in markers if m.lower() in lower])


def classify(text: str, *, origin: str = "group", directly_mentioned: bool = False, group_origin_task: bool = False) -> dict[str, Any]:
    """Classify group-surface visibility using explicit metadata plus coarse guards.

    This is not an intent router and does not grant permissions. It is a guardrail
    helper for Agent Room visible group output: sensitive/private/raw/routine
    content is suppressed; direct mentions and material group-collaboration
    content are allowed.
    """
    text = text or ""
    sensitive = contains_any(text, SENSITIVE_MARKERS)
    raw_internal = contains_any(text, RAW_INTERNAL_MARKERS)
    routine = contains_any(text, ROUTINE_MARKERS)
    material = contains_any(text, MATERIAL_MARKERS)
    group_collab = contains_any(text, GROUP_COLLAB_MARKERS)
    reasons: list[str] = []
    surface = "log_only"
    visible = False

    if sensitive:
        surface = "private_to_alex"
        reasons.append("sensitive_or_private_marker")
    elif raw_internal:
        surface = "log_only"
        reasons.append("raw_internal_or_log_payload")
    elif routine and not material:
        surface = "log_only"
        reasons.append("routine_or_low_value_ack")
    elif directly_mentioned:
        surface = "agent_room_group"
        visible = True
        reasons.append("direct_mention_response")
    elif group_origin_task and material:
        surface = "agent_room_group"
        visible = True
        reasons.append("group_origin_task_material_milestone")
    elif material and group_collab:
        surface = "agent_room_group"
        visible = True
        reasons.append("active_group_thread_material_contribution")
    else:
        reasons.append("no_group_visible_material_signal")

    return {
        "schema": "openclaw.agent_room.group_surface_decision.v0",
        "visible_in_group": visible,
        "surface": surface,
        "reasons": reasons,
        "signals": {
            "directly_mentioned": directly_mentioned,
            "group_origin_task": group_origin_task,
            "sensitive_markers": sensitive,
            "raw_internal_markers": raw_internal,
            "routine_markers": routine,
            "material_markers": material,
            "group_collab_markers": group_collab,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate/classify Agent Room group-surface messages")
    ap.add_argument("--policy", default=str(POLICY_PATH))
    ap.add_argument("--classify", default="")
    ap.add_argument("--directly-mentioned", action="store_true")
    ap.add_argument("--group-origin-task", action="store_true")
    args = ap.parse_args()
    load_policy(Path(args.policy))
    if args.classify:
        print(json.dumps(classify(args.classify, directly_mentioned=args.directly_mentioned, group_origin_task=args.group_origin_task), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": True, "policy": args.policy}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
