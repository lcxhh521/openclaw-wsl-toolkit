# Agent Room P0 Contract

Updated: 2026-05-20
Owner: Codex + OpenClaw main collaboration
Status: P0 draft, implementation baseline

## 1. Goal

Agent Room is a real multi-agent collaboration room, not a Telegram summary relay.

Alex's target experience:

- Each room is one bounded mailbox/context.
- Each participating agent has its own identity.
- Alex can see live discussion, participant state, blockers, and progress.
- Alex can address all agents or one agent with @ routing.
- External agents can contribute work without silently advancing canonical state.
- Fallback or blocked reviews must be visible and must not be presented as verified dual review.

## 2. Canonical State

Telegram is a projection layer. It is not canonical state.

Canonical state lives in workspace artifacts:

- room metadata: `room.json`
- participant state: `participants.json`
- task ledger: `agent-room/tasks.jsonl`
- message/comment ledger: `agent-comments/*.jsonl`
- result artifacts: `agent-task-results/<agent>/<task>/<run>/`
- schema files: `schemas/*.schema.json`

Telegram `message_id` is delivery/projection evidence only. It must not be used as the source of truth for task status, acceptance, or workflow completion.

## 3. turn.json Boundary

`turn.json` remains the Codex <-> OpenClaw main bilateral mailbox turn file.

It must not become the global scheduler for all agents.

Multi-agent room orchestration uses:

- task manifests
- per-agent comment/result lanes
- participant state
- acceptance gates
- side-effect gates

The Codex mailbox bridge is one participant adapter, not the room substrate.

## 4. Participant Roles

### openclaw-main

OpenClaw main is:

- primary coordinator
- execution participant when appropriate
- local runtime observer
- Alex-facing synthesizer

It is not only a passive moderator. Work that belongs to OpenClaw main should still be done by OpenClaw main, with the proper gates.

### codex

Codex is:

- architecture/protocol designer
- implementation reviewer
- local code/artifact editor when Alex asks
- room contract maintainer

Current limitation: Codex is not a reliable background daemon. Wake depends on Codex-side heartbeat/foreground availability until a dedicated adapter exists.

### claude-code

Claude Code is a first-class peer agent, not merely a fallback reviewer.

Its role should be comparable to Codex at the room level:

- architecture discussion
- implementation planning
- code/change review
- validation and testing
- bounded implementation
- research and synthesis
- batch execution when the task manifest grants it

Permission parity means parity of **task class and responsibility**, not unrestricted side effects.

Claude Code may receive the same class of tasks as Codex when the task manifest declares:

- lane
- input context
- write scope, if any
- expected outputs
- side-effect permissions
- acceptance gates
- rollback/review expectations

Claude Code may edit files only inside an explicit write scope. It must write results to comment/result/artifact lanes first, and any canonical import, Telegram send, Notion publish, GitHub push, workflow/prompt/model/QC change, or persistent runner installation still requires the same gates that would apply to Codex.

Claude Code must not be treated as lower-status just because it is external, but it must remain observable and attributable: every run needs a task id, run id, status, artifact path, and review/acceptance state.

### Claude Code Proactive Opinion Policy

Claude Code may proactively speak in the room as a peer agent.

This means Claude Code can write an advisory comment or room message when it detects:

- a likely blind spot in Codex/OpenClaw reasoning;
- a risk in an implementation plan;
- missing verification or insufficient acceptance criteria;
- a better decomposition of a task;
- a blocked external-agent integration path;
- a quality-surface change that should be foreground-reported to Alex.

Proactive speech is not the same as unilateral authority.

Claude Code proactive comments must:

- be attributable to `claude-code`;
- include a run id or message id;
- declare `kind=proactive_opinion`, `risk`, `question`, or `review_comment`;
- reference the room seq/task/run it observed when available;
- write to a comment/message lane first;
- avoid source edits, Telegram sends, Notion/GitHub actions, model/prompt/QC changes, or persistent runner installation unless a task manifest explicitly grants that side effect.

OpenClaw main and Codex should treat these comments as first-class discussion input, not as final decisions.

### antigravity

Antigravity is a candidate IDE/MCP participant.

Current state:

- MCP server and Windows wrapper smoke tests have passed.
- One GUI direct-paste writeback has been observed.
- Same-run-id queued roundtrip is not yet verified.
- `routing.enabled` must stay false until the P0 acceptance criteria pass.

## 5. @ Routing Semantics

`@all`:

- creates a room task manifest;
- moderator/main decomposes and routes bounded tasks to eligible agents;
- agents do not all run unbounded by default.

`@agent`:

