$ErrorActionPreference = "Stop"

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$observer = Join-Path $sourceDir "openclaw-reliability-observer.mjs"

if (-not (Test-Path -LiteralPath $observer)) {
    throw "Missing observer: $observer"
}

$wslHome = (& wsl.exe -d Ubuntu -- bash -lc 'printf %s "$HOME"').Trim()
if (-not $wslHome) { throw "Failed to resolve WSL HOME." }

$uncHome = "\\wsl.localhost\Ubuntu" + ($wslHome -replace '/', '\')
$uncInstallDir = Join-Path $uncHome ".local\share\openclaw-local-monitor"
New-Item -ItemType Directory -Force -Path $uncInstallDir | Out-Null
Copy-Item -Force -LiteralPath $observer -Destination (Join-Path $uncInstallDir "openclaw-reliability-observer.mjs")

$script = @'
set -e
install_dir="$HOME/.local/share/openclaw-local-monitor"
bin_dir="$HOME/.local/bin"
systemd_dir="$HOME/.config/systemd/user"
mkdir -p "$install_dir" "$bin_dir" "$systemd_dir" "$HOME/.openclaw/monitor-cache"
chmod 0644 "$install_dir/openclaw-reliability-observer.mjs"
cat > "$bin_dir/openclaw-reliability-observer" <<'EOF'
#!/usr/bin/env bash
exec node "$HOME/.local/share/openclaw-local-monitor/openclaw-reliability-observer.mjs"
EOF
chmod 0755 "$bin_dir/openclaw-reliability-observer"
cat > "$systemd_dir/openclaw-reliability-observer.service" <<'EOF'
[Unit]
Description=OpenClaw local monitor reliability observer

[Service]
Type=oneshot
ExecStart=%h/.local/bin/openclaw-reliability-observer
Nice=10
EOF
cat > "$systemd_dir/openclaw-reliability-observer.timer" <<'EOF'
[Unit]
Description=Refresh OpenClaw local monitor reliability status

[Timer]
OnBootSec=90s
OnUnitActiveSec=60s
AccuracySec=15s
Unit=openclaw-reliability-observer.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now openclaw-reliability-observer.timer
systemctl --user start openclaw-reliability-observer.service
systemctl --user status openclaw-reliability-observer.service --no-pager --lines=20 || true
'@

$installScriptPath = Join-Path $uncInstallDir "install-reliability-observer.sh"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($installScriptPath, ($script -replace "`r`n", "`n"), $utf8NoBom)
wsl.exe -d Ubuntu -- bash "$wslHome/.local/share/openclaw-local-monitor/install-reliability-observer.sh"
Write-Host "Installed OpenClaw reliability observer timer."
