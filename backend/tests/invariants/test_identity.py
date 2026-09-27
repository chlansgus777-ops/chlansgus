"""Invariant: everything follows the COMPANY, never the ticker label. After a rename (same CIK, new ticker), a reuse (the
ticker now names another CIK) and a relisting (the same CIK back after a delisting), bars, fundamentals, the
recommendation history, the previous recommendation and paper trading join exactly the records of the same company.
One function decides "same company": MarketStore.company_id(ticker, day)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from marketlens.domain.enums import Exchange
from marketlens.domain.market import Bar, Security

UTC = timezone.utc
D1, D2, D3, D4 = date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 15), date(2026, 9, 22)


def _sec(t: str, cik: int) -> Security:
    return Security(t, f"Co{cik}", Exchange.NASDAQ, "Technology", "Software", 1e10, cik=cik)


def _svc(tmp_path):  # noqa: ANN001, ANN202
    from tests.regression.test_eval9_followups import _live

    return _live(tmp_path)


def _rec(svc, ticker: str, day: date, action: str, stop: float = 90.0):  # noqa: ANN001, ANN202
    from marketlens.domain.market_calendar import NY
    from marketlens.infrastructure.db.models import RecommendationRow

    digest = {"ticker": ticker, "as_of": datetime.combine(day, time(10), tzinfo=NY).isoformat(), "score": 80.0, "components": {},
              "action": action, "price": 100.0, "in_buy_zone": True, "stop_breached": False, "rr": 2.5, "eps_revision_30d": None,
              "revenue_revision_30d": None, "last_earnings_date": None, "guidance_signature": None, "issue_ids": [], "major_issue_ids": [],
              "regime": None, "us10y": None, "thesis_invalidated": False, "stop": stop}
    with svc.sf() as s:
        row = RecommendationRow(ticker=ticker, as_of=datetime.combine(day, time(10), tzinfo=NY), mode="LIVE", price=100.0, score=80.0, confidence=60.0,
                                deterministic_action=action, final_action=action, result={"digest": digest, "entry": {"stop": stop}}, inputs={"splits": []},
                                data_quality="FRESH", committee_status="NOT_RUN", created_at=datetime.combine(day, time(15), tzinfo=UTC),
                                price_quality="FRESH", sector="Technology", sector_model="tech", regime="x", model_config_snapshot={},
                                input_fingerprint="t", scoring_model_version="t", decision_model_version="t", agent_prompt_version="t",
                                provider_version="t", config_version="t", schema_version="t")
        s.add(row)
        s.commit()
        return row.id


@pytest.fixture()
def world(tmp_path):  # noqa: ANN001, ANN201
    """A (CIK 1) is OLD, renamed NEW on D2. B (CIK 2) uses ABC until D2, when C (CIK 3) lists as ABC. E (CIK 5) is REL,
    missing from the listing from D2 (delisted), back on D4 (relisted)."""
    svc = _svc(tmp_path)
    st = svc.store
    st.save_grouped(D1, {"OLD": Bar(D1, 10, 10, 10, 10, 1e6), "ABC": Bar(D1, 20, 20, 20, 20, 1e6), "REL": Bar(D1, 30, 30, 30, 30, 1e6)}, "polygon")
    st.sync_universe([_sec("OLD", 1), _sec("ABC", 2), _sec("REL", 5)], D1)
    ids = {"old": _rec(svc, "OLD", D1, "BUY"), "abc_b": _rec(svc, "ABC", D1, "BUY"), "rel": _rec(svc, "REL", D1, "BUY")}
    st.save_grouped(D2, {"NEW": Bar(D2, 11, 11, 11, 11, 1e6), "ABC": Bar(D2, 5, 5, 5, 5, 1e6)}, "polygon")
    st.sync_universe([_sec("NEW", 1), _sec("ABC", 3)], D2)
    st.save_grouped(D3, {"NEW": Bar(D3, 12, 12, 12, 12, 1e6), "ABC": Bar(D3, 6, 6, 6, 6, 1e6)}, "polygon")
    st.sync_universe([_sec("NEW", 1), _sec("ABC", 3)], D3)
    st.save_grouped(D4, {"NEW": Bar(D4, 13, 13, 13, 13, 1e6), "ABC": Bar(D4, 7, 7, 7, 7, 1e6), "REL": Bar(D4, 31, 31, 31, 31, 1e6)}, "polygon")
    st.sync_universe([_sec("NEW", 1), _sec("ABC", 3), _sec("REL", 5)], D4)
    return svc, ids


def test_one_function_says_which_company(world):
    svc, _ = world
    cid = svc.store.company_id
    assert cid("OLD", D1) == cid("NEW", D4)            # rename: the same company
    assert cid("ABC", D1) != cid("ABC", D4)            # reuse: another company
    assert cid("REL", D1) == cid("REL", D4)            # relisted: the same company


def test_bars_follow_the_company(world):
    svc, _ = world
    new = svc.store.bars("NEW", D1, D4)
    assert [b.close for b in new] == [10, 11, 12, 13]  # OLD's history joins NEW's
    assert [b.close for b in svc.store.bars("ABC", D1, D4)] == [5, 6, 7]  # C only: B's 20 is not C's history


def test_the_previous_recommendation_follows_the_company(world):
    svc, ids = world
    at = datetime.combine(D4, time(16), tzinfo=UTC)
    with svc.sf() as s:
        look = svc._previous_lookup(s)
        d_new, a_new = look("NEW", at)
        d_abc, a_abc = look("ABC", at)
        d_rel, a_rel = look("REL", at)
    assert d_new is not None and a_new is not None and d_new.ticker == "OLD"  # the renamed company keeps its stop
    assert d_abc is None and a_abc is None                                    # C inherits nothing from B
    assert d_rel is not None                                                  # the relisted company is the same company


def test_the_recommendation_history_follows_the_company(world):
    svc, ids = world
    with svc.sf() as s:
        new_hist = [r.id for r in svc.company_recommendations(s, "NEW", datetime.combine(D4, time(16), tzinfo=UTC))]
        abc_hist = [r.id for r in svc.company_recommendations(s, "ABC", datetime.combine(D4, time(16), tzinfo=UTC))]
    assert ids["old"] in new_hist and ids["abc_b"] not in new_hist
    assert ids["abc_b"] not in abc_hist


def test_paper_exits_follow_the_company(world):
    """A paper position opened on OLD's BUY is closed by the company's later SELL, published under NEW."""
    svc, ids = world
    sell = _rec(svc, "NEW", D3, "SELL")
    from marketlens.infrastructure.db import repository as repo

    with svc.sf() as s:
        old = repo.get_recommendation(s, ids["old"])
        later = svc.company_recommendations(s, "OLD", datetime.combine(D4, time(16), tzinfo=UTC), on=D1)
        assert old is not None and sell in [r.id for r in later]
        b_rec = repo.get_recommendation(s, ids["abc_b"])
        later_b = svc.company_recommendations(s, "ABC", datetime.combine(D4, time(16), tzinfo=UTC), on=D1)
        assert b_rec is not None and all(r.id == ids["abc_b"] for r in later_b)  # C's recommendations never close B's position
