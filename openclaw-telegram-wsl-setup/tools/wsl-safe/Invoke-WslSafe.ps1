param(
  [string]$CommandText,

  [string]$CommandFile,

  [ValidateSet("bash", "python3")]
  [string]$Interpreter = "bash",

  [string]$Distro = "Ubuntu",

  [switch]$KeepRemote
)

$ErrorActionPreference = "Stop"

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

& wsl -d $Distro -- bash -lc "mkdir -p $remoteDir"
Copy-Item -LiteralPath $localPath -Destination $uncPath -Force

$exitCode = 0
try {
  if ($Interpreter -eq "python3") {
    & wsl -d $Distro -- python3 $remotePath
  } else {
    & wsl -d $Distro -- bash $remotePath
  }
  $exitCode = $LASTEXITCODE
}
finally {
  Remove-Item -LiteralPath $localPath -Force -ErrorAction SilentlyContinue
  if (-not $KeepRemote) {
    & wsl -d $Distro -- rm -f $remotePath 2>$null
  }
}

exit $exitCode
