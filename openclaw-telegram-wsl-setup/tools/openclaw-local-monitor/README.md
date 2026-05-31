# OpenClaw 控制中心

Windows 本机小程序面板，用于观察并手动控制运行在 Ubuntu on WSL2 里的 OpenClaw gateway。

它是桌面上的唯一主入口。打开后只读取状态，不会自动启动或关闭 OpenClaw。需要运行时点击 `开启 OpenClaw`；运行中再次点击同一按钮会显示并执行 `关闭 OpenClaw`。

面板显示：

- gateway 和 Telegram 是否可用。
- Telegram 是否已连接。冷启动细节不要塞进 Telegram 卡片，而是在顶部状态框内部用临时启动进度条显示：gateway 检查、Telegram 启动、Telegram 连接、模型/sidecar 预热。进度到 100% 后自动隐藏。
- 后台是否存在 `queued/running` task、活跃 TaskFlow，或正在持续产出的本地 daemon / 工作区产物心跳。
- Token / 上下文使用快照，以及主会话、Telegram、子任务的流向。
- 从当月本地 session 日志里的 `usage.cost` 汇总已记录成本，并按模型列出成本和 token 去向；每个自然月刷新一次，这不是服务商账单替代品。
- 最近会话和 Telegram / error 日志提醒。
- 系统托盘常驻能力。

## Install From The Skill

Run this from the `tools/openclaw-local-monitor` directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawMonitor.ps1
```

The installer copies the monitor into:

```text
%LOCALAPPDATA%\OpenClawMonitor
```

Then it builds `OpenClawMonitor.exe`, creates a Startup-folder shortcut, and starts the panel.

It also creates desktop and Start Menu shortcuts for:

- `OpenClaw Control`: opens the local control center. It shows the local panel first; use `开启 OpenClaw` when you actually want to start OpenClaw.

The installer removes old separate `OpenClaw Monitor`, `OpenClaw 启动`, and other old `OpenClaw*.lnk` shortcuts in the same shortcut folders, because the control center is now the only main entry.

## WSL Keepalive

Use `Install-WslKeepalive.ps1` when Windows needs a hidden Startup-folder anchor to keep the Ubuntu WSL session alive after login.

The keepalive entry is intentionally non-disruptive:

- It uses `systemctl --user start openclaw-gateway.service`, not `restart`, so repeated launches do not interrupt an already-running gateway.
- It keeps one marker process alive as `openclaw-keepalive-anchor`.
- It checks for an existing anchor before creating another one.
- It does not edit OpenClaw config, tokens, provider keys, channel settings, or gateway drop-ins.

## Native Browser Control

The local panel has a `原生 Control` button. Use it only when you need the original browser-based OpenClaw Control UI. The helper script `Start-OpenClaw.ps1` is kept as an internal launcher for that button, not as a separate desktop entry.

The button only opens browser Control when the gateway is already running. If OpenClaw is stopped, the panel asks the user to click `开启 OpenClaw` first instead of implicitly starting it.

After the gateway is running, the helper resolves the gateway token locally and opens the browser Control URL with a temporary `#token=...` fragment when OpenClaw exposes one. The token is not committed, printed to chat, or stored as a shortcut argument.

After opening the URL, the helper makes a best-effort attempt to restore and focus the browser window. This gives the user visible feedback whether the browser was minimized, hidden in the background, or not yet open.

Browser Control can trigger heavier session/model queries than the local panel. The button should warn before opening, and browser Control should not be kept open as the daily status monitor.

## Main Panel Refresh

The panel updates its display automatically as a lightweight vital-sign view. The main screen is not a task explorer, token dashboard, or repair workflow.

Hover hints should stay within the app window rather than using native tooltips that can spill outside the interface.

The main panel intentionally does not run periodic gateway RPC refresh. It refreshes when the window opens and after explicit power actions; deeper troubleshooting belongs behind the `诊断` button. This avoids a background window, minimized taskbar entry, or tray process competing with Telegram for the gateway event loop. The main panel does not expand `tasks list`, `sessions.list`, `logs.tail`, `tasks audit`, `tasks show`, TaskFlow, token snapshots, or monthly cost scans.

