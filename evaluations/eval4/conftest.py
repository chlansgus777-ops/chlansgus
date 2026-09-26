"""4차 평가 반례 모음 — backend 패키지와 backend/tests 도우미를 import 할 수 있게 경로만 추가한다."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
