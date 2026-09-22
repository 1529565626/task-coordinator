$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

if (-not (Test-Path "config\service.toml")) {
    Copy-Item "config\service.toml.example" "config\service.toml"
}

if (-not (Test-Path ".env")) {
    $password = -join ((48..57 + 65..90 + 97..122 | Get-Random -Count 24 | ForEach-Object { [char]$_ }))
    $env:TASKCOORD_BOOTSTRAP_PASSWORD = $password
    $hash = .\.venv\Scripts\python.exe -c "import os; from taskcoord.security import hash_password; print(hash_password(os.environ['TASKCOORD_BOOTSTRAP_PASSWORD']))"
    $secret = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    @(
        "TASKCOORD_ADMIN_USERNAME=admin"
        "TASKCOORD_ADMIN_PASSWORD_HASH=$hash"
        "TASKCOORD_SESSION_SECRET=$secret"
        "TASKCOORD_CONFIG=config/service.toml"
    ) | Set-Content -Encoding ascii .env
    Remove-Item Env:TASKCOORD_BOOTSTRAP_PASSWORD
    $migrationDir = Join-Path $root "data\migration"
    New-Item -ItemType Directory -Force -Path $migrationDir | Out-Null
    Set-Content -Path (Join-Path $migrationDir ".initial-admin-password") -Value $password -Encoding ascii -NoNewline
    Write-Host "Admin password, shown once. Also saved to data\migration\.initial-admin-password (gitignored)."
    Write-Host "Password: $password"
}

Write-Host "Bootstrap complete. Start with scripts\run_server.ps1"
