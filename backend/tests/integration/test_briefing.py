"""오늘 아침 브리핑 (owner 2026-09-29: "아침 브리핑 (한국시간 오전 7시)", in the app only): what it says, when it is
ready, the once-a-day alert, and that a part without data says so instead of disappearing."""

from __future__ import annotations

from datetime import timedelta

from tests.integration.test_service_api import NOW
from tests.integration.test_transactions import client  # noqa: F401

MORNING = NOW + timedelta(hours=8)  # Fri 2026-09-25 23:00 UTC = Sat 08:00 KST, after Friday's close


def test_the_briefing_sums_up_the_session_for_my_account(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 10, "cost_basis": 100}, {"ticker": "MSFT", "quantity": 2, "cost_basis": 300}]})
    assert c.post("/api/scan").status_code == 200  # the LIVE harness found the candidate list reading fields rows do not have
    svc._clock["t"] = MORNING
    b = c.get("/api/briefing").json()
    assert b["scan_as_of"] is not None
    for cand in b["candidates"]:
        assert cand["action_ko"] and cand["score"] > 0 and (cand["max_buy"] is None or cand["max_buy"] > 0)
    assert b["date_kst"] == "2026-09-26" and b["ready"] is True and b["session_expected"] == "2026-09-25"
    # the mock world's daily bars end on the 24th (as when the night's bars are not stored yet): said, not blank
    assert b["session"] == "2026-09-24" and any("9/25 종가가 아직" in n for n in b["notes"])
    assert [m["ticker"] for m in b["market"]] == ["SPY", "QQQ"] and all(m["change"] is not None for m in b["market"])
    acct = b["account"]
    assert acct["holdings"] == 2 and acct["priced"] == 2 and acct["pnl"] is not None and len(acct["movers"]) == 2
    # the P&L is the session's close-to-close move of each holding × its quantity, summed
    assert abs(acct["pnl"] - sum(m["pnl"] for m in acct["movers"])) < 0.02
    assert "S&P 500" in b["headline"] and "내 계좌" in b["headline"]
    assert isinstance(b["events"], list) and isinstance(b["candidates"], list) and isinstance(b["watch"], list)


def test_before_seven_it_is_not_ready_and_the_alert_goes_out_once_after(client):  # noqa: F811
    c, svc = client
    svc._clock["t"] = NOW + timedelta(hours=6)  # Sat 06:00 KST
    assert c.get("/api/briefing").json()["ready"] is False
    svc.briefing_tick()
    assert not [a for a in svc.judge.alerts() if a["kind"] == "BRIEFING"]
    svc._clock["t"] = MORNING
    svc.briefing_tick()
    svc.briefing_tick()
    got = [a for a in svc.judge.alerts() if a["kind"] == "BRIEFING"]
    assert len(got) == 1 and got[0]["text"].startswith("오늘 아침 브리핑 · ") and got[0]["ticker"] == ""
    svc._clock["t"] = MORNING + timedelta(days=1)  # the next Korean day: one more
    svc.briefing_tick()
    assert len([a for a in svc.judge.alerts() if a["kind"] == "BRIEFING"]) == 2
    # the briefing never lists its own alert among the overnight alerts
    assert all(a["kind"] != "BRIEFING" for a in c.get("/api/briefing?refresh=true").json()["alerts"])


def test_an_empty_account_says_so(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": []})
    svc._clock["t"] = MORNING
    b = c.get("/api/briefing").json()
    assert b["account"]["holdings"] == 0 and b["account"]["pnl"] is None and b["account"]["movers"] == []
    assert "내 계좌" not in b["headline"]
