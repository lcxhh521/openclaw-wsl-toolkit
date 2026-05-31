param(
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Continue"

function New-ConnectJsonEnvelope {
  param([string]$Json)
  $payload = [System.Text.Encoding]::UTF8.GetBytes($Json)
  $len = $payload.Length
  $bytes = New-Object byte[] (5 + $len)
  $bytes[0] = 0
  $bytes[1] = [byte](($len -shr 24) -band 0xff)
  $bytes[2] = [byte](($len -shr 16) -band 0xff)
  $bytes[3] = [byte](($len -shr 8) -band 0xff)
  $bytes[4] = [byte]($len -band 0xff)
  [Array]::Copy($payload, 0, $bytes, 5, $len)
  return $bytes
}

function ConvertTo-SafeBody {
  param([object]$Content, [string]$Token = "")
  if ($Content -is [byte[]]) {
    $text = [System.Text.Encoding]::UTF8.GetString($Content)
  } else {
    $text = [string]$Content
  }
  if ($Token) {
    $text = $text -replace [regex]::Escape($Token), '<redacted-token>'
  }
  $text = $text -replace '[A-Za-z0-9_\-]{30,}', '<redacted-long-value>'
  if ($text.Length -gt 500) { $text = $text.Substring(0, 500) }
  return $text
}

$startedAt = (Get-Date).ToString("o")
$antigravityCmd = 'D:\Antigravity\bin\antigravity.cmd'
$report = [ordered]@{
  probe = "antigravity_transport_probe"
  started_at = $startedAt
  mode = "dry_run_no_prompt_no_telegram_no_production_routing"
  antigravity_cmd_exists = (Test-Path -LiteralPath $antigravityCmd)
  extension_server_heartbeat = @()
  chat_client_stream = @()
  conclusion = [ordered]@{}
}

if (-not $report.antigravity_cmd_exists) {
  $report.conclusion = [ordered]@{
    status = "blocked"
    reason = "antigravity_cmd_missing"
  }
  $json = $report | ConvertTo-Json -Depth 8
  if ($OutputPath) {
    $dir = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
  }
  Write-Output $json
  exit 0
}

$processInfos = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like 'language_server_windows_x*' -or $_.CommandLine -like '*language_server_windows_x64*' }

$seenExtensionPorts = @{}
foreach ($procInfo in $processInfos) {
  $cmdLine = [string]$procInfo.CommandLine
  $portMatch = [regex]::Match($cmdLine, '--extension_server_port\s+(\d+)')
  $tokenMatch = [regex]::Match($cmdLine, '--extension_server_csrf_token\s+([^\s]+)')
  if (-not $portMatch.Success -or -not $tokenMatch.Success) { continue }
  $port = $portMatch.Groups[1].Value
  $token = $tokenMatch.Groups[1].Value
  if ($seenExtensionPorts.ContainsKey($port)) { continue }
  $seenExtensionPorts[$port] = $true
  $row = [ordered]@{ port = [int]$port; status = $null; ok = $false; error = $null; body = "" }
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/exa.extension_server_pb.ExtensionServerService/Heartbeat" `
      -Method Post `
      -Headers @{ 'x-codeium-csrf-token' = $token; 'connect-protocol-version' = '1' } `
      -ContentType 'application/json' `
      -Body '{}' `
      -UseBasicParsing `
      -TimeoutSec 1
    $row.status = [int]$response.StatusCode
    $row.ok = ($response.StatusCode -eq 200)
    $row.body = ConvertTo-SafeBody $response.Content $token
  } catch {
    $resp = $_.Exception.Response
    if ($resp) {
      $row.status = [int]$resp.StatusCode
      try {
        $reader = New-Object IO.StreamReader($resp.GetResponseStream())
        $row.body = ConvertTo-SafeBody $reader.ReadToEnd() $token
      } catch {}
    } else {
      $row.error = ConvertTo-SafeBody $_.Exception.Message $token
    }
  }
  $report.extension_server_heartbeat += $row
}

$bodyFile = Join-Path $env:TEMP "antigravity-connect-stream-body.bin"
[IO.File]::WriteAllBytes($bodyFile, (New-ConnectJsonEnvelope '{"clientType":"CHAT_CLIENT_REQUEST_STREAM_CLIENT_TYPE_IDE"}'))
$chatPath = '/exa.chat_client_server_pb.ChatClientServerService/StartChatClientRequestStream'

foreach ($procInfo in $processInfos) {
  $pidText = [string]$procInfo.ProcessId
  $argsText = [string]$procInfo.CommandLine
  $csrfMatch = [regex]::Match($argsText, '--csrf_token\s+([^\s]+)')
  if (-not $csrfMatch.Success) { continue }
  $token = $csrfMatch.Groups[1].Value
  $workspaceMatch = [regex]::Match($argsText, '--workspace_id\s+([^\s]+)')
  $workspaceId = if ($workspaceMatch.Success) { $workspaceMatch.Groups[1].Value } else { "" }
  $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq [int]$pidText -and $_.LocalAddress -eq '127.0.0.1' } |
    Select-Object -ExpandProperty LocalPort
  foreach ($port in $ports) {
    $url = "http://127.0.0.1:$port$chatPath"
    $out = & curl.exe -sS --max-time 1 -w "`nHTTP_STATUS:%{http_code}" `
      -H "x-codeium-csrf-token: $token" `
      -H "connect-protocol-version: 1" `
      -H "content-type: application/connect+json" `
      --data-binary "@$bodyFile" $url 2>&1
    $joined = ConvertTo-SafeBody ($out -join "`n") $token
    $report.chat_client_stream += [ordered]@{
      pid = [int]$pidText
      port = [int]$port
      workspace_id = $workspaceId
      initial_ack_seen = ($joined -match 'initialAck')
      http_status_200_seen = ($joined -match 'HTTP_STATUS:200')
      body = $joined
    }
  }
}

Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue

$heartbeatOk = @($report.extension_server_heartbeat | Where-Object { $_.ok }).Count -gt 0
$chatAckOk = @($report.chat_client_stream | Where-Object { $_.initial_ack_seen -and $_.http_status_200_seen }).Count -gt 0
$report.conclusion = [ordered]@{
  status = if ($heartbeatOk -and $chatAckOk) { "local_ipc_surfaces_verified_but_agent_turn_not_verified" } else { "blocked" }
  heartbeat_verified = $heartbeatOk
  chat_client_stream_verified = $chatAckOk
  production_routing_allowed = $false
  reason = "This probe proves local Antigravity IPC surfaces only. It does not prove a same-run model turn or MCP write_agent_comment roundtrip."
  next_gate = "Find or implement a safe prompt-to-agent-turn lane, then verify same_run_id writeback before enabling routing."
}

$json = $report | ConvertTo-Json -Depth 8
if ($OutputPath) {
  $dir = Split-Path -Parent $OutputPath
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false))
}
Write-Output $json
