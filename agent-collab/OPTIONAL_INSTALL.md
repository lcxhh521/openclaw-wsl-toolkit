# Optional Install: Agent Collaboration v0

`agent-collab` is an optional module. Do not enable it by default for every OpenClaw user.

Install it only when a user explicitly wants an asynchronous multi-agent mailbox, for example:

- Telegram-facing OpenClaw `main` coordinating with Codex/Cursor/Claude Code/etc.
- A coding agent and a coordinator sharing durable task state.
- Two local agents handing work back and forth without forcing the user to paste every message.

## What gets installed

A minimal installation creates:

- a mailbox directory;
- two Markdown message files;
- one `turn.json` state file;
- a watcher for `needs_reply=main`;
- optionally, a watcher for `needs_reply=codex` or another external agent.

The watchers are intentionally small and conservative:

- lock file to prevent duplicate invocations;
- per-seq retry cooldown;
- max trigger attempts;
- no file deletion;
- no secret reading;
- no automatic external publish/notify actions.

## How the agents remind each other

### Main reminds Codex / external agent

1. OpenClaw main writes a reply to `main_to_codex.md`.
2. Main updates `turn.json`:
   - increments `seq`;
   - sets `last_writer=main`;
   - sets `needs_reply=codex`;
   - sets `updated_at` and a short `note`.
3. The Codex-side watcher sees `needs_reply=codex`.
4. The watcher runs the local `CODEX_WAKE_COMMAND`, passing paths via placeholders:
   - `{seq}`
   - `{inbox}`
   - `{outbox}`
   - `{turn}`
5. Codex reads `main_to_codex.md`, writes `codex_to_main.md`, and advances `turn.json` back to `needs_reply=main`.

### Codex / external agent reminds Main

1. Codex writes a reply to `codex_to_main.md`.
2. Codex updates `turn.json`:
   - increments `seq`;
   - sets `last_writer=codex`;
   - sets `needs_reply=main`;
   - sets `updated_at` and `note`.
3. The OpenClaw-side watcher sees `needs_reply=main`.
4. It calls:

```bash
openclaw agent --session-id "$OPENCLAW_MAIN_SESSION_ID" --message "...mailbox reminder..."
```

5. Main reads `codex_to_main.md`, writes `main_to_codex.md`, and advances `turn.json` back to `needs_reply=codex`.

The reliable signal is `turn.json` advancement, not merely “a watcher process started”.

## Minimal setup

### 1. Create a mailbox

```bash
mkdir -p ~/.openclaw/agent-mailboxes/codex-main
cp agent-collab/examples/turn.example.json ~/.openclaw/agent-mailboxes/codex-main/turn.json
touch ~/.openclaw/agent-mailboxes/codex-main/codex_to_main.md
touch ~/.openclaw/agent-mailboxes/codex-main/main_to_codex.md
```

Edit `turn.json` paths to match the mailbox.

### 2. Configure OpenClaw main-side watcher

Set local environment variables:

```bash
export OPENCLAW_AGENT_MAILBOX_DIR="$HOME/.openclaw/agent-mailboxes/codex-main"
export OPENCLAW_MAIN_SESSION_ID="<your-main-session-id>"
export OPENCLAW_BIN="$HOME/.local/bin/openclaw"
```

Run once manually:

```bash
python3 agent-collab/scripts/openclaw-main-mailbox-watch.py
```

For periodic use, install your own systemd user timer/cron entry. Keep the interval low-frequency, e.g. 1-5 minutes, not high-frequency polling.

### 3. Configure Codex/external-agent watcher

Copy and edit:

```bash
cp agent-collab/examples/codex-watcher-env.example ~/.openclaw/agent-mailboxes/codex-main/codex-watcher.env
```

Set `CODEX_WAKE_COMMAND` to whatever safely wakes your local Codex/Desktop/CLI automation.

Examples:

```bash
# Dry-run/log-only example
CODEX_WAKE_COMMAND='echo "Codex turn {seq} waiting. Read {inbox}; write {outbox}; update {turn}."'

# CLI-style example, if your external agent supports it
CODEX_WAKE_COMMAND='codex --message "Mailbox turn {seq}: read {inbox}, reply to {outbox}, update {turn}"'
```

On Windows, adapt `examples/install-codex-watcher-task.ps1` to run the watcher every 5 minutes.

## Uninstall

Because this is optional, uninstall should be local and explicit:

- disable/remove your systemd timer/cron entry;
- disable/remove the Windows Scheduled Task if used;
- archive or remove the mailbox directory only after confirming it contains no needed conversation/artifact history.

Do not run broad cleanup commands.
