"""Findings of a second, independent evaluator (a partial report: the run was cut off before its tests were
delivered). Each defect was reproduced from its one-line description and fixed. The tests of the six defects fail on the
code before the fix (commit e66855c: 12 of 15); the other three guard behaviour that must not change.

1. BUY kept although the price had moved above the maximum buy price.
2. After-hours earnings treated as public before they were released.
3. "$900 million to $1.1 billion" guidance: wrong unit (900 billion).
4. A company missing from the listing for a while treated as delisted.
5. After the recommendation moved to HOLD, the stop was no longer watched.
6. After a stock split, price and consensus estimates on different share bases."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.domain.decision import DecisionThresholds, decide
from marketlens.domain.enums import Action
from tests.helpers import card, ctx, plan

UTC = timezone.utc
TH = DecisionThresholds()


# ---------------------------------------------------------------- 1. BUY above the maximum buy price
def test_a_previous_buy_is_not_kept_once_the_price_is_above_the_max_buy_price():
    """No material change (the price crossed the max buy price inside the ATR buffer): the flip-flop guard kept
    BUY although the plan itself rules out buying at that price."""
    d = decide(card(85), plan(price=103, max_buy=102), ctx(previous_action=Action.BUY, material_changes=()), TH)
    assert d.action == Action.WAIT, (d.action, d.notes)
    d = decide(card(74), plan(price=103, max_buy=102), ctx(previous_action=Action.BUY_SMALL, material_changes=()), TH)
    assert d.action == Action.WAIT


def test_the_flip_flop_guard_still_keeps_a_valid_buy():
    d = decide(card(78), plan(price=100, max_buy=102), ctx(previous_action=Action.BUY, material_changes=()), TH)
    assert d.action == Action.BUY


def test_a_previous_add_is_not_kept_outside_the_add_zone():
    d = decide(card(85), plan(price=101, add=(95.0, 98.0)), ctx(previous_action=Action.ADD, held=True, material_changes=()), TH)
    assert d.action == Action.HOLD


# ---------------------------------------------------------------- 2. after-hours earnings
def test_an_after_hours_release_is_not_public_at_the_close():
    from marketlens.application.pipeline import earnings_visible
    from marketlens.domain.earnings import EarningsReport, pair_with_releases

    rows = [{"period": date(2026, 7, 26), "actual": 2.05, "estimate": 1.98, "quarter": 2, "year": 2027}]
    released = datetime(2026, 8, 26, 20, 21, tzinfo=UTC)  # 16:21 New York
    (r,) = pair_with_releases(rows, [released], "x")
    assert r.released_at == released
    assert not earnings_visible(r, datetime(2026, 8, 26, 20, 5, tzinfo=UTC))  # 16:05 ET: after the close, before the release
    assert earnings_visible(r, datetime(2026, 8, 26, 20, 30, tzinfo=UTC))
    # without a release time, a report dated today is public only from the next day
    undated = EarningsReport(date(2026, 8, 26), "Q2", "finnhub", eps_actual=2.05)
    assert not earnings_visible(undated, datetime(2026, 8, 26, 23, 0, tzinfo=UTC))
    assert earnings_visible(undated, datetime(2026, 8, 27, 14, 0, tzinfo=UTC))


# ---------------------------------------------------------------- 3. mixed scale words
@pytest.mark.parametrize("sentence,lo,hi", [
    ("We expect fourth quarter revenue of $900 million to $1.1 billion.", 900e6, 1.1e9),
    ("We expect revenue between $950 million and $1.05 billion for fiscal 2027.", 950e6, 1.05e9),
    ("We expect revenue of $3.2 to $3.4 billion for the fourth quarter.", 3.2e9, 3.4e9),
    ("We expect capital expenditures of $900 million - $1.1 billion in fiscal 2027.", 900e6, 1.1e9),
])
def test_each_end_of_a_range_keeps_its_own_scale(sentence, lo, hi):
    from marketlens.domain.guidance import extract

    (i,) = extract(sentence)
    assert i.status == "EXTRACTED" and i.low == pytest.approx(lo) and i.high == pytest.approx(hi)


def test_a_range_that_does_not_read_low_to_high_is_never_guessed():
    from marketlens.domain.guidance import extract

    (i,) = extract("We expect revenue of $1.1 billion to $900 million for fiscal 2027.")
    assert i.status == "GUIDANCE_UNCLEAR"


# ---------------------------------------------------------------- 4. missing from the listing for a while
def _store(tmp_path):  # noqa: ANN001, ANN202
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


def test_a_name_missing_from_the_listing_while_it_trades_is_not_delisted(tmp_path):
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security
    from marketlens.infrastructure.db import repository as repo

    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    st.sync_universe([Security("ABC", "ABC", Exchange.NASDAQ, "Tech", "x", None, cik=7)], d1)
    st.save_grouped(d2, {"ABC": Bar(d2, 10, 10, 10, 10, 1e6)}, "polygon")  # it traded on d2
    st.sync_universe([], d2)  # but the SEC file omitted it that day
    assert any(s.ticker == "ABC" for s in st.securities(d2))
    with st.sf() as s:
        assert repo.delisted_on(s, "ABC", "LIVE") is None


def test_a_recorded_delisting_is_not_final_while_the_name_keeps_trading(tmp_path):
    """Recorded by an earlier sync (no bars yet), then its bars keep arriving: outcomes must not close the
    recommendation at a "last price"."""
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security
    from marketlens.infrastructure.db import repository as repo

    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    st.sync_universe([Security("ABC", "ABC", Exchange.NASDAQ, "Tech", "x", None, cik=7)], d1)
    st.sync_universe([], d2)
    with st.sf() as s:
        assert repo.delisted_on(s, "ABC", "LIVE") == d2
    st.save_grouped(date(2026, 6, 3), {"ABC": Bar(date(2026, 6, 3), 10, 10, 10, 10, 1e6)}, "polygon")
    with st.sf() as s:
        assert repo.delisted_on(s, "ABC", "LIVE") is None


# ---------------------------------------------------------------- 5. stop watched after BUY → HOLD
def _digest(action: str, stop: float | None, guard: float | None = None):  # noqa: ANN202
    from marketlens.domain.what_changed import AnalysisDigest

    return AnalysisDigest(ticker="T", as_of=datetime(2026, 6, 1, 20, tzinfo=UTC), score=80.0, action=action, price=100.0, in_buy_zone=True,
                          stop_breached=False, rr=2.5, eps_revision_30d=None, revenue_revision_30d=None, last_earnings_date=None,
                          guidance_signature=None, issue_ids=(), major_issue_ids=(), regime=None, us10y=None, thesis_invalidated=False,
                          components={}, stop=stop, guard_stop=guard)


def test_the_buy_stop_is_carried_while_the_position_is_held_after_hold():
    from marketlens.application.pipeline import carried_guard_stop, watched_stop

    buy = _digest("BUY", stop=92.0)
    assert watched_stop(buy, held=True) == 92.0
    # the next analysis says HOLD: the buy's stop is stored with it …
    guard = carried_guard_stop(Action.HOLD, plan_stop=85.0, watch=watched_stop(buy, True), held=True)
    assert guard == 92.0
    # … and the analysis after that still watches it (before the fix: None → a close below 92 went unnoticed)
    assert watched_stop(_digest("HOLD", stop=85.0, guard=guard), held=True) == 92.0
    # not held: a HOLD / WATCH carries nothing
    assert watched_stop(_digest("HOLD", stop=85.0, guard=92.0), held=False) is None


def test_a_close_below_the_carried_stop_sells_a_held_position():
    d = decide(card(70), plan(price=90), ctx(held=True, previous_action=Action.HOLD, prior_stop_breached=True, material_changes=()), TH)
    assert d.action == Action.SELL


def test_the_digest_field_survives_the_snapshot_codec():
    from marketlens.application.codec import decode, encode
    from marketlens.domain.what_changed import AnalysisDigest

    d = _digest("HOLD", stop=85.0, guard=92.0)
    assert decode(AnalysisDigest, encode(d)).guard_stop == 92.0
    old = encode(d)
    old.pop("guard_stop")  # a snapshot stored before this field existed
    assert decode(AnalysisDigest, old).guard_stop is None


# ---------------------------------------------------------------- 6. split basis of the consensus
def test_a_consensus_observed_before_a_split_is_put_on_the_post_split_basis():
    from marketlens.application.estimate_book import build
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.estimates import EstimateObservation

    split = SplitEvent("NVDA", date(2026, 6, 10), 1, 10, "polygon")
    hist = [EstimateObservation("NVDA", "alphavantage", "annual:2027-01-31", "annual", date(2027, 1, 31), date(2026, 6, 8), eps=30.0,
                                eps_high=33.0, eps_low=27.0, analyst_count=40, provider_revisions={"eps_30d_ago": 29.0, "up_30d": 5.0}),
            EstimateObservation("NVDA", "alphavantage", "annual:2027-01-31", "annual", date(2027, 1, 31), date(2026, 6, 20), eps=3.1,
                                analyst_count=40, provider_revisions={"eps_30d_ago": 3.0})]
    day = date(2026, 6, 12)
    rep = build("NVDA", hist[:1], day, [split])
    assert rep.snapshot is not None and rep.snapshot.forward_eps is not None and rep.snapshot.forward_eps < 4  # 3.0 basis, not 30
    # the split is not a −90 % revision
    later = build("NVDA", hist, date(2026, 6, 25), [split])
    assert later.snapshot is not None
    for k in ("7d", "30d"):
        v = later.revisions[k].value
        assert v is None or abs(v) < 0.2, (k, v)
    # before the split nothing changes
    assert build("NVDA", hist[:1], date(2026, 6, 9), [split]).snapshot.forward_eps > 20


_ = timedelta
