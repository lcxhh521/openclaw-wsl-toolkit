# Telegram Room Runtime Plan P0

Status: artifact-only design, no runtime watcher started by this document.
Owner: codex-main-bridge / OpenClaw main collaboration.
Language default: zh-CN for room-visible agent discussion.

## Goal

Build a Telegram-group-facing Agent Room where Alex can see agent discussion and issue instructions, while canonical state stays in workspace artifacts.

Telegram is the visible room surface. The workspace is the source of truth.

## Core Semantics

1. New Telegram group with OpenClaw/agent bot participation means create a new room/context/mailbox.
2. Adding an agent bot to an existing Telegram group means update participants in that existing room.
3. Removing an agent bot from an existing group means mark that participant inactive/removed in that existing room.
4. Adding or removing participants must not create a new room unless the Telegram group itself is new.
5. A room can have different participants from another room; there is no global default participant set.

## Canonical Artifacts

P0 keeps canonical state under:

- `agent-room/rooms/<room_id>/room.json`
- `agent-room/rooms/<room_id>/participants.json`
- `agent-room/rooms/<room_id>/tasks.jsonl`
- `agent-room/rooms/<room_id>/events.jsonl`
- `agent-comments/<agent_id>.jsonl`
- `telegram-room-bindings.json` for Telegram chat to room binding metadata
- `agent-room/telegram_agent_bots.json` for non-secret bot identity bindings

Existing root-level files remain compatibility artifacts until migration is complete.

## Telegram Event Mapping

### Group Create / Bot Added To A New Group

Input:
- Telegram group chat id
- group title
- observed OpenClaw session key
- bot membership list when available

Action:
- create `room_id` from explicit mapping or stable slug;
- create room artifacts if no binding exists;
- register OpenClaw main as coordinator participant;
- register observed agent bots as participants;
- status starts as `created_ingress_pending`;
- do not publish backlog.

### Agent Added To Existing Group

Input:
- known Telegram group chat id already bound to a room;
- new bot/member identity.

Action:
- update existing room `participants.json`;
- append event `participant_added`;
- do not create a new room;
- do not replay prior private/canonical transcript to the new participant unless explicitly approved.

### Agent Removed From Existing Group

Action:
- mark participant as `inactive` or `removed`;
- revoke future routing to that agent in this room;
- keep historical comments for audit.

### User Message In Group

Routing:
- `@all` creates one bounded task manifest with all eligible participants as `target_agents`.
- `@codex` targets Codex.
- `@claude` / `@claude-code` targets Claude Code.
- `@antigravity` targets Antigravity only when routing is verified.
- ordinary messages are room-level user context or coordinator instructions, depending on policy.

No message directly mutates canonical state. It creates an event or task manifest.



## Direct Private Bot Channels

Alex also wants one-to-one Telegram conversations that work like the current OpenClaw main private chat:

- opening `@lchcodex_bot` privately should create or reuse `dm-codex-<alex_user_id>`;
- opening `@lchclaudecode_bot` privately should create or reuse `dm-claude-code-<alex_user_id>`;
- a private bot message does not require `@agent`, because the receiver bot already selects the target agent;
- each direct room has its own room/context/mailbox identity and must not be collapsed with group rooms;
- each direct room key must include both Telegram user id and target agent id, otherwise Codex and Claude private chats from the same user will be incorrectly merged;
- direct room messages still become bounded task manifests first;
- direct replies should be sent by the corresponding agent bot only after that bot's runtime/outbound smoke is enabled;
- direct rooms follow the same secret, prompt, publication, and quality-surface gates as group rooms.

Current implementation status:

- WSL Codex CLI is installed at `/home/lcxhh/.local/bin/codex`, version `codex-cli 0.131.0`;
- WSL Codex CLI is not logged in yet, so Codex direct bot runtime can create tasks but cannot call Codex exec until login completes;
- Claude Code CLI/runtime must be treated as a local execution identity, not proof of a specific upstream model; in Alex's environment WSL `claude` may route through Ark/OpenClaw configuration, so provider/model must be verified or explicitly declared before quality-sensitive use;
- Claude direct bot runtime is allowed to create tasks and blocked comments, but real execution requires a ready/login/provider check;
- direct channel dry-run fixtures distinguish `dm-codex-100000001` from `dm-claude-code-100000001`.

## Agent Contracts

### Codex

Role:
- technical architect and implementation agent.

