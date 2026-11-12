# Start the dashboard at http://localhost:8000 (Windows PowerShell). macOS / Linux: ./run.sh
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\run.ps1
Set-Location $PSScriptRoot
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example - edit it (passwords!) and run again."
    exit 1
}
if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
}
if (-not (Select-String -Path .env -Pattern '^DASHBOARD_PASSWORD=.+' -Quiet)) {
    Write-Host "Warning: DASHBOARD_PASSWORD is empty. Set it in .env."
}
$hostAddr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$port = if ($env:PORT) { $env:PORT } else { "8000" }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host $hostAddr --port $port
