# OpenClaw Volcengine Coding Plan Module

This module configures OpenClaw for Volcengine Ark Coding Plan.

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
|-- module.json
`-- scripts/
    |-- set_volcengine_coding_plan_key.sh
    `-- verify_volcengine_coding_plan.sh
```

## What The Installer Changes

The installer writes the API key only to local OpenClaw state:

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

## Install

Run inside the WSL distro that owns OpenClaw:

```bash
cd /path/to/openclaw-wsl-toolkit/modules/openclaw-volcengine-coding-plan
./scripts/set_volcengine_coding_plan_key.sh
```

Paste the real Ark API key value when prompted. Do not paste the key name, key
ID, endpoint ID, Access Key ID, Secret Access Key, or a full command.

The script does not print the secret. It shows only the key length and a short
prefix/suffix shape for troubleshooting.

## Verify

```bash
./scripts/verify_volcengine_coding_plan.sh
```

Expected result:

- OpenClaw gateway service is active if systemd user service exists.
- Default model resolves to `volcengine-plan/ark-code-latest`.
- A local model call through OpenClaw returns text.

## Supported Coding Plan Models

The module registers these model references:

```text
volcengine-plan/ark-code-latest
volcengine-plan/doubao-seed-code
volcengine-plan/glm-4.7
volcengine-plan/deepseek-v3.2
volcengine-plan/doubao-seed-2.0-code
volcengine-plan/doubao-seed-2.0-pro
volcengine-plan/doubao-seed-2.0-lite
volcengine-plan/minimax-m2.5
volcengine-plan/kimi-k2.5
```

Keep `ark-code-latest` as the default when you want the Ark console to control
the underlying Coding Plan model.

## Safety Rules

- Never commit `~/.openclaw/secrets/volcengine.env`.
- Never paste Ark API keys into chat.
- Do not store real API keys in this module directory.
- Prefer the Coding Plan endpoint for Coding Plan quota.
- Restart `openclaw-gateway.service` after changing provider configuration.