The old manual recheck path has been removed from the main panel. Use `诊断` for architecture-level read-only troubleshooting, or `原生 Control` for the browser control surface. Starting/stopping OpenClaw remains an explicit power action.

During OpenClaw cold startup, the panel uses a lightweight startup probe first: gateway probe plus Telegram channel status. It skips heavier task, audit, log, token, cost, session, and workspace-artifact reads until the startup progress reaches ready. This keeps the control center from adding pressure while OpenClaw is still bringing up channels and sidecars.

Startup and control actions must not depend on a login-shell `PATH`. The monitor and `Start-OpenClaw.ps1` should prefer `/home/lcxhh/.local/bin/openclaw`, falling back to `command -v openclaw` only when that absolute path is unavailable. This prevents non-login WSL launch contexts from reporting `openclaw: command not found` while the gateway is otherwise healthy.

Telegram status is intentionally split: gateway reachable, Telegram configured/running/connected, inbound seen, outbound/reply completed, and entrance pressure/event-loop degraded are separate signals. Do not collapse them into a single “Telegram 正常” label; “connected” only proves transport state, not that Alex has received a fresh reply.

When restoring from the system tray or the Windows taskbar, the window should force layout and repaint before becoming visible. Avoid `WS_EX_COMPOSITED` full-window compositing for this panel because it can make child controls briefly appear as black or unpainted rectangles during startup or restore.

## Diagnostics v0

The `诊断` button opens a read-only diagnostics dialog for architecture-level troubleshooting. It must not start, stop, restart, patch, apply maintenance, clean sessions, write memory, or change models/bindings/secrets.

Diagnostics v0 shows seven sections:

- Gateway: reachability, runtime/admin capability, PID/resource snapshot, stability notes.
- Gateway Resilience: current gateway PID/start time/CPU/RSS, a restart timeline from recent stability files, and `openclaw-tasks` residual process detection through `ps` only.
- Network Stability: OpenClaw Network Observer / Netwatch install/timer/mode/state/log visibility. The monitor displays the observer state from inside Control Center, but Netwatch itself runs as a WSL user timer so auto-refresh does not compete with Telegram or the gateway event loop.
- Entrance Pressure: recent gateway-journal signals that explain why Telegram can be online but slow, including event-loop warnings, `memory-core`/dreaming timeouts, session locks, cleanup timeouts, provider fetch failures, and recent Telegram delivery success/failure. This uses `journalctl` keyword filtering only; it does not call OpenClaw `logs.tail`.
- Telegram: channel status, `telegram:default` binding, current Telegram session key, token threshold state.
- Sessions: 24h active sessions, main/telegram distribution, high-token sessions, legacy main Telegram session hints.
- Tasks & Logs: running/queued task pressure from `tasks list --json` only. Audit/log keyword scanning is disabled in v0 because `tasks audit/show` and `logs.tail` can be expensive under gateway pressure.

Each diagnostics source has its own timeout. If one section fails, the dialog should show that section as `读取失败/需观察` without affecting the main control center state. The dialog's copy button exports a redacted text report. Logs and command output must be redacted before entering the UI/report; API keys, bot tokens, OAuth material, gateway tokens/passwords, and bearer tokens must appear only as `[REDACTED]`.

Gateway Resilience is a safe observation path. It must not call `openclaw tasks audit`, `openclaw tasks show`, `maintenance --apply`, restart/stop/start gateway, cleanup sessions, cancel/delete tasks, patch config, or modify agent/model/binding/secrets/session state. Residual `openclaw-tasks` rows are displayed only; the monitor never kills them automatically. Stability files are treated as evidence: the monitor summarizes the latest restart/shutdown evidence in a `Restart timeline` row and keeps the recent evidence list visible. If a serious stability event belongs to a previous gateway PID and the current gateway process is running under a new PID, the event is treated as recovered-but-observable rather than an active failure. If task audit is needed, it should be a separate, explicitly confirmed maintenance action, not part of the control center refresh loop.

