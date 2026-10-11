"""The home screen's own routes (owner spec 2026-10-11): the goal amount and the routine's evidence file."""

import json

from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from tests.integration.test_service_api import make_service
from tests.integration.test_transactions import H


def test_goal_is_unset_then_saved_then_cleared():
    svc = make_service(universe=20)
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers=H) as c:
        assert c.get("/api/goal").json() == {"amount": None}
        assert c.put("/api/goal", json={"amount": 150000}).json() == {"amount": 150000}
        assert c.get("/api/goal").json() == {"amount": 150000.0}
        assert c.put("/api/goal", json={"amount": -5}).status_code == 422
        c.put("/api/goal", json={"amount": None})
        assert c.get("/api/goal").json() == {"amount": None}


def test_evidence_says_not_yet_until_the_file_exists(tmp_path, monkeypatch):
    import marketlens.config as C

    svc = make_service(universe=20)
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers=H) as c:
        monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
        r = c.get("/api/routine/evidence").json()
        assert r["available"] is False and "계산하는 중" in r["reason"]
        (tmp_path / "routine_evidence.json").write_text(json.dumps({"routine": {"cagr": 0.1}}), encoding="utf-8")
        assert c.get("/api/routine/evidence").json() == {"available": True, "routine": {"cagr": 0.1}}


def test_the_committed_evidence_matches_the_registered_routine_result():
    """config/routine_evidence.json recomputes M-NF on the same data: its yearly return must equal §20's file."""
    from marketlens.config import CONFIG_DIR

    ev = json.loads((CONFIG_DIR / "routine_evidence.json").read_text(encoding="utf-8"))
    s20 = json.loads((CONFIG_DIR / "strategy_results_s20.json").read_text(encoding="utf-8"))
    assert abs(ev["routine"]["cagr"] - s20["variants"]["M-NF"]["cagr"]) < 1e-12
    assert ev["source"]["data_sha256"] == s20["source"]["data_sha256"]
    assert {"routine", "spy", "deposit", "random"} <= set(ev)
