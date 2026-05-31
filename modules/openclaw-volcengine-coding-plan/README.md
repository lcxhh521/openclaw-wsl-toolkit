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
volcengine-plan/deepseek-v3.2
volcengine-plan/deepseek-v4-flash
volcengine-plan/deepseek-v4-pro
volcengine-plan/deepseek-reasoner
```

Keep `ark-code-latest` as the default when you want the Ark console to control
the underlying Coding Plan model.

`deepseek-v4-flash`, `deepseek-v4-pro`, and `deepseek-reasoner` were added on
2026-05-25 per Alex's request after they appeared in the Ark interactive lean
preview. The Claude Code model policy routes (`claude-code-model-policy.json`)
now route through V4 candidates and remap `deepseek-v3.2` to `deepseek-v4-pro`;
the local smoke matrix excludes the retired V3.2 candidate by default.

## Memory Enhancement / Embedding Entitlement

Ark Coding Plan also exposes the dedicated memory-enhancement embedding model:

```text
doubao-embedding-vision
```

The Volcengine Coding Plan documentation lists it under **专属权益 → 记忆增强-Embedding模型** and says to use the same Coding Plan endpoint as an OpenAI-compatible embedding endpoint:

```text
https://ark.cn-beijing.volces.com/api/coding/v3
```

OpenClaw consumes it through `agents.defaults.memorySearch`:

```jsonc
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "enabled": true,
        "provider": "openai",
        "model": "doubao-embedding-vision",
        "remote": {
          "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3",
          "apiKey": "<ARK_API_KEY>"
        },
        "fallback": "none"
      }
    }
  }
}
```

### Current local status checked on 2026-05-25

- `~/.openclaw/openclaw.json` has `agents.defaults.memorySearch.enabled=true`.
- Provider/model are `openai` + `doubao-embedding-vision`.
- Remote base URL is `https://ark.cn-beijing.volces.com/api/coding/v3`.
- API key is stored as an OpenClaw env secret reference (`VOLCANO_ENGINE_API_KEY`), not as a literal key in this module.
- `memory_search` debug reports provider `openai`, model `doubao-embedding-vision`.
- Known-memory searches with `minScore=0` return hits, which confirms the memory search path is alive.
- Live benign embedding probe passed on 2026-05-25: HTTP 200, model `doubao-embedding-vision`, vector dimension `2048`, prompt tokens `34`. No private memory content, API key, or vector values were printed.

### Smoke check

Run the local secret-safe checker:

```bash
modules/openclaw-volcengine-coding-plan/scripts/check_memory_enhancement.py
```

Run the live endpoint probe with a benign smoke string:

```bash
modules/openclaw-volcengine-coding-plan/scripts/check_memory_enhancement.py --live --json
```

The checker understands OpenClaw secret references such as:

```json
{
  "source": "env",
  "provider": "default",
  "id": "VOLCANO_ENGINE_API_KEY"
}
```

It resolves the key locally from env/secrets, but never prints the key or embedding vector values.

For recall reliability diagnostics, run the local-first gate:

```bash
modules/openclaw-volcengine-coding-plan/scripts/recall_gate.py '<query-or-id>' --json
```

This gate does **not** call remote embeddings by default. It first checks local exact-string matches over memory/module/artifact files, then inspects local SQLite memory indexes. Add `--semantic` only when it is acceptable for the query to go through the configured remote embedding provider.

Important nuance: strict/default semantic thresholds can still return zero hits for narrow or recently-written terms. That is a retrieval/ranking issue, not necessarily an embedding endpoint failure. For diagnostics, retry with broader wording and/or `minScore=0` before concluding the entitlement is broken.

### Fresh-write recall gap found on 2026-05-25

A harmless marker was appended to `memory/2026-05-25.md`:

```text
ARK_MEMORY_ENHANCEMENT_FRESH_RECALL_20260525_1941
```

Results:

- Direct file lookup found the marker immediately.
- `memory_search` did not find the marker, even with `minScore=0`.
- Local SQLite inspection with `scripts/recall_smoke_local.py` confirmed `memory/2026-05-25.md` was not present in main/telegram/codex `files` or `chunks`, so the immediate miss is an index freshness/coverage gap.
- `scripts/recall_gate.py` now gives a reusable local-first answer path: exact marker lookup succeeds even when semantic memory index is stale.
- `openclaw memory status --json` showed main/telegram memory stores using `doubao-embedding-vision`, vector dimension `2048`, and `dirty=false`.
- `openclaw memory search` for the exact marker hit repeated Ark embedding `429 AccountRateLimitExceeded` retries and returned `[]`.

Interpretation: the Coding Plan embedding endpoint is healthy, but the end-user recall path still needs architecture work around fresh-write indexing, exact-string/lexical fallback, ranking thresholds, and rate-limit/backoff. Do not run a broad `openclaw memory index --force` automatically; it can embed many memory chunks remotely and should be approved or staged.

Evidence artifact:

```text
codex-main-bridge/agent-room/artifacts/ark-coding-plan-memory-enhancement-20260525/fresh_write_recall_20260525.md
```

Historical note: the earlier Ark Coding Plan failure was on OpenClaw's native `volcengine-plan/*` runtime/subagent path. Direct Coding Plan API calls worked, so `tools/ark_coding_plan.py` was added as a local chat-completions router. The memory-enhancement embedding path is separate and is currently wired through `memorySearch.remote`.

## Safety Rules

- Never commit `~/.openclaw/secrets/volcengine.env`.
- Never paste Ark API keys into chat.
- Do not store real API keys in this module directory.
- Prefer the Coding Plan endpoint for Coding Plan quota.
- Prefer OpenClaw's native `openclaw configure`, `openclaw models`, and
  `openclaw config` flows for provider setup.
- Restart `openclaw-gateway.service` after changing provider configuration.
