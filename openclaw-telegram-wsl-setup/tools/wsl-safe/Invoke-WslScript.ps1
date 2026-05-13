param(
  [Parameter(Mandatory = $true)]
  [string] $LocalScriptPath,
  [string] $WslDistro = "Ubuntu",
  [string] $RemoteRoot = "/tmp/codex-wsl-scripts",
  [int] $TimeoutMilliseconds = 60000
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "WslUtf8Bridge.ps1")

function ConvertTo-BashSingleQuotedLocal([string] $Text) {
  if ($Text.Contains("'")) {
    throw "Single quotes are not supported in WSL script paths: $Text"
  }
  return "'" + $Text + "'"
}

$resolved = (Resolve-Path -LiteralPath $LocalScriptPath).Path
$leaf = Split-Path -Leaf $resolved
$stamp = (Get-Date).ToString("yyyyMMdd-HHmmss-ffff")
$remotePath = ($RemoteRoot.TrimEnd("/") + "/" + $stamp + "-" + $leaf)

Copy-OpenClawLocalFileToWsl `
  -SourcePath $resolved `
  -TargetPath $remotePath `
  -WslDistro $WslDistro `
  -TimeoutMilliseconds $TimeoutMilliseconds

$quotedRemote = ConvertTo-BashSingleQuotedLocal $remotePath
$command = "chmod 700 -- $quotedRemote; bash $quotedRemote"
$result = Invoke-OpenClawWslBash `
  -Command $command `
  -WslDistro $WslDistro `
  -TimeoutMilliseconds $TimeoutMilliseconds

Write-Output $result.Output
if ($result.ExitCode -ne 0) {
  Write-Error $result.Error
}
exit $result.ExitCode
