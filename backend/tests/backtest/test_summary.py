"""The 성과 screen's 과거 검증 comes only from a measured run: the summary refuses a results.json that is not the one its
manifest names, carries the run's hashes, and the API says "not yet" until the committed file exists."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from marketlens.backtest.schema import bt_engine
from tests.backtest import bt_fixture as FX


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    from marketlens.backtest.run import main

    d = tmp_path_factory.mktemp("sum")
    FX.build(str(d / "fx.db"))
    bt_engine(str(d / "fx.db"))
    main(["--db", str(d / "fx.db"), "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--out", str(d / "out"), "--leak-checks", "1"])
    return d / "out"


def test_the_summary_is_the_runs_own_numbers(run_dir, tmp_path):
    from marketlens.backtest.summary import main

    out = tmp_path / "backtest_results.json"
    main(["--results", str(run_dir), "--run-id", "42", "--out", str(out)])
    s = json.loads(out.read_text(encoding="utf-8"))
    res = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    man = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert s["available"] and s["source"]["results_sha256"] == man["results_sha256"] and s["source"]["workflow_run"] == "42"
    assert s["source"]["data_sha256"] == man["data_sha256"] and s["window"] == res["run"]["window"]
    assert set(s["elements"]) == set(res["horizons"]["60"]["elements"]) and "component.return_signals" in s["elements"]
    assert s["disclaimer"].startswith("과거 시뮬레이션") and s["application"]["final_weights"] is not None
    assert s["leak_checks"]["truncated_all_equal"] is True


def test_a_results_file_that_is_not_the_manifests_is_refused(run_dir, tmp_path):
    from marketlens.backtest.summary import main

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text((run_dir / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8")
    (bad / "results.json").write_text((run_dir / "results.json").read_text(encoding="utf-8").replace("0", "1", 1), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--results", str(bad), "--out", str(tmp_path / "x.json")])


def test_the_api_says_not_yet_until_the_file_exists(tmp_path, monkeypatch):
    import marketlens.config as C
    from tests.integration.test_service_api import make_service
    from marketlens.api.app import create_app
    from tests.integration.test_transactions import H

    svc = make_service(universe=20)
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers=H) as c:
        monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
        r = c.get("/api/performance/backtest").json()
        assert r["available"] is False and "계산하는 중" in r["reason"]
        (tmp_path / "backtest_results.json").write_text(json.dumps({"available": True, "weeks": 3}), encoding="utf-8")
        assert c.get("/api/performance/backtest").json() == {"available": True, "weeks": 3}
