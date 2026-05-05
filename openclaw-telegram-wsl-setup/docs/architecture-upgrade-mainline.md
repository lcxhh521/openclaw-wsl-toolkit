# OpenClaw Architecture Upgrade Mainline

This document tracks the local architecture work for making OpenClaw usable as a stable Telegram-driven operating console on Windows/WSL.

## Goal

Telegram should remain a light command surface. Long work can happen in the background, but the user must see clear receive, progress, failure, interruption, and completion signals.

The local control center should be a vital-sign dashboard and read-only diagnostics surface. It should not become an automatic repair loop or a competing workload on the gateway.

## Current State

- Telegram has been isolated to a dedicated `telegram` agent.
- The main model remains `openai-codex/gpt-5.5`.
- Control Center main panel has been simplified into a lightweight vital-sign view.
- Control Center automatic refresh no longer expands `tasks list`, `sessions.list`, `logs.tail`, `tasks audit/show`, TaskFlow, token snapshots, workspace scans, or cost scans.
- Control Center diagnostics keeps Gateway Resilience visibility through current gateway process data, recent stability files, and `openclaw-tasks` residual detection.
- Telegram Reliability v1 is being handled as an OpenClaw source-level patch, not by editing the installed `dist/` bundle.

## Workstreams

### P0: Control Center Observability

Status: landed locally.

The main panel should answer only:

- Is gateway alive?
- Is Telegram connected?
- Is there coarse background activity?
- Is there an obvious warning that needs the diagnostics dialog?

Details belong in the Diagnostics dialog or the browser Control UI, not in the always-refreshing desktop panel.

### P0: Telegram Reliability v1

Status: upstream draft PR under review.

Scope:

- Inflight Telegram message tracking.
- Explicit failure and interruption notification.
- Runtime restart interrupted-message scan.
- High-context long-input guard.
- No automatic retry of interrupted commands.

Boundaries:

- Do not restart gateway.
- Do not clean tasks.
- Do not modify model, binding, config, secrets, or sessions.
- Do not run `tasks audit/show` in the Telegram hot path.

### P0: Gateway Resilience

Status: observation landed locally; source-level resilience still pending.

Local Control Center can show:

- Current gateway PID, uptime, start time, CPU, and RSS.
- Restart timeline derived from recent stability files.
- Whether a serious stability event belongs to a previous PID while a new gateway PID is running.
- Residual `openclaw-tasks` processes by `ps` only.

OpenClaw source-level work should add:

- Shutdown phase timing.
- Slow channel/runtime dispose timing.
- Gateway restart/interruption lifecycle markers.
- Post-restart interrupted delivery detection.
- Clear non-model failure messages for timeout, context overflow, and session lock.

### P0: Network Stability / Recovery

Status: local toolkit module added; Control Center diagnostics visibility added; not installed automatically.

For the Windows/WSL toolkit, network stability is split in two: the Control Center is the user-visible control surface, while a small WSL user timer is the observation layer that records network/proxy/gateway state and recovery recommendations.

Local module:

- `tools/openclaw-netwatch/openclaw-netwatch`
- `tools/openclaw-netwatch/openclaw-netwatch.service`
- `tools/openclaw-netwatch/openclaw-netwatch.timer`
- `tools/openclaw-netwatch/Install-OpenClawNetwatch.ps1`
- `tools/openclaw-netwatch/Uninstall-OpenClawNetwatch.ps1`

Boundaries:

- Default installer mode is dry-run, and applied installs are observe-only.
- Netwatch records recovery recommendations; it does not restart gateway automatically.
- Control Center diagnostics shows install/timer/mode/state/log status, but does not install, enable, disable, or restart netwatch by itself.
- It does not edit OpenClaw config, model, binding, secrets, sessions, or tasks.
- It does not run `tasks audit/show`.
- It records confirmed offline-to-online recovery or repeated local gateway HTTP failure while network is online, with startup grace and cooldown.

### P1: Session Lifecycle / Handoff

Status: design settled; source behavior belongs in OpenClaw.

Rules:

- `/compact` is same-session compression.
- `/new` is a clean session.
- `/handoff` is user-requested semantic transfer, not a diagnostics flow.
- `/resume latest` explicitly loads the latest handoff.
- Handoff must not require gateway diagnostics, token thresholds, task audit, or control center snapshots.

### P1: Windows/WSL Stability

Status: partially handled by the local toolkit.

The Windows/WSL layer should focus on:

- systemd user gateway availability.
- startup/autostart clarity.
- Clash safe mode visibility.
- lightweight local process detection.
- avoiding Windows UI actions that accidentally restart or overload gateway.

### P2: Codex <-> OpenClaw MCP Coordination

Status: proven feasible, not productized.

Codex can connect to OpenClaw through `openclaw mcp serve` using a local MCP client. This is useful for coordinated architecture work, but should remain a controlled engineering path until authentication, failure behavior, and permissions are more explicit.

## Operating Rules

- No hidden repair loops.
- No automatic gateway restart from diagnostics.
- No automatic task cleanup from diagnostics.
- No `tasks audit/show` in hot paths.
- No large logs, reports, or diffs pushed into Telegram sessions.
- No secret, token, OAuth, or API key material in reports.
- Evidence should stay visible; conclusions should be based on current evidence, not user-maintained acknowledgement buttons.

## Next Steps

1. Continue reviewing the Telegram Reliability v1 upstream PR.
2. Validate OpenClaw Netwatch observe-only signals in the Control Center diagnostics report.
3. If OpenClaw remains unstable, prioritize source-level Gateway Resilience instrumentation over more UI changes.
4. Keep the local control center focused on vital signs and read-only evidence.
5. Revisit MCP coordination only after Telegram reliability and gateway restart behavior are less fragile.
