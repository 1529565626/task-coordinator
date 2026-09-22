$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (Test-Path ".env") {
    Get-Content .env | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -notmatch "=") { return }
        $name, $value = $_.Split("=", 2)
        Set-Item -Path "Env:$name" -Value $value
    }
}
.\.venv\Scripts\taskctl.exe admin backup --json
