"""PyInstaller entry point for the MarketLens backend sidecar (``marketlens-backend.exe``)."""

import sys

from marketlens.workers.cli import main

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    if not sys.argv[1:] or sys.argv[1] == "serve":
        from marketlens.config import env_file_path
        from marketlens.workers.desktop import initial_settings
        initial_settings(env_file_path())
    sys.exit(main(sys.argv[1:] or ["serve"]))
