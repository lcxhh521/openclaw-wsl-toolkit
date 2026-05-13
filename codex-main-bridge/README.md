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
