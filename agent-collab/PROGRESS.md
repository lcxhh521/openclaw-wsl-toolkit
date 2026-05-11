# Agent Collaboration Progress

Date: 2026-05-12

This file records the confirmed v0 progress for the optional `agent-collab` module.

## Confirmed direction

`agent-collab` is an optional install module, not base OpenClaw infrastructure.

Use it only when a user explicitly wants asynchronous collaboration between OpenClaw `main` and another local/external agent such as Codex, Cursor, Claude Code, or a coding agent.

## Confirmed collaboration protocol

The v0 protocol uses a shared mailbox directory:

- `main_to_codex.md`
- `codex_to_main.md`
- `turn.json`

`turn.json` is the source of truth:

- `seq` increments on every handoff;
- `last_writer` records who wrote last;
- `needs_reply` records who must act next;
- `updated_at` records the handoff time;
- `note` records a short operator/debug summary.

A watcher process starting is not enough to prove progress. The reliable completion signal is `turn.json` being advanced by the expected writer.

## Confirmed reminder mechanism

### Main reminds Codex / external agent

1. Main writes `main_to_codex.md`.
2. Main updates `turn.json` to `needs_reply=codex`.
3. `codex-mailbox-watch.py` detects the turn.
4. The watcher runs local `CODEX_WAKE_COMMAND` with `{seq}`, `{inbox}`, `{outbox}`, and `{turn}` placeholders.
5. Codex reads the inbox, writes the outbox, and advances `turn.json` back to `needs_reply=main`.

### Codex / external agent reminds Main

1. Codex writes `codex_to_main.md`.
2. Codex updates `turn.json` to `needs_reply=main`.
3. `openclaw-main-mailbox-watch.py` detects the turn.
4. The watcher calls OpenClaw main via `openclaw agent --session-id ... --message ...`.
5. Main reads the inbox, writes the outbox, and advances `turn.json` back to `needs_reply=codex`.

## Confirmed safety boundaries

- Optional install only; never enabled by default.
- Low-frequency watcher only; avoid high-frequency polling.
- Per-seq retry cooldown and max trigger attempts.
- Lock files prevent duplicate watcher invocations.
- No secret reading.
- No deletion of user files.
- No automatic Telegram/Notion/GitHub publishing from watchers.
- No model/quality/content prompt changes as part of installing agent collaboration.

## Confirmed current limitations

- This is v0, not a complete agent room/task-board system.
- External-agent wakeup depends on local automation provided through `CODEX_WAKE_COMMAND` or an equivalent scheduled task.
- The mailbox is a coordination layer; actual task execution still needs separate task contracts, artifacts, and approval gates.
- Watchers help recover missed turns, but they do not replace explicit task state or artifact review.

## Next likely iteration

- Generalize file names beyond `codex` naming for arbitrary agent pairs.
- Add a small status command that reports mailbox freshness and pending owner without waking either side.
- Add optional install/uninstall helpers that never touch secrets and never remove mailbox history without confirmation.
- Add examples for other external agents while keeping Codex as the first concrete example.
