$ErrorActionPreference = "Stop"

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = Join-Path $env:LOCALAPPDATA "OpenClawMonitor"

New-Item -ItemType Directory -Force -Path $installDir | Out-Null

$files = @(
    "OpenClawMonitor.cs",
    "OpenClawMonitor.ico",
    "OpenClawMonitorIcon.png",
    "Build-OpenClawMonitor.ps1",
    "Generate-OpenClawMonitorIcon.ps1",
    "Install-Autostart.ps1",
    "Install-WslKeepalive.ps1",
    "Install-UsageCache.ps1",
    "Uninstall-Autostart.ps1",
    "openclaw-usage-cache.mjs",
    "Start-OpenClaw.ps1",
    "Start-OpenClaw.cmd"
)

foreach ($file in $files) {
    Copy-Item -Force -LiteralPath (Join-Path $sourceDir $file) -Destination $installDir
}

& (Join-Path $installDir "Build-OpenClawMonitor.ps1")
& (Join-Path $installDir "Install-Autostart.ps1")

$exe = Join-Path $installDir "OpenClawMonitor.exe"
$icon = Join-Path $installDir "OpenClawMonitor.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
$shell = New-Object -ComObject WScript.Shell

foreach ($folder in @($desktop, $programs)) {
    Get-ChildItem -LiteralPath $folder -Filter "OpenClaw*.lnk" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

foreach ($shortcutPath in @(
    (Join-Path $desktop "OpenClaw Control.lnk"),
    (Join-Path $programs "OpenClaw Control.lnk")
)) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $installDir
    if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = $icon }
    $shortcut.Description = "OpenClaw local control center"
    $shortcut.Save()
}

Start-Process -FilePath $exe

Write-Host "OpenClaw Control installed and started:"
Write-Host $exe
