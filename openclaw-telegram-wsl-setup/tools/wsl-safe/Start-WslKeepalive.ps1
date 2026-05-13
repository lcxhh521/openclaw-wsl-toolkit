param(
  [string] $WslDistro = "Ubuntu",
  [string] $KeepaliveSeconds = "2147483647"
)

$ErrorActionPreference = "Stop"

$wsl = Join-Path $env:WINDIR "System32\wsl.exe"
if (-not (Test-Path -LiteralPath $wsl)) {
  throw "wsl.exe not found at $wsl"
}

$pattern = [regex]::Escape("wsl.exe") + ".*" + [regex]::Escape("-d $WslDistro --exec sleep $KeepaliveSeconds")
$existing = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match $pattern }

if ($existing) {
  Write-Host "WSL keepalive already running for $WslDistro"
  $existing | Select-Object ProcessId, ParentProcessId, Name, CommandLine
  exit 0
}

Start-Process `
  -FilePath $wsl `
  -ArgumentList "-d $WslDistro --exec sleep $KeepaliveSeconds" `
  -WindowStyle Hidden

Start-Sleep -Seconds 2

$started = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match $pattern } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine

if (-not $started) {
  throw "WSL keepalive did not remain running for $WslDistro"
}

Write-Host "Started WSL keepalive for $WslDistro"
$started
