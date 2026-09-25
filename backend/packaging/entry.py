"""PyInstaller entry point for the MarketLens backend sidecar (``marketlens-backend.exe``)."""

import sys

from marketlens.workers.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["serve"]))
