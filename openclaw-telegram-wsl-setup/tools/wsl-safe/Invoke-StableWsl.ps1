param(
  [string] $Command,
  [string] $WslDistro = "Ubuntu",
  [int] $TimeoutMilliseconds = 30000,
  [switch] $Health
)

$ErrorActionPreference = "Stop"

if (-not $Health -and [string]::IsNullOrWhiteSpace($Command)) {
  throw "Provide -Command unless using -Health."
}

function Get-NowIso {
  return (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
}

function Get-StableWslExe {
  $candidates = @(
    "C:\Windows\System32\wsl.exe",
    (Join-Path $env:SystemRoot "System32\wsl.exe"),
    (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\wsl.exe")
  )
  foreach ($candidate in $candidates) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate)) {
      return $candidate
    }
  }
  throw "wsl.exe not found in stable candidates."
}

function Write-StableWslState($Value) {
  $statePath = Join-Path $PSScriptRoot "stable-wsl-state.json"
  $json = $Value | ConvertTo-Json -Depth 20
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($statePath, $json + "`n", $encoding)
}

function Invoke-System32WslList {
  $wsl = Get-StableWslExe
  $output = & $wsl --list --verbose
  return [pscustomobject]@{
    ExitCode = if ($?) { 0 } elseif ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }
    Output = ($output -join "`n")
  }
}

function Invoke-StableWslBash {
  param(
    [Parameter(Mandatory = $true)][string] $BashCommand,
    [Parameter(Mandatory = $true)][string] $Distro
  )

  $wsl = Get-StableWslExe
  $output = & $wsl -d $Distro -- bash -lc $BashCommand 2>&1
  $exitCode = if ($?) { 0 } elseif ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }
  return [pscustomobject]@{
    ExitCode = $exitCode
    Output = ($output -join "`n")
    Error = if ($exitCode -eq 0) { "" } else { ($output -join "`n") }
  }
}

try {
  $list = Invoke-System32WslList
  $probe = Invoke-StableWslBash -BashCommand "true" -Distro $WslDistro
  if ($probe.ExitCode -ne 0) {
    $state = [ordered]@{
      status = "distro_not_visible"
      updated_at = Get-NowIso
      wsl_distro = $WslDistro
      command = $Command
      list_exit_code = $list.ExitCode
      distro_probe_exit_code = $probe.ExitCode
      distro_probe_error = $probe.Error
      list_output = $list.Output
      recommendation = "Use System32 wsl.exe from the same Windows user context; do not trust bare wsl from a sandboxed command context."
    }
    Write-StableWslState $state
    Write-Output "WSL distro '$WslDistro' is not visible through System32 wsl.exe."
    exit 2
  }

  if ($Health) {
    $probe = Invoke-StableWslBash -BashCommand "printf '%s\n' stable-wsl-ok; id -un; pwd" -Distro $WslDistro
    $state = [ordered]@{
      status = if ($probe.ExitCode -eq 0) { "ok" } else { "probe_failed" }
      updated_at = Get-NowIso
      wsl_distro = $WslDistro
      list_exit_code = $list.ExitCode
      probe_exit_code = $probe.ExitCode
      probe_output = $probe.Output
      probe_error = $probe.Error
    }
    Write-StableWslState $state
    Write-Output $probe.Output
    if ($probe.ExitCode -ne 0) {
      Write-Error $probe.Error
    }
    exit $probe.ExitCode
  }

  $result = Invoke-StableWslBash -BashCommand $Command -Distro $WslDistro
  $state = [ordered]@{
    status = if ($result.ExitCode -eq 0) { "ok" } else { "command_failed" }
    updated_at = Get-NowIso
    wsl_distro = $WslDistro
    command = $Command
    exit_code = $result.ExitCode
    stderr = $result.Error
  }
  Write-StableWslState $state
  Write-Output $result.Output
  if ($result.ExitCode -ne 0) {
    Write-Error $result.Error
  }
  exit $result.ExitCode
} catch {
  $state = [ordered]@{
    status = "wrapper_error"
    updated_at = Get-NowIso
    wsl_distro = $WslDistro
    command = $Command
    error = $_.Exception.Message
  }
  Write-StableWslState $state
  throw
}
