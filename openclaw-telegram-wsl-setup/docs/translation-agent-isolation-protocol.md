# Translation Agent Isolation Protocol

This is an optional/opt-in module, not part of the base OpenClaw Telegram WSL installation.

Purpose: keep the main Telegram assistant as command / oversight / acceptance layer, and the translation agent as an isolated executor. This is an architectural boundary, not just a style preference.

## Core boundary

- Main does not conduct translation reasoning in the chat window.
- For layout decisions, GPT participation happens inside the translation/layout workflow as an artifact-writing participant; main must not serve as GPT or final synthesizer.
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
  translation_agent_runtime_state_*.json
                         # worker-owned resumability state
```

Then main spawns translation agent with isolated context and points it to `handoff_brief.md`.

Main should not paste the entire evolving conversation into the translation agent unless the current task genuinely requires it. The translation agent should not inherit unrelated main-session state.

## Resumability

The translation/PDF worker must own its recovery state. Main may verify and
restart the worker, but should not need to reconstruct invisible progress from a
conversation transcript.

- Before material long-document or PDF work, write a run-local runtime state
  file naming the current step, last completed step, latest artifact, known
  blockers, next recovery command, and update time.
- After each completed step, update that state file and the relevant report
  files.
- If interrupted, the next worker must be able to continue from the run
  directory by reading `handoff_brief.md`, `acceptance_plan.json`, the runtime
  state file, and the latest gate/QA reports.
- A deterministic recovery command can rebuild internal candidates, but it does
  not by itself make a PDF sendable or final.

## Background supervision

Scheduled watchdogs and recovery heartbeats are internal supervision, not user
conversation.

- Prefer isolated/background cron jobs with `delivery: none` for translation
  watchdogs.
- A watchdog must write status and recovery evidence to run-local files, not to
  Telegram or the main chat.
- A watchdog must not call visible message-send tools for routine checks, gate
  passes, stale-worker probes, or no-op confirmations.
- If a watchdog discovers a true user-decision point or a new deliverable that
  needs review, it should write `pending_user_notification_*.md`. Main decides
  when and how to relay it as a normal concise update.
- Raw watchdog logs are inspectable on request, but should not be pushed into
  the chat by default.

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
6. for bilingual/parallel-text outputs: `translation_bilingual_integrity.py` passes, with no significant source-only/translation-only body blocks, Chinese-only body pages, repeated Chinese body text, or table/chart pages flattened into OCR paragraph fragments;
7. for PDFs/layout: built from audited content, fonts/glyphs/blank pages/large whitespace/table/OCR risks checked, with source tables/charts/figures rendered structurally or preserved as source images;
8. if Alex/user feedback rejected a prior candidate: a defect ledger records root cause, why existing gates missed it, repair target, and regression evidence, with no open feedback defects remaining;
9. if Alex cites a defective PDF page: main verifies that the same failure mode was scanned across the cited page, nearby pages, and continuation pages before accepting the repaired candidate;
10. for candidates promoted after model review: the model-review reports name
    or hash the latest artifact; stale review evidence from an earlier PDF build
    does not count;
11. if the channel cannot carry full output, main returns path(s), not a lossy summary disguised as completion.

Use `tools/translation_artifact_gate.py` where possible.
Use `tools/translation-agent/translation_bilingual_integrity.py` for bilingual or parallel-text deliverables.

## User Feedback Regression Loop

When Alex finds a defect in a candidate, the translation workflow must treat it
as a missed-system defect by default. The worker/main loop must:

1. mark the current candidate as rejected or blocked;
2. write a defect ledger entry naming the visible symptom, root cause, and why
   existing gates/model review missed it;
3. add or tighten a deterministic regression gate before repairing only the
   screenshot/example page;
4. scan the cited page plus nearby and continuation pages for the same failure
   mode instead of limiting the repair to the screenshot/example page;
5. run that gate across the whole artifact;
6. keep the defect open until the repaired artifact and regression evidence are
   current for the latest candidate.

Model review can support diagnosis, but it cannot override a failed local
regression gate.

## Failure conditions

Main must mark the translation run as failed or draft if:

- the agent returns long chat prose instead of the envelope;
- required artifacts are missing, tiny, or do not match reported byte counts;
- the agent changed scope, edition, source, style, or output format without Alex's approval;
- the agent claims coverage/layout/QA without producing auditable reports;
- a layout was built before coverage repair and then called final;
- a bilingual deliverable contains substantial source-only or translation-only body blocks;
- a PDF/book deliverable flattens source tables, charts, or figure pages into ordinary one-column OCR paragraph text;
- Alex/user feedback defects remain open in the defect ledger;
- the agent's story conflicts with file evidence.

## Multi-workstream protection

Translation runs must not consume the main task ledger. If Alex has assigned other active workstreams, main keeps them in `task_ledger.json` or the main conversation ledger and reports them separately.
