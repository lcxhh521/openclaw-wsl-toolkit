---
name: openclaw-telegram-wsl-setup
description: "A safe, transparent, simple OpenClaw guide and WSL toolkit for Windows. Use when the user wants to install, run, repair, monitor, or understand OpenClaw on Windows through WSL2; needs gateway readiness checks, keepalive/autostart, local OpenClaw Monitor panel installation, optional market information immersion module setup, IMA OpenAPI skill setup for Tencent ima knowledge bases, long-offline network recovery, stale socket or polling recovery after internet loss, Telegram bot setup/repair, safe token entry, proxy-aware connectivity, pairing approval, channel startup verification, or diagnosis of messages that are not received, not answered, delayed, or only work while WSL is awake."
---

# OpenClaw 养虾指南（WSL Toolkit）

Use this skill as a safe, transparent, simple guide for running OpenClaw on Windows through WSL2. Telegram is one supported remote-control channel, but the broader goal is to keep OpenClaw understandable and observable: install it carefully, verify the gateway and model, preserve user-controlled permissions, keep it alive after Windows login, recover after long offline periods, and expose local monitoring without hiding secrets or state from the user.

## Opening

For a new install, choose the installation language before explaining architecture. The first response after this skill loads should:

1. Ask the user to choose the installation language.
2. Continue entirely in the selected language.
3. Explain the difference between Windows-native installation and WSL2 installation.
4. Recommend Ubuntu on WSL2 as the default path.
5. Confirm the plan before starting installation commands.

Use this first prompt when the language is not already explicit:

```text
Please choose the installation language:

1. 中文
2. English
```

If the user is already clearly using Chinese or English, treat that as the language choice and do not ask again. If this is a continuation, keep the current conversation language.

After the language is known, explain the architecture in that language before running commands. Chinese example:

```text
开始之前，Windows 上运行 OpenClaw 通常有两种方式：

1. Windows 原生安装：表面上少装一个 Ubuntu，但 Telegram 机器人需要长期在线的 gateway。后台常驻、权限、路径、本地网络和代理行为在 Windows 原生环境里更容易变得不可预测。
2. Ubuntu on WSL2：前期多一步 WSL2/Ubuntu 设置，但 OpenClaw 可以运行在更接近 Linux 的环境里，systemd 用户服务、权限、路径和 gateway 行为更稳定。

为了让 Telegram 机器人可靠在线，我推荐使用 Ubuntu on WSL2。安装流程会默认使用 Ubuntu，然后配置 OpenClaw、gateway 服务、后台常驻运行和 Telegram。

我会按推荐路径安装：Windows + WSL2 + Ubuntu + Ubuntu 内的 OpenClaw + systemd user gateway + 后台常驻运行 + Telegram。只有在 Windows/Ubuntu 初始化、密码、BotFather、token 或 pairing 必须你操作时，我才会暂停让你处理。

现在开始吗？
```

English example:

```text
Before we start, there are two common ways to run OpenClaw on Windows:

1. Windows-native install: it looks simpler because there is no Ubuntu setup, but a Telegram bot needs a long-running gateway. Background services, permissions, paths, local networking, and proxy behavior are less predictable in a native Windows setup.
2. Ubuntu on WSL2: it takes one extra setup step, but OpenClaw runs in a Linux-like environment where systemd user services, permissions, paths, and gateway behavior are more stable.

For a reliable Telegram bot, I recommend Ubuntu on WSL2. The install will use Ubuntu by default, then configure OpenClaw, the gateway service, background persistence, and Telegram.

I will install using the recommended path: Windows + WSL2 + Ubuntu + OpenClaw inside Ubuntu + systemd user gateway + background persistence + Telegram. I will only pause when Windows/Ubuntu setup, passwords, BotFather, tokens, or pairing require your input.

Shall I start?
```

If the user confirms, begin the OpenClaw installation process. If WSL2/OpenClaw are already installed, skip the full installation pitch after this opening and continue with verification/repair. If the user explicitly rejects WSL2/Ubuntu, explain in the selected language that Windows-native setup is outside the recommended no-surprises path and ask whether to continue anyway.

## Distro Policy

For greenfield setup, standardize on **Ubuntu under WSL2**. This is the no-brainer path. Do not offer distro selection during normal installation. Use a different distro only when the user explicitly asks or an existing working OpenClaw install already lives there.

Run these baseline Windows checks first:

```powershell
wsl --status
wsl --version
wsl --list --verbose
$startup = [Environment]::GetFolderPath('Startup')
Get-ChildItem -LiteralPath $startup -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'OpenClaw|WSL|Keepalive' }
```

Interpret them this way:

- `wsl --status`: confirm WSL is available and whether WSL2 is the default.
- `wsl --version`: confirm the modern WSL package is installed. If unavailable, continue only if `wsl --status` and `wsl --list --verbose` still provide enough state.
- `wsl --list --verbose`: confirm whether `Ubuntu` exists, whether it is `VERSION 2`, and whether another distro already owns OpenClaw.

Use this decision tree:

1. New install:
   - Use `Ubuntu` on WSL2.
   - If WSL is missing, install Ubuntu with `wsl --install -d Ubuntu`.
   - If WSL exists but Ubuntu is missing, install Ubuntu with `wsl --install -d Ubuntu`.
   - If Ubuntu exists but is `VERSION 1`, convert it with `wsl --set-version Ubuntu 2` before installing OpenClaw.
   - If Windows reports that a reboot is required, stop the install, ask the user to reboot, and resume after they return. Do not continue with OpenClaw commands until Ubuntu launches successfully.
   - After Ubuntu is installed, have the user complete the first Ubuntu launch and Linux username/password creation if prompted.

2. Existing OpenClaw install:
   - Use the distro where `command -v openclaw` succeeds.
   - Prefer the distro where `openclaw-gateway.service` already exists.
   - Do not migrate a working non-Ubuntu install to Ubuntu unless the user asks.

3. Multiple distros:
   - If one distro has `openclaw-gateway.service`, use it.
   - If one distro has `command -v openclaw`, use it.
   - If no distro owns OpenClaw yet, use `Ubuntu` for the new setup.
   - Ask only when the user explicitly wants to use an existing non-Ubuntu distro or when two existing distros appear to own conflicting OpenClaw installs.

4. Non-Ubuntu distro:
   - Support it as an advanced existing-install path.
   - Do not make it the default tutorial path.
   - Replace `Ubuntu` in keepalive/autostart commands only after confirming that distro is the intended OpenClaw owner.

5. Windows-native OpenClaw:
   - Do not use it for gateway/Telegram service work unless the user explicitly rejects WSL2.
   - If the user rejects WSL2, explain that this is outside the recommended no-surprises path and that service, permission, path, proxy, and keepalive behavior may require more manual repair.

Useful commands for the default Ubuntu path:

```powershell
wsl --install -d Ubuntu
wsl --set-version Ubuntu 2
wsl -d Ubuntu -- bash -lc "id -un && pwd"
```

## Greenfield Install Flow

For a user who wants OpenClaw + Telegram installed from scratch on Windows, follow this default sequence without asking them to choose architecture. This is an assisted installer flow: keep the user oriented, keep the desktop quiet, close successful or stale windows promptly, and give detailed instructions for every action only the user can perform.

### Window And Prompt Discipline

Windows + WSL installation can open multiple PowerShell, Ubuntu, installer, error, token, or helper windows. Manage the lifecycle of every window explicitly:

- Before asking the user to type anything, identify the exact window they should use by title, visible prompt, or command text.
- Keep windows open only while they are actively needed for a password, Linux username, token entry, BotFather action, pairing code, visible installer progress, or an intentional long-running keepalive.
- Close or tell the user to close successful installer/helper windows after their result has been verified and no further user input is needed there.
- Close or tell the user to close stale error/helper windows only after their useful diagnostic state has been captured or is no longer needed.
- Prefer hidden/minimized background processes for keepalive. A keepalive may continue running, but it should not leave an unnecessary interactive window in the user's way when a hidden/minimized option is available.
- Do not make the user compare multiple terminal windows. Use commands to identify the active WSL distro, gateway service, and process state whenever possible.
- If a command opens a new terminal unexpectedly, explain whether it is expected, whether it should stay open, and what text/prompt the user should look for.
- If a command fails in one terminal and a better path exists, name the failed window as stale and move the user to the active path.
- Never ask the user to paste bot tokens or API keys into chat. Token entry belongs in a local terminal prompt or provider UI.

Window state rule:

- **Needs user action**: keep the relevant window visible and describe exactly what to do.
- **Running in background**: hide or minimize when possible, and tell the user it is expected to stay running.
- **Succeeded**: verify the result from commands, then close or tell the user to close the window.
- **Failed/stale**: capture the useful error, then close or tell the user to close the window and continue on the active path.

For each user-required action, provide:

- **Where**: the exact window or app to use, such as "Ubuntu window", "BotFather chat", or "the terminal asking `Telegram bot token:`".
- **What to do**: the concrete input or click sequence.
- **What success looks like**: the prompt, message, or status that means the action completed.
- **What to close**: which successful or stale windows can be closed after the action succeeds.
- **What not to do**: especially do not close active setup windows, do not paste tokens into chat, and do not restart randomly during gateway startup.

### Command Discovery Discipline

Codex may be able to infer and run the right install commands from the current environment, but the skill should not rely on memory alone. For Node/npm, OpenClaw install, and gateway service setup:

