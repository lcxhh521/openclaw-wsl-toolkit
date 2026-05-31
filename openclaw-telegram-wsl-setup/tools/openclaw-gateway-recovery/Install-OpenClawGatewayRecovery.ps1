param(
    [string]$Distro = "Ubuntu",
    [ValidateSet("observe", "recover-start", "recover-restart")]
    [string]$Mode = "observe",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $toolDir "openclaw-gateway-recovery"
$servicePath = Join-Path $toolDir "openclaw-gateway-recovery.service"
$timerPath = Join-Path $toolDir "openclaw-gateway-recovery.timer"

foreach ($path in @($scriptPath, $servicePath, $timerPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing required file: $path"
    }
}

function Invoke-WslBash {
    param([Parameter(Mandatory=$true)][string]$Command)
    & wsl.exe -d $Distro -- bash -lc $Command
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code $LASTEXITCODE"
    }
}

function Copy-ToWslFile {
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$TargetPath
    )

    $content = [System.IO.File]::ReadAllText($SourcePath, [System.Text.Encoding]::UTF8)
    $content = $content -replace "`r`n", "`n"
    $content = $content -replace "`r", "`n"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "wsl.exe"
    $psi.Arguments = "-d $Distro -- bash -lc ""cat > $TargetPath"""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Write($content)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) {
        throw "Failed to copy $SourcePath to WSL $TargetPath. $stdout $stderr"
    }
}

Write-Host "OpenClaw Gateway Recovery installer"
Write-Host "Distro: $Distro"
Write-Host "Mode: $Mode"
Write-Host "Apply: $Apply"
Write-Host ""
Write-Host "This installs a separate WSL user timer:"
Write-Host "  ~/.local/bin/openclaw-gateway-recovery"
Write-Host "  ~/.config/systemd/user/openclaw-gateway-recovery.service"
Write-Host "  ~/.config/systemd/user/openclaw-gateway-recovery.timer"
Write-Host "  ~/.config/openclaw-gateway-recovery.env"
Write-Host ""
Write-Host "Modes:"
Write-Host "  observe        : record only; no service changes"
Write-Host "  recover-start : start openclaw-gateway.service when it is inactive/failed"
Write-Host "  recover-restart: also restart after confirmed probe failure and cooldown"
Write-Host ""

if (-not $Apply) {
    Write-Host "Dry run only. Re-run with -Apply to install."
    exit 0
}

Invoke-WslBash "set -e; mkdir -p ~/.local/bin ~/.config/systemd/user ~/.config"
Copy-ToWslFile -SourcePath $scriptPath -TargetPath "~/.local/bin/openclaw-gateway-recovery"
Copy-ToWslFile -SourcePath $servicePath -TargetPath "~/.config/systemd/user/openclaw-gateway-recovery.service"
Copy-ToWslFile -SourcePath $timerPath -TargetPath "~/.config/systemd/user/openclaw-gateway-recovery.timer"

$envContent = @"
OPENCLAW_GATEWAY_RECOVERY_MODE=$Mode
OPENCLAW_GATEWAY_RECOVERY_COOLDOWN_SECONDS=900
OPENCLAW_GATEWAY_RECOVERY_CONFIRM_COUNT=2
OPENCLAW_GATEWAY_RECOVERY_STARTUP_GRACE_SECONDS=240
OPENCLAW_GATEWAY_RECOVERY_MAX_ACTIONS_PER_HOUR=3
"@
$tempEnv = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tempEnv, $envContent, [System.Text.Encoding]::UTF8)
    Copy-ToWslFile -SourcePath $tempEnv -TargetPath "~/.config/openclaw-gateway-recovery.env"
}
finally {
    Remove-Item -LiteralPath $tempEnv -Force -ErrorAction SilentlyContinue
}

Invoke-WslBash "set -e; chmod +x ~/.local/bin/openclaw-gateway-recovery; systemctl --user daemon-reload; systemctl --user enable --now openclaw-gateway-recovery.timer; systemctl --user start openclaw-gateway-recovery.service; systemctl --user status openclaw-gateway-recovery.timer --no-pager"

Write-Host ""
Write-Host "Installed OpenClaw Gateway Recovery."
Write-Host "Logs inside WSL: ~/.cache/openclaw-gateway-recovery/recovery.log"
Write-Host "Status inside WSL: ~/.cache/openclaw-gateway-recovery/status.json"
Write-Host "Change mode by editing ~/.config/openclaw-gateway-recovery.env, then run:"
Write-Host "  systemctl --user restart openclaw-gateway-recovery.timer"
