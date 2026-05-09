# OpenClaw Volcengine Coding Plan Notes

This module documents the Volcengine Ark Coding Plan endpoint and OpenClaw
model references.

It exists because Ark Coding Plan is easy to misconfigure as the normal Ark
inference API. The Coding Plan route must use the dedicated coding endpoint:

```text
https://ark.cn-beijing.volces.com/api/coding/v3
```

The default model reference is:

```text
volcengine-plan/ark-code-latest
```

Do not use the normal Ark endpoint for Coding Plan:

```text
https://ark.cn-beijing.volces.com/api/v3
```

That route can create extra pay-as-you-go cost instead of using the Coding Plan
quota.

## Files

```text
modules/openclaw-volcengine-coding-plan/
|-- README.md
`-- module.json
```

## Local State

```text
~/.openclaw/secrets/volcengine.env
```

It also updates both OpenClaw model configuration locations:

```text
~/.openclaw/openclaw.json
~/.openclaw/agents/main/agent/models.json
```

Updating both files matters. Some OpenClaw runs resolve provider catalog data
from the agent-level `models.json`, so changing only `openclaw.json` can leave
the model unavailable or routed through stale provider settings.

## Supported Coding Plan Models

The module registers these model references:

```text
volcengine-plan/ark-code-latest
volcengine-plan/doubao-seed-2.0-code
volcengine-plan/doubao-seed-2.0-pro
volcengine-plan/doubao-seed-2.0-lite
volcengine-plan/doubao-seed-code
volcengine-plan/glm-5.1
volcengine-plan/minimax-m2.7
volcengine-plan/kimi-k2.6
volcengine-plan/minimax-m2.5
volcengine-plan/kimi-k2.5
volcengine-plan/glm-4.7
volcengine-plan/deepseek-v3.2
```

Keep `ark-code-latest` as the default when you want the Ark console to control
the underlying Coding Plan model.

## Safety Rules

- Never commit `~/.openclaw/secrets/volcengine.env`.
- Never paste Ark API keys into chat.
- Do not store real API keys in this module directory.
- Prefer the Coding Plan endpoint for Coding Plan quota.
- Prefer OpenClaw's native `openclaw configure`, `openclaw models`, and
  `openclaw config` flows for provider setup.
- Restart `openclaw-gateway.service` after changing provider configuration.
