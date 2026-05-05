# OpenClaw Netwatch

OpenClaw Netwatch is a small WSL user timer for network recovery. It exists because a long-running Telegram gateway can stay alive while network sockets, Telegram polling, provider connections, or local gateway HTTP become stale after network/proxy changes.

It keeps network observation in the background while surfacing the result through the local Control Center, instead of making the user repeatedly poke terminals.

## Mode

OpenClaw Netwatch is observe-only. It records network/gateway state and writes recovery recommendations to its log. It never restarts `openclaw-gateway.service`.

It records recovery recommendations for:

- confirmed offline -> online transition;
- network online, gateway startup grace elapsed, and local gateway HTTP probe failed repeatedly.

It does not:

- edit OpenClaw config;
- change model/provider/binding/secrets/session;
- run `openclaw tasks audit/show`;
- clean tasks or sessions;
- restart gateway.

## Install

Dry run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawNetwatch.ps1
```

Install:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawNetwatch.ps1 -Apply
```

## Inspect

Inside WSL:

```bash
systemctl --user status openclaw-netwatch.timer --no-pager
systemctl --user status openclaw-netwatch.service --no-pager
tail -n 40 ~/.cache/openclaw-netwatch/watchdog.log
cat ~/.config/openclaw-netwatch.env
```

## Uninstall

Dry run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Uninstall-OpenClawNetwatch.ps1
```

Remove:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Uninstall-OpenClawNetwatch.ps1 -Apply
```

## Notes

The script tries to reuse proxy environment variables from `openclaw-gateway.service` and always preserves local gateway bypasses:

```text
NO_PROXY=127.0.0.1,localhost,::1
no_proxy=127.0.0.1,localhost,::1
```

The Telegram reachability probe checks `https://api.telegram.org` and treats any HTTP response as network reachability. It does not require or store a bot token.