- First inspect the current state with narrow commands.
- Prefer the installed OpenClaw CLI help, `openclaw doctor`, and `openclaw doctor --fix` when available.
- If OpenClaw is not installed, use the current recommended package path for the environment; ask before networked installs or updates.
- Do not hard-code stale commands when the local CLI can reveal the current command shape.
- After each install/repair command, verify with an observable success check before moving on.

### Default Phase Order

1. Confirm WSL support and install Ubuntu on WSL2 if missing.
   - User action may be required for Windows reboot, Microsoft Store/WSL prompts, or first Ubuntu launch.
   - Tell the user exactly which Ubuntu window to keep open for Linux user creation.
   - After Ubuntu setup succeeds and `wsl -d Ubuntu -- bash -lc "id -un && pwd"` works, close or tell the user to close any extra Ubuntu setup windows that are no longer needed.

2. Open or enter Ubuntu and confirm the Linux user is ready.
   - Verify with `wsl -d Ubuntu -- bash -lc "id -un && pwd"`.
   - If Ubuntu asks for a Linux username/password, guide the user through it and wait.

3. Install Node/npm prerequisites if OpenClaw requires them and they are missing.
   - First discover current state with `node --version`, `npm --version`, and package-manager checks.
   - If installing dependencies opens prompts or asks for a password, tell the user exactly what prompt is expected.
   - Close successful dependency installer windows after `node --version` and `npm --version` verify the install.

4. Install OpenClaw inside Ubuntu.
   - Do not use Windows-native OpenClaw commands for gateway/Telegram service work.
   - Discover the current install path from official/current CLI or package manager state instead of relying only on memory.
   - Verify with `command -v openclaw` and `openclaw --version`.
   - Close successful installer/helper windows after the binary and version are verified.

5. Run `openclaw doctor` and fix required issues.
   - Treat required gateway/service issues as blockers.
   - Treat optional startup optimization or informational warnings as non-blocking unless they explain the current failure.
   - Close successful doctor/fix helper windows after required issues are verified as fixed.

6. Enable or repair `openclaw-gateway.service` as a WSL `systemd --user` service.
   - Prefer OpenClaw-provided repair/install commands discovered from `openclaw doctor`, `openclaw doctor --fix`, or relevant CLI help.
   - Verify service state before continuing.
   - If service repair opens helper windows, keep only the active helper path and close successful or stale windows after capturing/verifying results.

7. Configure or verify OpenClaw visibility/permission scope with explicit user confirmation.
   - Treat this as an explicit install phase: OpenClaw should only see the files, folders, tools, and execution surface the user intends.
   - Show the proposed visibility/permission scope in plain language and ask the user to confirm before continuing.
   - Let the user decide scope in natural language; translate their intent into the supported OpenClaw configuration path.
   - Use current OpenClaw CLI help, Control UI, or doctor output to discover the supported configuration path; do not invent stale permission commands.
   - Ask the user before broadening filesystem visibility, execution policy, tool access, or external integrations.
   - Verify the selected scope without printing secrets or raw config.

8. Choose, configure, and verify the model before Telegram setup.
   - Treat model readiness as a required checkpoint: Telegram is only the message entry point; the model is what actually produces replies.
   - Ask the user to choose the model/provider in natural language when no working default model is present.
   - Use current OpenClaw CLI help, Control UI, or doctor output to discover the supported model configuration path; do not invent stale model commands.
   - Never ask the user to paste model API keys, auth profiles, or provider secrets into chat.
   - Verify one local OpenClaw/model response before moving on to Telegram.

9. Add or verify keepalive/autostart quietly so Ubuntu stays awake and the gateway starts after Windows login.
   - Treat this as internal infrastructure work, not a separate user-facing module.
   - Mention it to the user only as "I will keep OpenClaw running in the background after login" unless a permission prompt, visible window, or explicit confirmation is needed.
   - Prefer hidden keepalive startup. If an older Startup-folder `.cmd` exists, replace it with the hidden `.vbs` keepalive so no WSL terminal remains visible.
   - Close successful setup windows after verifying the keepalive file/task exists and the gateway can be reached.

10. Verify `openclaw gateway probe`.
   - Do not continue to Telegram until gateway responds, not merely listens.

11. Install or verify the local OpenClaw Control Center on Windows when this skill bundle includes `tools/openclaw-local-monitor`.
   - Treat the control center as optional but recommended infrastructure after gateway/model/keepalive are healthy.
   - It is the single user-facing desktop entry: opening it should show local status without automatically starting or stopping OpenClaw. The user starts or stops OpenClaw explicitly with the `开启 OpenClaw` / `关闭 OpenClaw` button. Do not keep a separate `OpenClaw 启动` shortcut.
   - The panel must not store tokens, auth profiles, logs, or raw OpenClaw config. Its `原生 Control` helper may resolve the gateway token locally and pass it only as a transient browser URL fragment so the user does not have to paste the gateway token manually. Treat browser Control as an advanced entry that can trigger heavier session/model queries; warn before opening it and do not keep it open as the daily status monitor.
   - Build it locally on Windows from source; do not download or run third-party binaries.
   - Install it to a user-local path such as `%LOCALAPPDATA%\OpenClawMonitor`, create a Startup-folder shortcut named `OpenClaw Control.lnk`, remove old `OpenClaw Monitor` / `OpenClaw 启动` shortcuts, and start it as a tray-capable app.
   - Explain its backend-work meaning: the "后台任务" count should come from `queued/running` tasks, active/blocked/cancel-requested TaskFlow pressure, and clearly labeled local daemon/workspace artifact heartbeat. Recent Telegram/session activity is only context and must not be presented as a running background task by itself.
   - Token/cost cards should use the optional offline usage cache at `~/.openclaw/monitor-cache/usage-summary.json`. Install `Install-UsageCache.ps1` when the user wants these cards. The collector scans local session files on a WSL timer, about every 10 minutes; it must not connect to gateway, restart OpenClaw, change config, or touch secrets.
   - Verify the control center opens without auto-starting OpenClaw, can explicitly start and stop OpenClaw with the power button, can open `原生 Control` with confirmation and without manual gateway-token entry after the gateway is running, can show cache-backed token/cost cards when the cache timer is installed, and can be minimized/closed to the system tray.

12. If the user wants an automated market information immersion daily report, offer the optional `modules/openclaw-market-immersion` job module.
   - Treat it as an opt-in OpenClaw job module, not base OpenClaw infrastructure and not a skill that should be installed by default.
   - Make the user choose whether to install the module, whether to enable Notion publishing, whether to enable Telegram notifications, and whether to enable systemd timers.
   - Never ask the user to paste MX, Notion, Telegram, model, or provider keys into chat. Use local terminal prompts or provider UI only.
   - Explain that scheduling is handled by WSL `systemd --user` timers calling the module; the module then calls OpenClaw for整理. It is not OpenClaw's built-in scheduler.

13. If the user wants optional API enhancements, offer Jina embeddings and Tavily web search from `tools/openclaw-optional-apis`.
   - Treat both as opt-in enhancements, not required OpenClaw infrastructure.
   - Jina is for `memorySearch` embeddings and semantic memory recall. It is not internet search.
   - Tavily is for OpenClaw `web_search` / periodic internet absorption. It is not memory embedding.
   - If the user declines either API, skip it cleanly and continue setup.
   - Store keys only through local terminal prompts and `~/.openclaw/secrets/*.env`; never ask the user to paste keys into chat.
   - Configure OpenClaw API keys as real env SecretRef objects using `openclaw config set ... --ref-provider default --ref-source env --ref-id ...`. Do not leave `env:JINA_API_KEY` as a raw string; OpenClaw may send that literal string as the API key and get false 401 errors.
   - Restart the gateway only after the user agrees, because it can briefly interrupt Telegram and channel startup.

14. If the user wants local audio recognition through Doubao/Volcengine, install or verify the helper in `tools/openclaw-doubao-asr`.
   - Treat this as a separate ASR adapter, not as model routing.
   - Doubao text models can analyze transcripts; Ark chat models should not be described as native local-audio listeners unless the current provider docs prove that exact audio path works.
   - The Volcengine recording-file ASR path needs a speech resource in addition to the general API key. Flash mode normally uses `volc.bigasr.auc_turbo`; standard mode normally uses `volc.seedasr.auc`.
   - Store keys only through a local terminal prompt or existing `~/.openclaw/secrets/volcengine.env`; never ask the user to paste keys into chat.
   - The helper may run `openclaw-doubao-asr --self-check` without uploading audio.
   - Before transcribing a real local audio file, confirm with the user that the selected audio will be uploaded to Volcengine.

15. Configure Telegram using a local token prompt or token file.
   - Guide the user through BotFather in Telegram if they do not already have a bot.
   - Token entry happens only in the local terminal prompt, never in chat.
   - Close token-entry windows after `openclaw channels status --json --timeout 30000` shows the token/config is available, unless the window is also the active gateway/keepalive path.

16. Restart gateway once, wait for channel startup, approve pairing if needed, and verify a fresh Telegram message receives a reply.
   - Explain the 60-120 second startup window.
   - Ask for a fresh message only after Telegram is ready or the startup grace period has passed.
   - Close successful setup windows after end-to-end Telegram reply verification, leaving only intentional hidden/minimized keepalive infrastructure.

Only diverge from this path if the user already has a working OpenClaw install elsewhere, explicitly refuses WSL2/Ubuntu, or the environment blocks Ubuntu installation.

## Installation Baseline Discovery

