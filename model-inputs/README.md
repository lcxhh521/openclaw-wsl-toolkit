# Model Inputs Transport

This directory is the public contract for large model-input transport. It is infrastructure, not a place to commit real prompts.

Some OpenClaw workflows need to pass very large prompts or information-flow packs to a model. When the payload is too large for command-line argv or a chat message, the workflow writes the exact UTF-8 payload to a local artifact, records its SHA-256, and sends only a short file-reference instruction to the model lane.

Repository policy:

- Commit this contract and sanitized examples.
- Do not commit real `*.prompt.txt` files.
- Do not commit raw market feeds, People's Daily text, user conversations, Notion exports, Telegram delivery state, or model outputs.
- Use placeholders in examples.
- Market daily prompt-building code may be tracked as part of the product workflow.
- People's Daily deep-read prompt contents are private deployment material and should live outside the repository, normally under `~/.openclaw/private-prompts/people_daily/`.

Runtime layout example:

```text
model-inputs/
  native-prompt-transport/
    <session-or-run-id>/
      <timestamp>-<sha-prefix>.prompt.txt      # ignored, real local payload
      <timestamp>-<sha-prefix>.manifest.json   # ignored when produced at runtime
```

The manifest should include at least:

- `transport`: `workspace_prompt_artifact_v1`
- `prompt_path`
- `sha256`
- `chars`
- `created_at`
- `session_id` or workflow run id

The consumer must read the file as UTF-8 and treat the file contents as the actual model prompt. The wrapper instruction itself is not the task.
