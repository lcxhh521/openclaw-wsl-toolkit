param(
  [string]$Distro = "Ubuntu",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Wsl = Join-Path $env:WINDIR "System32\wsl.exe"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

$script = @'
set -euo pipefail
mkdir -p "$HOME/.openclaw/workspace/codex-main-bridge/agent-room/tools" "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/openclaw-agent-room-retention-prune.service" <<'UNIT'
[Unit]
Description=OpenClaw Agent Room retention prune

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 %h/.openclaw/workspace/codex-main-bridge/agent-room/tools/agent_room_retention_prune.py --apply
UNIT
cat > "$HOME/.config/systemd/user/openclaw-agent-room-retention-prune.timer" <<'UNIT'
[Unit]
Description=Run OpenClaw Agent Room retention prune hourly

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now openclaw-agent-room-retention-prune.timer
python3 "$HOME/.openclaw/workspace/codex-main-bridge/agent-room/tools/agent_room_retention_prune.py"
systemctl --user status openclaw-agent-room-retention-prune.timer --no-pager
'@

if ($DryRun) {
  Write-Host "Would install agent_room_retention_prune.py and enable openclaw-agent-room-retention-prune.timer in $Distro."
  exit 0
}

$target = "\\wsl.localhost\$Distro\home"
if (-not (Test-Path -LiteralPath $target)) {
  throw "Cannot access \\wsl.localhost\$Distro. Start WSL or use tools/wsl-safe/Invoke-WslSafe.ps1 first."
}

$linuxUser = (& $Wsl -d $Distro -- bash -lc "id -un").Trim()
$toolDir = "\\wsl.localhost\$Distro\home\$linuxUser\.openclaw\workspace\codex-main-bridge\agent-room\tools"
New-Item -ItemType Directory -Force -Path $toolDir | Out-Null
Copy-Item -LiteralPath (Join-Path $Here "agent_room_retention_prune.py") -Destination (Join-Path $toolDir "agent_room_retention_prune.py") -Force

& $Wsl -d $Distro -- bash -lc $script
if ($LASTEXITCODE -ne 0) {
  throw "installer failed with exit code $LASTEXITCODE"
}