Before assuming Telegram is misconfigured or reinstalling OpenClaw, classify the current machine. This keeps the greenfield install path recoverable when the user has already tried an install, rebooted halfway through, opened multiple windows, or has an older OpenClaw setup.

A healthy no-brainer Windows + WSL OpenClaw setup usually has this structure:

- Windows runs Ubuntu through WSL2 for new installs.
- OpenClaw gateway operations run inside Ubuntu or the existing chosen WSL distro, not from a Windows-native shell.
- The working binary lives under the WSL user's package-manager prefix, commonly `~/.local`, `~/.npm-global`, `/usr/local`, or another npm/pnpm-managed prefix.
- Gateway runs as a WSL `systemd --user` service named `openclaw-gateway.service`.
- The gateway listens on loopback port `18789` unless configured otherwise and responds to `openclaw gateway probe`.
- Keepalive/autostart exists so Ubuntu or the chosen distro stays awake and the gateway starts after login/reboot.
- Telegram uses OpenClaw's `telegram` channel account, usually `default`, in polling mode.
- A gateway proxy drop-in may exist at `~/.config/systemd/user/openclaw-gateway.service.d/*.conf`, commonly pointing to a WSL-local or Windows-forwarded proxy. Preserve `NO_PROXY=127.0.0.1,localhost,::1`.

Classify into exactly one next action state:

1. **Fresh Windows / WSL missing**: install Ubuntu on WSL2 using the Distro Policy path.
2. **Ubuntu missing or not WSL2**: install Ubuntu or convert Ubuntu to WSL2 before OpenClaw work.
3. **Ubuntu ready, OpenClaw missing**: continue to Node/npm and OpenClaw install inside Ubuntu.
4. **OpenClaw installed, gateway service missing**: repair/install `openclaw-gateway.service` before Telegram.
5. **Gateway service installed, gateway unreachable**: repair gateway/service/proxy before Telegram.
6. **Gateway reachable, model missing/unverified**: configure or repair model/provider auth, then verify one local model response before Telegram.
7. **Model verified, keepalive missing**: add keepalive/autostart before Telegram final verification.
8. **Model verified, Telegram missing/unconfigured**: configure Telegram with local token entry.
9. **Telegram configured but not running/connected**: wait for startup, inspect channel status/logs, then repair channel runner.
10. **Telegram receives inbound but no outbound**: diagnose pairing, allowlist, agent/model, tasks, or session state.
11. **Fully working**: gateway reachable, model locally verified, keepalive/autostart verified, Telegram `running=true`, `connected=true`, and a fresh message receives a reply.

Run Windows-side checks first:

```powershell
wsl --status
wsl --version
wsl --list --verbose
$startup = [Environment]::GetFolderPath('Startup')
Get-ChildItem -LiteralPath $startup -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'OpenClaw|WSL|Keepalive' }
schtasks /Query /TN "OpenClaw WSL Keepalive" 2>$null
```

If Task Scheduler access is denied or the task is absent, do not treat that alone as failure; a Startup-folder keepalive may be the intended user-level path.

Then run WSL/OpenClaw checks inside the selected distro, usually Ubuntu:

```bash
id -un
pwd
uname -a
command -v openclaw || true
openclaw --version 2>/dev/null || true
node --version 2>/dev/null || true
npm --version 2>/dev/null || true
npm config get prefix 2>/dev/null || true
pnpm config get prefix 2>/dev/null || true
systemctl --user status openclaw-gateway.service --no-pager -l 2>/dev/null || true
systemctl --user show openclaw-gateway.service -p FragmentPath -p DropInPaths -p ExecStart -p ActiveState -p SubState 2>/dev/null || true
ss -ltnp | grep 18789 || true
openclaw gateway probe 2>/dev/null || true
openclaw models --help 2>/dev/null || true
openclaw models status --plain 2>/dev/null || true
openclaw infer --help 2>/dev/null || true
openclaw channels list --json 2>/dev/null || true
openclaw channels status --json --timeout 30000 2>/dev/null || true
```

Use only presence/status outputs for credentials. Do not print raw `~/.openclaw/openclaw.json`, token files, process environments, or broad credential-bearing logs.

Do not reinstall OpenClaw just because Telegram is not replying. First classify whether OpenClaw itself is missing, gateway is down, model readiness is missing, keepalive is missing, Telegram is unconfigured, or the agent/model turn is slow. Then continue from the matching phase of the Greenfield Install Flow or the relevant diagnosis section.

## Keepalive Infrastructure

Treat keepalive/autostart as required infrastructure for a reliable Telegram bot, not as a later troubleshooting trick or a separate user-facing module. In a smooth install, Codex should set it up or verify it quietly, then continue. A complete Windows + WSL OpenClaw setup needs both:

1. A WSL `systemd --user` gateway service.
2. A host-level or distro-level keepalive/autostart path that keeps WSL awake and starts the gateway after login/reboot.

`systemctl --user enable openclaw-gateway.service` and `loginctl enable-linger` are useful, but they may not keep the WSL distro alive by themselves. If Windows stops the distro when no process is alive, Telegram will only work while a shell, diagnostic command, or other WSL process happens to be running.

### Keepalive Setup Order

1. Install or repair OpenClaw inside WSL.
2. Enable and verify `openclaw-gateway.service`.
3. Create or verify keepalive/autostart quietly.
4. Wait for gateway, sidecars, and Telegram startup.
5. Verify `openclaw gateway probe`.
6. Verify Telegram `running=true`, `connected=true`.
7. Test one fresh Telegram message.

User-facing guidance should stay minimal. Prefer saying: "I am setting up OpenClaw to keep running in the background after Windows login." Do not present keepalive as a separate product/module unless the user asks what it is or a visible window/permission prompt requires explanation.

### Keepalive Options

Prefer keepalive options in this order:

1. **Windows Scheduled Task** when the environment allows it. This is the cleanest long-term host-level autostart path, but it may require permissions and can fail with access denied.
2. **Current-user Startup folder hidden `.vbs`** as the default no-admin path. It is simple, user-level, and keeps WSL alive without opening a visible terminal.
3. **Current-session hidden `Start-Process` keepalive** for immediate repair before persistent autostart is created or verified.
4. **Visible terminal keepalive** only for debugging or when hidden/minimized launch is unavailable.

For the default no-admin Startup-folder path, use `tools/openclaw-local-monitor/Install-WslKeepalive.ps1` or create an equivalent hidden `.vbs` entry:

```powershell
$startup = [Environment]::GetFolderPath('Startup')
$vbs = Join-Path $startup 'OpenClaw WSL Keepalive.vbs'
@'
Set shell = CreateObject("WScript.Shell")
shell.Run "wsl.exe -d Ubuntu -- bash -lc ""systemctl --user restart openclaw-gateway.service; exec sleep infinity""", 0, False
'@ | Set-Content -LiteralPath $vbs -Encoding ASCII
```

If repairing an existing non-Ubuntu install, replace `Ubuntu` only after confirming that distro is the intended OpenClaw owner.

If an old visible Startup-folder entry exists at `OpenClaw WSL Keepalive.cmd`, rename it to `OpenClaw WSL Keepalive.cmd.disabled` after creating the hidden `.vbs`. Do not leave both entries active.

For immediate current-session repair, prefer a hidden process:

```powershell
Start-Process -WindowStyle Hidden -FilePath 'wsl.exe' -ArgumentList @('-d','Ubuntu','--','bash','-lc','systemctl --user restart openclaw-gateway.service; exec sleep infinity')
```

### Window Behavior

- Keepalive should be hidden or minimized whenever possible.
- Do not leave an interactive terminal visible after keepalive setup succeeds unless the visible terminal is intentionally being used for debugging.
- Close successful setup windows after verifying the startup entry and gateway state.
- If replacing an old Startup-folder `.cmd`, explain that the `.vbs` keeps WSL alive invisibly after login and the disabled `.cmd` is only a rollback copy.
- If the user sees multiple keepalive or helper windows, identify the active one, verify whether keepalive is already running, then close successful or stale extras.

### Idempotency And Duplicate Control

The keepalive should be safe to create or verify repeatedly:

- Restart the gateway service once at launch.
- Keep a harmless long-lived process alive, such as `sleep infinity`.
- Avoid printing secrets.
- Prefer Scheduled Task settings or a single Startup entry to avoid duplicate launches.
- Before creating another Startup entry, check whether an OpenClaw/WSL keepalive entry already exists in the user Startup folder.
- If duplicate keepalive processes already exist, do not kill them blindly; first identify whether one is the active path and whether killing it would stop the gateway.

### Success Criteria

Do not mark keepalive complete just because a file or task exists. Mark it complete only after:

1. The persistent startup entry exists, or the user explicitly chose current-session-only repair.
2. The keepalive launches Ubuntu or the selected distro.
3. `openclaw-gateway.service` is active after keepalive launch.
4. `openclaw gateway probe` succeeds.
5. Telegram can reach `running=true`, `connected=true` after the gateway startup window.

After adding keepalive, test the cold path when feasible:

```powershell
wsl --shutdown
# Start the keepalive or simulate Windows login startup.
wsl -d Ubuntu -- bash -lc 'systemctl --user is-active openclaw-gateway.service; openclaw gateway probe'
```

If shutdown testing would interrupt the user's active work, explain the risk and postpone it. Replace `Ubuntu` only when the selected install distro is different.

## Visibility And Permission Scope

OpenClaw should not be given broad visibility or execution rights by accident. Treat visibility/permission scope as a required setup checkpoint before Telegram final verification, and require explicit user confirmation before continuing.

