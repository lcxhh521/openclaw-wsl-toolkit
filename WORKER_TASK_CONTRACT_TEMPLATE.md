# Worker Task Contract Template

Use this when main delegates work to a background worker or direct provider worker.

## Task

- task_id:
- task_type:
- owner: worker
- reviewer: main

## Goal

Describe the concrete outcome. Avoid vague commands such as "do it well".

## Inputs

- source files:
- source text/range:
- required references:

## Constraints

- do not modify OpenClaw config/model/binding/secrets/session;
- do not write long output to Telegram;
- write artifacts to the output directory;
- preserve source meaning and structure unless explicitly told otherwise;
- record failures in `error.json` rather than retrying indefinitely.

## Output Artifacts

Required:

- `manifest.json`
- `result.md`

Optional:

- `response.json`
- `error.json`
- `preview.md`
- generated files such as `.pdf`, `.docx`, `.xlsx`

## Quality Gates

- completeness:
- style/format:
- factual/source fidelity:
- publication readiness:

## Review Handoff

The worker should summarize only:

- what changed;
- artifact paths;
- known gaps;
- whether main review is required.