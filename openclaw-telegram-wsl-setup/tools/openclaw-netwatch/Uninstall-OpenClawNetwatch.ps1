param(
    [string]$Distro = "Ubuntu",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

Write-Host "OpenClaw Netwatch uninstall"
Write-Host "Distro: $Distro"
Write-Host "Apply: $Apply"
Write-Host ""
Write-Host "This will disable the user timer and remove:"
Write-Host "  ~/.local/bin/openclaw-netwatch"
Write-Host "  ~/.config/systemd/user/openclaw-netwatch.service"
Write-Host "  ~/.config/systemd/user/openclaw-netwatch.timer"
Write-Host "  ~/.config/openclaw-netwatch.env"
Write-Host ""

if (-not $Apply) {
    Write-Host "Dry run only. Re-run with -Apply to uninstall."
    exit 0
}

& wsl.exe -d $Distro -- bash -lc @"
set -e
systemctl --user disable --now openclaw-netwatch.timer 2>/dev/null || true
rm -f ~/.local/bin/openclaw-netwatch
rm -f ~/.config/systemd/user/openclaw-netwatch.service
rm -f ~/.config/systemd/user/openclaw-netwatch.timer
rm -f ~/.config/openclaw-netwatch.env
systemctl --user daemon-reload
"@

if ($LASTEXITCODE -ne 0) {
    throw "WSL uninstall command failed with exit code $LASTEXITCODE"
}

Write-Host "Removed OpenClaw Netwatch."
