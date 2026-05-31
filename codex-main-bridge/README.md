# Codex Main Bridge Mailbox

This directory is the simple fallback bridge between Codex Desktop and OpenClaw
main.

Use it when direct MCP/session delivery is unavailable or unstable.

Files:

- `codex_to_main.md`: Codex writes messages or architecture briefs for OpenClaw
  main.
- `main_to_codex.md`: OpenClaw main writes replies, objections, and execution
  feedback for Codex.
- `status.json`: small turn/status marker for humans and agents.

Rules:

- Do not write secrets, tokens, or private credentials here.
- Keep each turn short and append-only unless the user asks to summarize.
- Prefer concrete questions, constraints, and next actions.
- User-facing reliability and existing features are higher priority than
  internal collaboration elegance.

Windows/WSL file writes:

- Use `WslUtf8Bridge.ps1` for every Windows-to-WSL file write.
- Do not pass file content through PowerShell command strings, here-strings, or
  text pipelines.
- Do not base64 large payloads into a single `wsl.exe ... bash -lc` command; it
  can hit Windows command-line limits.
- Write bytes to `StandardInput.BaseStream` and let WSL write the target file.
- Treat `turn.json` advancement, not process launch, as the success signal.

Watcher modes:

- Full OpenClaw main participation is the default collaboration path when main's
  judgment matters. The watcher prefers the separate background main session
  `agent:main:main` when available, so architecture collaboration does not drag
  Alex's long foreground Telegram transcript into every turn.
- The foreground Telegram/main session remains Alex's interactive chat. The
  background main session is still the same `main` agent configuration and can
  contribute runtime background, local evidence, feasibility feedback, and
  objections, but it does not send Telegram messages by default.
- `foreground_guard.json` is kept as an observation artifact only. It should not
  block full main review unless Alex explicitly asks to pause collaboration.
- `lightweight_main_responder.py` is no longer part of the automatic watcher
  path. Keep it only as a manual/debug helper for explicit bridge health checks.
- Improve speed by sending fewer, higher-signal briefs to main and by fixing
  root causes, not by replacing main's participation with a weaker responder.
- `watch-runs/active-run.json` records the currently running or most recent
  watcher launch so stale or overlapping runs are visible.

## Append-only Dialogue Archive

The mailbox files are current-state files and may be overwritten each turn. The
durable collaboration history lives in:

- `archive/mailbox-turns.jsonl`: canonical append-only history.
- `archive/snapshots/`: human-readable Markdown snapshots.

Writers and watchers should call:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/archive_mailbox_turn.py --event <event> --actor <actor> --note <short-note>
```

Archive rules:

- Treat `turn.json` advancement as the success signal.
- Archive immediately after a side advances `turn.json` whenever practical.
- Preserve duplicate watcher observations as audit records.
- Use the `idempotency_key` to detect duplicates without deleting history.
- Do not store secrets, raw tokens, provider keys, or large unrelated logs.
- Write Markdown snapshots atomically so partial files are not mistaken for
  complete history.

Use the read-only verifier when checking health:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/verify_mailbox_archive.py
```

It reports row count, sequence range, missing sequence gaps, duplicate
idempotency keys, and whether the current mailbox state has been archived.

## Telegram Room Relay

`telegram_room_transcript_relay.py` is the bridge-owned relay from the mailbox
conversation to the Telegram group binding in `telegram-room-bindings.json`.
It is intentionally summary-only and must not send secrets or full transcripts.

Operational contract:

- Binding is room-scoped: `room_id=openclaw-evolution`, not a global default.
- Outbound scope is summary-only; the relay sends deterministic summary cards
  and keeps full mailbox bodies local. Full transcript still requires an
  explicit user request.
- The relay de-duplicates archive/current mailbox records by `(seq, writer)` so
  the same turn is not sent twice when the current file and archive overlap.
- Timer catch-up is latest-first and bounded: the systemd service runs with
  `--max-items 1`, so a stale relay resumes by sending the latest key turn and
  advancing `last_seen_seq`, instead of flooding the group with old backlog.
- Summary cards are capped and include title/status/key bullets plus a pointer
  to the local mailbox/archive; do not relay raw architecture briefs by default.
- Gateway/event-loop pressure is a warning unless Telegram delivery/fetch or
  stuck-recovery signals are present; transport failures still use exponential
  backoff.
- Group ingress remains separate from outbound relay. Ordinary group messages,
  `@bot`, and slash commands must be verified before marking ingress active.

Health checks:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/telegram_room_transcript_relay.py --status
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/telegram_room_transcript_relay.py --dry-run --max-items 3
systemctl --user status openclaw-room-transcript-relay.timer --no-pager
```

Resume options after downtime:

```bash
# Safe resume from now: mark the latest local seq seen without sending backlog.
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/telegram_room_transcript_relay.py --mark-current-seen

# Send only the latest pending summary, then advance last_seen_seq.
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/telegram_room_transcript_relay.py --max-items 1
```

Systemd units:

- `~/.config/systemd/user/openclaw-room-transcript-relay.service`
- `~/.config/systemd/user/openclaw-room-transcript-relay.timer`

Do not enable the timer or send a live group test unless Alex has approved group
outbound for that action. Local dry-runs are safe.

## Mailbox Writer Wrapper

All participant adapters should write mailbox turns through
`write_mailbox_turn.py` instead of updating the message file and `turn.json`
separately. This prevents mismatches where `turn.json` advances but the current
message file contains stale content.

Recommended flow:

1. Put the outgoing message in a UTF-8 temp file.
2. Call the writer wrapper:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/write_mailbox_turn.py \
  --writer codex \
  --needs-reply main \
  --content-file /tmp/codex-message.md \
  --event codex_writer_commit \
  --note short-note
```

