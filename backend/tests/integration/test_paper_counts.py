"""Owner 2026-10-04 "왜 지금까지 모의투자가 0건이야": before the first evaluation the home screen said only "no results
yet". It now counts the paper positions a scan opened (by status), so a pending position is never shown as none."""

from __future__ import annotations

from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import make_service


def test_the_home_screen_counts_paper_positions_before_any_result():
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        rows = repo.all_paper_positions(s)
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers={"X-MarketLens-Client": "t"}) as c:
        d = c.get("/api/dashboard").json()
    assert d["performance"] is None  # nothing evaluated yet
    assert sum(d["paper_counts"].values()) == len(rows) > 0
    assert d["paper_counts"].get("PENDING") == len(rows)
