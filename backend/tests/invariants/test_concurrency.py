"""Invariant: whatever runs at the same time — the background data preparation, a scan, another sync — or fails half-way
(a database error, the app closed), every lock is free afterwards, the recorded state says what happened, and no
request is carried out twice (no second scan behind the first, no session downloaded twice)."""

from __future__ import annotations

import threading
from collections import Counter

import pytest


def _live(tmp_path):  # noqa: ANN001, ANN202
    from tests.regression.test_eval9_followups import _live as live

    return live(tmp_path)


def _join(name: str) -> None:
    for t in threading.enumerate():
        if t.name == name:
            t.join(timeout=120)
            assert not t.is_alive()


def _locks_free(svc) -> None:  # noqa: ANN001
    for lk in (svc._lock, svc._sync_lock, svc._sync_run, svc._ledger_lock):
        assert lk.acquire(blocking=False), lk
        lk.release()


def test_preparation_scan_and_another_sync_at_once(tmp_path, monkeypatch):
    from marketlens.application.services import ScanRefused
    from marketlens.application.sync import _find

    svc = _live(tmp_path)
    grouped = _find(svc.registry, "price", "get_grouped_daily")
    real = grouped.get_grouped_daily
    loaded: Counter = Counter()
    first_call, go = threading.Event(), threading.Event()

    def spy(d):  # noqa: ANN001, ANN202
        first_call.set()
        go.wait(timeout=20)
        bars = real(d)
        if bars:
            loaded[d] += 1
        return bars

    monkeypatch.setattr(grouped, "get_grouped_daily", spy)
    assert svc.start_sync()["started"]
    assert first_call.wait(timeout=20)
    other = threading.Thread(target=svc.sync_market, name="other-sync")
    other.start()
    with pytest.raises(ScanRefused):  # a scan now would judge half-prepared data
        svc.run_scan(run_committee=False)
    go.set()
    other.join(timeout=120)
    _join("marketlens-sync")
    assert svc.sync_status()["job"]["status"] in ("DONE", "PAUSED")
    assert not [d for d, n in loaded.items() if n > 1]
    _locks_free(svc)
    svc.run_scan(run_committee=False)  # afterwards the scan runs
    assert svc.scan_status()["state"]["status"] == "COMPLETE"
    _locks_free(svc)


def test_a_second_scan_while_one_runs_is_refused_not_queued():
    from marketlens.application.services import ScanRefused
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    inside, go = threading.Event(), threading.Event()
    real = svc._persist
    calls = Counter()

    def slow(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls["persist"] += 1
        inside.set()
        go.wait(timeout=20)
        return real(*a, **k)

    svc._persist = slow  # type: ignore[method-assign]
    t = threading.Thread(target=svc.run_scan, kwargs={"run_committee": False}, name="scan-1")
    t.start()
    assert inside.wait(timeout=30)
    with pytest.raises(ScanRefused):
        svc.run_scan(run_committee=False)
    go.set()
    t.join(timeout=120)
    before = calls["persist"]
    assert svc.scan_status()["state"]["status"] == "COMPLETE"
    assert before == svc.scan_status()["state"]["saved"]  # exactly one scan's results
    _locks_free(svc)


def test_a_database_error_mid_scan_frees_the_lock_and_says_so():
    from sqlalchemy.exc import OperationalError

    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    real = svc._persist
    n = Counter()

    def fail_third(*a, **k):  # noqa: ANN002, ANN003, ANN202
        n["x"] += 1
        if n["x"] == 3:
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return real(*a, **k)

    svc._persist = fail_third  # type: ignore[method-assign]
    with pytest.raises(OperationalError):
        svc.run_scan(run_committee=False)
    st = svc.scan_status()["state"]
    assert st["status"] == "FAILED" and st["saved"] == 2 and "locked" in st.get("error", "")
    _locks_free(svc)
    svc._persist = real  # type: ignore[method-assign]
    svc.run_scan(run_committee=False)  # it can run again
    assert svc.scan_status()["state"]["status"] == "COMPLETE"


def test_the_api_refuses_a_second_scan_with_a_reason(monkeypatch):
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    assert svc._lock.acquire(blocking=False)  # a scan is running
    try:
        with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
            r = c.post("/api/scan?committee=false")
            assert r.status_code == 409 and "스캔" in r.json()["detail"]
    finally:
        svc._lock.release()
