param(
    [string]$Distro = "Ubuntu",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $toolDir "openclaw-netwatch"
$servicePath = Join-Path $toolDir "openclaw-netwatch.service"
$timerPath = Join-Path $toolDir "openclaw-netwatch.timer"

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

Write-Host "OpenClaw Netwatch installer"
Write-Host "Distro: $Distro"
Write-Host "Mode: Observe-only"
Write-Host "Apply: $Apply"
Write-Host ""
Write-Host "This will install a WSL user timer:"
Write-Host "  ~/.local/bin/openclaw-netwatch"
Write-Host "  ~/.config/systemd/user/openclaw-netwatch.service"
Write-Host "  ~/.config/systemd/user/openclaw-netwatch.timer"
Write-Host "  ~/.config/openclaw-netwatch.env"
Write-Host ""

if (-not $Apply) {
    Write-Host "Dry run only. Re-run with -Apply to install."
    Write-Host "OpenClaw Network Observer / Netwatch only observes and records recovery recommendations; it never restarts gateway."
    exit 0
}

Invoke-WslBash "set -e; mkdir -p ~/.local/bin ~/.config/systemd/user ~/.config"
Copy-ToWslFile -SourcePath $scriptPath -TargetPath "~/.local/bin/openclaw-netwatch"
Copy-ToWslFile -SourcePath $servicePath -TargetPath "~/.config/systemd/user/openclaw-netwatch.service"
Copy-ToWslFile -SourcePath $timerPath -TargetPath "~/.config/systemd/user/openclaw-netwatch.timer"

$envContent = @"
OPENCLAW_NETWATCH_MODE=observe
OPENCLAW_NETWATCH_COOLDOWN_SECONDS=300
OPENCLAW_NETWATCH_OFFLINE_CONFIRM_COUNT=2
OPENCLAW_NETWATCH_GATEWAY_FAIL_CONFIRM_COUNT=2
OPENCLAW_NETWATCH_GATEWAY_STARTUP_GRACE_SECONDS=240
"@
$tempEnv = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tempEnv, $envContent, [System.Text.Encoding]::UTF8)
    Copy-ToWslFile -SourcePath $tempEnv -TargetPath "~/.config/openclaw-netwatch.env"
}
finally {
    Remove-Item -LiteralPath $tempEnv -Force -ErrorAction SilentlyContinue
}

Invoke-WslBash "set -e; chmod +x ~/.local/bin/openclaw-netwatch; systemctl --user daemon-reload; systemctl --user enable --now openclaw-netwatch.timer; systemctl --user status openclaw-netwatch.timer --no-pager"

Write-Host ""
Write-Host "Installed OpenClaw Netwatch."
Write-Host "Logs inside WSL: ~/.cache/openclaw-netwatch/watchdog.log"
Write-Host "Mode can be changed by editing ~/.config/openclaw-netwatch.env, then running:"
Write-Host "  systemctl --user restart openclaw-netwatch.timer"
