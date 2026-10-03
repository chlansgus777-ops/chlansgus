"""Owner 2026-10-03: "saveticker에서 가져오는 옵션정보가 내가 관심도 없는 종목의 옵션 정보를 보여주는데 종목후보들의
옵션 정보를 가지고오게 수정해줘". The extension read the option aggregates of the (at most three) names the newest
articles mentioned. Now MarketLens names them in its answer to each news delivery: the candidate list in its live order
(the stocks open on screen first), each name once a day — the aggregates are the prior day's — at most ten per read.
The screens show only those names, in that order. What arrives is also kept, dated, for measuring later whether it
helps the return estimates ("수익계산에 도움이 된다면 계산법에 포함")."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from tests.contract.test_saveticker import NOW, payload
from tests.integration.test_background_sync import _live
from tests.integration.test_transactions import client  # noqa: F401

H = {"X-MarketLens-Client": "test"}


def _option(symbol: str, day: str = "2026-09-30") -> dict:
    return {"symbol": symbol, "optionable": True, "snapshotIsPriorDay": True, "batchIsPriorDay": True,
            "snapshotDate": day, "batchDate": day, "nearestExpiry": "2026-10-02", "referencePrice": 100.0, "putCallRatioVolume": 0.7}


@pytest.fixture()
def bridge(tmp_path, monkeypatch):  # noqa: ANN201
    monkeypatch.setenv("MARKETLENS_API_TOKEN", "desktop-launch-secret")
    service = _live(tmp_path, live_quotes=False)
    service.settings = replace(service.settings, data_dir=tmp_path)  # the dated copies go to this test's folder
    clock = {"t": NOW}
    service._now = lambda: clock["t"]
    candidates = ["NVDA", "AMD", "MU", "AVGO"]
    service.option_universe = lambda limit=None: list(candidates)  # the candidate list (its own test is below)
    app = create_app(service.settings, service=service, run_migrations=False)
    with TestClient(app, headers=H | {"X-MarketLens-Token": "desktop-launch-secret"}) as c:
        key = c.post("/api/saveticker/browser").json()["key"]
        yield service, c, {"X-MarketLens-News-Bridge": key}, clock, candidates, tmp_path
    service.stop_background()


def test_the_answer_to_a_news_delivery_names_the_candidates_still_missing_today(bridge):
    service, c, bh, clock, candidates, _tmp = bridge
    first = c.post("/api/saveticker/browser/news", headers=bh, json={"news": payload()}).json()
    assert first["option_symbols"] == ["NVDA", "AMD", "MU", "AVGO"]  # the candidates, never the article's $NVDA/$AMD order
    got = c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": {"options": [_option("NVDA"), _option("AMD")]}})
    assert got.status_code == 200 and got.json()["items_normalized"] == 2
    clock["t"] += timedelta(minutes=2)
    again = c.post("/api/saveticker/browser/news", headers=bh, json={"news": payload()}).json()
    assert again["option_symbols"] == ["MU", "AVGO"]  # read today already: not asked again
    clock["t"] += timedelta(hours=6, minutes=1)
    later = c.post("/api/saveticker/browser/news", headers=bh, json={"news": payload()}).json()
    assert later["option_symbols"][:2] == ["NVDA", "AMD"]  # the next day's figures: asked again after six hours


def test_at_most_ten_names_a_read_and_the_parser_takes_ten(bridge):
    service, c, bh, _clock, candidates, _tmp = bridge
    candidates[:] = [f"T{i}" for i in range(25)]
    answer = c.post("/api/saveticker/browser/news", headers=bh, json={"news": payload()}).json()
    assert answer["option_symbols"] == [f"T{i}" for i in range(10)]
    ten = {"options": [_option(f"T{i}") for i in range(10)]}
    assert c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": ten}).status_code == 200
    eleven = {"options": [_option(f"T{i}") for i in range(11)]}
    assert c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": eleven}).status_code == 422


def test_the_screens_show_only_the_candidates_in_their_order(bridge):
    service, c, bh, _clock, candidates, _tmp = bridge
    body = {"options": [_option("AMD"), _option("TSLA"), _option("NVDA")]}  # TSLA: not a candidate (an article's name)
    assert c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": body}).status_code == 200
    body = {"options": [_option("MU")]}  # a later read adds to the names already received
    assert c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": body}).status_code == 200
    view = c.get("/api/saveticker/supplement").json()["resources"]["options"]
    assert [r["related_companies"][0]["ticker"] for r in view["rows"]] == ["NVDA", "AMD", "MU"]
    assert view["candidates"] == candidates
    candidates.remove("AMD")  # a name that leaves the candidate list leaves the screen
    view = c.get("/api/saveticker/supplement").json()["resources"]["options"]
    assert [r["related_companies"][0]["ticker"] for r in view["rows"]] == ["NVDA", "MU"]


def test_what_arrives_is_kept_dated_once_for_the_later_measurement(bridge):
    service, c, bh, clock, _candidates, tmp = bridge
    body = {"options": [_option("NVDA"), _option("AMD")]}
    c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": body})
    c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": body})  # the same day again
    clock["t"] += timedelta(days=1)
    c.post("/api/saveticker/browser/news", headers=bh, json={"resource": "options", "payload": {"options": [_option("NVDA", "2026-10-01")]}})
    lines = [json.loads(x) for x in (tmp / "saveticker" / "options_history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["key"] for x in lines] == ["NVDA|2026-09-30", "AMD|2026-09-30", "NVDA|2026-10-01"]
    assert lines[2]["recorded_at"] == (NOW + timedelta(days=1)).isoformat()
    assert lines[0]["row"]["metrics"]["putCallRatioVolume"] == 0.7 and lines[0]["row"]["snapshot_prior_day"] is True


def test_the_candidate_list_order_open_stocks_then_buys_never_held_only_names(client):  # noqa: F811
    c, svc = client
    svc.run_scan(run_committee=False)
    rows = c.get("/api/opportunities").json()["rows"]
    bullish = [r["ticker"] for r in rows if r["action"] in ("BUY", "BUY SMALL", "ADD")]
    names = svc.option_universe()
    assert names[: len(bullish)] == sorted(bullish, key=lambda t: names.index(t))  # the buys lead
    assert set(names[: len(bullish)]) == set(bullish)
    assert len(names) <= svc.OPTION_UNIVERSE and len(set(names)) == len(names)
    listed = {r["ticker"] for r in rows}
    held_only = next(sec.ticker for sec in svc.data.securities().value if sec.ticker not in listed and not sec.is_etf)
    c.put("/api/portfolio", json={"cash": 100_000, "holdings": [{"ticker": held_only, "quantity": 1, "cost_basis": 10}]})
    svc._pool_cache = None
    assert held_only not in svc.option_universe()  # held, but not a candidate and not on screen
    svc.quotes.view([held_only])  # its stock page is open
    svc._pool_cache = None
    assert svc.option_universe()[0] == held_only
