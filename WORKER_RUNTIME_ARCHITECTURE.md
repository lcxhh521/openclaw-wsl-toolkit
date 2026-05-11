# Worker Runtime Architecture

Goal: keep Telegram/main as the operator console while heavy work continues in the background without monopolizing the interactive OpenClaw gateway.

## Lanes

### 1. Interactive Gateway

Use for:

- Telegram conversation
- main-agent clarification and decisions
- final review of artifacts
- short status reports

Do not use for long translation, long summaries, PDF generation, large log review, or repeated retry loops.

### 2. Gateway Model Lane

Use only when a background workflow truly needs OpenClaw agent/session/tool behavior.

Entry points:

- `scripts/openclaw_model_call.py`
- `gateway_model_lane.run_openclaw_model_call`

Properties:

- still uses the OpenClaw gateway;
- allows only one local background OpenClaw model call at a time;
- fails fast when the gateway is unavailable;
- reports retryable lane-busy/provider failures through task records.

### 3. Direct Provider Worker Lane

Preferred for heavy, bounded worker tasks that do not need the OpenClaw gateway in the hot path.

Entry point:

```bash
python3 scripts/direct_provider_worker.py \
  --task-id <task_id> \
  --task-type <translation|market_summary|people_daily|generic_worker> \
  --profile ark-coding-plan \
  --model kimi-k2.6 \
  --contract-file <task_contract.md> \
  --input-file <input.md> \
  --output-dir <artifact_dir>
```

Properties:

- calls an OpenAI-compatible provider directly;
- writes `manifest.json`, `result.md`, `response.json`, or `error.json`;
- sets `gateway_used=false` in the manifest;
- does not read or write Telegram chat;
- does not modify OpenClaw config, models, bindings, sessions, or secrets.

## Quality Model

The direct worker does not replace main. It executes a task contract.

Flow:

1. main defines a task contract: goal, constraints, style, output format, quality gates.
2. worker executes against a provider and writes artifacts.
3. main reviews the manifest/result/error.
4. only a short status or final answer returns to Telegram.
5. if quality fails, main asks for a scoped worker rerun against the relevant artifact.

This keeps main as the editor/architect/reviewer rather than the long-running executor.

## Module Guidance

Translation:

- use direct provider workers per chapter/section when possible;
- write terminology tables and result markdown as artifacts;
- main reviews samples and final PDF/report.

Market and People Daily:

- use direct provider workers for summaries and draft synthesis when OpenClaw tools are not required;
- keep Notion publish/checkpoint logic outside Telegram;
- main reviews `task.json`, `manifest.json`, and short previews.

Future modules:

- start with direct provider workers by default;
- use gateway model lane only when the task needs OpenClaw agent memory/tools;
- keep all long output in artifact files.

## Non-goals

- no automatic gateway restart;
- no task cleanup;
- no model/config/binding/secret changes;
- no automatic retry loop without task-record backoff;
- no long logs or long drafts in Telegram.