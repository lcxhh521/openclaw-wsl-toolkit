#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_ROOM_ROOT", str(Path.home() / ".openclaw" / "workspace" / "codex-main-bridge")))
ROOM = ROOT / "agent-room"
TOOLS = ROOM / "tools"
SECRET_ENV = Path.home() / ".openclaw" / "secrets" / "agent-room-telegram-bots.env"
BOT_META = ROOM / "telegram_agent_bots.json"
POLL_ROOT = ROOM / "poll-runs" / "telegram-agent-bridge"
STATE_PATH = ROOM / "telegram_agent_bridge_poll_state.json"


def load_bridge_module() -> Any:
    path = TOOLS / "telegram_agent_bridge.py"
    spec = importlib.util.spec_from_file_location("telegram_agent_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def bot_entries() -> list[dict[str, Any]]:
    meta = read_json(BOT_META, {})
    env = load_env(SECRET_ENV)
    entries: list[dict[str, Any]] = []
    for bot in meta.get("bots", []):
        if bot.get("ingress_enabled") is not True:
            continue
        agent_id = str(bot.get("agent_id") or "")
        username = str(bot.get("telegram_username_verified") or bot.get("telegram_username") or "")
        ref = str(bot.get("token_secret_ref") or "")
        env_name = ref.removeprefix("env:")
        token = env.get(env_name)
        entries.append({
            "agent_id": agent_id,
            "username": username,
            "token": token,
            "token_present": bool(token),
            "ingress_enabled": True,
        })
    return entries


def telegram_get_updates(token: str, offset: int | None, timeout: int, allowed_updates: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "timeout": timeout,
        "allowed_updates": json.dumps(allowed_updates, ensure_ascii=False),
    }
    if offset is not None:
        params["offset"] = offset
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/getUpdates",
        data=body,
        headers={"User-Agent": "openclaw-agent-room-poll/0"},
    )
    with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def redact_strings(value: Any, redact_text: Any) -> Any:
    if isinstance(value, dict):
        return {k: redact_strings(v, redact_text) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_strings(item, redact_text) for item in value]
    if isinstance(value, str) and callable(redact_text):
        return redact_text(value)
    return value


def sanitize_update(update: dict[str, Any], agent_id: str, username: str, redact_text: Any = None) -> dict[str, Any]:
    clone = json.loads(json.dumps(update, ensure_ascii=False))
    clone = redact_strings(clone, redact_text)
    clone["receiver_agent_id"] = agent_id
    clone["receiver_bot_username"] = username
    return clone


def poll_max_workers(default: int = 4) -> int:
    raw = os.environ.get("AGENT_ROOM_POLL_MAX_WORKERS", str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def poll_bot_entry(
    index: int,
    entry: dict[str, Any],
    *,
    offset: int | None,
    timeout: int,
    limit_per_bot: int,
    redact_text: Any = None,
) -> dict[str, Any]:
    agent_id = entry["agent_id"]
    username = entry["username"]
    token = entry["token"]
    if not token:
        return {
            "index": index,
            "agent_id": agent_id,
            "username": username,
            "ok": False,
            "error": "missing_token",
            "updates": [],
            "next_offset": None,
            "offset_used": offset,
        }
    try:
        data = telegram_get_updates(
            token,
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "edited_message", "my_chat_member", "chat_member"],
        )
        updates = data.get("result") or []
        if limit_per_bot >= 0:
            updates = updates[:limit_per_bot]
        sanitized_updates = [
            sanitize_update(update, agent_id, username, redact_text)
            for update in updates
        ]
        next_offset = max(int(u.get("update_id", 0)) for u in updates) + 1 if updates else None
        return {
            "index": index,
            "agent_id": agent_id,
            "username": username,
            "ok": bool(data.get("ok")),
            "updates": sanitized_updates,
            "next_offset": next_offset,
            "offset_used": offset,
        }
    except Exception as exc:
        return {
            "index": index,
            "agent_id": agent_id,
            "username": username,
            "ok": False,
            "error": type(exc).__name__ + ": " + str(exc),
            "updates": [],
            "next_offset": None,
            "offset_used": offset,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll Telegram agent bots and normalize updates into Agent Room artifacts.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Normalize updates without committing offsets or canonical state.")
    parser.add_argument("--commit-offset", action="store_true", help="Advance getUpdates offset after successful normalization.")
    parser.add_argument("--timeout", type=int, default=0, help="Telegram long-poll timeout seconds.")
    parser.add_argument("--limit-per-bot", type=int, default=20, help="Maximum updates per bot to normalize in this run.")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    bridge = load_bridge_module()
    state = read_json(STATE_PATH, {"schema": "openclaw.agent_room.telegram_agent_bridge_poll_state.v0", "offsets": {}})
    all_updates: list[dict[str, Any]] = []
    bot_results: list[dict[str, Any]] = []
    max_offsets: dict[str, int] = {}
    entries = bot_entries()
    max_workers = max(1, min(len(entries) or 1, poll_max_workers()))
    poll_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                poll_bot_entry,
                index,
                entry,
                offset=state.get("offsets", {}).get(entry["agent_id"]),
                timeout=args.timeout,
                limit_per_bot=args.limit_per_bot,
                redact_text=getattr(bridge, "redact_room_text", None),
            )
            for index, entry in enumerate(entries)
        ]
        for future in as_completed(futures):
            poll_rows.append(future.result())

    for row in sorted(poll_rows, key=lambda item: int(item.get("index") or 0)):
        agent_id = str(row.get("agent_id") or "")
        updates = row.get("updates") if isinstance(row.get("updates"), list) else []
        all_updates.extend(updates)
        next_offset = row.get("next_offset")
        if agent_id and next_offset is not None:
            max_offsets[agent_id] = int(next_offset)
        bot_result = {
            "agent_id": agent_id,
            "username": row.get("username"),
            "ok": row.get("ok"),
            "updates": len(updates),
            "offset_used": row.get("offset_used"),
            "next_offset": next_offset,
        }
        if row.get("error"):
            bot_result["error"] = row.get("error")
        bot_results.append(bot_result)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else POLL_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "raw-updates.sanitized.json", all_updates)

    normalized = bridge.normalize_updates(all_updates, out_dir)
    result = {
        "schema": "openclaw.agent_room.telegram_agent_bridge_poll.v0",
        "ok": all(r.get("ok") is not False for r in bot_results) and normalized.get("ok", False),
        "mode": "dry_run" if args.dry_run else "poll",
        "out_dir": str(out_dir),
        "bots": bot_results,
        "normalized": normalized,
        "commit_offset_requested": bool(args.commit_offset),
        "offset_committed": False,
        "telegram_outbound": False,
        "external_side_effects": False,
        "poll_concurrency": max_workers,
        "tokens_printed": False,
    }
    if args.commit_offset:
        state.setdefault("offsets", {}).update(max_offsets)
        state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_json(STATE_PATH, state)
        result["offset_committed"] = True
    write_json(out_dir / "poll-result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
