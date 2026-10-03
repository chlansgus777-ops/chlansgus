"""Build the credential-free browser extension download; never package account data or browser profiles."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
with ZipFile(root / "frontend/public/saveticker-browser.zip", "w", ZIP_DEFLATED) as archive:
    for name in ("manifest.json", "background.js", "source.js", "local.js"):
        archive.write(root / "integrations/saveticker-browser" / name, name)
