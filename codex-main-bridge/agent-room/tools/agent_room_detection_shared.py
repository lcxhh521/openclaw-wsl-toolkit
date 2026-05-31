#!/usr/bin/env python3
from __future__ import annotations


def idle_agent_contribution_problem_requested(text: str) -> bool:
    lowered = str(text or "").lower()
    agent_marker = any(marker in lowered for marker in (
        "agent", "bot", "机器人", "codex", "claude", "claudecode",
        "lchcodex", "lchclaude", "你们",
    ))
    idle_or_work_marker = any(marker in lowered for marker in (
        "闲着", "闲下来", "闲下来了", "没活干", "没有活干", "没活", "没有活",
        "找活干", "找活", "自己找活", "主动找活",
        "应该干活", "要干活", "继续干活",
    ))
    prior_rule_marker = any(marker in lowered for marker in (
        "我们不是说了", "不是说了", "之前说了", "不是应该",
        "应该", "要找活", "不能闲着", "不该闲着",
        "又闲", "又闲下来", "又闲下来了",
    ))
    return agent_marker and idle_or_work_marker and prior_rule_marker
