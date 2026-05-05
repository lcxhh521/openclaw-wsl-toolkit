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

## Open The Browser Control

The local panel has an `打开 Control` button. Use it only when you need the original browser-based OpenClaw Control UI. The helper script `Start-OpenClaw.ps1` is kept as an internal launcher for that button, not as a separate desktop entry.

The button only opens browser Control when the gateway is already running. If OpenClaw is stopped, the panel asks the user to click `开启 OpenClaw` first instead of implicitly starting it.

After the gateway is running, the helper resolves the gateway token locally and opens the browser Control URL with a temporary `#token=...` fragment when OpenClaw exposes one. The token is not committed, printed to chat, or stored as a shortcut argument.

After opening the URL, the helper makes a best-effort attempt to restore and focus the browser window. This gives the user visible feedback whether the browser was minimized, hidden in the background, or not yet open.

## Main Panel Refresh

The panel updates its display automatically as a lightweight vital-sign view. The main screen is not a task explorer, token dashboard, or repair workflow.

Hover hints should stay within the app window rather than using native tooltips that can spill outside the interface.

Automatic refresh is intentionally lightweight: it checks gateway reachability and Telegram channel state, and may show coarse background activity from the gateway probe summary. It does not expand `tasks list`, `sessions.list`, `logs.tail`, `tasks audit`, `tasks show`, TaskFlow, token snapshots, or monthly cost scans. This keeps the desktop panel from competing with Telegram for the gateway event loop.

The old manual recheck path has been removed from the main panel. Use `诊断` for architecture-level read-only troubleshooting, or `打开 Control` for the browser control surface. Starting/stopping OpenClaw remains an explicit power action.

During OpenClaw cold startup, the panel uses a lightweight startup probe first: gateway probe plus Telegram channel status. It skips heavier task, audit, log, token, cost, session, and workspace-artifact reads until the startup progress reaches ready. This keeps the control center from adding pressure while OpenClaw is still bringing up channels and sidecars.

When restoring from the system tray or the Windows taskbar, the window should force layout and repaint before becoming visible. Avoid `WS_EX_COMPOSITED` full-window compositing for this panel because it can make child controls briefly appear as black or unpainted rectangles during startup or restore.

## Diagnostics v0

The `诊断` button opens a read-only diagnostics dialog for architecture-level troubleshooting. It must not start, stop, restart, patch, apply maintenance, clean sessions, write memory, or change models/bindings/secrets.

Diagnostics v0 shows five sections:

- Gateway: reachability, runtime/admin capability, PID/resource snapshot, stability notes.
- Gateway Resilience: current gateway PID/start time/CPU/RSS, a restart timeline from recent stability files, and `openclaw-tasks` residual process detection through `ps` only.
- Telegram: channel status, `telegram:default` binding, current Telegram session key, token threshold state.
- Sessions: 24h active sessions, main/telegram distribution, high-token sessions, legacy main Telegram session hints.
- Tasks & Logs: running/queued task pressure from `tasks list --json` only. Audit/log keyword scanning is disabled in v0 because `tasks audit/show` and `logs.tail` can be expensive under gateway pressure.

Each diagnostics source has its own timeout. If one section fails, the dialog should show that section as `读取失败/需观察` without affecting the main control center state. The dialog's copy button exports a redacted text report. Logs and command output must be redacted before entering the UI/report; API keys, bot tokens, OAuth material, gateway tokens/passwords, and bearer tokens must appear only as `[REDACTED]`.

Gateway Resilience is a safe observation path. It must not call `openclaw tasks audit`, `openclaw tasks show`, `maintenance --apply`, restart/stop/start gateway, cleanup sessions, cancel/delete tasks, patch config, or modify agent/model/binding/secrets/session state. Residual `openclaw-tasks` rows are displayed only; the monitor never kills them automatically. Stability files are treated as evidence: the monitor summarizes the latest restart/shutdown evidence in a `Restart timeline` row and keeps the recent evidence list visible. If a serious stability event belongs to a previous gateway PID and the current gateway process is running under a new PID, the event is treated as recovered-but-observable rather than an active failure. If task audit is needed, it should be a separate, explicitly confirmed maintenance action, not part of the control center refresh loop.

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
- Cost shown by the panel comes from local OpenClaw session usage records. Treat it as OpenClaw's recorded/estimated model cost, not as a replacement for provider billing pages.
- The panel assumes the WSL distro is named `Ubuntu` and that `openclaw` is available on the WSL user's login-shell `PATH`; adjust `OpenClawMonitor.cs` before building if the target machine differs.