Do not configure this silently. Even when the proposed default is least-privilege, summarize it in plain language and ask the user to confirm. The user-facing explanation can be simple: "I need you to confirm what OpenClaw is allowed to see and do."

The user should be able to decide permissions in natural language. Do not require them to know OpenClaw config keys, policy names, or filesystem boundary syntax. Ask simple questions and translate the answer into the supported OpenClaw configuration path.

Natural-language examples:

- "Only let OpenClaw see this project folder."
- "It can read and write my Playground folder, but not Desktop or Downloads."
- "It can use Telegram and browser control, but ask me before running shell commands."
- "It can manage OpenClaw itself, but not unrelated files."
- "For now, keep it read-only except for OpenClaw config and logs."

Scope areas to verify:

- Filesystem visibility: which folders OpenClaw can read/write.
- Workspace/home boundaries: whether access is limited to an intended workspace, project folder, or selected safe directories.
- Tool/execution policy: whether OpenClaw may run shell commands, edit files, start services, or use plugins.
- External integrations: whether Telegram, browser, memory, proxy, or other integrations are enabled.
- Secrets: token files and model keys should remain hidden from chat and logs.

Rules:

- Prefer least privilege for new installs.
- Always ask the user to confirm the final visibility/permission scope before Telegram final verification.
- Convert the user's natural-language intent into concrete OpenClaw settings, then restate the effective scope before applying it.
- Use the current OpenClaw CLI help, Control UI, or `openclaw doctor` output to discover the supported configuration path.
- Do not invent stale permission commands.
- Do not weaken filesystem boundaries, execution policy, or tool access just to make Telegram work.
- Ask before expanding access beyond the default workspace or beyond what the user explicitly requested.
- Verify scope with presence/status checks, not by printing raw config or secrets.
- If a visibility/permission UI opens, guide the user through the exact choices and close successful setup windows after verification.

## Model Selection And Readiness

Telegram should be configured only after OpenClaw has a working model path. A Telegram bot can receive messages while still failing to answer if model selection, provider auth, quota, local model runtime, or default session routing is not ready.

Treat model readiness as a required setup checkpoint after visibility/permission confirmation and before Telegram token setup. The user-facing explanation can be simple: "Before we connect Telegram, I need to make sure OpenClaw can answer locally."

First discover the current model surface from the installed OpenClaw version. Do not rely on stale command memory. Prefer current CLI help, Control UI, `openclaw doctor`, and status commands:

```bash
openclaw models --help 2>/dev/null || true
openclaw models status --plain 2>/dev/null || true
openclaw infer --help 2>/dev/null || true
openclaw infer model --help 2>/dev/null || true
openclaw status 2>/dev/null || true
```

Classify model state:

1. **Working model already configured**: status/help outputs show a default or selected model, and a local test reply succeeds. Continue to keepalive and Telegram.
2. **Provider selected but auth missing**: guide the user through local terminal/provider UI auth. Never collect API keys in chat.
3. **No model selected**: ask the user to choose in natural language, then translate the choice into the supported OpenClaw configuration path.
4. **Local model requested**: verify the local runtime/model server is installed, running, reachable, and compatible before Telegram.
5. **Model configured but replies fail**: inspect model auth, quota, rate limits, default session routing, task queue, and recent redacted logs before touching Telegram credentials.

Natural-language model choice examples:

- "Use the OpenAI model I already configured."
- "Use Claude if my Anthropic login is available."
- "Use a local model if it is already running."
- "Use the cheapest reliable cloud model."
- "Do not enable fallback unless I say so."

Rules:

- Ask the user to choose or confirm the model/provider when no working default is present.
- Do not ask the user to know provider-specific config keys.
- Never ask for model API keys, auth profiles, or provider secrets in chat.
- Use a local terminal prompt, browser/provider auth flow, or OpenClaw-supported secret store for credentials.
- Do not print raw config, environment variables, auth profiles, or model keys.
- Do not enable automatic model fallback unless the user explicitly chooses it.
- Restate the effective model choice in plain language before final verification.

Verification:

- Run one local OpenClaw/model test before Telegram setup. Use the current CLI shape discovered from help output.
- The test should prove that OpenClaw can produce a simple reply without Telegram.
- If no direct local infer command exists, use the narrowest supported OpenClaw status/task/session test that proves the model route works.
- Do not mark model readiness complete just because a provider name is configured.
- Continue to Telegram only after local model readiness passes or the user explicitly chooses to continue with known model risk.

If Telegram later shows `lastInboundAt` changing but `lastOutboundAt` does not, return to this section before rotating Telegram tokens.

## Prime Directive

The goal is a working Telegram path, not a tour of every chat provider.

- Treat Telegram as the primary supported channel.
- Do not configure or troubleshoot other chat channels unless the user explicitly asks.
- If another channel is blocking Telegram startup and the user wants Telegram-only operation, ask before disabling that other channel, then disable it cleanly and restart once.
- Never ask the user to paste bot tokens, API keys, or auth profiles into chat.
- If the user offers to paste a bot token, API key, or auth profile into chat, stop them and switch to a local terminal prompt or provider UI flow.
- Never print token values from config, logs, process environments, or command output.
- Telegram-only cleanup is limited to selected chat channel entries. Never remove or weaken model auth, proxy, gateway, memory, filesystem, execution policy, or other non-chat configuration as part of Telegram setup.

## Operator Loop

1. Observe current state with narrow read-only checks.
2. Explain the next change in one or two sentences.
3. Only ask the user to act for passwords, bot tokens, BotFather UI, Telegram pairing, restarts, or security choices.
4. Run commands yourself when state is command-observable.
5. After changes, verify one layer at a time.
6. Avoid repeated restarts and probes while gateway/channel startup is still inside its grace period.
7. Do not start by reconfiguring Telegram or asking for a token when status already shows `configured=true` and `tokenStatus=available`; first find whether gateway, WSL lifetime, channel runner, or agent/model handling is the failing layer.
8. When a step opens windows, apply Window And Prompt Discipline: keep only active user-action windows visible, and close successful or stale windows after verification.
9. Treat background persistence/keepalive as part of the initial install/repair path, but handle it quietly unless user action, permissions, or visible-window explanation is needed.

## Fast Triage

Classify the failure before changing anything:

- `openclaw status` shows gateway unreachable, `ss` has no `127.0.0.1:18789`: gateway is not listening; inspect systemd and WSL lifetime.
- `ss` shows `127.0.0.1:18789` listening but `openclaw gateway probe` or `curl http://127.0.0.1:18789/` times out: gateway is present but not responding; inspect gateway startup, sidecars, CPU, and recent logs before touching Telegram credentials.
- `openclaw channels status --json` shows Telegram `configured=true`, `tokenStatus=available`, and `probe.ok=true`: bot/token/API are good; do not ask for a token.
- Telegram `running=true`, `connected=true`, `lastInboundAt` changes, but `lastOutboundAt` does not: Telegram works; diagnose agent routing, pairing/allowlist, model latency, model auth, task queue, or session locks.
- The bot replies only after WSL or a diagnostic command wakes the machine: suspect WSL lifetime/autostop first. Check for repeated journal blocks with `Stopping openclaw-gateway.service` and changing WSL boot IDs.
- If background persistence/keepalive is missing, repair it quietly as internal infrastructure unless user confirmation, permissions, or visible startup-entry explanation is required.
- If many terminal/helper windows are open, classify window state before continuing: active user prompt, intentional background keepalive, succeeded, or failed/stale.
- If one gateway probe times out but another RPC/status command succeeds, run a sequential probe/status pair and inspect logs before restarting.
- First reply after a cold WSL start can take 60-120 seconds because gateway, sidecars, browser, heartbeat, and Telegram provider start asynchronously. Later replies should not require WSL to be poked awake.

## Safe State Checks

Use equivalent commands for the user's distro and OpenClaw path. Prefer absolute paths if non-login systemd environments cannot find `openclaw`.

```powershell
wsl --status
wsl --version
wsl --list --verbose
```

```bash
id -un
pwd
uname -a
command -v openclaw || true
openclaw --version
systemctl --user is-active openclaw-gateway.service 2>/dev/null || true
loginctl show-user "$USER" -p Linger 2>/dev/null || true
ss -ltnp | grep 18789 || true
openclaw models status --plain 2>/dev/null || true
openclaw channels list --json 2>/dev/null || true
openclaw channels status --json --timeout 30000 2>/dev/null || true
```

For config inspection, print presence only:

```bash
node -e 'const fs=require("fs"); const p=process.env.HOME+"/.openclaw/openclaw.json"; const c=JSON.parse(fs.readFileSync(p,"utf8")); const t=(c.channels&&c.channels.telegram)||{}; console.log("telegram.present="+!!c.channels?.telegram); console.log("telegram.enabled="+(t.enabled===true)); console.log("telegram.botToken="+(t.botToken?"SET":"MISSING"));'
```

Prefer OpenClaw CLI status/list outputs over raw config parsing when available. Use raw config parsing only for presence checks and never print credential values.

For logs, prefer narrow channel/gateway commands and redact before relaying:

```bash
openclaw channels logs --channel telegram --lines 120
openclaw logs --plain --limit 240 --timeout 30000
```

Do not use broad recursive searches over `~/.openclaw` or unfiltered `systemctl --user cat`, `journalctl`, `env`, `printenv`, `ps e`, or raw config dumps when credentials may be present. Do not ask the user to screenshot or transcribe windows that may contain bot tokens, API keys, auth profiles, or secret config; if a screenshot is already provided, avoid repeating sensitive values.

