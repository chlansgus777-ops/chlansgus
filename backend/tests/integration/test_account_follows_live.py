"""Owner 2026-10-03 ("2,3,4번은 고쳐줘" — review finding 2 on the 0.1.1 hand-over): an account change no longer locks
every recommendation until the next scan. The recommendations made before it are held back only until the next live
round (about a second), which judges each pooled name again with the account as it is now — cash, the name's own
weight, sector, theme and correlation limits — and the scheduler analyses the list again for the names without a live
price."""

from __future__ import annotations

from datetime import timedelta

from tests.integration.test_service_api import NOW
from tests.integration.test_transactions import client  # noqa: F401


def _live_buy(c, svc):  # noqa: ANN001, ANN202
    svc.run_scan(run_committee=False)
    rows = c.get("/api/opportunities").json()["rows"]
    for r in rows:  # every listed name gets a live price, as the Toss feed prints them
        svc.quotes.ingest_poll(r["ticker"], float(r["price"] or 100.0), NOW - timedelta(seconds=1), "toss")
    svc.live_rejudge()
    rows = c.get("/api/opportunities").json()["rows"]
    buy = next(r for r in rows if r["action"] in ("BUY", "BUY SMALL") and r["actionable_now"])
    return buy


def _row(c, rid):  # noqa: ANN001, ANN202
    return next(r for r in c.get("/api/opportunities").json()["rows"] if r["id"] == rid)


def test_the_next_live_round_judges_again_with_the_new_account_no_scan_needed(client):  # noqa: F811
    c, svc = client
    buy = _live_buy(c, svc)
    assert c.put("/api/portfolio", json={"cash": 500_000, "holdings": []}).status_code == 200  # a deposit
    held_back = _row(c, buy["id"])
    assert not held_back["actionable_now"] and "계좌" in held_back["current_status_reason"]  # only until the next round
    assert svc.scan_wanted == "계좌 변경"  # the names without a live price: analysed again by the scheduler
    svc.live_rejudge()  # the next second's round — no scan in between
    after = _row(c, buy["id"])
    assert after["current_status"] == "CURRENT" and after["actionable_now"] is True, after["current_status_reason"]
    assert after["live_at"] and after["id"] == buy["id"]  # the same recommendation, judged again live


def test_a_new_position_in_the_name_caps_its_live_buy(client):  # noqa: F811
    """Buying the candidate itself (here: an entered holding of 60 % of the account) makes the next live round apply
    the single-name limit of the account as it is now — no more buying of it, said with the portfolio reason."""
    c, svc = client
    buy = _live_buy(c, svc)
    qty = 30_000 / float(buy["price"])
    assert c.put("/api/portfolio", json={"cash": 20_000, "holdings": [{"ticker": buy["ticker"], "quantity": qty, "cost_basis": buy["price"]}]}).status_code == 200
    svc.quotes.ingest_poll(buy["ticker"], float(buy["price"]), svc.now() - timedelta(seconds=1), "toss")
    svc.live_rejudge()
    live = svc.rejudged(buy["id"])
    assert live["action"] not in ("BUY", "BUY SMALL", "ADD"), live["action"]
    assert not live["actionable_now"]
    plan = c.get(f"/api/stocks/{buy['ticker']}").json()["position_plan"]
    assert plan["available"] is False


def test_an_account_change_asks_the_scheduler_for_one_analysis_of_the_list(client, monkeypatch):  # noqa: F811
    """With no live price for a name (market closed, a quiet name) the live round cannot reach it: the scheduler runs
    one scan with the new account at its next tick, and not again within ten minutes."""
    from marketlens.workers.scheduler import BackgroundScheduler

    c, svc = client
    svc.run_scan(run_committee=False)
    sched = BackgroundScheduler(svc)
    ran: list[object] = []
    real = svc.run_scan
    monkeypatch.setattr(svc, "run_scan", lambda run_committee=False: ran.append(run_committee) or real(run_committee=False))
    sched.step(svc.now())  # the first tick scans as usual (nothing scanned by the scheduler yet)
    ran.clear()
    sched.step(svc.now() + timedelta(minutes=1))
    assert ran == []  # nothing due: a scan ran a minute ago
    assert c.put("/api/portfolio", json={"cash": 123_456, "holdings": []}).status_code == 200
    sched.step(svc.now() + timedelta(minutes=2))
    assert len(ran) == 1 and svc.scan_wanted is None
    c.put("/api/portfolio", json={"cash": 123_457, "holdings": []})
    sched.step(svc.now() + timedelta(minutes=3))
    assert len(ran) == 1  # at most one such scan every ten minutes
    sched.step(svc.now() + timedelta(minutes=13))
    assert len(ran) == 2
    # the scan used the new account: the list's newest rows were made after the change, nothing held back
    rows = c.get("/api/opportunities").json()["rows"]
    assert all("계좌" not in (r.get("current_status_reason") or "") for r in rows)
