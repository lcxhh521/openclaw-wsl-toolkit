# WSL Safe Command Runner

`Invoke-WslSafe.ps1` is a small Windows-side helper for running Bash or Python snippets inside Ubuntu on WSL without letting PowerShell quoting, CRLF line endings, or UTF-8 BOMs corrupt the script.

Use it when a workflow needs to pass multi-line scripts from Windows/Codex into WSL, especially for OpenClaw maintenance, status checks, or repository sync work.

It does three things deliberately:

1. Accepts exactly one of `-CommandText` or `-CommandFile`.
2. Normalizes CRLF/CR to LF and writes UTF-8 without BOM.
3. Copies the temporary script into `\\wsl.localhost\<Distro>\tmp\codex_wsl_safe\` and executes it with the selected interpreter.

Example:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-WslSafe.ps1" -CommandFile .\check-openclaw.sh
```

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenClawWslTools\Invoke-WslSafe.ps1" -Interpreter python3 -CommandText 'print("hello from WSL")'
```

Safety notes:

- The helper does not store secrets.
- It removes the temporary local and WSL files by default.
- Use `-KeepRemote` only for debugging.
- Keep destructive commands out of generated scripts unless the user explicitly approved them.