## Gateway Readiness

Before Telegram work, prove the local OpenClaw gateway layer is healthy. Do not treat a configured Telegram bot as meaningful until the gateway responds.

Required checks:

1. Confirm the selected WSL distro is running and the Linux user exists.
2. Confirm OpenClaw is installed inside WSL, not only in Windows-native shell.
3. Confirm `systemd --user` works.
4. Confirm `openclaw-gateway.service` exists or can be repaired by OpenClaw.
5. Confirm port `18789` listens only after gateway has had time to start.
6. Confirm the gateway responds, not merely listens.

Useful commands:

```bash
wsl -d Ubuntu -- bash -lc "id -un && pwd"
command -v openclaw || true
openclaw --version
systemctl --user status --no-pager 2>/dev/null || true
systemctl --user status openclaw-gateway.service --no-pager -l 2>/dev/null || true
ss -ltnp | grep 18789 || true
openclaw gateway probe
curl --max-time 5 -i http://127.0.0.1:18789/ 2>&1 | head -n 20
```

Interpretation:

- Service missing: use OpenClaw-discovered repair paths such as `openclaw doctor`, `openclaw doctor --fix`, or current CLI help. Do not invent stale service commands.
- Port missing: inspect service state, WSL lifetime, and gateway logs before Telegram work.
- Port listening but `gateway probe` or HTTP times out: gateway/sidecar/event-loop is the problem, not Telegram token.
- Gateway reachable: continue to background persistence and Telegram setup.

If OpenClaw is missing inside WSL, return to the Greenfield install flow. Ask before networked install/update commands. If an existing OpenClaw install is present, prefer `openclaw update --dry-run` before `openclaw update --yes`.

Apply Window And Prompt Discipline: after successful gateway readiness verification, close successful helper windows and leave only intentional hidden/minimized background persistence.

## Network And Proxy

Telegram needs outbound HTTPS to Telegram APIs. Do not assume the user always has a proxy enabled, and do not configure gateway proxy settings until a proxy endpoint is verified.

Test direct WSL network first:

```bash
curl --max-time 10 -I https://api.telegram.org
```

If direct access fails or the user commonly toggles a Windows proxy:

- Detect or reuse the current Windows/WSL proxy bridge; do not hard-code ports from another machine.
- Verify the proxy endpoint before putting it into gateway service environment.
- Always preserve local bypasses for gateway self-access:

```text
NO_PROXY=127.0.0.1,localhost,::1
no_proxy=127.0.0.1,localhost,::1
```

Final behavior should be adaptive: use direct access when it works; use proxy only when needed and verified. If gateway listens but self-access fails, inspect `NO_PROXY` before changing Telegram credentials.

## Long Offline Recovery

Use this section when the machine stays on but internet access is disabled for a long time, then OpenClaw or Telegram does not resume cleanly after the network returns. This is an infrastructure recovery problem, not a token/model reset problem.

Important distinction:

- `Restart=always` only restarts `openclaw-gateway.service` after the process exits.
- It does not repair a process that is still alive while Telegram polling, HTTP transport, or provider sockets are stale after a long network outage.
- Do not rotate Telegram tokens, re-run pairing, or change models before checking for stale polling/network recovery symptoms.

Recognize the pattern:

```text
Polling stall detected
Network request for 'getUpdates' failed
stale-socket
channel stop timed out
```

If these appear after a long offline period and `openclaw status` still shows the gateway service as running, add or verify a network-recovery watchdog. Treat the watchdog as debounce infrastructure: a single failed probe or a slow gateway probe should not immediately restart OpenClaw, because that can amplify a short network/proxy wobble into delayed Telegram replies. The preferred behavior is:

1. Check network reachability on a short interval.
2. Require at least two consecutive network probe failures before recording `offline`.
3. While offline, only record `offline`; do not repeatedly restart OpenClaw.
4. When state changes from confirmed `offline` to `online`, record a recovery recommendation instead of restarting gateway.
5. Use a cooldown so repeated recommendations do not spam logs or encourage restart loops.
6. While online, optionally check local gateway health with a simple HTTP request to the dashboard endpoint, but never during the gateway startup grace period. Do not use `openclaw gateway probe` inside the systemd watchdog; it can fail under a different user-service environment and create false restarts. Use `openclaw gateway probe` only as an interactive verification command outside the timer.

Recommended user-level systemd design:

- Script: `~/.local/bin/openclaw-netwatch`
- Service: `~/.config/systemd/user/openclaw-netwatch.service`
- Timer: `~/.config/systemd/user/openclaw-netwatch.timer`
- Interval: 60 seconds
- Cooldown: at least 300 seconds
- Network probe: `curl -fsS --connect-timeout 4 --max-time 8 https://api.telegram.org`
- Gateway health probe: `curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:18789/`
- Confirm counts: at least 2 consecutive failures before declaring network offline or gateway unhealthy.
- Gateway startup grace: at least 240 seconds after `openclaw-gateway.service` starts. During this grace period, skip gateway health recovery recommendations because OpenClaw may be staging bundled runtime dependencies or starting channels/sidecars.
- Preserve proxy environment when the gateway already uses one.
- Preserve `NO_PROXY=127.0.0.1,localhost,::1`.
- If generating the script from Windows or PowerShell, ensure the final file has Linux LF line endings. Mixed CRLF/LF line endings can make systemd report `unexpected end of file`.

This skill bundle includes an installable implementation under:

```text
tools/openclaw-netwatch/
```

Run the installer without `-Apply` first for a dry run. Applied installs are observe-only: the timer records network/gateway recovery recommendations, but it never restarts gateway automatically.

Use the bundled implementation as the source of truth. Do not recreate an older watchdog that calls `systemctl --user restart openclaw-gateway.service` automatically.

After installing the timer, verify:

```bash
chmod +x ~/.local/bin/openclaw-netwatch
systemctl --user daemon-reload
systemctl --user enable --now openclaw-netwatch.timer
systemctl --user status openclaw-netwatch.timer --no-pager
tail -n 20 ~/.cache/openclaw-netwatch/watchdog.log
openclaw status --json
```

Expected result after initialization:

- The first run records current network/gateway state.
- One failed network or gateway probe should log "waiting for confirmation" and should not restart the gateway.
- During the gateway startup grace period, the watchdog should not record gateway HTTP recovery recommendations. This prevents false alarms while OpenClaw installs bundled runtime deps or starts Telegram sidecars.
- If gateway appears slow from Telegram but `openclaw gateway probe` is fast interactively, inspect watchdog logs first; do not restart from the watchdog path.
- `openclaw status` should return `gateway.reachable=true` after startup settles.

If a user's outage pattern is Windows network interface up/down rather than proxy reachability, prefer surfacing that state in Control Center diagnostics before adding any repair action. Do not add a Windows Task Scheduler reconnect restart by default.

Prefer the WSL timer when the user wants continuous observation without configuring Windows event triggers.

## Local OpenClaw Control Center

After OpenClaw is installed, the model is verified, and keepalive/autostart is in place, install the local Windows control center when the skill bundle includes:

```text
tools/openclaw-local-monitor/
```

The control center is the user-local Windows entry point. Opening it should observe current state first, not silently start OpenClaw. It should help the user answer:

- Is the gateway reachable?
- Is Telegram connected?
- Are there real background tasks running?
- What token/cost totals are available from the offline usage cache?
- Are there recent Telegram/error notices?

Important semantics:

- "后台任务" means real backend work: `openclaw tasks list --json` entries with `status=queued` or `status=running`, TaskFlow pressure from `openclaw tasks flow list` where active/blocked/cancel-requested is nonzero, plus clearly labeled local daemon/workspace artifact heartbeat when OpenClaw is producing local learning artifacts outside the task registry.
- Recent Telegram messages, recently updated sessions, or token growth are not enough to say a background task is running. Show them only as context unless there is also a task, TaskFlow pressure, daemon, or artifact heartbeat.
- Token/cost shown in the panel must come from the offline cache `~/.openclaw/monitor-cache/usage-summary.json`, generated by the optional `openclaw-usage-cache` WSL timer about every 10 minutes. The main panel must not scan monthly sessions or call gateway RPCs to fill token/cost cards. Treat amounts as local estimates, not provider invoices.
- The control center may start or stop `openclaw-gateway.service` only after the user clicks the explicit `开启 OpenClaw` / `关闭 OpenClaw` button. It may keep WSL awake after an explicit start and open the browser-based `原生 Control` UI after the gateway is running and the user confirms. It must not edit OpenClaw config, auth profiles, tokens, provider keys, channel settings, or gateway service files.
- The Telegram card should stay simple: `未配置`, `已连接`, or `需检查`. Do not show message-flow states such as `已收到未回复` on that card. Use `openclaw channels status --json --timeout 30000` in addition to `gateway probe` to drive a separate temporary startup progress bar inside the top status panel. The progress bar should describe the current startup stage: gateway check, Telegram startup, Telegram connection, model/sidecar warmup, and ready. Hide it automatically when progress reaches 100%.
- During cold startup, keep the panel probe light: run only gateway probe and Telegram channel status first. Token/cost cards should use cache only; do not fill missing cache data by scanning sessions or asking the gateway. The goal is to show reliable startup progress without adding extra load or WebSocket churn while OpenClaw is still starting channels and sidecars.
- The control center may offer `Clash 安全模式` for Clash Verge Rev users who need TUN/global-style routing for OpenClaw, Codex, or foreign model providers while preserving domestic direct/rule routing for WeChat, Tencent services, and China-region links. This option should use the local Mihomo named pipe to keep Clash in rule mode when necessary, let OpenClaw/Codex follow the user's selected `GLOBAL` proxy group, and leave node choice to Clash Verge's `GLOBAL` group. If the user is not using Clash global/TUN, or domestic apps already work normally, explain that this option is usually unnecessary. It must not store proxy subscriptions, provider secrets, auth profiles, raw Clash config, or machine-specific node names in the repository.
- The `原生 Control` path should use `Start-OpenClaw.ps1` as an internal helper only when the gateway is already running. That helper may resolve the gateway token locally and pass it as a temporary browser URL fragment; it must not create a token-bearing shortcut, print the token, or commit it to the repository. Warn before opening because browser Control can trigger heavier session/model queries than the local panel. After opening the browser Control URL, it should attempt to restore and focus a browser window so the user sees an immediate pop-up/foreground action.
- The main panel should remain a lightweight vital-sign view. Do not keep a redundant `重新检测` button on the main panel, and do not run periodic heavy gateway refresh. Deeper troubleshooting belongs behind `诊断`; starting/stopping OpenClaw remains an explicit power action.
- Hover hints should be concise, bounded inside the app window, and sized to their text. Do not use native tooltips that can overflow outside the interface.
- Restoring from tray or the Windows taskbar should not show black or unpainted regions. Use ordinary double buffering and explicit layout/repaint on show/restore paths. Avoid `WS_EX_COMPOSITED` full-window compositing here; on this WinForms panel it can cause child controls to appear as black or unpainted rectangles during startup or restore.
- Use a transparent-background, friendly red OpenClaw-style mascot icon for desktop, taskbar, and tray. Do not use a screenshot or asset with a dark background as the icon.
- The panel must not print or store secrets.
- The compiled `.exe` is a local build artifact. Do not commit it to the skill repo.

