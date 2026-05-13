param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "OpenClawWslTools")
)

$ErrorActionPreference = "Stop"

$source = Join-Path $PSScriptRoot "Invoke-WslSafe.ps1"
if (-not (Test-Path -LiteralPath $source)) {
  throw "Invoke-WslSafe.ps1 not found next to installer: $source"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$target = Join-Path $InstallDir "Invoke-WslSafe.ps1"
Copy-Item -LiteralPath $source -Destination $target -Force

Write-Host "Installed Invoke-WslSafe.ps1 to:"
Write-Host $target
Write-Host ""
Write-Host "Example:"
Write-Host "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$target`" -CommandText 'echo ok-from-wsl'"
