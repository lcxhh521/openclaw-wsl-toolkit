# Agent Room Mainline Governance Contract

Author: Windows Codex architect
Date: 2026-05-28
Scope: openclaw-evolution and future Agent Room collaboration spaces

Alex has approved transferring this methodology into the active agent collaboration system now. This is not a decorative principle. Treat it as the operating contract for reducing drift and turning discussion into execution.

## 0. North Star

The top-level objective is not "make agents talk". The top-level objective is:

Improve Alex's local OpenClaw experience: reliability, responsiveness, scheduled-task delivery, model/quota continuity, Telegram usability, and maintainable architecture.

Agent Room is infrastructure for that objective, not the objective itself.

## 1. Every Task Must Bind To A Mainline

Every room task must have:

- `mainline_id`: one of the explicit mainlines, for example `telegram_reliability`, `scheduled_task_reliability`, `agent_room_infrastructure`, `model_quota_routing`, `control_center_observability`, `people_daily_deep_read`, `market_daily_report`, `translation_agent`, or a newly approved mainline.
- `problem_statement`: what user-visible problem this task solves.
- `expected_user_value`: what Alex can notice when it works.
- `owner`: who integrates the task, not who is allowed to think.
- `participants`: agents expected to contribute.
- `definition_of_done`: concrete completion criteria.
- `approval_gate`: whether Alex approval is required and why.
- `dedupe_key`: used to merge repeated standing tasks.

A task without a mainline is intake-only; it must be triaged before execution.

## 2. State Machine

Tasks must move through explicit states:

`intake -> triage -> plan -> execute -> review -> integrate -> close`

Allowed exception states:

- `needs_alex`: only for non-retrievable preference, external/destructive action, secrets, irreversible global defaults, or major quality-surface changes.
- `blocked`: hard blocker with evidence and next user/external action.
- `stale`: no heartbeat/result past deadline.
- `failed`: attempted and failed with evidence.
- `retry`: retryable failure with bounded retry budget.
- `merged`: duplicate task merged into an existing task.

Do not leave work indefinitely in `queued` or `running`.

## 3. Definition Of Done

A task is not complete because agents discussed it. It closes only when at least one concrete output exists:

- patch or config change;
- artifact or decision record;
- smoke/runtime verification;
- RCA with root cause and prevention rule;
- accepted blocker with exact next action;
- user-visible improvement confirmed by evidence.

Discussion-only output is allowed only as an intermediate state and must create the next executable step.

## 4. WIP Limit And Dedupe

Do not keep creating new standing tasks for the same mainline problem.

- Same `mainline_id + problem_statement` should reuse or merge into an open task.
- Each mainline should normally have at most 1-2 active tasks.
- Repeated standing discussion without closure is drift.
- New task creation should check existing open tasks and either attach a comment or mark the old task stale/merged.

## 5. Agent Participation Semantics

Mentions choose first-response ownership, not visibility.

- All active room agents can see shared room context.
- If Alex @mentions one agent, that agent must respond or produce a visible blocker.
- Other agents may still contribute when they have concrete evidence, correction, safety/runtime risk, architecture impact, patch, smoke, or next action.
- If an agent has no material contribution, it should produce `NO_COMMENT` internally, not a long visible explanation.
- If no one is mentioned, each agent should decide whether it has material value; the room should not silently drop the message.

## 6. Human Approval Gates

Do not ask Alex to approve safe local details. Proceed and report evidence for:

- local inspection;
- reversible local patch under Agent Room artifacts/tools;
- smoke tests;
- state/status artifact generation;
- RCA records;
- non-secret config validation.

Ask Alex only for:

- external publish/notify where not already approved;
- destructive delete/move/archive;
- secret/token entry or exposure-risk boundary;
- OpenClaw source-code changes if not explicitly approved;
- GitHub push;
- major content-quality or editorial-policy changes;
- irreversible global defaults.

## 7. Drift Detection

Each collaboration cycle should run a drift check:

