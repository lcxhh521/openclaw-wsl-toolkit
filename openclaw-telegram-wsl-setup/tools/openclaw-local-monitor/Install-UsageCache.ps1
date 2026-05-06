$ErrorActionPreference = "Stop"

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$collector = Join-Path $sourceDir "openclaw-usage-cache.mjs"

if (-not (Test-Path -LiteralPath $collector)) {
    throw "Missing collector: $collector"
}

$wslHome = (& wsl.exe -d Ubuntu -- bash -lc 'printf %s "$HOME"').Trim()
if (-not $wslHome) { throw "Failed to resolve WSL HOME." }

$uncHome = "\\wsl.localhost\Ubuntu" + ($wslHome -replace '/', '\')
$uncInstallDir = Join-Path $uncHome ".local\share\openclaw-local-monitor"
New-Item -ItemType Directory -Force -Path $uncInstallDir | Out-Null
Copy-Item -Force -LiteralPath $collector -Destination (Join-Path $uncInstallDir "openclaw-usage-cache.mjs")

$script = @'
set -e
install_dir="$HOME/.local/share/openclaw-local-monitor"
bin_dir="$HOME/.local/bin"
systemd_dir="$HOME/.config/systemd/user"
mkdir -p "$install_dir" "$bin_dir" "$systemd_dir" "$HOME/.openclaw/monitor-cache"
chmod 0644 "$install_dir/openclaw-usage-cache.mjs"
cat > "$bin_dir/openclaw-usage-cache" <<'EOF'
#!/usr/bin/env bash
exec node "$HOME/.local/share/openclaw-local-monitor/openclaw-usage-cache.mjs"
EOF
chmod 0755 "$bin_dir/openclaw-usage-cache"
cat > "$systemd_dir/openclaw-usage-cache.service" <<'EOF'
[Unit]
Description=OpenClaw local monitor usage cache refresh

[Service]
Type=oneshot
ExecStart=%h/.local/bin/openclaw-usage-cache
Nice=10
EOF
cat > "$systemd_dir/openclaw-usage-cache.timer" <<'EOF'
[Unit]
Description=Refresh OpenClaw local monitor usage cache

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
AccuracySec=1min
Unit=openclaw-usage-cache.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now openclaw-usage-cache.timer
systemctl --user start openclaw-usage-cache.service
systemctl --user status openclaw-usage-cache.service --no-pager --lines=20 || true
'@

$installScriptPath = Join-Path $uncInstallDir "install-usage-cache.sh"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($installScriptPath, ($script -replace "`r`n", "`n"), $utf8NoBom)
wsl.exe -d Ubuntu -- bash "$wslHome/.local/share/openclaw-local-monitor/install-usage-cache.sh"
Write-Host "Installed OpenClaw usage cache timer."
