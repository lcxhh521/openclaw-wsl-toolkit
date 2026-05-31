import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_projection_crash_uses_non_recursive_plain_fallback(monkeypatch):
    reply = load_tool("telegram_agent_reply")

    def boom(_text):
        raise NameError("PAIRED_SINGLE_STAR_RE")

    monkeypatch.setattr(reply, "telegram_plain_text_projection", boom)
    monkeypatch.setattr(reply, "telegram_html_projection", boom)

    projected = reply.build_telegram_projection("**Claude Code** runner 失败 <x>")

    assert projected["parse_mode"] is None
    assert projected["projection_error"]["type"] == "NameError"
    assert "Claude Code" in projected["text"]
    assert "＜x＞" in projected["text"]
    assert projected["plain_fallback_text"] == projected["text"]


def test_user_visible_runner_failure_is_not_suppressed_in_reply_layer():
    reply = load_tool("telegram_agent_reply")
    comment = {
        "telegram_projection_status": "user_visible_runner_failure",
        "title": "claude-code runner did not produce a publishable reply",
        "body": "claude-code 本轮 runner 进程已经不存在，且没有留下可发布正文。",
        "blockers": ["runner_process_missing"],
    }
    assert reply.is_internal_runner_failure_comment(
        comment, comment["title"], comment["body"], comment["blockers"]
    ) is False


def test_user_visible_runner_failure_is_material_and_projectable():
    bridge = load_tool("agent_room_resident_bridge")
    task = {
        "source": {"transport": "telegram"},
        "requested_by": "telegram-user",
        "delivery_policy": "targeted_reply",
    }
    comment = {
        "telegram_projection_status": "user_visible_runner_failure",
        "title": "claude-code runner did not produce a publishable reply",
        "body": "claude-code 本轮 runner 进程已经不存在，且没有留下可发布正文。",
        "blockers": ["runner_process_missing"],
    }
    assert bridge.is_material_peer_comment(comment) is True
    assert bridge.is_internal_runner_failure_comment(comment) is False
    assert bridge.telegram_projection_decision(task, [comment]) == (True, "normal")


def test_room_text_redacts_bare_provider_keys():
    bridge = load_tool("telegram_agent_bridge")

    text = "DeepSeek key " + "sk-" + "a" * 32 + " can be used"

    assert bridge.redact_room_text(text) == "DeepSeek key [REDACTED] can be used"


def test_poll_sanitize_update_redacts_bare_provider_keys():
    poll = load_tool("telegram_agent_bridge_poll")
    bridge = load_tool("telegram_agent_bridge")
    update = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "text": "sk-" + "b" * 32,
            "caption": "api_key=" + "sk-" + "c" * 32,
        },
    }

    sanitized = poll.sanitize_update(update, "codex", "lchcodex_bot", bridge.redact_room_text)

    assert sanitized["message"]["text"] == "[REDACTED]"
    assert sanitized["message"]["caption"] == "api_key=[REDACTED]"
    assert sanitized["receiver_agent_id"] == "codex"


def test_poll_bot_entry_sanitizes_updates_and_tracks_next_offset(monkeypatch):
    poll = load_tool("telegram_agent_bridge_poll")
    bridge = load_tool("telegram_agent_bridge")

    def fake_get_updates(token, offset, timeout, allowed_updates):
        assert token == "fake-token"
        assert offset == 7
        assert timeout == 0
        assert "message" in allowed_updates
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 41,
                    "message": {"text": "sk-" + "d" * 32},
                },
            ],
        }

    monkeypatch.setattr(poll, "telegram_get_updates", fake_get_updates)
    row = poll.poll_bot_entry(
        0,
        {"agent_id": "codex", "username": "lchcodex_bot", "token": "fake-token"},
        offset=7,
        timeout=0,
        limit_per_bot=20,
        redact_text=bridge.redact_room_text,
    )

    assert row["ok"] is True
    assert row["next_offset"] == 42
    assert row["updates"][0]["message"]["text"] == "[REDACTED]"
    assert row["updates"][0]["receiver_agent_id"] == "codex"