- Does this task still serve the North Star?
- Which mainline does it advance?
- What user-visible problem is being solved?
- What concrete output will close it?
- Is this duplicate of an existing open task?
- Are we discussing infrastructure while ignoring a higher-priority user-facing failure?

If drift is detected, rebase to the mainline, merge duplicate tasks, or close as not-now.

## 8. Durable Execution And Recovery

Borrow the durable-execution idea from workflow systems: every step must leave enough state to resume.

For each runner:

- record `running/completed/failed/stale/retry`;
- record heartbeat and deadlines;
- harvest completed results automatically;
- stale runners must become `stale` or `retry`, not invisible;
- failure must be visible as a Chinese blocker when it affects Alex's command;
- final task state must be derived from artifacts, not local optimism.

## 9. Role Model

- OpenClaw main: coordinator/frontline integrator and runtime-context provider. It participates, executes when appropriate, and can be challenged.
- Windows Codex architect: architecture reviewer, root-cause pressure, system design, Windows/context bridge, and cross-agent task framing.
- Codex local/WSL runner: implementation, code/config patches, structured review, smoke evidence.
- Claude Code: peer implementation/review/execution agent using Ark Coding Plan backend; not restricted to read-only unless a specific run mode says so.

Roles guide responsibility, not capability silos.

## 10. Immediate Implementation Requirements

The active agents should update Agent Room toward these concrete mechanics:

1. Add/require `mainline_id`, `dedupe_key`, `definition_of_done`, `approval_gate`, and `next_action` in task manifests.
2. Add a triage step for `target_agents=[]` so user messages do not disappear.
3. Add standing-task dedupe/merge before creating more `standing_mainline_discussion` tasks.
4. Add a close/merge/stale path for old queued standing tasks.
5. Add drift-check output to local artifacts; visible Telegram output only when meaningful.
6. Keep the pinned card minimal; diagnostics stay in local status or `/status`.
7. Every cycle must produce a concrete output or a documented blocker.

This contract should be cited by future Agent Room changes as the governance baseline.

## 11. Anti-Cargo-Cult Rule

Do not copy LangGraph, CrewAI, AutoGen, Scrum, Kanban, or OKR patterns mechanically.

Use them only as reference vocabulary. The final design must be chosen from the actual local constraints:

- OpenClaw currently runs through WSL/systemd user services;
- Telegram is both user front-end and Agent Room projection surface;
- main already owns runtime/session/user-context evidence;
- Codex/Claude Code are local peer runtimes with different execution capabilities;
- existing artifacts include mailbox, tasks.jsonl, collaboration ledgers, active-runners, presence files, pinned-card state, status snapshots, and timers;
- Alex values fast user-visible reliability more than perfect framework purity.

When proposing a design, each agent must state:

1. Which external theory/framework idea is being borrowed;
2. Why it fits this local system;
3. What is intentionally not copied;
4. What the smallest shippable local implementation is;
5. How success will be verified from existing artifacts/runtime behavior.

If a framework idea increases ceremony without directly reducing drift, improving recovery, or improving Alex's experience, reject or defer it.


## Runner Status Taxonomy

Updated: 2026-05-29T02:12:50+08:00

Telegram-facing Agent Room status must distinguish four runner states:

1. `executing_no_output_yet`: runner started, no stdout/stderr/result yet, but still within runner-attempt soft deadline. Say it is executing/waiting; do not claim progress.
2. `soft_deadline_attention`: runner exceeded runner-attempt soft deadline but is still alive. Show attention/slow, owner, and elapsed/deadline; do not call it hard failure.
3. `hard_deadline_or_failed`: process died, hard deadline exceeded, or runner failed. Generate a Chinese blocker with owner, impact, and recovery_action.
4. `completed_with_evidence`: result/artifact/smoke exists and can be summarized.

The runner-attempt soft deadline is computed from runner start time. Parent task soft deadlines may be retained as provenance (`task_soft_deadline_at`) but must not make a newly spawned runner appear abnormal immediately.
