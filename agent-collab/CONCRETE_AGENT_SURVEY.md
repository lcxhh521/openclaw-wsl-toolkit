# Concrete open-source agent survey

Date: 2026-05-12

Purpose: look beyond abstract frameworks and inspect concrete agents/products we can learn from when evolving OpenClaw `agent-collab`, coding-agent workflows, and background task handoff.

## Snapshot table

| Project | What it is | Agent shape | Notable implementation idea | What we can borrow |
|---|---|---|---|---|
| OpenAI Codex CLI | Local coding agent in terminal / IDE / desktop | Interactive CLI/app, tool execution, local repo context | Lightweight local coding loop; ChatGPT login or API key; familiar CLI/app split | Treat external agents as pluggable participants; support desktop/CLI wake commands separately |
| OpenHands | AI-driven development environment with SDK, CLI, local GUI, cloud | SDK engine + CLI + GUI + REST API | Multiple entrypoints around shared agentic engine; local GUI resembles Devin/Jules; cloud adds integrations/RBAC | Separate engine/protocol from UI; expose mailbox/status via simple API later |
| Aider | Terminal pair programmer | Git-first single-agent coding loop | Repo map; automatic commits; diff/undo workflow; lint/test loop | Require coding worker to produce diff/test/commit metadata; git is review substrate |
| SWE-agent / mini-SWE-agent | Issue-solving software engineering agent | Task/issue oriented autonomous loop | Single YAML/config; benchmarkable issue tasks; simpler mini version; traces around tool use | Use task contracts with repo/commit/issue scope; keep runs reproducible and auditable |
| Goose | General-purpose local agent, desktop + CLI + API | Local native agent with providers and MCP extensions | Provider abstraction; MCP extension ecosystem; local-first, multi-platform | Keep capabilities modular/permissioned; expose external tools through opt-in connectors |
| Qwen Code | Terminal coding agent optimized for Qwen models | Terminal-first, IDE-friendly, skills + subagents | Built-in skills/subagents; multi-provider auth; Claude Code-like UX | Study skills/subagent packaging; our install skill should recognize agent-collab as opt-in capability |
| Continue CLI | Source-controlled AI checks for PRs | CI/PR agent checks | Agents/checks are markdown files in repo; enforceable in CI with pass/fail and suggested diff | Make agent tasks source-controlled; add `.agent/checks` style review tasks later |
| LangGraph | Stateful agent/workflow framework | Graph/state machine | Durable execution, resume, human-in-loop, memory | Evolve `turn.json` into durable state machine |
| CrewAI | Role-based autonomous teams + event flows | Crews + flows | Roles/goals/tasks; flow orchestration | Add participant roles and task responsibility metadata |

## Concrete observations

### 1. OpenAI Codex CLI

Source: <https://github.com/openai/codex>

What exists:

- Local coding agent that runs on the user's computer.
- Terminal, IDE, desktop, and cloud product surfaces exist around the same general agent idea.
- Auth can be ChatGPT account or API key.

Takeaways:

- Do not assume one wake mechanism. Codex may be CLI, desktop app, IDE extension, or cloud session.
- `agent-collab` should keep `CODEX_WAKE_COMMAND` local and configurable rather than pretending there is one universal Codex API.
- Our mailbox should store tasks/artifacts, not rely on Codex UI being alive.

### 2. OpenHands

Source: <https://github.com/OpenHands/OpenHands>

What exists:

- SDK: composable Python library / core agentic engine.
- CLI: local user-facing mode, familiar to Codex/Claude Code users.
- Local GUI: laptop agent runner with REST API and React UI.
- Cloud/enterprise: integrations, RBAC, collaboration.

Takeaways:

- A serious agent product separates engine, CLI, GUI, API, and cloud surfaces.
- Our v0 mailbox is only the persistence/control layer. Later, a tiny local API/status UI could sit on top without changing protocol.
- Collaboration and permissions become product features, not prompt tricks.

### 3. Aider

Source: <https://github.com/Aider-AI/aider>

What exists:

- Pair-programming agent in terminal.
- Builds a repo map for larger codebases.
- Strong git workflow: automatic commits, easy diff/undo.
- Lint/test integration after edits.

Takeaways:

