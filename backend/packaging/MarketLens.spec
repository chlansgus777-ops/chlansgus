# Standalone Windows GUI executable. Build frontend/dist first; end users need no toolchain.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
datas = [(str(ROOT / 'config'), 'config'), (str(ROOT / 'backend/alembic'), 'alembic'), (str(ROOT / 'frontend/dist'), 'frontend/dist')]
datas += copy_metadata('keyring')
datas.append((str(ROOT / 'backend/packaging/MarketLens.ico'), '.'))
a = Analysis([str(ROOT / 'backend/packaging/desktop_entry.py')], pathex=[str(ROOT / 'backend')], datas=datas,
    hiddenimports=collect_submodules('marketlens') + collect_submodules('uvicorn') + collect_submodules('websockets') + collect_submodules('keyring.backends') + ['tzdata', 'alembic', 'sqlalchemy.dialects.sqlite'],
    excludes=['matplotlib', 'IPython', 'playwright', 'pytest', 'PyInstaller'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name='MarketLens', console=False, upx=False,
          icon=str(ROOT / 'backend/packaging/MarketLens.ico'))
