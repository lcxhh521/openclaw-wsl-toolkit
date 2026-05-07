param(
    [string]$Distro = "Ubuntu",
    [string]$Name = "OpenClaw WSL Keepalive"
)

$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$vbsPath = Join-Path $startup ($Name + ".vbs")
$cmdPath = Join-Path $startup ($Name + ".cmd")
$disabledCmdPath = Join-Path $startup ($Name + ".cmd.disabled")

$bash = "if pgrep -af '^openclaw-keepalive-anchor( |$)' >/dev/null; then systemctl --user start openclaw-gateway.service; exit 0; fi; systemctl --user start openclaw-gateway.service; exec -a openclaw-keepalive-anchor sleep infinity"
$command = 'wsl.exe -d ' + $Distro + ' -- bash -lc "' + $bash + '"'
$content = @(
    'Set shell = CreateObject("WScript.Shell")',
    'shell.Run "' + $command.Replace('"', '""') + '", 0, False'
)

Set-Content -LiteralPath $vbsPath -Value $content -Encoding ASCII

if (Test-Path -LiteralPath $cmdPath) {
    Move-Item -LiteralPath $cmdPath -Destination $disabledCmdPath -Force
}

Write-Host "Installed hidden WSL keepalive:"
Write-Host $vbsPath

if (Test-Path -LiteralPath $disabledCmdPath) {
    Write-Host "Disabled visible keepalive:"
    Write-Host $disabledCmdPath
}
