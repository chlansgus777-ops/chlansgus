"""The read-only Toss client against a fake server that follows the published spec (tests/toss_fake.py): every request
it makes and every response the fake sends are checked against the pinned spec subset."""

from __future__ import annotations

import logging
import threading
from datetime import date
from decimal import Decimal

import httpx
import pytest

from marketlens.providers.live.toss import TossClient, TossError, dec, mask_account, parse_fills, parse_holdings
from tests.toss_fake import SPEC, FakeToss, check, holding, order, response_schema


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def client(fake: FakeToss, clock: Clock | None = None, cid: str | None = None, secret: str | None = None) -> TossClient:
    c = clock or Clock()
    return TossClient(cid if cid is not None else fake.client_id, secret if secret is not None else fake.secret, transport=fake.transport(), clock=c, sleep=c.sleep)


@pytest.fixture()
def fake() -> FakeToss:
    return FakeToss()


def test_the_fake_follows_the_spec_and_the_spec_is_the_pinned_version(fake):
    assert SPEC["info"]["version"] == "1.2.17"
    c = client(fake)
    acct = c.brokerage_account()
    c.holdings(acct.seq), c.buying_power(acct.seq, "USD"), c.buying_power(acct.seq, "KRW"), c.usd_krw(), c.fills(acct.seq, date(2026, 9, 1), date(2026, 9, 29))
    assert fake.errors == []  # no request broke the spec, no fixture response broke the response schema
    assert {r.method for r in fake.requests} == {"POST", "GET"} and all(r.url.path == "/oauth2/token" for r in fake.requests if r.method == "POST")
    # the checker itself catches a drift (a number instead of a decimal string, a missing required field)
    bad = {"result": {**FakeToss._summary([holding("NVDA", "1", "1", "1")])}}
    bad["result"]["items"][0]["quantity"] = 1
    del bad["result"]["items"][0]["name"]
    errs = check(response_schema("/api/v1/holdings", "get", 200), bad)
    assert any("quantity" in e for e in errs) and any("name" in e and "required" in e for e in errs)


def test_holdings_are_parsed_exactly_with_us_tickers_in_one_spelling(fake):
    c = client(fake)
    hs = c.holdings(c.brokerage_account().seq)
    by = {h.symbol: h for h in hs}
    assert set(by) == {"NVDA", "BRK.B", "005930"}
    n = by["NVDA"]
    assert (n.market, n.currency, n.quantity, n.avg_price, n.last_price) == ("US", "USD", Decimal("10"), Decimal("100.00"), Decimal("120.50"))
    assert by["BRK.B"].quantity == Decimal("2.5")  # fractional US shares
    k = by["005930"]
    assert (k.market, k.currency, k.name, k.tax is not None) == ("KR", "KRW", "삼성전자", True)
    assert n.tax is None  # the spec: null when there is no tax


def test_one_token_is_cached_and_reissued_only_near_expiry(fake):
    clk = Clock()
    c = client(fake, clk)
    acct = c.brokerage_account()
    for _ in range(5):
        c.holdings(acct.seq)
    assert c.issued == 1
    clk.t += 86400 - 299  # inside the 5-minute margin
    c.holdings(acct.seq)
    assert c.issued == 2


def test_a_token_revoked_by_another_program_is_replaced_once_then_explained(fake):
    c = client(fake)
    acct = c.brokerage_account()
    fake.token = "someone-elses-newer-token"  # another program issued a token with the same key: ours is revoked
    c.holdings(acct.seq)  # one fresh token, then it works
    assert c.issued == 2
    # every token we issue is revoked right away (a second program issuing in a loop): explained, no endless re-issue
    orig = fake.handle

    def revoke_after_issue(req: httpx.Request) -> httpx.Response:
        r = orig(req)
        if req.url.path == "/oauth2/token":
            fake.token = "revoked-by-other"
        return r
    c2 = TossClient(fake.client_id, fake.secret, transport=httpx.MockTransport(revoke_after_issue), clock=Clock(), sleep=lambda s: None)
    with pytest.raises(TossError) as e:
        c2.accounts()
    assert e.value.kind == "TOKEN_REVOKED" and "다른 프로그램" in e.value.text and c2.issued == 2


