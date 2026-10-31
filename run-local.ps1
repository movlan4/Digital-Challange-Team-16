# Local run without Docker (Windows PowerShell). Requires Python 3.11+.
# Usage:  .\run-local.ps1          -> starts the app on http://localhost:8000
#         .\run-local.ps1 -Test    -> runs the test suite
#         .\run-local.ps1 -Import  -> rebuilds app/data/network.json from the KMZ (+ OpenStreetMap)
param([switch]$Test, [switch]$Import)

$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

# Load settings (admin credentials etc.) from .env, falling back to .env.example
$envFile = @(".env", ".env.example") | ForEach-Object { Join-Path $PSScriptRoot $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$' } | ForEach-Object {
        if (-not [Environment]::GetEnvironmentVariable($Matches[1])) { Set-Item "env:$($Matches[1])" $Matches[2] }
    }
}

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    python -m venv (Join-Path $backend ".venv")
}
& $python -m pip install --quiet -r (Join-Path $backend "requirements-dev.txt")

Push-Location $backend
try {
    if ($Test) {
        & $python -m pytest -v
    } elseif ($Import) {
        & $python -I -u tools\import_kmz.py tools\source\buslines.kmz
    } else {
        Write-Host "Passenger: http://localhost:8000/passenger.html"
        Write-Host "Operator:  http://localhost:8000/operator.html  (admin credentials: see .env / .env.example)"
        Write-Host "API docs:  http://localhost:8000/docs"
        & $python -m uvicorn app.main:create_app --factory --port 8000
    }
} finally {
    Pop-Location
}