The wrapper:

- takes an exclusive mailbox write lock;
- increments `turn.json` sequence from the current state;
- writes the message file and `turn.json` with temp-then-rename;
- calls `archive_mailbox_turn.py` before releasing the lock.

Future Claude Code, Antigravity, or group-chat adapters should use this wrapper
as their only write path.

## Snapshot Retention

Markdown snapshots are derived review artifacts. They are useful for recent
human inspection, but `archive/mailbox-turns.jsonl` remains the canonical full
record.

Default retention policy:

- keep snapshots from the last 14 days;
- keep at least the newest 500 snapshots;
- always keep snapshots tied to gaps, verifier/repair/debug/blocker events,
  protocol/schema/migration/writer-wrapper milestones, and Alex decisions;
- never clean snapshots unless the verifier is `ok` and has no unacknowledged
  sequence gaps.

Use dry-run first:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/cleanup_archive_snapshots.py --dry-run
```

The script is dry-run by default. To clean, use:

```bash
python3 ${OPENCLAW_MAILBOX_ROOT:-$HOME/.openclaw/workspace/codex-main-bridge}/cleanup_archive_snapshots.py --apply
```

`--apply` moves candidates into `archive/snapshots-trash/<timestamp>/` with a
manifest instead of deleting them outright.

## Agent Room Collaboration Ledger (Lightweight)

This is the minimal shared state for transparent multi-agent collaboration without flooding the Telegram room.

### Core Concepts
- **Work Item**: A small, bounded piece of work with clear expected output.
- **Claim**: Explicit ownership of a work item by one agent.
- **Role**: A lightweight per-turn hint such as `lead`, `reviewer`, or `implementer`; it guides coverage but does not override ownership. Multi-agent room tasks rotate these roles deterministically from the task key instead of pinning the same agent to the same role forever.
- **Artifact**: Concrete output produced (patch, schema, smoke result, etc.).
- **Blocker**: Something preventing progress, visible to all agents.
- **Handoff**: Transfer of a work item from one agent to another.

### Storage Structure
- Task manifests get a `collaboration` object (already in `task.schema.json`).
- Per-turn collaboration state lives next to `turn.json` as `collaboration_ledger.json`.
- Append-only audit trail lives in `archive/collaboration_ledger.jsonl`.

### Example Collaboration Ledger
```json
{
  "schema": "openclaw.agent_room.collaboration_ledger.v0",
  "room_id": "openclaw-evolution",
  "turn_seq": 42,
  "work_items": [
    {
      "id": "wi-001",
      "title": "检查并修复输出中的特殊字符问题",
      "description": "确保Claude Code输出干净的中文文本，无emoji/图标字符",
      "role": "reviewer",
      "status": "claimed",
      "claimed_by": "claude-code",
      "priority": "high",
      "expected_output": "patch + smoke"
    },
    {
      "id": "wi-002",
      "title": "把collaboration对象落到task manifest",
      "description": "在task生成时添加collaboration字段，包含work_items/claims/handoffs",
      "role": "lead",
      "status": "in_progress",
      "claimed_by": "codex",
      "priority": "high",
      "expected_output": "patch + smoke"
    }
  ],
  "claims": [
    {
      "work_item_id": "wi-001",
      "agent_id": "claude-code",
      "claimed_at": "2026-05-20T18:54:00+08:00",
      "status": "active"
    }
  ],
  "artifacts": [
    {
      "id": "art-001",
      "type": "patch",
      "title": "task.schema.json添加collaboration字段",
      "path": "codex-main-bridge/agent-room/schemas/task.schema.json",
      "produced_by": "codex"
    }
  ],
  "blockers": [],
  "handoffs": []
}
```

### Usage Rules
1. Each agent claims work items explicitly before starting.
2. Artifact, blocker, and agent-scoped status events must come from the current owner of the work item.
3. Handoffs must be created by the current owner and must name a different receiving participant.
4. Acceptance verdicts must come from another participant; the work item owner cannot self-accept their own output.
5. Multi-agent room manifests should create one `assigned_to` work item per local runtime agent and include lightweight rotated `role` hints plus `role_policy`; a shared unassigned item is only for explicit competitive claiming.
6. Artifacts are recorded with paths so others can find them.
7. Blockers are raised early and clearly.
8. Telegram room only gets high-signal summaries, not raw ledger updates.
9. The ledger is additive; don't delete history, just mark status changes.

### Runtime Tool
Use `agent-room/tools/collaboration_ledger.py` to initialize the current ledger from a task manifest and append claim/status/artifact/blocker/handoff/acceptance events. The tool maintains `collaboration_ledger.json` next to `turn.json` and appends every event to `archive/collaboration_ledger.jsonl`; `agent-room/tools/check_agent_room_artifacts.py --json` validates both when present and runs a temp-dir smoke for duplicate claim rejection, owner-only artifact/blocker writes, empty handoff target rejection, handoff acceptance, active-owner transfer, self-acceptance rejection, and peer acceptance recording.

`agent-room/tools/agent_task_runner.py` also records runtime collaboration evidence automatically for tasks with a `collaboration` object: it initializes the ledger if needed, claims the selected work item before agent execution, and records the resulting room comment as either a `comment_jsonl` artifact or a blocker.

If the ledger rejects the claim, `agent_task_runner.py` stops before executing the agent and writes a local blocker comment instead of letting a non-owner produce work for that item.
