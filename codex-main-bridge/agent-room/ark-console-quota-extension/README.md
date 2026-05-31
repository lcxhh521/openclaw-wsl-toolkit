# OpenClaw Ark Quota Bridge

This unpacked Chrome extension reads only visible Ark Coding Plan quota
percentages from the Volcengine console page and posts them to
`http://127.0.0.1:18793/`.

It does not read cookies, API keys, hidden prompts, Telegram data, or OpenClaw
runtime state. The WSL ingest service writes:

`/home/lcxhh/.openclaw/workspace/codex-main-bridge/agent-room/token_channel_usage.json`

Then `openclaw-token-channel-cache` merges the data into the local monitor
cache without touching OpenClaw gateway or models.

Install path:

`/home/lcxhh/.openclaw/workspace/codex-main-bridge/agent-room/ark-console-quota-extension`
