# WSL Safe Command Runner

`Invoke-WslSafe.ps1` is a small Windows-side helper for running Bash or Python snippets inside Ubuntu on WSL without letting PowerShell quoting, CRLF line endings, or UTF-8 BOMs corrupt the script.

Use it when a workflow needs to pass multi-line scripts from Windows/Codex into WSL, especially for OpenClaw maintenance, status checks, repository sync work, and Unicode-heavy diagnostics.

It does four things deliberately:

1. Accepts exactly one of `-CommandText` or `-CommandFile`.
2. Normalizes CRLF/CR to LF and writes UTF-8 without BOM.
3. Uses `C:\Windows\System32\wsl.exe` and retries transient WSL visibility failures.
4. Copies the temporary script into `\\wsl.localhost\<Distro>\tmp\codex_wsl_safe\`, falling back to UTF-8 stdin when UNC copying is unavailable.

Example:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-WslSafe.ps1" -CommandFile .\check-openclaw.sh
```

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-WslSafe.ps1" -Interpreter python3 -CommandText 'print("hello from WSL")'
```

## Stable WSL Compatibility Helpers

Some Windows automation contexts can see a different WSL state than the interactive user. Typical symptoms are `WSL_E_DISTRO_NOT_FOUND`, an empty `wsl --list --verbose`, or `\\wsl.localhost\<Distro>` being unavailable even though Ubuntu is running for the real Windows user.

The compatibility helpers are included for that class of issue:

- `Invoke-StableWsl.ps1` resolves `C:\Windows\System32\wsl.exe` explicitly, probes the selected distro, and writes a bounded `stable-wsl-state.json` diagnostic next to the installed helper.
- `WslUtf8Bridge.ps1` writes bytes into WSL through `wsl.exe` stdin instead of relying on `\\wsl.localhost` file copies.
- `Invoke-WslScript.ps1` copies a local script into WSL with the UTF-8 bridge, then runs it with Bash.
- `Start-WslKeepalive.ps1` starts a hidden long-lived `sleep` process to keep the selected distro warm after login.

Use these when `Invoke-WslSafe.ps1` is too dependent on UNC WSL shares, or when the problem is clearly Windows-side WSL visibility rather than the OpenClaw command itself.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-StableWsl.ps1" -Health
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-WslScript.ps1" -LocalScriptPath .\check-openclaw.sh
```

The generated `stable-wsl-state.json` is local runtime state and should not be committed.

## Move a WSL Distro to a Data Drive

When the OpenClaw workspace/VHDX is pressuring C:, move the whole distro instead of copying runtime folders by hand:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Move-WslDistroToDataDrive.ps1 -Distro Ubuntu -TargetRoot E:\WSL -WhatIf
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Move-WslDistroToDataDrive.ps1 -Distro Ubuntu -TargetRoot E:\WSL
```

The script uses `wsl --manage <distro> --move <target>`, then verifies the moved distro. It does not copy secrets, edit OpenClaw config, or touch Telegram/model credentials.

## Safety Notes

- The helpers do not store secrets.
- Temporary local and WSL files are removed by default.
- Use `-KeepRemote` only for debugging.
- Keep destructive commands out of generated scripts unless the user explicitly approved them.
