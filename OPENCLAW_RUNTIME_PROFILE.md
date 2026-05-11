# OpenClaw Runtime Profile

Generated: 2026-05-11

## Goal

Keep Telegram/main as the full main brain, but avoid carrying the full plugin universe on every interactive message.

This is not a personality/memory downgrade. It separates runtime loading into hot, warm, and cold capability layers.

## Layers

### Hot

Always-on interactive path for Telegram/main:

- telegram
- openai
- volcengine
- codex
- memory-core
- tavily
- openclaw-weixin
- openclaw-wechat-desktop
- deepseek
- file-transfer
- device-pair
- web-readability
- document-extract

### Warm

Recently used capabilities can be temporarily added with TTL, for example:

- browser_research
- alt_llm_providers
- qqbot
- voice
- media_generation

### Cold

Everything else stays available in the full reference profile but is not carried by default.

## Implemented Pieces

- `runtime_profiles/openclaw-runtime-profiles.json`: profile definitions.
- `scripts/openclaw_runtime_profile.py`: status/plan/apply/touch/clear-warm/prewarm/rollback.
- `openclaw-gateway.service.d/10-runtime-profile.conf`: applies auto profile before gateway starts.
- `openclaw-runtime-profile-prune.timer`: refreshes profile TTL state every 30 minutes.
- `openclaw_reliability_sidecar.py`: reports current runtime profile in monitor cache.

## Current State

The current config has been changed from the original full allow-list to hot profile:

- before: 74 plugins in `plugins.allow`
- now: 13 plugins in `plugins.allow`
- backup: `~/.openclaw/openclaw.json.bak-runtime-profile-20260511-185825`

The currently running gateway will not observe this until it restarts. No gateway restart was performed by this change.

## Common Commands

Status:

```bash
~/.openclaw/workspace/scripts/openclaw_runtime_profile.py status
```

Preview auto profile:

```bash
~/.openclaw/workspace/scripts/openclaw_runtime_profile.py plan --profile auto
```

Temporarily warm browser research for 2 hours:

```bash
~/.openclaw/workspace/scripts/openclaw_runtime_profile.py touch browser_research --ttl-minutes 120 --apply-auto --apply
```

Clear a warm capability:

```bash
~/.openclaw/workspace/scripts/openclaw_runtime_profile.py clear-warm browser_research --apply-auto --apply
```

Restore the full allow-list from backup:

```bash
~/.openclaw/workspace/scripts/openclaw_runtime_profile.py rollback ~/.openclaw/openclaw.json.bak-runtime-profile-20260511-185825
```

## Boundaries

This mechanism only changes `plugins.allow`.

It does not change:

- secrets
- models
- agents
- bindings
- channels
- sessions
- memory
- Telegram token
- Notion token

It does not kill, restart, or clean tasks.

## Remaining Work

This is still a local runtime/profile layer. It does not yet implement true OpenClaw core lazy-loading or provider/model auth caching. For that, upstream source changes would be needed.
