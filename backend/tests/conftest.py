from __future__ import annotations

import math
import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault("MARKETLENS_MODE", "MOCK")

from marketlens.config import load_model_config  # noqa: E402
from marketlens.domain.market import Bar  # noqa: E402
from marketlens.domain.market_calendar import is_trading_day  # noqa: E402

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)  # Friday 11:00 ET, regular session


@pytest.fixture(autouse=True)
def _no_real_provider_calls(monkeypatch):
    """No test may reach a real provider (evaluation 6: a LIVE test built without a mock transport could call the
    real FINRA API from a CI runner with network). Only local servers (the E2E app) are allowed; a real request
    fails as a network error, exactly like an offline machine."""
    import httpx

    real = httpx.HTTPTransport.handle_request

    def guarded(self, request):  # noqa: ANN001, ANN202
        if request.url.host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            raise httpx.ConnectError(f"tests never call real providers ({request.url.host})", request=request)
        return real(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)


@pytest.fixture(scope="session")
def cfg():
    return load_model_config()


@pytest.fixture
def now():
    return NOW


def trading_days(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def make_bars(end: date, n: int = 260, start: float = 100.0, drift: float = 0.0008, amp: float = 0.01, volume: float = 2_000_000) -> list[Bar]:
    days = trading_days(end, n)
    out = []
    px = start
    for i, d in enumerate(days):
        wave = amp * math.sin(i / 6.0)
        c = px * (1 + drift + wave * 0.3)
        o = px * (1 + wave * 0.1)
        out.append(Bar(d, round(o, 4), round(max(o, c) * 1.01, 4), round(min(o, c) * 0.99, 4), round(c, 4), volume))
        px = c
    return out
