"""토스증권 account in the app (application/broker.py, domain/broker.py, /api/broker/toss, /api/portfolio) against the
spec-following fake server: a key is stored only when it works; the account decides the holdings and cash it has;
other holdings stay, marked; domestic holdings stay apart in won; a failure keeps the last snapshot with its time;
disconnecting forgets the key and the account's data; nothing secret reaches a response or a log."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.application.broker import BrokerSync
from marketlens.domain.broker import BrokerPosition, merge_broker
from marketlens.domain.portfolio import Holding
from tests.integration.test_service_api import NOW, make_service
from tests.toss_fake import FakeToss, holding

H = {"X-MarketLens-Client": "test"}


class Keys:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.forgotten: list[str] = []

    def save(self, values: dict[str, str]) -> dict[str, str]:
        self.saved.update(values)
        return {k: "keychain" for k in values}

    def forget(self, names: tuple[str, ...]) -> None:
        self.forgotten += list(names)
        for n in names:
            self.saved.pop(n, None)


@pytest.fixture()
def world():  # noqa: ANN201
    fake, keys = FakeToss(), Keys()
    svc = make_service(universe=40)
    svc.attach_broker(BrokerSync(svc.sf, svc.now, None, None, enabled=True, transport=fake.transport(), save_keys=keys.save, forget_keys=keys.forget))
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers=H) as c:
        yield c, svc, fake, keys


def connect(c: TestClient, fake: FakeToss, secret: str | None = None):  # noqa: ANN201
    return c.put("/api/broker/toss/credentials", json={"client_id": fake.client_id, "client_secret": secret or fake.secret})


def by_ticker(pf: dict) -> dict[str, dict]:
    return {h["ticker"]: h for h in pf["holdings"]}


def test_a_wrong_key_is_refused_and_never_stored(world):
    c, svc, fake, keys = world
    r = connect(c, fake, secret="wrong-secret-000000")
    assert r.status_code == 400 and "client_secret" in r.json()["detail"]
    assert keys.saved == {} and not svc.broker.configured and not svc.broker.active()
    fake.ip_allowed = False
    r = connect(c, fake)
    assert r.status_code == 400 and "허용 IP 관리" in r.json()["detail"] and keys.saved == {}
    r = c.put("/api/broker/toss/credentials", json={"client_id": "short", "client_secret": "x y"})
    assert r.status_code == 422 and keys.saved == {}


def test_the_account_decides_what_it_holds_and_the_cash(world):
    c, svc, fake, keys = world
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 3, "cost_basis": 50}, {"ticker": "MSFT", "quantity": 4, "cost_basis": 300}], "cash": 9999})
    r = connect(c, fake)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["active"] and st["account"] == {"seq": 7, "masked": "····8901"} and keys.saved.keys() == {"TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"}
    pf = c.get("/api/portfolio").json()
    hs = by_ticker(pf)
    assert hs["NVDA"]["source"] == "toss" and hs["NVDA"]["quantity"] == pytest.approx(10) and hs["NVDA"]["cost_basis"] == pytest.approx(100)
    assert hs["BRK.B"]["quantity"] == pytest.approx(2.5) and hs["BRK.B"]["source"] == "toss"
    assert "005930" not in hs  # domestic: apart, in won
    assert [d["symbol"] for d in pf["broker"]["domestic"]] == ["005930"] and pf["broker"]["domestic"][0]["currency"] == "KRW"
    assert hs["MSFT"]["outside_broker"] is True and hs["MSFT"]["source"] == "manual"  # kept (another broker?), marked
    assert any("MSFT" in n and "다른 증권사" in n for n in pf["notes"])
    assert any("NVDA" in n and "토스증권 계좌 기준 10주" in n for n in pf["notes"])
    assert {"ticker": "NVDA", "quantity": 3.0} in pf["unused_manual"]  # the entered NVDA line is offered for removal
    assert pf["cash"] == pytest.approx(1234.56) and pf["cash_source"] == "toss_usd" and pf["cash_entered"] is True
    r = c.put("/api/broker/toss/prefs", json={"cash": "toss_usd_krw"})
    assert r.status_code == 200
    assert c.get("/api/portfolio").json()["cash"] == pytest.approx(1234.56 + 500000 / 1385.5)
    c.put("/api/broker/toss/prefs", json={"cash": "manual"})
    assert c.get("/api/portfolio").json()["cash"] == pytest.approx(9999)
    assert fake.errors == []


def test_a_change_in_the_account_reaches_the_verdicts_the_quotes_and_the_screens(world):
    c, svc, fake, _keys = world
    connect(c, fake)
    v0 = svc.app_state()["broker"]["version"]
    svc.live_plans()
    fake.items = [holding("NVDA", "4", "100.00", "120.50"), holding("AMD", "7", "150", "160")]
    r = c.post("/api/broker/toss/sync")
    assert r.status_code == 200
    assert svc.app_state()["broker"]["version"] == v0 + 1
    assert svc.refresher.peek("live-plans").value is None  # the held flags are reloaded on the next tick
    assert svc.quotes._pinned["holdings"] >= {"NVDA", "AMD"} and "BRK.B" not in svc.quotes._pinned["holdings"]
    hs = by_ticker(c.get("/api/portfolio").json())
    assert hs["NVDA"]["quantity"] == pytest.approx(4) and "AMD" in hs and "BRK.B" not in hs
    c.post("/api/broker/toss/sync")  # nothing changed: no new version, no reload storm
    assert svc.app_state()["broker"]["version"] == v0 + 1


def test_a_failed_sync_keeps_the_last_snapshot_with_its_time_and_backs_off(world):
    c, svc, fake, _keys = world
    connect(c, fake)
    taken = svc.broker.status()["synced_at"]
    fake.ip_allowed = False
    svc._clock["t"] = NOW + timedelta(minutes=2)
    r = c.post("/api/broker/toss/sync")
    assert r.status_code == 400 and "허용 IP" in r.json()["detail"]
    st = c.get("/api/broker/toss").json()
    assert st["active"] and st["synced_at"] == taken and st["age_s"] == 120 and st["error"]["kind"] == "IP_NOT_ALLOWED"
    assert by_ticker(c.get("/api/portfolio").json())["NVDA"]["source"] == "toss"  # the last snapshot, with its time
    assert not svc.broker.due()  # a key/IP problem is retried every 10 minutes, not every tick
    svc._clock["t"] = NOW + timedelta(minutes=13)
    assert svc.broker.due()
    fake.ip_allowed = True
    assert c.post("/api/broker/toss/sync").status_code == 200 and c.get("/api/broker/toss").json()["error"] is None
    fake.faults = [__import__("httpx").ConnectError("down")] * 4
    r = c.post("/api/broker/toss/sync")
    assert r.status_code == 503 and "마지막으로 받은 보유 현황" in r.json()["detail"]


def test_the_background_schedule_follows_the_sessions(world):
    c, svc, fake, _keys = world
    connect(c, fake)
    b = svc.broker
    assert not b.due()
    svc._clock["t"] = b.now() + timedelta(seconds=b.interval() + 1)
    assert b.due()
    from datetime import datetime, timezone

    svc._clock["t"] = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)  # a Sunday: every 10 minutes
    assert b.interval() == 600
    svc._clock["t"] = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)  # Monday, US regular session
    assert b.interval() == 60
    svc._clock["t"] = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)  # Monday 10:00 KST: the Korean session
    assert b.interval() == 60


def test_disconnect_forgets_the_key_and_the_account_but_not_the_owners_records(world):
    c, svc, fake, keys = world
    c.put("/api/portfolio", json={"holdings": [{"ticker": "MSFT", "quantity": 4, "cost_basis": 300}]})
    connect(c, fake)
    r = c.delete("/api/broker/toss")
    assert r.status_code == 200 and not r.json()["configured"] and not r.json()["active"]
    assert set(keys.forgotten) == {"TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"} and keys.saved == {}
    pf = c.get("/api/portfolio").json()
    assert set(by_ticker(pf)) == {"MSFT"} and by_ticker(pf)["MSFT"]["outside_broker"] is False and "domestic" not in pf["broker"]
    assert pf["cash_source"] == "manual"
    from marketlens.infrastructure.db import repository as repo

    with svc.sf() as s:
        assert repo.get_setting(s, "broker.toss.snapshot") is None and repo.get_setting(s, "broker.toss.fills") is None


def test_the_snapshot_survives_a_restart(world):
    c, svc, fake, _keys = world
    connect(c, fake)
    again = BrokerSync(svc.sf, svc.now, fake.client_id, fake.secret, enabled=True, transport=fake.transport())
    assert again.active() and {p.ticker for p in again.positions()} == {"NVDA", "BRK.B", "005930"}
    assert again.status()["synced_at"] == svc.broker.status()["synced_at"]
    off = BrokerSync(svc.sf, svc.now, None, None, enabled=True)
    assert not off.active()  # a stored snapshot without a key is not used (the key was removed outside the app)


def test_mock_mode_never_connects_and_setup_does_not_take_the_key():
    svc = make_service(universe=40)
    assert svc.broker.enabled is False
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers=H) as c:
        r = c.put("/api/broker/toss/credentials", json={"client_id": "c_01TESTCLIENT0000", "client_secret": "s3cr3t-value-for-tests"})
        assert r.status_code == 422 and "실데이터" in r.json()["detail"]
        r = c.put("/api/settings/setup", json={"values": {"TOSS_CLIENT_SECRET": "s3cr3t-value-for-tests"}})
        assert r.status_code == 422 and "토스증권 연결" in r.json()["detail"]
        assert c.get("/api/portfolio").json()["broker"] is None


def test_fills_are_listed_newest_first(world):
    c, svc, fake, _keys = world
    connect(c, fake)
    body = c.get("/api/broker/toss/fills").json()
    fills = body["fills"]
    assert len(fills) == 130 and body["status"]["fills"]["complete"] is True
    keys = [f["filled_at"] for f in fills]
    assert keys == sorted(keys, reverse=True)
    assert all(f["side"] in ("BUY", "SELL") and float(f["quantity"]) > 0 for f in fills)


def test_nothing_secret_reaches_a_response_or_a_log(world, caplog):
    c, svc, fake, _keys = world
    texts = []
    with caplog.at_level(logging.DEBUG):
        texts.append(connect(c, fake, secret="wrong-secret-000000").text)
        texts.append(connect(c, fake).text)
        for path in ("/api/broker/toss", "/api/portfolio", "/api/broker/toss/fills", "/api/settings"):
            texts.append(c.get(path).text)
        fake.ip_allowed = False
        texts.append(c.post("/api/broker/toss/sync").text)
    blob = "\n".join(texts) + caplog.text + json.dumps(svc.broker.status())
    for secret in (fake.secret, "wrong-secret-000000", fake.client_id, fake.token or "none-issued", "12345678901"):
        assert secret not in blob, secret


# ------------------------------------------------------------------ the merge rule (pure)
def test_merge_rule():
    prof = lambda t: ("Tech", (), 0.0)  # noqa: E731
    own = [Holding("NVDA", 3, 50, "Tech", source="ledger", realized_pnl=12.0, dividends=1.0), Holding("MSFT", 4, 300, "Tech")]
    pos = [BrokerPosition("NVDA", "NVIDIA", "US", "USD", 10, 100), BrokerPosition("005930", "삼성전자", "KR", "KRW", 30, 65000),
           BrokerPosition("XYZ", "Some bond", "OTHER", "OTHER", 1, 1), BrokerPosition("ZERO", "sold", "US", "USD", 0, 0)]
    hs, notes = merge_broker(own, pos, prof)
    by = {h.ticker: h for h in hs}
    assert set(by) == {"NVDA", "MSFT"}
    assert by["NVDA"].source == "toss" and by["NVDA"].quantity == 10 and by["NVDA"].realized_pnl == 12.0 and by["NVDA"].dividends == 1.0
    assert by["MSFT"].outside_broker and not by["NVDA"].outside_broker
    assert any("Some bond" in n for n in notes) and any("거래 기록 3주" in n for n in notes)
    same, notes = merge_broker([Holding("NVDA", 10, 100, "Tech")], pos[:1], prof)
    assert not any("NVDA" in n for n in notes)  # the same numbers: nothing to say
    two, _ = merge_broker([], [BrokerPosition("A", "a", "US", "USD", 1, 10), BrokerPosition("A", "a", "US", "USD", 3, 20)], prof)
    assert two[0].quantity == 4 and two[0].cost_basis == pytest.approx(17.5)
