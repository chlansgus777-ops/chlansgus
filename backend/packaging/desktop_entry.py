"""No command line, Python installation, or terminal required."""
import os
import sys

if __name__ == '__main__':
    # GUI executables have no stdout/stderr; library logging still expects writable streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    from multiprocessing import freeze_support
    freeze_support()
    from marketlens.workers.desktop import main
    raise SystemExit(main())
