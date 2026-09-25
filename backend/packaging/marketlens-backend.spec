# PyInstaller spec — builds the backend sidecar used by the Tauri desktop shell.
# Usage (from backend/):  pyinstaller packaging/marketlens-backend.spec
# -*- mode: python -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]  # repository root

datas = [
    (str(ROOT / "config"), "config"),
    (str(ROOT / "backend" / "alembic"), "alembic"),
]
dist = ROOT / "frontend" / "dist"
if dist.exists():
    datas.append((str(dist), "frontend/dist"))

a = Analysis(
    [str(ROOT / "backend" / "packaging" / "entry.py")],
    pathex=[str(ROOT / "backend")],
    datas=datas,
    hiddenimports=collect_submodules("marketlens") + collect_submodules("uvicorn") + ["tzdata", "alembic", "sqlalchemy.dialects.sqlite"],
    excludes=["tkinter", "matplotlib", "IPython", "cryptography", "playwright", "pytest", "PyInstaller"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="marketlens-backend", console=True, upx=False)
