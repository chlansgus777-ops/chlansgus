# MarketLens — one-time developer setup on Windows (PowerShell 5+).
# Requires: Python 3.11+, Node.js 20+. Optional for the desktop shell: Rust (rustup) + WebView2.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $Root
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".\backend[dev,anthropic,keyring]"
Push-Location frontend
npm ci
npm run build
Pop-Location
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example (MOCK mode)." }
.\.venv\Scripts\python -m marketlens migrate
Write-Host "Setup complete. Run scripts\windows\run.ps1"
