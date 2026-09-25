# ADR-0008: Windows packaging with Tauri + PyInstaller sidecar

Status: Accepted

## Decision
Backend: Python/FastAPI frozen with PyInstaller into `marketlens-backend.exe`. Desktop: Tauri 2 shell
(WebView2) that starts the backend as a sidecar bound to 127.0.0.1 and kills it on exit. A browser-only
path (`run-windows.bat`) exists for users without the Rust toolchain. Builds run on `windows-latest`
in GitHub Actions (`.github/workflows/windows-build.yml`).

## Consequences
Small installer relative to Electron; the Python runtime is bundled in the sidecar. Tauri bundling
cannot be produced from the Linux development container (no WebView2/MSVC); the PyInstaller spec was
validated on Linux.
