# Developer build. Users run the resulting setup.exe; they need no Python/Node/Rust.
param([string]$Python = 'python', [string]$Node = 'node', [string]$Output = '')
$ErrorActionPreference = 'Stop'
$mlRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
if (-not $Output) { $Output = Join-Path $mlRoot 'installer' }
function Assert-BuildStep([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed ($LASTEXITCODE)" } }
Push-Location "$mlRoot\frontend"
try {
    & $Node node_modules/typescript/bin/tsc -b
    Assert-BuildStep 'TypeScript'
    & $Node node_modules/vite/bin/vite.js build --mode desktop
    Assert-BuildStep 'Frontend build'
} finally { Pop-Location }
Push-Location "$mlRoot\backend"
try {
    & $Python -m PyInstaller --noconfirm packaging/marketlens-backend.spec
    Assert-BuildStep 'Backend packaging'
} finally { Pop-Location }
Copy-Item -LiteralPath "$mlRoot\backend\dist\marketlens-backend.exe" -Destination "$mlRoot\frontend\src-tauri\binaries\marketlens-backend-x86_64-pc-windows-msvc.exe" -Force
Push-Location "$mlRoot\frontend"
try {
    & $Node node_modules/@tauri-apps/cli/tauri.js build --bundles nsis --config '{"build":{"beforeBuildCommand":""}}'
    Assert-BuildStep 'Windows installer'
} finally { Pop-Location }
New-Item -ItemType Directory -Path $Output -Force | Out-Null
$mlVersion = (Get-Content "$mlRoot\frontend\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json).version
Copy-Item -LiteralPath "$mlRoot\frontend\src-tauri\target\release\bundle\nsis\MarketLens_${mlVersion}_x64-setup.exe" -Destination "$Output\MarketLens-Setup-$mlVersion.exe" -Force
Write-Output "$Output\MarketLens-Setup-$mlVersion.exe"
