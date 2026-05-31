param(
  [string]$CommandText,

  [string]$CommandFile,

  [ValidateSet("bash", "python3")]
  [string]$Interpreter = "bash",

  [string]$Distro = "Ubuntu",

  [switch]$KeepRemote
)

$ErrorActionPreference = "Stop"

function Invoke-WslWithUtf8Input {
  param(
    [Parameter(Mandatory = $true)]
    [string[]] $Arguments,

    [Parameter(Mandatory = $true)]
    [string] $InputText
  )

  function Quote-ProcessArgument {
    param([string] $Value)
    if ($Value -notmatch '[\s"]') {
      return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
  }

  $argLine = (($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " ")
  $lastStdout = ""
  $lastStderr = ""
  $lastExitCode = 1

  for ($attempt = 1; $attempt -le 3; $attempt++) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Join-Path $env:WINDIR "System32\wsl.exe")
    $psi.Arguments = $argLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($null -eq $proc) {
      $lastStdout = ""
      $lastStderr = "failed to start wsl.exe"
      $lastExitCode = 1
    } else {
      $proc.StandardInput.Write($InputText)
      $proc.StandardInput.Close()
      $lastStdout = $proc.StandardOutput.ReadToEnd()
      $lastStderr = $proc.StandardError.ReadToEnd()
      $proc.WaitForExit()
      $lastExitCode = $proc.ExitCode
    }

    if ($lastExitCode -eq 0) {
      if ($lastStdout) {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        [Console]::Out.Write($lastStdout)
      }
      if ($lastStderr) {
        [Console]::Error.Write($lastStderr)
      }
      return 0
    }

    $combined = ("$lastStdout`n$lastStderr") -replace "`0", ""
    $looksTransient = $lastExitCode -eq -1 -or $combined -match 'WSL_E_DISTRO_NOT_FOUND|DISTRO_NOT_FOUND|distro.*not.*found|Access is denied|拒绝访问'
    if ($attempt -lt 3 -and $looksTransient) {
      Start-Sleep -Seconds (2 * $attempt)
      continue
    }
    break
  }

  if ($lastStdout) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::Out.Write($lastStdout)
  }
  if ($lastStderr) {
    [Console]::Error.Write($lastStderr)
  }
  return $lastExitCode
}

if (($CommandText -and $CommandFile) -or (-not $CommandText -and -not $CommandFile)) {
  throw "Provide exactly one of -CommandText or -CommandFile."
}

$extension = if ($Interpreter -eq "python3") { ".py" } else { ".sh" }
$id = [Guid]::NewGuid().ToString("N")
$localPath = Join-Path $env:TEMP "codex-wsl-safe-$id$extension"
$remoteDir = "/tmp/codex_wsl_safe"
$remotePath = "$remoteDir/codex-wsl-safe-$id$extension"
$uncDir = "\\wsl.localhost\$Distro\tmp\codex_wsl_safe"
$uncPath = "$uncDir\codex-wsl-safe-$id$extension"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
if ($CommandFile) {
  $CommandText = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $CommandFile), [System.Text.Encoding]::UTF8)
}
$CommandText = $CommandText -replace "`r`n", "`n"
$CommandText = $CommandText -replace "`r", "`n"
[System.IO.File]::WriteAllText($localPath, $CommandText, $utf8NoBom)

$copiedRemote = $false
try {
  & wsl -d $Distro -- bash -lc "mkdir -p $remoteDir"
  if ($LASTEXITCODE -ne 0) {
    throw "failed to create remote temp dir through wsl.exe"
  }
  Copy-Item -LiteralPath $localPath -Destination $uncPath -Force
  $copiedRemote = $true
}
catch {
  $copiedRemote = $false
}

$exitCode = 0
try {
  if ($copiedRemote) {
    if ($Interpreter -eq "python3") {
      & wsl -d $Distro -- python3 $remotePath
    } else {
      & wsl -d $Distro -- bash $remotePath
    }
    $exitCode = $LASTEXITCODE
  } elseif ($Interpreter -eq "python3") {
    $exitCode = Invoke-WslWithUtf8Input -Arguments @("-d", $Distro, "--", "python3", "-") -InputText $CommandText
  } else {
    $exitCode = Invoke-WslWithUtf8Input -Arguments @("-d", $Distro, "--", "bash", "-s") -InputText $CommandText
  }
}
finally {
  Remove-Item -LiteralPath $localPath -Force -ErrorAction SilentlyContinue
  if ($copiedRemote -and -not $KeepRemote) {
    & wsl -d $Distro -- rm -f $remotePath 2>$null
  }
}

exit $exitCode
