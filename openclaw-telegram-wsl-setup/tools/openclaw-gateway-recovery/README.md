# OpenClaw Gateway Recovery

`openclaw-gateway-recovery` is a separate local recovery lane for Windows/WSL OpenClaw installs.
It is meant to cover the common post-update failure mode where WSL wakes up but
`openclaw-gateway.service` is inactive, failed, or locally unreachable.

It is intentionally separate from `openclaw-netwatch`.
Netwatch remains an observer that records signals and recommendations.
Gateway Recovery is the optional action layer, with its own state, cooldowns, and logs.

## Safety Boundary

This tool may only act on `openclaw-gateway.service`.

It must not:

- change Telegram tokens or channel config;
- change model/provider/thinking/text/prompt/content settings;
- edit sessions, tasks, memory, Notion pages, market workflows, or People Daily workflows;
- run heavy agent sessions;
- publish or send Telegram messages.

## Modes

Configure mode in `~/.config/openclaw-gateway-recovery.env`:

```sh
OPENCLAW_GATEWAY_RECOVERY_MODE=observe
```

Supported modes:

- `observe`: record status only; no service changes.
- `recover-start`: if network is online and `openclaw-gateway.service` is inactive or failed, run `systemctl --user start openclaw-gateway.service`.
- `recover-restart`: in addition to `recover-start`, restart the service after confirmed local probe failure and cooldown.

For conservative production use, prefer `recover-start` first.
Only enable `recover-restart` after reviewing local gateway restart safety.

## Runtime Files

```text
~/.cache/openclaw-gateway-recovery/status.json
~/.cache/openclaw-gateway-recovery/state
~/.cache/openclaw-gateway-recovery/recovery.log
```

## Install

From Windows PowerShell:

```powershell
.\\Install-OpenClawGatewayRecovery.ps1 -Mode observe
.\\Install-OpenClawGatewayRecovery.ps1 -Mode recover-start -Apply
```

Default public/template mode should stay `observe`.
A personal local install may opt into `recover-start`.