Preferred install command from the skill's monitor directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenClawMonitor.ps1
```

What the installer should do:

1. Copy monitor source/assets into `%LOCALAPPDATA%\OpenClawMonitor`.
2. Build `OpenClawMonitor.exe` with the built-in .NET Framework compiler.
3. Create a Startup-folder shortcut named `OpenClaw Control.lnk`.
4. Remove old `OpenClaw Monitor` and `OpenClaw 启动` shortcuts from Desktop, Start Menu, and Startup when present.
5. Start the control center.
6. Verify that the window opens, shows the custom icon, does not auto-start OpenClaw, explicitly starts/stops OpenClaw through the power button, opens `原生 Control` only after confirmation and without manual gateway-token entry after the gateway is running, brings the browser window forward as visible feedback when possible, reads token/cost from the offline cache if installed, and minimizes/closes to the system tray.

Manual build command if needed:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Build-OpenClawMonitor.ps1
```

If the user's WSL distro differs from the default, adjust `WslDistro` in `OpenClawMonitor.cs` before building. The default assumptions are:

```text
WSL distro: Ubuntu
OpenClaw command: openclaw is available on the WSL login-shell PATH
Gateway URL: ws://127.0.0.1:18789
```

If the monitor shows no backend task while OpenClaw recently replied in Telegram, explain that these are different signals: queued/running tasks and TaskFlow are authoritative for registered work; local daemon/artifact heartbeat is evidence of local productive work; Telegram/session recency alone is only activity context.

## Optional Market Immersion Module

Use this section only when the user explicitly wants OpenClaw to run a long-term market information immersion workflow, market daily report, scheduled 7x24 financial-news collection, or Notion-published market briefing. This is an optional `openclaw-job-module`, not base OpenClaw infrastructure and not a normal Codex skill install.

The bundled module lives at:

```text
modules/openclaw-market-immersion/
```

User choice checkpoints:

- Ask whether to install the module files into the OpenClaw workspace.
- Ask whether to use the MiaoXiang/MX finance skills and verify `MX_APIKEY` locally.
- Ask whether to enable Notion publishing. If yes, guide the user to create a Notion internal connection, grant page access, and enter the token locally. Do not ask for the token in chat.
- Ask whether this market immersion module should send its report link or report file to Telegram. This is separate from the base OpenClaw Telegram channel: the normal Telegram bot may stay enabled even if market-report Telegram push is disabled.
- Ask whether to enable the WSL `systemd --user` timers.
- Run a smoke test first. Do not run a formal publishing job manually unless the user explicitly asks or the scheduled timer triggers it.

User-operation and window rules:

- For every browser, Notion, provider-console, PowerShell, Ubuntu, token prompt, or setup window opened during this module setup, track its lifecycle explicitly: active user input, waiting for verification, success and ready to close, stale/failed and ready to close, or intentional background infrastructure.
- Decide whether the user's action succeeded by verifying local state, command output, service status, provider-page state, or a requested screenshot. Do not make the user judge success from ambiguous UI alone.
- Close successful, stale, failed, duplicate, or no-longer-needed windows directly whenever the available tools can control them. Only ask the user to close a window when it is outside tool control; in that case, identify the exact window and why it can be closed.
- If the user must complete a provider UI action, describe what success looks like before they act, then verify the result yourself from commands, provider state, or screenshot before moving on.
- If the next step depends on a screenshot, explicitly ask the user for a screenshot and say which part of the window must be visible. Do not assume the user knows when a screenshot is needed.
- If a window is stale, failed, or no longer needed, preserve any useful error text, close it directly when possible, and continue from the active path.
- Never leave token-entry, Notion integration, or helper windows open just because setup succeeded. Close them after local verification unless they are the active long-running keepalive path.

Key handling rules:

- Store MX credentials in `~/.openclaw/secrets/mx.env`.
- Store Notion credentials in `~/.openclaw/secrets/notion.env`.
- The bundled `config/market_immersion_config.json` must remain portable: use `~/.openclaw/...` paths, leave user-specific Notion page IDs and Telegram targets blank, and keep Notion/Telegram publishing disabled until the user opts in.
- When the user opts into Notion or Telegram publishing, update that user's installed config under `~/.openclaw/workspace/market-immersion-module/config/market_immersion_config.json`; do not commit their IDs or targets back to the repository.
- Keep secret files mode `0600`.
- Never print, log, screenshot, commit, or chat-paste secret values.
- If a secret is exposed in chat, logs, screenshots, or command history, recommend rotating it.

Architecture to explain to the user:

- The module is installed as files under the OpenClaw workspace.
- Scheduling is handled by WSL `systemd --user` timers.
- Each timer calls `scripts/run_market_immersion.sh`.
- The script collects source feeds, deduplicates them, marks coverage warnings when a source cannot fully page back to the requested window start, then calls OpenClaw to produce a compact information digest.
- Notion publishing happens only after source collection and OpenClaw digest generation succeed.
- This is not OpenClaw's own built-in scheduler. It is a reliable host timer that invokes OpenClaw as the processing layer.

Default report structure:

```text
1. 信息汇总
2. 原始消息流
3. 本地 manifest / 调试归档
```

Report rules:

- The digest is written as 3-5 coherent natural paragraphs, not as fixed category columns.
- The digest should preserve concrete subjects, numbers, event details, repeated themes, and meaningful differences across sources.
- Do not mechanically include message publish time, title, or source inside the digest paragraphs. Include time only when the reported event time is itself part of the information.
- The raw message flow keeps source, publish time, title, type, URL when available, and full original content in chronological order.
- If the source pool is non-empty but OpenClaw digest generation fails or returns empty/low-quality summary paragraphs, treat the run as failed and do not publish it as a successful report.
- Formal runs should attempt to cover the full requested feed window. If a source cannot page back far enough, publish only with an explicit coverage warning instead of silently implying completeness. Hard-fail only when core collection, OpenClaw整理, or enabled delivery steps fail.

Default time windows:

```text
09:05 morning: previous day 22:10 -> current run time
12:15 midday: same day 09:05 -> current run time
15:20 close: same day 12:15 -> current run time
22:10 night: same day 15:20 -> current run time
```

If `last_success_at` exists and is earlier than the scheduled end time, use it as the next run's start boundary to avoid gaps. Avoid casual manual formal runs at odd times because they can split the next scheduled window.

Timer reliability expectations:

- Use `Persistent=yes` on timers so missed runs fire after WSL starts again.
- Enable user linger when appropriate so user services can run without an interactive WSL shell.
- Use a Windows Startup keepalive only after explaining that it starts WSL after Windows login.
- For network outages, use service retry (`Restart=on-failure`, a short `RestartSec`, and no tight start limit). This is retry-on-failure behavior, not a literal network-restored event hook.
- Explain honestly that if Windows is fully powered off, the missed timer runs after Windows login starts WSL; it cannot run while the machine is off.

Installation outline:

```bash
mkdir -p "$HOME/.openclaw/workspace"
cp -a /path/to/modules/openclaw-market-immersion "$HOME/.openclaw/workspace/market-immersion-module"
chmod +x "$HOME/.openclaw/workspace/market-immersion-module/scripts/"*.sh
python3 -m compileall "$HOME/.openclaw/workspace/market-immersion-module/scripts"
python3 -m json.tool "$HOME/.openclaw/workspace/market-immersion-module/config/market_immersion_config.json" >/dev/null
```

Use the module's local prompts for secrets when available:

```bash
"$HOME/.openclaw/workspace/market-immersion-module/scripts/set_notion_token.sh"
```