- For coding agent tasks, artifacts should include:
  - repo path;
  - base commit;
  - changed files;
  - diff;
  - test/lint commands and output;
  - whether commit was made or only patch produced.
- Main should review diffs and test evidence before telling Alex a coding task is done.

### 4. SWE-agent / mini-SWE-agent

Source: <https://github.com/SWE-agent/SWE-agent>

What exists:

- Takes a GitHub issue/custom task and tries to fix it autonomously.
- Research/benchmark orientation, especially SWE-bench.
- Current project recommends mini-SWE-agent as much simpler while matching performance.
- Configurable through a single YAML file.

Takeaways:

- Simpler can be better. Our first coding-agent contract should stay small and benchmarkable.
- Each run should have a clear task id, issue/brief, repo commit, allowed actions, output dir, and verification.
- Do not start with a complex multi-agent swarm when a one-worker issue loop is enough.

### 5. Goose

Source: <https://github.com/aaif-goose/goose>

What exists:

- General-purpose local agent for code, workflows, research, writing, automation, data analysis.
- Desktop app, CLI, and API.
- Multi-provider support and MCP extension ecosystem.
- Local-first orientation.

Takeaways:

- Provider choice and tool extension are separate layers.
- We should keep `agent-collab` focused on coordination; tool exposure belongs to the specific worker/agent configuration.
- Optional modules should be packaged cleanly, not always-on.

### 6. Qwen Code

Source: <https://github.com/QwenLM/qwen-code>

What exists:

- Open-source terminal coding agent optimized for Qwen models.
- Supports multiple providers/auth modes.
- Mentions built-in tools, skills, and subagents.
- Terminal-first with IDE integrations.

Takeaways:

- Agent capability packaging matters. Skills/subagents can be installable units.
- `agent-collab` should be exposed to installation skills as an opt-in capability, not as part of base OpenClaw install.
- External coding agents may have their own subagent/skill concepts; our protocol should not compete with them, just coordinate handoff.

### 7. Continue CLI

Source: <https://github.com/continuedev/continue>

What exists:

- Source-controlled AI checks, enforceable in CI.
- Each agent/check can be a markdown file in `.continue/checks/`.
- Runs on pull requests, returns green/red and suggested diff.

Takeaways:

- Agent behavior can be source-controlled and reviewable.
- Future OpenClaw toolkit could add `.openclaw-agent/checks/` or `agent-collab/checks/` for reusable review tasks.
- This is useful for coding-agent L2/L3 benchmarks: architecture review, security review, regression-risk review.

## What this says about our current design

Our current v0 is intentionally smaller than these systems. That is good.

Confirmed choices that still look right:

- mailbox + `turn.json` for low-tech durable handoff;
- opt-in module instead of default install;
- watcher retries with cooldown/max attempts;
- artifact-first handoff;
- main as coordinator/reviewer, coding agent as worker;
- approval gate before external writes/destructive/config/secrets/model/prompt changes.

Gaps to close next:

1. **Participant model**
   - Add `participants.example.json` with id, role, inbox, outbox, wake command, allowed actions.

2. **Task/run model**
   - Add `handoff.schema.json` or `task.schema.json`.
   - Fields: task id, requester, assignee, goal, constraints, inputs, artifacts, approvals, status.

3. **Artifact registry**
   - Add `artifact_registry.json` per run.
   - Track files, diffs, logs, tests, publish status, review status.

4. **Read-only status tool**
   - Add `mailbox_status.py` that never wakes agents.
   - Show pending owner, seq age, last writer, retries, stale status.

5. **Coding-agent benchmark ladder**
   - L1: brief/artifact compliance.
   - L2: read-only architecture audit.
   - L3: small patch + tests.
   - L4: issue closure with diff/review.
   - L5: research-engineering tools.

6. **Generic naming**
   - Keep Codex compatibility, but add neutral names for future participants.
   - Avoid `codex_to_main.md` as the only possible protocol.

## Caution

Many projects market themselves as autonomous agents, but their useful engineering patterns are usually mundane:

- explicit state;
- scoped tasks;
- tool permissions;
- git diffs;
- test evidence;
- artifacts;
- human approval;
- retry/backoff;
- observability.

Those are exactly the parts we should copy first.
