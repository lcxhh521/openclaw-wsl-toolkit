<#
Example Windows Scheduled Task installer for the Codex-side mailbox watcher.
Edit paths and environment values before running. This file contains no secrets.

It runs every 5 minutes, hidden, and relies on codex-mailbox-watch.py's own
per-seq cooldown/max-attempt lock to avoid duplicate wakeups.
#>

param(
  [string]$TaskName = "AgentCollabCodexMailboxWatch",
  [string]$PythonExe = "python",
  [string]$WatcherScript = "C:\\path\\to\\agent-collab\\scripts\\codex-mailbox-watch.py",
  [string]$MailboxDir = "C:\\path\\to\\agent-mailbox",
  [string]$WakeCommand = "cmd /c echo Codex turn {seq} waiting. Read {inbox}; write {outbox}; update {turn}."
)

$envPrefix = "set AGENT_MAILBOX_DIR=$MailboxDir&& set CODEX_WAKE_COMMAND=$WakeCommand&& "
$argument = "/c " + $envPrefix + "`"$PythonExe`" `"$WatcherScript`""

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered scheduled task: $TaskName"