After the user opts into Notion, set only the installed local config to `"notion": {"enabled": true, ...}`. After the user opts into Telegram report push, set only the installed local config to `"telegram": {"enabled": true, "target": "<user target>", ...}`. The repository template should keep both disabled and target/page fields blank.

Install timers only after the user opts in:

```bash
mkdir -p "$HOME/.config/systemd/user"
cp "$HOME/.openclaw/workspace/market-immersion-module/systemd/"* "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now openclaw-market-immersion-morning.timer
systemctl --user enable --now openclaw-market-immersion-midday.timer
systemctl --user enable --now openclaw-market-immersion-close.timer
systemctl --user enable --now openclaw-market-immersion-night.timer
systemctl --user enable --now openclaw-market-feed-snapshot.timer
```

Verification:

```bash
systemctl --user list-timers 'openclaw-market-immersion*'
systemctl --user status openclaw-market-immersion-morning.timer --no-pager
"$HOME/.openclaw/workspace/market-immersion-module/scripts/run_market_immersion.sh" --phase smoke --no-publish
```

If the smoke test cannot query sources because of network, proxy, API, or provider changes, report that plainly and do not claim the scheduled daily report is production-ready.

## Optional API Enhancements

Use this section only when the user explicitly wants extra OpenClaw capabilities beyond the base WSL/gateway/Telegram setup. These APIs are optional:

- **Jina embeddings**: enables OpenClaw `memorySearch` semantic recall through an OpenAI-compatible embeddings endpoint.
- **Tavily web search**: enables OpenClaw `web_search` for current web retrieval or periodic internet absorption workflows.

Do not install either API by default. Ask plainly whether the user wants each one. If they decline, skip it without weakening the normal OpenClaw setup.

When this skill bundle includes:

```text
tools/openclaw-optional-apis/
```

use the bundled local prompts instead of asking for keys in chat:

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-optional-apis
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-JinaApiKey.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Set-TavilyApiKey.ps1
```

Key handling rules:

- Save Jina to `~/.openclaw/secrets/jina.env` as `JINA_API_KEY`.
- Save Tavily to `~/.openclaw/secrets/tavily.env` as `TAVILY_API_KEY`.
- Add user-systemd drop-ins under `~/.config/systemd/user/openclaw-gateway.service.d/`.
- Keep the files `0600` and never print or commit the key values.
- Default to not restarting the gateway; restart only after the user agrees.

OpenClaw configuration rules:

- For Jina, use:
  - `agents.defaults.memorySearch.enabled=true`
  - `agents.defaults.memorySearch.provider=openai`
  - `agents.defaults.memorySearch.model=jina-embeddings-v4`
  - `agents.defaults.memorySearch.remote.baseUrl=https://api.jina.ai/v1`
  - `agents.defaults.memorySearch.remote.apiKey` as a real SecretRef object: `--ref-provider default --ref-source env --ref-id JINA_API_KEY`
  - `agents.defaults.memorySearch.fallback=none`
  - `agents.defaults.memorySearch.remote.batch.enabled=false`
- Do not configure Jina as a raw string such as `env:JINA_API_KEY`; that can be treated as the literal API key and produce misleading 401 errors.
- For Tavily, enable `plugins.entries.tavily.enabled=true`, set `plugins.entries.tavily.config.webSearch.apiKey` as an env SecretRef for `TAVILY_API_KEY`, then set `tools.web.search.enabled=true` and `tools.web.search.provider=tavily`.

Verification:

```bash
set -a; . ~/.openclaw/secrets/jina.env; set +a
python3 /path/to/tools/openclaw-optional-apis/Verify-JinaKey.py

set -a; . ~/.openclaw/secrets/tavily.env; set +a
python3 /path/to/tools/openclaw-optional-apis/Verify-TavilyKey.py
```

If direct verification succeeds but OpenClaw still reports unavailable embeddings or web search, inspect the active config shape and restart status before asking the user to replace keys. Common causes are: gateway not restarted after env drop-in changes, raw `env:...` strings instead of SecretRefs, endpoint/network interruption, or provider-side rate/region blocking.

For Jina specifically, distinguish "actual query works" from "`deep status` health probe fails":

- If `memory search` returns results but `openclaw memory status --deep` reports `fetch failed`, `other side closed`, or `Client network socket disconnected before secure TLS connection was established`, check for the OpenClaw 2026.4.26 CLI entry false-negative before changing keys.
- The known failure path is: the new `entry.js` bootstrap treats `memory` as a normal CLI command and eagerly warms the model context-window cache. That can start Codex/OpenAI model-discovery network requests in parallel with the Jina embedding probe, especially under WSL proxy/TUN setups, and make the health probe fail even though Jina itself is usable.
- Use `tools/openclaw-optional-apis/Repair-OpenClawMemoryDeepStatus.ps1` or its WSL helper `repair-openclaw-memory-deep-status.py`. The helper adds `memory` to OpenClaw's eager-warmup skip list, backs up the installed context file, and does not touch secrets, proxy settings, gateway config, or the control center.
- After repair, verify both:
  - `openclaw memory status --deep --json` should show `embeddingProbe.ok: true`.
  - `openclaw memory search --query "OpenClaw" --max-results 3 --json` should return results.

## Doubao / Volcengine ASR Helper

Use this section when the user wants OpenClaw to process local audio through Doubao/Volcengine, especially after a Gemini audio path is unavailable or too limited.

Keep the model boundary clear:

- Doubao text models are useful for transcript analysis, taxonomy review, tone/style summaries, and fallback reasoning.
- Ark chat model calls are not a reliable substitute for native audio understanding unless the current provider explicitly supports that input shape.
- Volcengine recording-file ASR is a speech endpoint. It needs enabled ASR resources in addition to the general API key. Flash mode normally uses `volc.bigasr.auc_turbo`; standard mode normally uses `volc.seedasr.auc`.
- Gemini can still be used for native audio understanding when the task needs more than transcription.

When this skill bundle includes:

```text
tools/openclaw-doubao-asr/
```

install the local helper from Windows PowerShell:

```powershell
cd .\openclaw-telegram-wsl-setup\tools\openclaw-doubao-asr
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-DoubaoAsrTool.ps1
```

The installer should:

1. Copy `openclaw-doubao-asr` into `~/.local/bin` inside Ubuntu.
2. Add non-secret ASR defaults to `~/.openclaw/secrets/volcengine.env`:
   - `VOLCENGINE_ASR_RESOURCE_ID=volc.bigasr.auc_turbo`
   - `VOLCENGINE_ASR_ENDPOINT=https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`
   - `VOLCENGINE_ASR_MODEL_NAME=bigmodel`
   - `VOLCENGINE_STANDARD_RESOURCE_ID=volc.seedasr.auc`
   - `VOLCENGINE_STANDARD_SUBMIT_ENDPOINT=https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit`
   - `VOLCENGINE_STANDARD_QUERY_ENDPOINT=https://openspeech.bytedance.com/api/v3/auc/bigmodel/query`
   - `VOLCENGINE_STANDARD_MODEL_NAME=bigmodel`
3. Preserve existing key variables and never print them.
4. Run `openclaw-doubao-asr --self-check`.

For key handling:

- Reuse the existing Volcengine key only if it is already saved locally.
- If a key is missing, open a local terminal prompt. Do not ask the user to paste it into chat.
- Prefer `VOLCANO_ENGINE_API_KEY` for OpenClaw's Volcengine provider compatibility, and allow ASR-specific aliases such as `VOLCENGINE_ASR_API_KEY`.
- If the Volcengine speech-service page provides `APP ID` and `Access Token`, save them locally with `tools/openclaw-doubao-asr/Set-DoubaoAsrCredentials.ps1`; the helper will prefer `VOLCENGINE_ASR_APP_KEY + VOLCENGINE_ASR_ACCESS_KEY` over the generic API key.

Before running an actual transcription command, explain and confirm the data transfer:

```text
This will send <audio path or URL> to Volcengine for ASR. Continue?
```

Only after the user approves, run:

```bash
openclaw-doubao-asr /path/to/audio.wav --output result.json
openclaw-doubao-asr --text-only /path/to/audio.wav
openclaw-doubao-asr --mode standard --url "https://example.com/audio.wav" --wait --output result.json
```

If self-check passes but transcription fails:

- Check whether the Volcengine project has the big-model recording-file ASR resource enabled.
- Check whether the key belongs to the project that owns the ASR resource.
- Check account quota or billing status.
- Check whether the audio file is too large; use smaller clips for ordinary OpenClaw workflows.
- Do not keep retrying large private audio files without user approval.

## IMA OpenAPI Knowledge Base Setup

Use this section when the user wants OpenClaw to call Tencent ima knowledge bases, notes, or "IMA Skills" through natural language. Prefer the official IMA OpenAPI skill path over desktop automation skills unless the user explicitly wants to control the ima.copilot desktop app.

Principles:

- Never ask the user to paste IMA Client ID or API Key into chat.
- Use a local terminal prompt or provider UI for credentials.
- IMA OpenAPI requires both Client ID and API Key from `https://ima.qq.com/agent-interface`.
- Treat the skill as passive: it should not add a long-running process or call IMA during gateway startup. It should only run when a natural-language request mentions knowledge bases, notes, uploads, URL import, or IMA search.
- If credentials were exposed in chat, screenshots, shell history, or logs, recommend rotating them in IMA, even if the user says it is not urgent.

Install and inspect:

