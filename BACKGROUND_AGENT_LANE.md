# Background Agent Lane

This workspace treats Telegram/main as the operator entrypoint. Long-running local workflows may call agents, but they must not fan out OpenClaw model calls directly against the gateway.

## Rule

Any local workflow that invokes `openclaw agent` for background work should go through the shared lane:

```bash
python3 scripts/openclaw_model_call.py --timeout 330 -- openclaw agent --local --agent <agent> --session-id <id> --json --thinking <level> --timeout <seconds> --model <model> --message <prompt>
```

Python workflows should import:

```python
from gateway_model_lane import run_openclaw_model_call
```

and use it instead of calling `subprocess.run` or a custom process-group helper for model calls.

## Why

- keep Telegram/main responsive while background workers run;
- allow only one local background OpenClaw model call at a time;
- fail fast when the gateway is unreachable;
- report `gateway_model_lane_busy` as retryable/cooling-down work instead of starting more work;
- keep future modules such as translation, research, market, and Notion publishing on the same control surface.

## What Still Does Not Cover

This only guards local scripts/workflows that call the OpenClaw CLI. If OpenClaw core itself dispatches agent-to-agent work inside the gateway, that still needs upstream reliability/stuck-session support.

## Module Checklist

For every new background module:

1. Write a task record under `tasks/<task_id>/task.json`.
2. Write artifacts/checkpoints to files, not Telegram chat.
3. Run OpenClaw agent/model calls through `scripts/openclaw_model_call.py` or `run_openclaw_model_call`.
4. Treat `gateway_model_lane_busy`, `gateway_unavailable`, `summary_timeout`, and provider timeouts as retryable with cooldown.
5. Send Telegram only short status/results/needs-review messages.

## Direct Provider Lane

For heavy work that does not need OpenClaw agent/session/tool behavior, prefer the direct provider worker instead of the gateway lane:

```bash
python3 scripts/direct_provider_worker.py --profile ark-coding-plan --model kimi-k2.6 --task-id <task_id> --task-type <type> --contract-file <contract.md> --input-file <input.md> --output-dir <artifact_dir>
```

This writes artifacts with `gateway_used=false`. The main agent should review those artifacts later instead of executing the whole job inside the Telegram session.