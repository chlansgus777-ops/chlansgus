# Builds the Windows desktop app: PyInstaller backend sidecar + Tauri shell (NSIS/MSI installers).
# Requires Rust (stable-x86_64-pc-windows-msvc) and the WebView2 runtime.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $Root
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $py -m pip install pyinstaller
Push-Location frontend
npm ci
npm run build:desktop
Pop-Location
Push-Location backend
& $py -m PyInstaller --noconfirm packaging\marketlens-backend.spec
Pop-Location
$triple = "x86_64-pc-windows-msvc"
Copy-Item "backend\dist\marketlens-backend.exe" "frontend\src-tauri\binaries\marketlens-backend-$triple.exe" -Force
Push-Location frontend
if (-not (Test-Path "src-tauri\icons\icon.ico")) { Write-Warning "No icons found: run 'npx tauri icon <logo.png>' first"; }
npx tauri build
Pop-Location
Write-Host "Installers: frontend\src-tauri\target\release\bundle\"