```powershell
wsl -d Ubuntu -- bash -lc 'openclaw skills search ima'
wsl -d Ubuntu -- bash -lc 'openclaw skills install ima-skills'
wsl -d Ubuntu -- bash -lc 'sed -n "1,220p" ~/.openclaw/workspace/skills/ima-skills/SKILL.md'
wsl -d Ubuntu -- bash -lc 'sed -n "1,220p" ~/.openclaw/workspace/skills/ima-skills/knowledge-base/SKILL.md'
```

If network fetch fails inside the sandbox, rerun the install with approval for network access. The installed skill name may appear as `ima-skill` even when the ClawHub slug is `ima-skills`.

Credential storage:

```bash
mkdir -p "$HOME/.config/ima" "$HOME/.openclaw/secrets"
chmod 700 "$HOME/.config/ima" "$HOME/.openclaw/secrets"
read -rp "IMA OpenAPI Client ID: " IMA_CLIENT_ID
read -rsp "IMA OpenAPI API Key: " IMA_API_KEY
printf '\n'
umask 077
printf '%s\n' "$IMA_CLIENT_ID" > "$HOME/.config/ima/client_id"
printf '%s\n' "$IMA_API_KEY" > "$HOME/.config/ima/api_key"
{
  printf 'IMA_OPENAPI_CLIENTID=%s\n' "$IMA_CLIENT_ID"
  printf 'IMA_OPENAPI_APIKEY=%s\n' "$IMA_API_KEY"
  printf 'IMA_CLIENT_ID=%s\n' "$IMA_CLIENT_ID"
  printf 'IMA_API_KEY=%s\n' "$IMA_API_KEY"
} > "$HOME/.openclaw/secrets/ima.env"
chmod 600 "$HOME/.config/ima/client_id" "$HOME/.config/ima/api_key" "$HOME/.openclaw/secrets/ima.env"
unset IMA_CLIENT_ID IMA_API_KEY
```

When driving this from Windows, prefer opening a local PowerShell prompt that uses `Read-Host -AsSecureString` for the API Key and writes to `\\wsl.localhost\Ubuntu\home\<user>\.config\ima\...`. Do not ask the user to type credentials at a normal shell prompt.

Load the credentials into the gateway service:

```bash
mkdir -p "$HOME/.config/systemd/user/openclaw-gateway.service.d"
cat > "$HOME/.config/systemd/user/openclaw-gateway.service.d/ima.conf" <<'EOF'
[Service]
EnvironmentFile=%h/.openclaw/secrets/ima.env
EOF
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
```

Validation:

```bash
cd "$HOME/.openclaw/workspace/skills/ima-skills"
node ima_api.cjs openapi/wiki/v1/search_knowledge_base '{"query":"","cursor":"","limit":20}'
systemctl --user is-active openclaw-gateway.service
openclaw gateway probe --timeout 15000
```

Success looks like `{"code":0,"msg":"success",...}` with an `info_list` of knowledge bases. Report the knowledge-base names only; never print or inspect credential file contents. If `openclaw skills list` still says `needs setup` while the API call succeeds, explain that the runtime is healthy and the checker may only inspect environment metadata.

Natural-language smoke tests for the user:

- "帮我看看 IMA 里有哪些知识库"
- "搜索 IMA 里有没有关于 OpenClaw 的内容"
- "把这个微信文章链接加入轻舟的知识库"
- "上传这个 PDF 到指定 IMA 知识库"

Startup-speed expectation: installing `ima-skills` and adding `ima.env` should not materially slow gateway startup. The skill is passive and has no resident process; only an environment file is read during service startup. If startup becomes slow, diagnose gateway/plugins/sidecars from logs before blaming IMA.

## Telegram Setup And Pairing

Configure Telegram only after gateway readiness, visibility/permission scope, model readiness, and background persistence are handled. Use BotFather to create or select a bot, but keep token entry local.

Never ask the user to paste the bot token into chat. If the user offers, stop them and switch to a local terminal prompt or provider UI flow.

Prefer a locked token file:

```bash
mkdir -p "$HOME/.openclaw/secrets"
chmod 700 "$HOME/.openclaw" "$HOME/.openclaw/secrets"
read -rsp "Telegram bot token: " OPENCLAW_TELEGRAM_TOKEN
printf '\n'
printf '%s\n' "$OPENCLAW_TELEGRAM_TOKEN" > "$HOME/.openclaw/secrets/telegram-bot-token"
chmod 600 "$HOME/.openclaw/secrets/telegram-bot-token"
unset OPENCLAW_TELEGRAM_TOKEN
openclaw channels add --channel telegram --account default --token-file "$HOME/.openclaw/secrets/telegram-bot-token"
```

If the installed OpenClaw version has different channel arguments, inspect `openclaw channels add --help` and use the current CLI shape. If existing config stores a token directly and the user wants stronger hygiene, migrate it to a token file or OpenClaw secret reference before continuing. If a token was accidentally printed into logs or chat, recommend rotating it in BotFather.

After Telegram is configured:

1. Restart gateway once.
2. Wait for Telegram channel startup before asking for `/start`.
3. Ask the user to send `/start` to the bot.
4. If the bot replies `access not configured` with a pairing code, approve it locally:

```bash
openclaw pairing approve telegram <PAIRING_CODE>
```

Treat the pairing code as transient. After approval, ask the user to send one ordinary test message.

Close token-entry or BotFather-helper windows after configuration is verified, unless that window is also the active gateway/background path.

## Startup And Verification

Telegram startup is asynchronous. Do not call every `running=false` a failure, and do not repeatedly restart during the startup grace period.

After any gateway restart:

1. Wait 60-120 seconds for gateway, sidecars, and Telegram provider startup.
2. Run one status check:

```bash
openclaw channels status --json --timeout 30000
```

Interpret status:

- `configured=true`, `running=false`, no `lastError`, recent gateway start: still starting; wait.
- `running=true`, `connected=true`: ask for one fresh Telegram test message.
- `lastInboundAt` changes: OpenClaw received the Telegram message.
- `lastOutboundAt` changes: OpenClaw replied.
- `lastInboundAt` changes but `lastOutboundAt` does not: Telegram works; diagnose pairing/allowlist/agent/model/task state.
- No `lastInboundAt`: diagnose token, network, polling, pairing/allowlist, or whether the user messaged before the bot was ready.
- One `gateway probe` timeout while another RPC/status succeeds: run a sequential probe/status pair and inspect logs before restarting.

End-to-end verification order:

```bash
openclaw --version
openclaw config validate
systemctl --user restart openclaw-gateway.service
# wait 60-120 seconds
systemctl --user is-active openclaw-gateway.service
ss -ltnp | grep 18789 || true
openclaw gateway probe
openclaw channels status --json --timeout 30000
openclaw channels logs --channel telegram --lines 120
```

Do not claim success until a fresh Telegram message receives a reply, or the status/logs clearly identify the next failing layer. After success, close successful setup windows and leave only intentional hidden/minimized background persistence.

## Failure Diagnosis

Work from the narrowest layer upward and change one layer at a time:

- Gateway not active: inspect systemd user service, WSL state, and port listener.
- Gateway active but intermittently stopped by WSL: quietly repair background persistence before changing channel config.
- Gateway port listens but WebSocket/HTTP times out: inspect gateway logs, process state, sidecar startup, and resource pressure; do not ask for a bot token yet.
- Telegram not running: inspect `channels status`, startup timing, and narrow Telegram logs.
- `Something went wrong`: channel is alive; diagnose model/tool/agent turn rather than token.
- `access not configured`: approve pairing or fix allowlist.
- Network failures: test direct Telegram API access, then proxy bridge, then gateway service proxy env.
- High CPU or websocket timeout while service is active: inspect whether gateway is still loading channels before changing credentials.

Slow reply triage:

- `lastInboundAt` is null after a fresh user message: Telegram polling, token, network, webhook conflict, pairing, or allowlist is suspect.
- `lastInboundAt` updates quickly and `lastOutboundAt` stays null: Telegram is healthy; inspect OpenClaw agent/model handling.
- `openclaw status` shows the default session just became active, but no outbound exists yet: the turn is likely running or waiting on the model/tool layer.
- Gateway probe is fast while outbound is missing: do not restart gateway immediately; check model auth, model latency, tasks, and recent logs.
- Gateway probe times out during the same period: inspect event-loop stalls, resource pressure, sidecars, and WSL lifetime.

Useful checks:

```bash
openclaw status
openclaw tasks list --json
openclaw tasks audit --json
openclaw logs --plain --limit 240 --timeout 30000
ps -eo pid,ppid,stat,etime,pcpu,pmem,rss,args | grep -E 'openclaw|node' | grep -v grep || true
```

## Telegram-Only Cleanup

If the user wants a clean Telegram-only channel setup:

1. List configured channels without printing secrets.
2. Ask before disabling or removing non-Telegram channels.
3. Remove only the selected channel config and any service drop-ins created solely for that channel.
4. Reload systemd user units and restart gateway once.
5. Wait 90-120 seconds and verify Telegram again.

Never delete unrelated model, proxy, filesystem boundary, auth, memory, gateway, or execution-policy configuration while cleaning chat channels.

## Safety Defaults

- Use local terminal prompts for tokens.
- Prefer token files or OpenClaw secret references over raw config tokens.
- Redact before relaying logs.
- Do not print `~/.openclaw/openclaw.json`.
- Do not use broad grep over credential-bearing config.
- Do not weaken execution policy or filesystem boundaries while setting up Telegram.
- Do not enable automatic model fallback unless the user explicitly chooses it.
- Keep Telegram as the default supported channel for this skill.

