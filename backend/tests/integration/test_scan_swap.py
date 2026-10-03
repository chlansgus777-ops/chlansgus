"""Owner 2026-09-29: "자동으로 시장스캔이 될 때마다 종목탭에 종목들이 다 사라지고 하나씩 다시 보인다" — while a scan is being
saved the screens keep the previous complete scan; the new one replaces it at once when it is complete."""

from __future__ import annotations

import json

from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import ScanRunRow
from tests.integration.test_transactions import client  # noqa: F401


def test_the_list_stays_while_the_next_scan_is_saved(client):  # noqa: F811
    c, svc = client
    assert c.post("/api/scan?committee=false").status_code == 200
    first = c.get("/api/opportunities").json()
    assert first["rows"]
    # a second scan has written its run row and is saving results (the service holds the scan lock)
    with svc.sf() as s:
        run = ScanRunRow(as_of=svc.now(), mode=svc.mode.value, stages=[], excluded_count=0, regimes=[], issues=[],
                         scoring_model_version="x", config_version="x", created_at=repo.now())
        s.add(run)
        s.flush()
        repo.set_setting(s, "scan_state", json.dumps({"scan_id": run.id, "status": "RUNNING", "saved": 0, "total": 40}))
        s.commit()
        new_id = run.id
    with svc._lock:
        during = c.get("/api/opportunities").json()
        assert during["scan"]["id"] == first["scan"]["id"] and len(during["rows"]) == len(first["rows"])
        assert svc.app_state()["scan_id"] == first["scan"]["id"]  # the screens are not told to reload yet
    # the lock is released (complete, or the app stopped half-way): the newest scan is shown
    assert c.get("/api/opportunities").json()["scan"]["id"] == new_id


def test_a_name_analysed_again_shows_that_analysis_in_the_list(client):  # noqa: F811
    from datetime import timedelta

    c, svc = client
    assert c.post("/api/scan?committee=false").status_code == 200
    rows = c.get("/api/opportunities").json()["rows"]
    t, rank = rows[-1]["ticker"], rows[-1]["rank"]
    svc._clock["t"] = svc._clock["t"] + timedelta(minutes=10)
    assert c.post(f"/api/stocks/{t}/analysis", headers={"X-MarketLens-Client": "test"}).status_code == 202
    svc.analyses.wait(f"analysis:{t}", 10)
    assert c.get(f"/api/stocks/{t}").status_code == 200
    after = {r["ticker"]: r for r in c.get("/api/opportunities").json()["rows"]}
    row = after[t]
    assert row["rank"] == rank and row["reanalyzed_at"] and row["id"] != rows[-1]["id"]
    assert len(after) == len(rows)  # same list, one row replaced in place
