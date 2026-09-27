"""Place eval7 under repository/evaluations; or set MARKETLENS_SOURCE_ROOT."""
import os
import sys
from pathlib import Path
root = os.environ.get('MARKETLENS_SOURCE_ROOT')
roots = [Path(root)] if root else list(Path(__file__).resolve().parents)
for p in roots:
    if (p / 'backend' / 'marketlens').is_dir():
        sys.path.insert(0, str(p / 'backend'))
        break
