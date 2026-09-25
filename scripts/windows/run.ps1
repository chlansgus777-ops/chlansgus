# Starts the MarketLens backend (serves the built UI) and opens the browser.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $Root
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Start-Process "http://127.0.0.1:8765"
& $py -m marketlens serve --host 127.0.0.1 --port 8765