Default:
- can write Codex mailbox replies and approved workspace artifacts;
- no GitHub push unless Alex explicitly requests;
- no Telegram outbound unless policy and command allow.

### OpenClaw Main

Role:
- room coordinator, local-state observer, front-stage summarizer, and executor for OpenClaw-owned tasks.

Default:
- participates in discussion and execution;
- reports local reality;
- must not silently downgrade quality-surface decisions.

### Claude Code

Role:
- first-class peer reviewer/executor candidate.

P0 status:
- comments/advisory lane until CLI login and runtime verification are complete.

Claude Code self-review recommendations incorporated:
- input must be a bounded task manifest with run_id, agent_id, seq_observed, scope, gates, and success criteria;
- output goes to `agent-comments/claude.jsonl`;
- comment kinds: `review_comment`, `architecture_note`, `risk`, `question`, `status`;
- every comment includes `agent_id`, `run_id`, `timestamp`, `seq_observed`, `confidence`, title/body;
- no canonical state changes by default;
- edit/execute/publish/notify require explicit gates;
- stale `seq_observed`, duplicate comments, or over-scope briefs must be rejected or downgraded to a question.

### Antigravity

Role:
- future peer reviewer/executor.

P0 status:
- manual GUI + MCP writeback has historical proof;
- same-run-id queued roundtrip is not verified;
- `routing.enabled=false` until:
  `main send_task -> Antigravity reads bounded brief -> write_agent_comment same run_id -> main read_result verified`.

No foreground GUI automation or duplicate window spawning should be used for normal runtime.

## Projection Policy

Alex wants visible room discussion, not only status summaries.

P0 projection rules:
- agent-visible discussion messages may be sent to the Telegram group in zh-CN;
- raw secrets, tokens, raw prompts, hidden system prompts, oversized artifacts, and private logs are never projected;
- full transcript can be projected only when explicitly requested and after redaction;
- routine watcher ticks, dry-run probes, and duplicate status are not projected;
- quality-surface changes must be proactively surfaced to Alex before acceptance.

## Side-Effect Gates

Default closed:
- Telegram send by newly registered agent bots;
- Notion publish;
- GitHub push;
- source edits outside declared task scope;
- model/provider/prompt/QC changes;
- Antigravity production routing;
- Claude canonical-state advancement.

Opening a gate requires:
- room policy flag;
- task manifest permission;
- runtime smoke or dry-run evidence when relevant;
- explicit Alex approval for quality or external publication surfaces.

## Dedupe, Rate Limit, Backoff

Each projector/sender should keep:
- event hash;
- last sent timestamp;
- target chat id;
- agent id;
- room id.

Suppress:
- same event hash within the same room;
- repeated unchanged status;
- retry storms after Telegram errors.

Backoff:
- first retry after short delay;
- then exponential backoff;
- persistent failure becomes `blocked_telegram_outbound` and is visible in room state.

## Failure Labels

Use explicit labels instead of vague success:

- `created_ingress_pending`
- `participant_update_pending`
- `agent_secret_present_unverified`
- `agent_cli_not_logged_in`
- `agent_comment_lane_verified`
- `same_run_id_roundtrip_missing`
- `blocked_review`
- `blocked_telegram_outbound`
- `dry_run_passed`
- `runtime_ready`

## Dry-Run Validation Plan

No production watcher starts in P0. Validate with artifact-only checks first:

1. Validate all schemas and examples with `check_agent_room_artifacts.py`.
2. Verify `telegram_agent_bots.json` exists and contains no token values.
3. Verify token env file exists and is mode 600 without printing it.
4. Simulate:
   - new group -> create room event;
   - existing group + add Claude bot -> participant update;
   - `@all` -> bounded task manifest;
   - `@claude` -> Claude comment-lane task;
   - Antigravity queued task -> blocked until same-run-id comment arrives.
5. Only after dry-run evidence, enable a small always-on sidecar for ingress observation.
6. Only after outbound smoke, enable per-agent bot projection.

## Production Runtime Shape

P1 should be a resident, observable sidecar:

- systemd user service/timer or long-running user service inside WSL;
- reads Telegram/OpenClaw group events;
- writes room events/tasks;
- dispatches to enabled agent adapters;
- projects selected agent discussion to Telegram through each agent's own bot where possible;
- exposes health/status artifacts;
- does not depend on Codex Desktop heartbeat for normal room progress.

