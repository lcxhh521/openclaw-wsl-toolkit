$ErrorActionPreference = "Stop"

function Get-OpenClawWslExe {
  $path = Join-Path $env:WINDIR "System32\wsl.exe"
  if (-not (Test-Path -LiteralPath $path)) {
    throw "wsl.exe not found at $path"
  }
  return $path
}

function ConvertTo-BashSingleQuoted([string] $Text) {
  if ($Text.Contains("'")) {
    throw "Single quotes are not supported in WSL paths: $Text"
  }
  return "'" + $Text + "'"
}

function Get-WslParentPath([string] $Path) {
  $normalized = $Path.Replace("\", "/").TrimEnd("/")
  $index = $normalized.LastIndexOf("/")
  if ($index -le 0) {
    return "."
  }
  return $normalized.Substring(0, $index)
}

function Invoke-OpenClawWslBash {
  param(
    [Parameter(Mandatory = $true)][string] $Command,
    [string] $WslDistro = "Ubuntu",
    [int] $TimeoutMilliseconds = 30000
  )

  $wsl = Get-OpenClawWslExe
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $wsl
  $psi.Arguments = "-d $WslDistro -- bash -lc ""$Command"""
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false

  $process = [System.Diagnostics.Process]::Start($psi)
  try {
    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
      try { $process.Kill() } catch {}
      throw "Timed out running WSL command in distro $WslDistro"
    }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    return [pscustomobject]@{
      ExitCode = $process.ExitCode
      Output = $stdout
      Error = $stderr
    }
  } finally {
    $process.Dispose()
  }
}

function Write-OpenClawWslFileBytes {
  param(
    [Parameter(Mandatory = $true)][string] $TargetPath,
    [Parameter(Mandatory = $true)][byte[]] $Bytes,
    [string] $WslDistro = "Ubuntu",
    [int] $TimeoutMilliseconds = 30000
  )

  $quotedTarget = ConvertTo-BashSingleQuoted $TargetPath
  $quotedParent = ConvertTo-BashSingleQuoted (Get-WslParentPath $TargetPath)
  $command = "mkdir -p -- $quotedParent; cat > $quotedTarget"
  $wsl = Get-OpenClawWslExe
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $wsl
  $psi.Arguments = "-d $WslDistro -- bash -lc ""$command"""
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false

  $process = [System.Diagnostics.Process]::Start($psi)
  try {
    $process.StandardInput.BaseStream.Write($Bytes, 0, $Bytes.Length)
    $process.StandardInput.Close()
    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
      try { $process.Kill() } catch {}
      throw "Timed out writing $TargetPath in WSL distro $WslDistro"
    }
    $stderr = $process.StandardError.ReadToEnd()
    if ($process.ExitCode -ne 0) {
      throw "Failed to write $TargetPath in WSL distro $WslDistro. $stderr"
    }
  } finally {
    $process.Dispose()
  }
}

function Write-OpenClawWslFileUtf8 {
  param(
    [Parameter(Mandatory = $true)][string] $TargetPath,
    [Parameter(Mandatory = $true)][string] $Content,
    [string] $WslDistro = "Ubuntu",
    [int] $TimeoutMilliseconds = 30000
  )

  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
  Write-OpenClawWslFileBytes `
    -TargetPath $TargetPath `
    -Bytes $bytes `
    -WslDistro $WslDistro `
    -TimeoutMilliseconds $TimeoutMilliseconds
}

function Copy-OpenClawLocalFileToWsl {
  param(
    [Parameter(Mandatory = $true)][string] $SourcePath,
    [Parameter(Mandatory = $true)][string] $TargetPath,
    [string] $WslDistro = "Ubuntu",
    [int] $TimeoutMilliseconds = 30000
  )

  $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $SourcePath).Path)
  Write-OpenClawWslFileBytes `
    -TargetPath $TargetPath `
    -Bytes $bytes `
    -WslDistro $WslDistro `
    -TimeoutMilliseconds $TimeoutMilliseconds
}
