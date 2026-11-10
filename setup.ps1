# One-time setup: creates the virtual environment, installs dependencies, prepares .env.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (Python 3.11)..."
    py -3.11 -m venv .venv
}
Write-Host "Installing dependencies (a few minutes the first time)..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --no-warn-script-location

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`nCreated .env - open it and paste your Anthropic API key." -ForegroundColor Yellow
}
Write-Host "`nRunning the component check (downloads the speech models on first run)..."
.\.venv\Scripts\python.exe main.py --check
