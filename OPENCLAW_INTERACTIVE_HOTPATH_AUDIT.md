# OpenClaw Interactive Hot Path Audit

Generated: 2026-05-11

## Current Finding

Telegram/main is not mainly blocked by market/people/translation background workers right now. The active pressure is the interactive gateway embedded-run path itself.

Evidence from gateway logs since 18:00:

```json
{
  "startup_attempt_dispatch_total": {
    "count": 14,
    "min": 16613,
    "median": 40179.5,
    "max": 65191
  },
  "prep_stream_ready_total": {
    "count": 13,
    "min": 35935,
    "median": 58838,
    "max": 193748
  },
  "core-plugin-tools": {
    "count": 13,
    "min": 10894,
    "median": 19154,
    "max": 39994
  },
  "bundle-tools": {
    "count": 13,
    "min": 1300,
    "median": 3718,
    "max": 23827
  },
  "system-prompt": {
    "count": 13,
    "min": 4213,
    "median": 12002,
    "max": 74462
  },
  "session-resource-loader": {
    "count": 13,
    "min": 1037,
    "median": 2938,
    "max": 9231
  },
  "stream-setup": {
    "count": 13,
    "min": 4611,
    "median": 14455,
    "max": 74337
  },
  "model-resolution": {
    "count": 14,
    "min": 2790,
    "median": 23122.5,
    "max": 31618
  },
  "auth": {
    "count": 14,
    "min": 3958,
    "median": 10099.5,
    "max": 20943
  },
  "attempt-dispatch": {
    "count": 14,
    "min": 2762,
    "median": 9767.0,
    "max": 16780
  }
}
```

## Config Surface

Enabled plugin entries:

```text
deepseek, telegram, openai, openclaw-weixin, openclaw-wechat-desktop, tavily, memory-core, volcengine, codex
```

Current `plugins.allow` count: 74

Lean preview `plugins.allow` count: 13

Kept in preview:

```text
telegram, openai, volcengine, codex, memory-core, tavily, openclaw-weixin, openclaw-wechat-desktop, deepseek, file-transfer, device-pair, web-readability, document-extract
```

Removed from preview:

```text
acpx, alibaba, amazon-bedrock, amazon-bedrock-mantle, anthropic, anthropic-vertex, arcee, azure-speech, bonjour, browser, byteplus, cerebras, chutes, cloudflare-ai-gateway, comfy, copilot-proxy, deepgram, deepinfra, elevenlabs, fal, fireworks, github-copilot, google, groq, huggingface, inworld, kilocode, kimi, litellm, lmstudio, microsoft, microsoft-foundry, minimax, mistral, moonshot, nvidia, ollama, opencode, opencode-go, openrouter, phone-control, qianfan, qqbot, qwen, runway, senseaudio, sglang, stepfun, synthetic, talk-voice, tencent, together, tts-local-cli, venice, vercel-ai-gateway, vllm, voyage, vydra, xai, xiaomi, zai
```

## Preview Files

- `~/.openclaw/workspace/tmp/openclaw-interactive-lean-preview.json`
- `~/.openclaw/workspace/tmp/openclaw-interactive-lean-preview.patch.json`

## Boundary

This is a preview only. It has not changed `~/.openclaw/openclaw.json`.

The preview does not change secrets, models, agents, bindings, channels, or sessions. It only proposes reducing `plugins.allow` for the interactive hot path.

## Expected Effect

Potentially reduces `core-plugin-tools` and `bundle-tools` preparation cost. It will not directly fix provider auth/model-resolution latency or network send failures.

## Apply/rollback shape

Apply would require:

1. Back up `~/.openclaw/openclaw.json`.
2. Replace only `plugins.allow` with the preview keep-list.
3. Restart gateway for config reload.
4. Compare sidecar/gateway logs for `core-plugin-tools`, `bundle-tools`, `system-prompt`, `stream-setup`.
5. Roll back by restoring the backup if a required plugin disappears.


## Runtime Profile Follow-up

Implemented local hot/warm/cold runtime profile management. See `OPENCLAW_RUNTIME_PROFILE.md`. Current config `plugins.allow` is now hot profile (13 plugins), with backup `~/.openclaw/openclaw.json.bak-runtime-profile-20260511-185825`. Current running gateway will observe it after next restart; no restart was performed during implementation.
