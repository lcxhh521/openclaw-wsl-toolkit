param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "OpenClawWslTools")
)

$ErrorActionPreference = "Stop"

$tools = @(
  "Invoke-WslSafe.ps1",
  "Invoke-StableWsl.ps1",
  "WslUtf8Bridge.ps1",
  "Invoke-WslScript.ps1",
  "Start-WslKeepalive.ps1"
)

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

foreach ($tool in $tools) {
  $source = Join-Path $PSScriptRoot $tool
  if (-not (Test-Path -LiteralPath $source)) {
    throw "$tool not found next to installer: $source"
  }
  Copy-Item -LiteralPath $source -Destination (Join-Path $InstallDir $tool) -Force
}

Write-Host "Installed OpenClaw WSL helper scripts to:"
Write-Host $InstallDir
Write-Host ""
Write-Host "Examples:"
Write-Host "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Invoke-WslSafe.ps1`" -CommandText 'echo ok-from-wsl'"
Write-Host "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Invoke-StableWsl.ps1`" -Health"
Write-Host "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Invoke-WslScript.ps1`" -LocalScriptPath .\script.sh"
