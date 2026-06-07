param(
  [string]$Distro = "Ubuntu",
  [string]$TargetRoot = "E:\WSL",
  [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

function Get-StableWslExe {
  $path = Join-Path $env:WINDIR "System32\wsl.exe"
  if (-not (Test-Path -LiteralPath $path)) {
    throw "wsl.exe not found at $path"
  }
  return $path
}

$wsl = Get-StableWslExe
$target = Join-Path $TargetRoot $Distro

Write-Host "WSL distro: $Distro"
Write-Host "Target root: $target"
Write-Host "This uses 'wsl --manage <distro> --move <target>' and does not export/import secrets."

if ($WhatIf) {
  Write-Host "[what-if] Would create $target, shut down WSL, move the distro, and verify it."
  exit 0
}

New-Item -ItemType Directory -Force -Path $target | Out-Null

Write-Host "Stopping WSL before moving the VHDX..."
& $wsl --shutdown
Start-Sleep -Seconds 3

Write-Host "Moving distro. This can take a while and may appear stuck near the end while the VHDX is finalized."
& $wsl --manage $Distro --move $target
if ($LASTEXITCODE -ne 0) {
  throw "wsl --manage move failed with exit code $LASTEXITCODE"
}

Write-Host "Verifying moved distro..."
& $wsl -d $Distro -- bash -lc "printf '%s\n' moved-wsl-ok; id -un; df -h /"
if ($LASTEXITCODE -ne 0) {
  throw "moved distro verification failed with exit code $LASTEXITCODE"
}

Write-Host "Done. If free space is not returned to C: immediately, run Windows Disk Cleanup/Storage Sense or compact the old VHDX path if it still exists."
