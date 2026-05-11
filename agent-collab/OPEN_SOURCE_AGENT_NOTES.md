# Open-source agent references for agent-collab

Date: 2026-05-12

This note tracks open-source agent projects and patterns worth studying while evolving the optional `agent-collab` module. It is not an endorsement list and should be periodically refreshed.

## What to learn from existing projects

### LangGraph

Repo: <https://github.com/langchain-ai/langgraph>

Relevant ideas:

- Long-running, stateful workflows.
- Durable execution and resume after failure.
- Human-in-the-loop interruption and state inspection.
- Explicit state transitions rather than relying on chat history.

Possible `agent-collab` takeaway:

- Treat `turn.json` as the start of a small durable state machine.
- Add explicit states such as `waiting`, `running`, `blocked`, `needs_approval`, `completed`, `failed`.
- Make resume/retry operate from state, not from process existence.

### AutoGen / Microsoft Agent Framework

Repos:

- <https://github.com/microsoft/autogen>
- Microsoft now points new users toward Microsoft Agent Framework as AutoGen's successor.

Relevant ideas:

- Multi-agent conversations and group-chat style collaboration.
- Agents with roles, turn-taking, and tool/workbench integration.
- Cross-runtime interoperability direction via A2A/MCP in the successor stack.

Possible `agent-collab` takeaway:

- Keep the mailbox format agent-agnostic.
- Avoid hard-coding Codex names forever; generalize to `agent_a`, `agent_b`, or participant IDs.
- Define a narrow handoff envelope: sender, receiver, task, artifacts, requested action, approval needs.

### CrewAI

Repo: <https://github.com/crewAIInc/crewAI>

Relevant ideas:

- Role-based agents working as a crew.
- Separation between high-level collaborative crews and lower-level event-driven flows.
- Emphasis on task/role definitions for repeatable handoffs.

Possible `agent-collab` takeaway:

- Add a lightweight role file per participant, e.g. coordinator, coding worker, reviewer, researcher.
- Keep task briefs explicit instead of letting agents infer goals from long conversation history.
- Separate durable flow state from free-form discussion.

### OpenHands

Repo: <https://github.com/OpenHands/OpenHands>

Relevant ideas:

- Agentic development environment with CLI/web entrypoints.
- SDK/CLI split: composable components plus user-facing shell.
- Artifacts and development tasks are first-class, not just chat replies.

Possible `agent-collab` takeaway:

- Keep our main Telegram channel as an operator console, not the place where long code output lives.
- Make artifacts/manifests the primary handoff object for coding tasks.

### Aider

Repo: <https://github.com/Aider-AI/aider>

Relevant ideas:

- Git-centered coding workflow.
- Changes are inspectable as diffs/commits.
- Strong bias toward small, reviewable edits.

Possible `agent-collab` takeaway:

- For coding-agent tasks, require output artifacts and diffs.
- Let main review `git diff`, test output, and manifest before user-visible completion.

### SWE-agent

Repo: <https://github.com/SWE-agent/SWE-agent>

Relevant ideas:

- Issue-oriented autonomous software engineering.
- Clear benchmark/task boundaries.
- Tool execution with reproducible traces.

Possible `agent-collab` takeaway:

- Every coding run should have task id, brief, repo/commit, allowed actions, output directory, and verification.
- Keep run traces separate from user chat.

### Goose

Repo: <https://github.com/block/goose>

Relevant ideas:

- Local extensible agent with tool execution and MCP integrations.
- On-device/local-first orientation.

Possible `agent-collab` takeaway:

- Treat local tools as capabilities that must be explicitly exposed and permissioned.
- Keep optional integrations opt-in and modular.

### CLI agent ecosystem / orchestration lists

Reference: <https://github.com/bradAGI/awesome-cli-coding-agents>

Relevant ideas:

- The CLI coding agent ecosystem is broad: Codex CLI, OpenCode, Aider, Goose, Continue, Qwen Code, Roo Code, SWE-agent, Plandex, etc.
- There are also harnesses/orchestrators for parallel sessions and autonomous loops.

Possible `agent-collab` takeaway:

- Design `agent-collab` as a neutral mailbox/handoff layer rather than a Codex-only bridge.
- Watcher wake commands should be pluggable per external agent.

## Design principles for our module

1. **Optional install**: agent collaboration should never be part of base OpenClaw installation unless the user asks.
2. **State over process**: `turn.json` or a future state file is authoritative; process start is only a signal.
3. **Artifacts over chat**: long work writes files/manifests; chat carries brief status and decisions.
4. **Human approval gate**: external writes, destructive actions, secrets/config/model/prompt changes require owner approval.
5. **Low-frequency watchers**: no high-frequency polling; use cooldowns, locks, max attempts, and explicit status.
6. **Agent-agnostic names**: evolve from Codex-specific file names to participant IDs once v0 stabilizes.
7. **Reviewable execution**: coding work should produce manifest, diff, tests/checks, and verification.

## Candidate next changes

- Add `mailbox_status.py`: read-only status, no wakeup.
- Add `participants.example.json`: participants, roles, wake commands, allowed actions.
- Add generalized file names: `messages/<seq>-<sender>-to-<receiver>.md` while keeping v0 compatibility.
- Add `handoff.schema.json` for structured task/action requests.
- Add `artifact_registry.json` for produced files, diffs, logs, and review status.
