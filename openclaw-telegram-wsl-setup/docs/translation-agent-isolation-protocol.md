# Translation Agent Isolation Protocol

Purpose: keep the main Telegram assistant as command / oversight / acceptance layer, and the translation agent as an isolated executor. This is an architectural boundary, not just a style preference.

## Core boundary

- Main does not conduct translation reasoning in the chat window.
- Main does not absorb or summarize the translation agent's long narrative as truth.
- Translation agent works from a file-based handoff brief and writes file-based artifacts.
- Main accepts only a small machine-readable completion envelope from the translation agent.
- Main independently verifies artifacts before reporting final status to Alex.

## Dispatch shape

For non-trivial translation tasks, main should create a run directory:

```text
translation-runs/<run-id>/
  user_request.md        # Alex's original request, faithfully preserved
  handoff_brief.md       # mechanical task brief; no narrowing unless Alex asked
  task_ledger.json       # main-owned task ledger
  acceptance_plan.json   # gates main will run before accepting
```

Then main spawns translation agent with isolated context and points it to `handoff_brief.md`.

Main should not paste the entire evolving conversation into the translation agent unless the current task genuinely requires it. The translation agent should not inherit unrelated main-session state.

## Translation agent response envelope

The translation agent's chat final response must be only one JSON object:

```json
{
  "status": "candidate_ready | blocked | failed",
  "run_id": "...",
  "manifest": "translation-runs/<run-id>/manifest.json",
  "artifacts": ["translation-runs/<run-id>/translation.md"],
  "claims": ["short factual claim only"],
  "needs_main_verification": true,
  "needs_alex_decision": []
}
```

No long prose, no pasted translation, no self-declared final delivery, no broad narrative status.

## Main acceptance gate

Main must treat the envelope as a candidate only. Before reporting success to Alex, main should verify:

1. expected files exist and plausible byte counts;
2. `manifest.json` exists and names source paths/ranges/model(s)/artifacts;
3. requested scope is covered;
4. source/version boundary is respected;
5. for long/complete documents: coverage audit and repair are complete;
6. for PDFs/layout: built from audited content, fonts/glyphs/blank pages/large whitespace/table/OCR risks checked;
7. if the channel cannot carry full output, main returns path(s), not a lossy summary disguised as completion.

Use `tools/translation_artifact_gate.py` where possible.

## Failure conditions

Main must mark the translation run as failed or draft if:

- the agent returns long chat prose instead of the envelope;
- required artifacts are missing, tiny, or do not match reported byte counts;
- the agent changed scope, edition, source, style, or output format without Alex's approval;
- the agent claims coverage/layout/QA without producing auditable reports;
- a layout was built before coverage repair and then called final;
- the agent's story conflicts with file evidence.

## Multi-workstream protection

Translation runs must not consume the main task ledger. If Alex has assigned other active workstreams, main keeps them in `task_ledger.json` or the main conversation ledger and reports them separately.
