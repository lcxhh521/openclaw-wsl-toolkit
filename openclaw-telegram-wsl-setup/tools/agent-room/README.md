# Agent Room Runtime Retention

Agent Room can generate many short-lived runtime snapshots while Codex, OpenClaw main, Claude Code, and other agents coordinate. These snapshots are useful for recent debugging, but they should not grow without bounds.

This tool prunes only low-value regenerated runtime directories:

- `daemon-runs/*`
- `resident-runs/*`
- `finished-runners/*`

It never deletes canonical state:

- `collaboration-ledgers/`
- `tasks/`
- `tasks.jsonl`
- `rooms/`
- `config/`
- `tools/`

Runner directories that contain `result.json` are kept until terminal evidence exists in the task manifest or canonical ledger text. This prevents deleting the only copy of a real agent result.

## Dry Run

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-AgentRoomRetentionPrune.ps1 -Distro Ubuntu -DryRun
```

Inside WSL:

```bash
python3 ~/.openclaw/workspace/codex-main-bridge/agent-room/tools/agent_room_retention_prune.py
```

## Install Hourly Timer

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-AgentRoomRetentionPrune.ps1 -Distro Ubuntu
```

The timer runs hourly with a small randomized delay. It writes its latest report to:

```text
~/.openclaw/workspace/codex-main-bridge/agent-room/maintenance/retention-prune/latest.json
```

This timer does not call Telegram, OpenClaw gateway RPC, model providers, Notion, GitHub, or any publish/notify path.