def test_two_threads_never_issue_two_tokens(fake):
    import time

    slow = fake.handle

    def slow_token(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth2/token":
            time.sleep(0.05)
        return slow(req)
    c = TossClient(fake.client_id, fake.secret, transport=httpx.MockTransport(slow_token))
    errs: list[Exception] = []

    def run() -> None:
        try:
            c.accounts()
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert errs == [] and c.issued == 1


@pytest.mark.parametrize(("setup", "kind", "words"), [
    (lambda f: setattr(f, "ip_allowed", False), "IP_NOT_ALLOWED", "허용 IP 관리"),
    (lambda f: setattr(f, "secret", "rotated"), "BAD_KEY", "client_secret"),
    (lambda f: setattr(f, "accounts", []), "NO_ACCOUNT", "종합매매"),
    (lambda f: setattr(f, "accounts", [{"accountNo": "1", "accountSeq": 3, "accountType": "PENSION_SAVINGS"}]), "NO_ACCOUNT", "종합매매"),
])
def test_failures_say_what_to_do(fake, setup, kind, words):
    c = client(fake)  # the key as it was entered
    setup(fake)
    with pytest.raises(TossError) as e:
        c.brokerage_account()
    assert e.value.kind == kind and words in e.value.text


def test_not_configured_makes_no_request(fake):
    c = client(fake, cid="", secret="")
    with pytest.raises(TossError) as e:
        c.accounts()
    assert e.value.kind == "NOT_CONFIGURED" and fake.requests == []


def test_rate_limit_waits_retry_after_then_gives_up_without_a_storm(fake):
    clk = Clock()
    c = client(fake, clk)
    c.accounts()
    r429 = lambda ra: httpx.Response(429, headers={"Retry-After": ra, "X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "0"},  # noqa: E731
                                     json={"error": {"requestId": "r", "code": "rate-limit-exceeded", "message": ""}})
    fake.faults = [r429("2")]
    assert c.accounts()  # waited 2 s, then fine
    assert 2.0 in clk.slept
    fake.faults = [r429("1"), r429("1"), r429("1")]
    with pytest.raises(TossError) as e:
        c.accounts()
    assert e.value.kind == "RATE_LIMITED"
    fake.faults = [r429("60")]  # a long wait is not done inside a request
    n = len(fake.requests)
    with pytest.raises(TossError):
        c.accounts()
    assert len(fake.requests) == n + 1


def test_server_and_network_failures_retry_once(fake):
    c = client(fake)
    c.accounts()
    fake.faults = [httpx.Response(500, json={"error": {"requestId": "r", "code": "internal", "message": ""}})]
    assert c.accounts()
    fake.faults = [httpx.ConnectError("down"), httpx.ConnectError("down")]
    with pytest.raises(TossError) as e:
        c.accounts()
    assert e.value.kind == "UNAVAILABLE"


def test_closed_orders_are_paged_to_the_end_and_only_fills_are_kept(fake):
    fake.orders += [order(900, "AAPL", "SELL", "3", "200.00", "2026-09-25", filled="0", status="CANCELED"),
                    order(901, "AAPL", "SELL", "3", "200.00", "2026-09-26", filled="1", status="CANCELED"),
                    order(902, "005930", "BUY", "5", "70000", "2026-09-26", cur="KRW")]
    c = client(fake)
    seq = c.brokerage_account().seq
    fills, complete = c.fills(seq, date(2026, 9, 1), date(2026, 9, 30))
    assert complete and len(fills) == 130 + 2  # the unfilled cancel is not a fill; the partly filled one counts 1 share
    part = next(f for f in fills if f.order_id == "ord0901")
    assert part.quantity == Decimal("1") and part.status == "CANCELED"
    assert next(f for f in fills if f.order_id == "ord0902").symbol == "005930"
    pages = [r for r in fake.requests if r.url.path == "/api/v1/orders"]
    assert len(pages) == 2 and all("limit=100" in str(r.url) for r in pages)
    few, complete = c.fills(seq, date(2026, 9, 1), date(2026, 9, 30), max_pages=1)
    assert not complete and len(few) == 99  # the first 100 orders hold the unfilled cancel


def test_malformed_payloads_are_bad_data_never_guessed():
    with pytest.raises(TossError) as e:
        parse_holdings({"items": [{"symbol": "X", "marketCountry": "US", "quantity": "ten"}]})
    assert e.value.kind == "BAD_DATA"
    with pytest.raises(TossError):
        parse_holdings({"nope": []})
    with pytest.raises(TossError):
        dec("NaN", "x")
    with pytest.raises(TossError):
        dec(1.5, "x")  # a float is not the spec's decimal string
    assert dec(None, "x", required=False) is None
    h = holding("QQQX", "1", "1", "1")
    h["marketCountry"] = "JP"  # an unknown enum value (the spec: clients must tolerate them)
    assert parse_holdings({"items": [h]})[0].market == "OTHER"
    o = order(1, "NVDA", "BUY", "1", "1", "2026-09-01")
    del o["orderedAt"]
    with pytest.raises(TossError):
        parse_fills([o])


def test_no_secret_or_account_number_leaves_the_client(fake, caplog):
    from marketlens.infrastructure.logging import redact_text

    c = client(fake)
    acct = c.brokerage_account()
    assert acct.masked == "····8901" and "12345678901" not in repr(acct) and mask_account("12") == "····"
    tok = fake.token
    assert redact_text(f"id {fake.client_id} secret {fake.secret} token {tok}").count("REDACTED") == 3
    fake.secret = "changed"
    fake.token = None
    c2 = client(fake, secret="wrong-secret-123456")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TossError) as e:
            c2.accounts()
    text = str(e.value) + repr(e.value.as_dict()) + caplog.text
    assert "wrong-secret-123456" not in text and "s3cr3t" not in text


def test_the_client_has_no_order_endpoint():
    import marketlens.providers.live.toss as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert '"POST", "/api/v1' not in src and "/cancel" not in src and "/modify" not in src and "conditional-orders" not in src
    assert not any(n for n in dir(TossClient) if "order" in n.lower() and n != "fills")
