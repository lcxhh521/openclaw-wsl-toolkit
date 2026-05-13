# User Experience Contract

This file describes what the Codex/OpenClaw loop should feel like for the user.
It is more important than making the internal collaboration look clever.

## Primary Experience

The user should be able to open a Codex window and say a natural-language
request. Codex should then:

1. Restate the request in practical terms.
2. Explain the first concrete step and why it helps the user's experience.
3. Check whether OpenClaw is ready.
4. Dispatch at most one OpenClaw task at a time.
5. Give short progress updates instead of going silent.
6. Stop with a clear blocker if OpenClaw is not ready, slow, or failing.
7. Preserve existing working features by default.

## What "不卡" Means

- The user can ask "现在到哪了" and Codex can answer from local status.
- Long tasks have a visible phase, deadline, last observation, and next action.
- Codex does not start multiple hidden OpenClaw jobs that compete for resources.
- If a task exceeds its time budget, the result is "timed out with evidence",
  not a silent wait.

## What "按时交付" Means

- Broad requests are split into staged slices.
- Read-only review tasks should come back quickly.
- Implementation tasks should report partial progress before expensive tests.
- Test hangs should be marked as verification blocked, not confused with a
  complete implementation failure.

## What "Scheduled Tasks On Time" Means

- A scheduled task has a visible planned time, actual start time, phase,
  deadline, last checkpoint, and next recovery action.
- If a workflow cannot start on time, the reason is classified: WSL/gateway
  offline, scheduler did not fire, previous run still active, source data empty,
  model/provider slow, quality gate blocked, publish/notify failed, or status
  classifier misread the artifacts.
- Recovery should resume from checkpoints instead of rerunning the whole job
  blindly.
- Original scheduled-task recovery may proceed without per-run approval, but
  publish/notify remains gated by quality and the user's rules.

## Default Decision Rules

- If the request affects user-facing behavior, Codex proposes the experience
  change first.
- If the request affects architecture, OpenClaw reviews the proposal before
  implementation.
- OpenClaw main does not need to be treated as the only complete source of
  context. Its role is to participate deeply: provide runtime background,
  historical clues, environmental constraints, evidence from the local system,
  objections, and implementation feasibility feedback.
- If OpenClaw is stuck or its feedback is not useful, Codex may directly improve
  the local experience layer and explain why.
- OpenClaw source code is not the first target unless the user explicitly asks
  for product-level changes or a local experience fix cannot solve the problem.

## Preserved Existing Features

- Telegram/channel routing.
- Existing Gateway sessions and approvals.
- Model login/auth flows.
- Existing CLI `openclaw agent --json` behavior.
- Any local custom workflow the user already relies on.