- creates a bounded task for the named agent;
- includes lane, context, permissions, expected outputs, and gates;
- external agents write to comment/result lanes first.

No @ route may bypass:

- quality change reporting rules;
- side-effect gates;
- source edit scopes;
- secret/token boundaries;
- canonical import review.

## 6. Review Status Labels

Review state must be first-class:

- `not_requested`
- `requested`
- `running`
- `fallback_review`
- `blocked_review`
- `single_agent_review`
- `dual_review_verified`
- `accepted`
- `rejected`

Runner exit code 0 means only that the runner completed. It does not mean review accepted.

## 7. Telegram Projection Rules

Telegram room messages should make collaboration visible but not noisy.

Allowed by default:

- high-value progress updates
- decisions
- blockers
- participant state changes
- requests for Alex approval
- quality-surface change reports
- short links/paths to artifacts

Not allowed by default:

- raw tokens/secrets
- raw prompt dumps
- full hidden transcripts
- internal stack traces unless needed for a blocker
- routine heartbeat spam

Full transcript projection requires explicit Alex request and redaction.

## 8. Side-Effect Gates

External side effects include:

- Telegram outbound messages
- Notion publish/update
- GitHub push/PR/comment
- source edits
- workflow/prompt/model/QC changes
- persistent runner/timer/service installation

Every side effect must have:

- declared permission
- actor
- target
- gate status
- evidence path
- failure behavior

## 9. Antigravity P0 Acceptance Criteria

Before `antigravity.routing.enabled=true`, all must be true:

1. OpenClaw main creates run_id `R`.
2. A bounded task manifest and brief exist for `R`.
3. Antigravity reads the bounded brief for `R`.
4. Antigravity writes `agent-comments/antigravity.jsonl` with exact `run_id=R`.
5. `antigravity_adapter.py read --run-id R` reports `roundtrip_verified=true`.
6. No source edits occurred.
7. No Telegram outbound occurred.
8. No secrets/tokens were read or exposed.
9. No global workflow state changed.

If Antigravity requires a GUI/tool permission check, that is a one-time bootstrap gate, not the production runner path.

## 10. P0 Deliverables

Minimum implementation artifacts:

- `agent-room/room_contract_p0.md`
- `schemas/participant_state.schema.json`
- `schemas/task_manifest.schema.json`
- `schemas/room_message.schema.json`
- read-only status reporter that summarizes room readiness
- task/comment digest that does not advance canonical state

## 11. Telegram Group Auto-Room Creation

Telegram groups can act as a room creation ingress.

When Alex creates or selects a Telegram group and invites/mentions one or more supported agent bots, OpenClaw should create or bind a corresponding Agent Room automatically.

Important distinction:

- Telegram group = visible ingress/projection surface.
- Agent Room = canonical workspace artifact space.

The group invite/member-change event should create or update:

- `agent-room/rooms/<room_id>/room.json`
- `agent-room/rooms/<room_id>/participants.json`
- `agent-room/rooms/<room_id>/tasks.jsonl`
- `agent-room/rooms/<room_id>/comments/*.jsonl`
- `agent-room/rooms/<room_id>/projections/telegram.jsonl`
- a binding record linking Telegram chat_id/message thread to room_id.

Room binding rules:

- A newly created Telegram group means create a new Agent Room binding.
- An existing Telegram group with an existing room binding must keep the same room_id.
- Inviting a new supported agent bot into an existing bound group updates that room's participants; it must not create a new room.
- A Telegram chat_id maps to one active room binding by default.
- A new binding or participant update must record what triggered it: group_created, bot_invited, agent_mentioned, or explicit command.
- Pulling supported agent bots into the group should add them as room participants if their adapter is available.
- Unsupported bots or users should not become executable agents automatically.
- Canonical room creation/update must be idempotent: repeated group/member updates must not create duplicate rooms for the same chat_id unless Alex explicitly asks for a new room.
- Auto-created rooms start with side-effect gates closed.
- Auto-created rooms may project compact status to Telegram but must not expose raw prompts, secrets, or hidden transcripts.

Activation examples:

- Alex creates group `openclaw??` and invites OpenClaw bot: create one room binding for that Telegram group and add OpenClaw main.
- Alex later invites Claude Code bot into the same group: update that same room's participants with `claude-code`; do not create a second room.
- Alex later invites Antigravity bot into the same group: update that same room with `antigravity` as candidate/blocked unless routing is ready; do not create a second room.
- Alex types `@all ????????`: create a task manifest in the room and route bounded tasks according to participants and permissions.

This makes the act of forming a Telegram group also create the corresponding collaboration space, while preserving workspace artifacts as canonical state.

