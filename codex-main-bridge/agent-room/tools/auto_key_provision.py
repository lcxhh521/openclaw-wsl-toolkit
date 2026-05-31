#!/usr/bin/env python3
"""
auto_key_provision.py — 自动检测 Alex "要添加 API Key" 意图，自动准备密钥环境。

设计目的：
  Alex 说「我要添加新的apikey」时，无需自己输入命令。
  本脚本检测到关键词后，自动准备好 ~/.openclaw/secrets 目录结构，
  并在房间回应一条可直接复制粘贴的单行命令。

用法（由房间桥接守护进程轮询触发）：
  python3 auto_key_provision.py [--dry-run]

检测关键词（全在文字中搜索，不区分大小写）：
  - 添加(新)?(的)?(api|API)?key
  - add(新)?(的|new)?(api|API)?key
  - 粘贴key / paste key
  - 输入key
  - 注册key

回应策略：
  - 同一条消息不重复响应（基于消息 id/hash 去重）
  - 检测到意图后只回应一次，避免刷屏
  - 不暴露完整 key，不提原始 key 文本
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw" / "workspace" / "codex-main-bridge"
ROOM = ROOT / "agent-room"
MESSAGES_FILE = ROOM / "messages.jsonl"
RESPONSE_HISTORY_FILE = ROOM / "tools/.auto_key_provision_responded.json"
SECRETS_DIR = Path.home() / ".openclaw" / "secrets"
SIGNAL_DIR = ROOM / "signals"
ADD_KEY_SIGNAL_FILE = SIGNAL_DIR / "add-key.json"

# 检测关键词模式 —— Alex 说"添加apikey"就能触发
KEYWORD_PATTERNS = [
    re.compile(r"添加(?:新)?(?:的)?(?:api|API)?[_\s-]?key", re.IGNORECASE),
    re.compile(r"add(?:新|的|new)?[_\s-]?(?:api|API)?[_\s-]?key", re.IGNORECASE),
    re.compile(r"粘贴[_\s-]?key", re.IGNORECASE),
    re.compile(r"paste[_\s-]?key", re.IGNORECASE),
    re.compile(r"输入[_\s-]?key", re.IGNORECASE),
    re.compile(r"注册[_\s-]?key", re.IGNORECASE),
    re.compile(r"apikey|api.?key(?:注入|设置|配置|准备|添加|注册|输入)", re.IGNORECASE),
]

# Alex 的 sender 标识（在 agent room 中）
ALEX_SENDER_PATTERNS = ["alex", "user", "admin"]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except Exception:
            continue
    return out


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def message_id(msg: dict[str, Any]) -> str:
    """Generate a stable unique ID for a message."""
    text = str(msg.get("text", msg.get("body", "")))
    sender = str(msg.get("sender", msg.get("role", "")))
    ts = str(msg.get("ts", msg.get("timestamp", "")))
    raw = f"{sender}:{ts}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_alex_message(msg: dict[str, Any]) -> bool:
    """Detect if a message is from Alex."""
    sender = str(msg.get("sender", msg.get("role", ""))).lower().strip()
    if sender in ALEX_SENDER_PATTERNS:
        return True
    return False


def detect_key_intent(text: str) -> bool:
    """Check if text expresses intent to add/configure an API key."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in KEYWORD_PATTERNS)


def already_responded(msg_id: str) -> bool:
    """Check dedup set."""
    history: set[str] = set(read_json(RESPONSE_HISTORY_FILE) or [])
    return msg_id in history


def mark_responded(msg_id: str) -> None:
    """Record that we've responded to this message."""
    history: set[str] = set(read_json(RESPONSE_HISTORY_FILE) or [])
    history.add(msg_id)
    write_json(RESPONSE_HISTORY_FILE, sorted(history))


def provision_secrets_dir(dry_run: bool = False) -> bool:
    """Pre-create the secrets directory and a placeholder hint file.

    Returns True if provisioned (or would have provisioned in dry-run mode).
    """
    if dry_run:
        return True

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    # Set restrictive permissions
    os.chmod(str(SECRETS_DIR), 0o700)

    # Create a README hint for Alex (safe, no real key)
    hint_file = SECRETS_DIR / "README.txt"
    if not hint_file.exists():
        hint_file.write_text(
            "OpenClaw API Key Secrets Directory\n"
            "==================================\n"
            "Files in this directory are auto-loaded by OpenClaw gateways and workers.\n"
            "Permissions are restricted: only the owner can read them.\n"
            "To add a key, run: bash tools/save_provider_api_key.sh <provider>\n"
            "Supported providers: deepseek, openai, custom\n"
        )

    return True


def infer_provider_hint(text: str) -> str:
    lowered = str(text or "").lower()
    if "openai" in lowered:
        return "openai"
    if "custom" in lowered:
        return "custom"
    return "deepseek"


def write_add_key_signal(provider: str) -> None:
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider,
        "requested_at": now_iso(),
        "detector": "auto_key_provision.py",
    }
    ADD_KEY_SIGNAL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_response(msg: dict[str, Any], provider: str) -> str:
    """Build the response message with ready-to-copy command."""
    lines = [
        "✅ 检测到你提到要添加 API Key，我已自动准备好密钥环境：",
        "",
        "📁 目录已就绪：`~/.openclaw/secrets/`",
        "",
        "👉 **只需复制下面一行到终端粘贴执行**（Key 会安全输入，不回显）：",
        "",
        "```bash",
        f"bash tools/save_provider_api_key.sh {provider}",
        "```",
        "",
        "执行后终端会提示你粘贴 Key，直接粘贴回车即完成。",
        "",
        "💡 若你用的是 OpenAI key：`bash tools/save_provider_api_key.sh openai`",
        "💡 若需自定义：`bash tools/save_provider_api_key.sh custom`",
        "",
        "⚠️ 注意：Claude Code Ark lane 只认 `VOLCANO_ENGINE_API_KEY`，",
        "   DeepSeek/OpenAI key 不能直接注入 Ark lane，走 provider worker 用上面命令即可。",
    ]
    return "\n".join(lines)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    messages = read_jsonl(MESSAGES_FILE)
    if not messages:
        print(json.dumps({"status": "no_messages", "action": "none"}))
        return

    # Scan recent messages (last 50) from Alex for key intent
    triggered_msg = None
    for msg in reversed(messages[-50:]):
        if not is_alex_message(msg):
            continue
        text = str(msg.get("text", msg.get("body", "")))
        if not detect_key_intent(text):
            continue
        mid = message_id(msg)
        if already_responded(mid):
            continue
        triggered_msg = msg
        triggered_msg["_detected_id"] = mid
        break

    if triggered_msg is None:
        # No new intent detected
        print(json.dumps({"status": "no_intent_detected", "action": "none"}))
        return

    mid = triggered_msg["_detected_id"]
    msg_text = str(triggered_msg.get("text", triggered_msg.get("body", "")))
    detected_provider = infer_provider_hint(msg_text)

    # Provision
    provision_secrets_dir(dry_run=dry_run)
    response_text = build_response(triggered_msg, detected_provider)

    # Record response (dedup)
    if not dry_run:
        mark_responded(mid)

    result = {
        "status": "intent_detected",
        "action": "responded" if not dry_run else "dry_run",
        "detected_message": msg_text[:120],
        "message_id": mid,
        "provider": detected_provider,
        "response_text": response_text,
    }
    if not dry_run:
        provision_secrets_dir(dry_run=dry_run)
        write_add_key_signal(detected_provider)
        mark_responded(mid)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
