"""A-grade acceptance: restatements, splits and point-in-time fundamentals through the production parser."""

from __future__ import annotations

from datetime import date

from marketlens.domain.corporate_actions import SplitEvent, normalize_quarters, split_factor
from marketlens.domain.fundamentals import FIRST_REPORTED, LATEST_KNOWN_AS_OF, as_of
from marketlens.providers.live.sec_edgar import parse_company_facts


def _f(val, end, start, filed, form="10-Q"):
    return {"val": val, "end": end, "start": start, "filed": filed, "form": form, "fy": 2024, "fp": "Q1"}


def _facts(eps_items):
    rev = [_f(100e9 + i, e, s, fd) for i, (e, s, fd) in enumerate([("2024-03-31", "2024-01-01", "2024-05-01"), ("2024-06-30", "2024-04-01", "2024-08-01"),
                                                                    ("2024-09-30", "2024-07-01", "2024-11-01"), ("2024-12-31", "2024-10-01", "2025-02-01")])]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "EarningsPerShareDiluted": {"units": {"USD/shares": eps_items}}}}}


def test_restated_value_is_used_only_after_it_was_filed():
    """Audit P0: first filing EPS 10, a later filing reported the same period as 1 → parser kept 10 forever."""
    qs = parse_company_facts(_facts([_f(10.0, "2024-03-31", "2024-01-01", "2024-05-01"), _f(1.0, "2024-03-31", "2024-01-01", "2025-05-01")]), "T")
    q1 = next(q for q in qs if q.period_end == date(2024, 3, 31))
    assert q1.eps_diluted == 10.0 and q1.revisions["eps_diluted"] == ((date(2025, 5, 1), 1.0),)  # nothing overwritten
    before = next(q for q in as_of(qs, date(2025, 1, 1)) if q.period_end == date(2024, 3, 31))
    after = next(q for q in as_of(qs, date(2025, 6, 1)) if q.period_end == date(2024, 3, 31))
    assert before.eps_diluted == 10.0 and before.restated == ()  # the restatement was not public yet
    assert after.eps_diluted == 1.0 and after.restated == ("eps_diluted",)
    research = next(q for q in as_of(qs, date(2025, 6, 1), basis=FIRST_REPORTED) if q.period_end == date(2024, 3, 31))
    assert research.eps_diluted == 10.0 and LATEST_KNOWN_AS_OF != FIRST_REPORTED


def test_split_puts_eps_and_prices_on_one_basis():
    """10-for-1 split on 2024-06-10: EPS filed before it is divided by 10, comparatives filed after are not."""
    qs = parse_company_facts(_facts([_f(6.0, "2024-03-31", "2024-01-01", "2024-05-01"), _f(0.68, "2024-06-30", "2024-04-01", "2024-08-01")]), "NVDA")
    split = (SplitEvent("NVDA", date(2024, 6, 10), 1, 10, "polygon"),)
    norm, notes = normalize_quarters(as_of(qs, date(2024, 9, 1)), split, date(2024, 9, 1))
    by = {q.period_end: q for q in norm}
    assert abs(by[date(2024, 3, 31)].eps_diluted - 0.6) < 1e-12 and by[date(2024, 6, 30)].eps_diluted == 0.68
    assert any("주식분할" in n for n in notes)
    # a basis date before the split leaves everything unchanged (no future split applied)
    early, _ = normalize_quarters(as_of(qs, date(2024, 5, 20)), split, date(2024, 5, 20))
    assert early[0].eps_diluted == 6.0
    assert split_factor(split, date(2024, 5, 1), date(2024, 9, 1)) == 10 and split_factor(split, date(2024, 7, 1), date(2024, 9, 1)) == 1


def test_stored_prefsplit_bars_are_rescaled_once(tmp_path):
    from datetime import datetime, timezone

    from marketlens.application.market_store import MarketStore
    from marketlens.domain.market import Bar
    from marketlens.infrastructure.db.models import Base, PriceBarRow
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    st = MarketStore(make_session_factory(eng), "LIVE")
    st.save_bars("NVDA", [Bar(date(2024, 6, 7), 1200, 1210, 1190, 1200, 1e6)], "polygon")
    with st.sf() as s:  # the row was retrieved before the split took effect
        s.get(PriceBarRow, ("NVDA", date(2024, 6, 7), "polygon")).retrieved_at = datetime(2024, 6, 8, tzinfo=timezone.utc)
        s.commit()
    st.save_splits([SplitEvent("NVDA", date(2024, 6, 10), 1, 10, "polygon")])
    assert st.adjust_bars_for_splits() == 1 and st.adjust_bars_for_splits() == 0  # idempotent
    b = st.bars("NVDA", date(2024, 6, 1), date(2024, 6, 30))[0]
    assert b.close == 120 and b.volume == 1e7


def test_finra_uses_oauth_bearer_token_not_basic_credentials_on_data_requests():
    """Audit P1: credentials were sent as Basic auth straight to the data endpoint."""
    from marketlens.providers.live.finra import FinraShortInterestProvider
    from tests.live_fixtures import live_transport

    seen: list[str] = []
    headers: list[str] = []
    base = live_transport(seen)

    class Spy:
        def handle_request(self, req):  # noqa: ANN001
            headers.append(f"{req.url.host} {req.headers.get('authorization', '')[:6]}")
            return base.handle_request(req)

    p = FinraShortInterestProvider("key", "secret", transport=Spy())
    snap = p.get_short_interest("NVDA")
    p.get_short_interest("NVDA")
    assert snap.short_interest_shares is not None
    assert headers.count("ews.fip.finra.org Basic ") == 1  # one token request, cached
    assert all(h == "api.finra.org Bearer" for h in headers if h.startswith("api.finra.org"))