Network Stability belongs in Control Center as visibility and explicit user control. The `openclaw-netwatch` script/timer is only the execution layer for OpenClaw Network Observer / Netwatch. Diagnostics reads whether it is installed, active, in observe-only mode, and what it last recorded. The main auto-refresh path does not run network recovery logic, and diagnostics does not install, enable, disable, or restart Netwatch. Netwatch never restarts gateway automatically; it only records recovery recommendations.

## Clash Safe Mode

The panel includes a `Clash 安全模式` option for Clash Verge Rev users who need TUN/global-style routing for OpenClaw, Codex, or other foreign large-model providers while keeping WeChat and domestic links usable.

Use it when turning on Clash Verge global mode or TUN makes domestic apps, Tencent/WeChat traffic, or China-region websites stop working. When enabled, the monitor talks to the local Mihomo named pipe exposed by Clash Verge Rev and keeps the core in rule mode if it is switched to global mode. This lets OpenClaw/Codex follow the selected `GLOBAL` proxy group while WeChat and domestic traffic can continue to use direct/rule routing. Switching proxy nodes should happen inside the `GLOBAL` group in Clash Verge Rev; the monitor does not pin a specific country or node.

If Clash Verge is already in rule mode and domestic apps work normally, or if the user is not using TUN/global-style routing for foreign model providers, this option is usually unnecessary. Leaving it off will not affect OpenClaw's normal gateway, Telegram, or local monitor behavior.

This option does not store proxy subscriptions, tokens, auth profiles, provider keys, or raw Clash config in the repository.

## Build Manually

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Build-OpenClawMonitor.ps1
```

The build uses the built-in .NET Framework C# compiler:

```text
%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
```

No external package manager is required.

## Regenerate Icon

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Generate-OpenClawMonitorIcon.ps1
```

The icon is a transparent-background, friendly red OpenClaw-style mascot for desktop, taskbar, and tray use. Do not use a screenshot with a dark background as the icon.

## Uninstall Autostart

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Uninstall-Autostart.ps1
```

This removes only the Startup shortcut. It does not delete the monitor folder.

## Notes

- Do not store OpenClaw tokens, API keys, auth profiles, or logs in this folder.
- Token/cost shown by the panel comes from the offline usage cache at `~/.openclaw/monitor-cache/usage-summary.json`. Treat it as a local estimate, not as a replacement for provider billing pages.
- The panel assumes the WSL distro is named `Ubuntu` and that `openclaw` is available on the WSL user's login-shell `PATH`; adjust `OpenClawMonitor.cs` before building if the target machine differs.
- The `原生 Control` button is an advanced browser entry. It warns before opening because browser Control can trigger heavier session/model queries than the local panel. Do not keep browser Control open as the daily status monitor.

## Usage Cache

Token and cost cards on the main panel are cache-only. The panel reads:

```text
~/.openclaw/monitor-cache/usage-summary.json
```

It never calls `sessions.list`, `models.list`, `logs.tail`, `tasks audit`, or a monthly session scan to render those cards. If the cache is missing, the cards stay hidden or show stale/cache status; the panel does not query gateway to fill the gap. Token cards show today's cached flow, while the cost card shows the current natural month's accumulated local estimate and resets naturally at the start of a new month.

Install the optional WSL timer from this directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-UsageCache.ps1
```

The timer installs `~/.local/bin/openclaw-usage-cache` and runs it every 10 minutes. The collector scans local session files offline and writes the small JSON cache. It does not connect to the gateway, does not restart OpenClaw, does not change config, and does not touch secrets.

## Reliability Observer

The main panel can also read a local reliability cache:

```text
~/.openclaw/monitor-cache/reliability-status.json
```

Install the optional WSL timer from this directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-ReliabilityObserver.ps1
```

The observer runs about once per minute and reads only local gateway logs, the user journal for `openclaw-gateway.service`, and recent stability files. It detects recent signals such as model overload, Telegram `sendMessage` delivery failure, provider/network timeout, session lock, context overflow, and gateway shutdown/startup incidents.

It is intentionally observational: it does not send Telegram messages, retry commands, restart OpenClaw, call gateway RPC, call `tasks audit/show`, change config, or touch secrets. The control center uses the cache to explain why a Telegram command may have gone silent without adding new gateway load.
