"""Regression tests for every defect listed in the external audit (section 11 of the requirements).

Each test reproduces the audited failure mode and asserts the corrected behaviour. None of them relax an
existing assertion; they add constraints.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from marketlens.application.codec import decode
from marketlens.application.issue_engine import build_issues, news_for_ticker, relevance, title_similarity
from marketlens.application.pipeline import AnalysisInputs, run_analysis
from marketlens.domain.decision import decide
from marketlens.domain.entry import EntryConfig, build_entry_plan
from marketlens.domain.enums import Action, DataQuality, Exchange, HardVeto
from marketlens.domain.freshness import check_age, recommendation_freshness
from marketlens.domain.fundamentals import QuarterlyFinancials, as_of
from marketlens.domain.indicators import TechnicalSnapshot
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import add_trading_days
from marketlens.domain.paper import AccountItem, PaperConfig, PaperSignal, simulate_account
from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, review_candidate
from marketlens.domain.sector_models import select_sector_model
from marketlens.domain.what_changed import diff, material_reasons
from marketlens.providers.contracts import NewsItem
from marketlens.providers.live.finnhub import FinnhubProvider
from marketlens.providers.live.sec_edgar import parse_company_facts, parse_submissions_profile
from marketlens.providers.live.sic import classify_sic
from tests.helpers import card, ctx, plan

UTC = timezone.utc
FIX = Path(__file__).parents[1] / "golden" / "fixtures"


def golden(t: str = "NVDA") -> AnalysisInputs:
    return decode(AnalysisInputs, json.loads((FIX / f"{t}.json").read_text()))


# ---------------------------------------------------------------- P0-A data freshness


def test_three_year_old_financials_are_never_fresh(cfg):
    inp = golden()
    shift = timedelta(days=3 * 365)
    old = tuple(replace(q, period_end=q.period_end - shift, filed_date=q.filed_date - shift, field_filed={}) for q in inp.quarters)
    chk = check_age("fundamentals", old[-1].period_end, inp.as_of)
    assert chk.quality == DataQuality.STALE and not chk.usable
    r = run_analysis(replace(inp, quarters=old), cfg)
    fund = next(c for c in r.data_quality.checks if c.data_type == "fundamentals")
    assert fund.quality == DataQuality.STALE and "오래됨" in fund.reason_ko
    assert HardVeto.STALE_CORE_DATA in r.decision.vetoes and r.decision.action == Action.DATA_INSUFFICIENT


def test_old_daily_bars_are_never_fresh(cfg):
    inp = golden()
    last = inp.bars[-1].day
    cutoff = add_trading_days(last, -30)
    old_bars = tuple(b for b in inp.bars if b.day <= cutoff)
    r = run_analysis(replace(inp, bars=old_bars), cfg)
    hist = next(c for c in r.data_quality.checks if c.data_type == "price_history")
    assert hist.quality == DataQuality.STALE and hist.age is not None and hist.age >= 30
    assert HardVeto.STALE_CORE_DATA in r.decision.vetoes and r.decision.action == Action.DATA_INSUFFICIENT


def test_row_counts_alone_never_make_data_fresh():
    """Twelve quarters present but all ancient → still STALE (the old check was 'len(quarters) >= 4')."""
    now = datetime(2026, 9, 25, 15, tzinfo=UTC)
    assert check_age("fundamentals", date(2023, 6, 30), now).quality == DataQuality.STALE
    assert check_age("fundamentals", date(2026, 6, 30), now).quality == DataQuality.FRESH
    assert check_age("price_history", date(2026, 9, 24), now).quality == DataQuality.FRESH
    assert check_age("price_history", date(2026, 9, 10), now).quality == DataQuality.STALE
    assert check_age("analyst", date(2026, 7, 1), now).quality == DataQuality.STALE
    assert check_age("fundamentals", date(2026, 10, 1), now).quality == DataQuality.CONFLICTING  # from the future


def test_stored_recommendation_freshness_is_rejudged_now():
    made = datetime(2026, 9, 21, 15, tzinfo=UTC)  # Monday 11:00 ET
    soon = recommendation_freshness(made, "FRESH", made + timedelta(minutes=10))
    assert soon.status == "CURRENT" and "추천 당시 데이터 최신" in soon.reason_ko
    # stricter since evaluation 3 (N11): 30 minutes of trading without a newer quote needs a price check
    assert recommendation_freshness(made, "FRESH", made + timedelta(minutes=30)).status == "NEEDS_REVALIDATION"
    # 3 hours later in the same session the price may have moved: not CURRENT without a current quote
    # (the second audit found "same day = CURRENT" let a 6-hour-old intraday BUY look actionable)
    same_day = recommendation_freshness(made, "FRESH", made + timedelta(hours=3))
    assert same_day.status == "NEEDS_REVALIDATION" and "추천 당시 데이터 최신" in same_day.reason_ko
    later = recommendation_freshness(made, "FRESH", datetime(2026, 9, 25, 15, tzinfo=UTC))
    assert later.status == "EXPIRED" and later.at_creation == "FRESH" and later.sessions_since >= 3
    stale_then = recommendation_freshness(made, "STALE", made + timedelta(minutes=5))
    assert stale_then.status == "EXPIRED"


# ---------------------------------------------------------------- P0-B portfolio risk gate


def test_portfolio_cap_blocks_add():
    held_ctx = ctx(held=True, previous_action=None, portfolio_size_cap="WATCH")
    d = decide(card(85), plan(price=96.5, add=(95.0, 98.0)), held_ctx)
    assert d.raw_action == Action.ADD and d.action == Action.HOLD and d.size_limit == "WATCH"


def test_portfolio_cap_blocks_buy_small():
    d = decide(card(74), plan(), ctx(portfolio_size_cap="WATCH"))
    assert d.raw_action == Action.BUY_SMALL and d.action == Action.WATCH


def test_add_is_judged_on_post_add_weight():
    """Holding already at 9% of the account: another full 5% slice would breach the 10% single-name cap."""
    pf = Portfolio((Holding("NVDA", 90, 100, "Technology"),), 91_000)
    r = review_candidate(pf, {"NVDA": 100}, CandidateProfile("NVDA", "Technology", (), 0.0), {})
    assert r.size_cap.value in ("SMALL", "WATCH") and any("NVDA" in w for w in r.warnings)


def test_add_requires_reward_to_risk():
    thin = plan(price=96.5, stop=95.5, t1=97.5, add=(95.8, 98.0))  # R/R = 1.0 < 2.0
    d = decide(card(85), thin, ctx(held=True))
    assert d.action == Action.HOLD and any("손익비" in r for r in d.reasons)


# ---------------------------------------------------------------- P0-C SEC point-in-time


def _sec_fact(val, end, start, filed, form="10-Q"):
    return {"val": val, "end": end, "start": start, "filed": filed, "form": form, "fy": 2026, "fp": "Q1"}


def test_field_published_in_august_is_invisible_to_a_may_analysis():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_sec_fact(100, "2026-03-31", "2026-01-01", "2026-05-01")]}},
        "NetIncomeLoss": {"units": {"USD": [_sec_fact(10, "2026-03-31", "2026-01-01", "2026-05-01")]}},
        # the Q1 stock-comp figure first appears as a comparative in the Q2 10-Q filed in August
        "ShareBasedCompensation": {"units": {"USD": [_sec_fact(7, "2026-03-31", "2026-01-01", "2026-08-05")]}},
    }}}
    (q1,) = parse_company_facts(facts, "X")
    assert q1.filed_date == date(2026, 5, 1) and q1.field_filed["sbc"] == date(2026, 8, 5)
    may = as_of([q1], date(2026, 5, 20))
    assert may and may[0].revenue == 100 and may[0].sbc is None  # the August value never leaks into May
    assert as_of([q1], date(2026, 8, 6))[0].sbc == 7


def test_restatement_does_not_overwrite_the_original_and_amendments_are_ignored():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_sec_fact(100, "2026-03-31", "2026-01-01", "2026-05-01"), _sec_fact(90, "2026-03-31", "2026-01-01", "2026-09-01", "10-Q/A")]}},
        "NetIncomeLoss": {"units": {"USD": [_sec_fact(10, "2026-03-31", "2026-01-01", "2026-05-01")]}},
    }}}
    (q1,) = parse_company_facts(facts, "X")
    assert q1.revenue == 100


def test_missing_q1_never_turns_half_year_cash_flow_into_a_quarter():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_sec_fact(100, "2026-06-30", "2026-04-01", "2026-08-01")]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_sec_fact(50, "2026-06-30", "2026-01-01", "2026-08-01")]}},  # H1 YTD, no Q1
    }}}
    (q2,) = parse_company_facts(facts, "X")
    assert q2.operating_cash_flow is None


def test_future_filing_in_inputs_is_masked_by_the_pipeline(cfg):
    inp = golden()
    last = inp.quarters[-1]
    future = replace(last, period_end=last.period_end + timedelta(days=91), filed_date=inp.as_of.date() + timedelta(days=30), revenue=9e15, field_filed={})
    r = run_analysis(replace(inp, quarters=inp.quarters + (future,)), cfg)
    assert r.metrics is not None and r.metrics.latest_period == last.period_end


# ---------------------------------------------------------------- P1 news


def test_market_news_request_uses_the_market_news_path():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=[])

    p = FinnhubProvider("k", transport=httpx.MockTransport(handler), rate_per_s=1000)
    assert p.get_news(datetime(2026, 9, 24, tzinfo=UTC), None) == []
    assert seen[0].url.path == "/api/v1/news" and seen[0].url.params["category"] == "general"
    p.get_news(datetime(2026, 9, 24, tzinfo=UTC), ["NVDA"])
    assert seen[1].url.path == "/api/v1/company-news" and seen[1].url.params["symbol"] == "NVDA"


def _news(nid, title, summary, tickers, source="Reuters", stype="WIRE", url=None, hours=1):
    return NewsItem(nid, datetime(2026, 9, 25, 14, tzinfo=UTC) - timedelta(hours=hours), title, summary, url or f"https://x/{nid}", source, stype, tickers)


def test_irrelevant_news_never_reaches_the_ai_context():
    items = [
        _news("a", "NVIDIA raises data-center guidance", "NVIDIA lifts outlook.", ("NVDA",)),
        _news("b", "Ten stocks to watch", "Round-up.", ("NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL")),
        _news("c", "Dividend ideas for retirees", "Income picks.", ("NVDA",)),
    ]
    res = build_issues(items, datetime(2026, 9, 25, 15, tzinfo=UTC), {"NVDA": "NVIDIA CORP"})
    arts = news_for_ticker(res, "NVDA", [i.issue_id for i in res.issues])
    assert [a.news_id for a in arts] == ["a"]
    assert relevance(items[1], "NVDA", "NVIDIA CORP") < 0.5 and relevance(items[2], "NVDA", "NVIDIA CORP") < 0.5


def test_syndicated_copies_do_not_inflate_importance():
    base = "NVIDIA raises data-center guidance"
    one = build_issues([_news("a", base, "NVIDIA lifts outlook.", ("NVDA",))], datetime(2026, 9, 25, 15, tzinfo=UTC), {"NVDA": "NVIDIA"})
    copies = [_news(f"s{i}", base + (" - Yahoo Finance" if i % 2 else ""), "NVIDIA lifts outlook.", ("NVDA",), source=f"Aggregator{i}", stype="COMMERCIAL", hours=1 + i / 10) for i in range(6)]
    many = build_issues([_news("a", base, "NVIDIA lifts outlook.", ("NVDA",))] + copies, datetime(2026, 9, 25, 15, tzinfo=UTC), {"NVDA": "NVIDIA"})
    assert len(many.issues) == 1 and many.syndicated_dropped == 6
    assert many.issues[0].importance == one.issues[0].importance


def test_headline_similarity_beats_plain_jaccard_on_rewrites():
    assert title_similarity("NVIDIA raises guidance on AI demand", "Nvidia raised its guidance, citing AI demand") >= 0.55
    assert title_similarity("NVIDIA raises guidance", "Oil jumps on supply disruption") < 0.3


def test_news_fetch_failure_is_not_the_same_as_no_news(cfg):
    inp = golden()
    ok = run_analysis(replace(inp, issues=()), cfg)
    failed = run_analysis(replace(inp, issues=(), missing_reasons={**inp.missing_reasons, "news": "HTTP 503"}), cfg)
    assert ok.issue_score_swing == 0.0
    assert failed.issue_score_swing is None
    assert next(c for c in failed.data_quality.checks if c.data_type == "news").quality == DataQuality.MISSING


# ---------------------------------------------------------------- P1 sector routing (free SIC data)


@pytest.mark.parametrize("ticker,sic,expected", [("NVDA", 3674, "semiconductor"), ("JPM", 6021, "financial"), ("TSM", 3674, "semiconductor"), ("DUK", 4911, "utilities"), ("ATO", 4932, "utilities"), ("XOM", 2911, "energy")])
def test_sector_routing_from_sec_sic_codes(cfg, ticker, sic, expected):
    sector, industry = classify_sic(sic)
    sec = Security(ticker, ticker, Exchange.NYSE, sector, industry, 1e11)
    assert select_sector_model(sec, cfg.sector_models)[0].model_id == expected


def test_foreign_filer_profile_is_flagged_adr():
    prof = parse_submissions_profile({"sic": "3674", "filings": {"recent": {"form": ["20-F", "6-K"]}}, "addresses": {"business": {"stateOrCountryDescription": "Taiwan"}}})
    assert prof["foreign_issuer"] and prof["industry"] == "Semiconductors" and prof["country"] == "Taiwan"


def test_unknown_sector_lowers_confidence_and_blocks_full_buy(cfg):
    inp = golden()
    known = run_analysis(inp, cfg)
    unk = run_analysis(replace(inp, security=replace(inp.security, sector="Unknown", industry="Unknown")), cfg)
    assert not unk.sector_known and unk.sector_model_id == "generic" and "업종 분류 정보 없음" in unk.sector_model_reason
    assert unk.decision.action != Action.BUY
    assert unk.decision.confidence <= known.decision.confidence


# ---------------------------------------------------------------- P1 entry / risk


def _tech(**kw) -> TechnicalSnapshot:
    base = dict(last_close=100.0, sma20=None, sma50=None, sma100=None, sma200=None, ema20=None, ema50=None, rsi14=50.0, atr14=2.0,
                high_20d=None, low_20d=None, high_52w=None, low_52w=None, volume_ratio=1.0, avg_volume_20d=1e6, avg_dollar_volume_20d=1e8,
                gap_pct=None, anchored_vwap=None, anchor_date=None, rs_3m=None, rs_6m=None, return_1m=None, return_3m=None, volatility_20d=0.3,
                supports=(), resistances=())
    base.update(kw)
    return TechnicalSnapshot(**base)


def test_gap_down_never_produces_stop_above_price():
    """After a gap from 110 to 100 the old supports (109, 106) and SMA50 (108) are ABOVE the price.
    The old engine used 109 as support → stop 108 > price 100 (a "long" plan that was already stopped out)."""
    p = build_entry_plan(100.0, _tech(last_close=110.0, supports=(109.0, 106.0), sma50=108.0, resistances=(115.0,)), EntryConfig())
    assert p is not None and p.support_used is None
    assert p.stop == pytest.approx(96.0) and p.stop < p.current_price < p.target1 < p.target2  # 2-ATR volatility stop


def test_long_plan_invariant_holds_for_any_technical_snapshot():
    import random

    rnd = random.Random(11)
    produced = 0
    for _ in range(2000):
        price = rnd.uniform(5, 500)
        atr = price * rnd.uniform(0.005, 0.08)
        lv = lambda: price * rnd.uniform(0.7, 1.3)  # noqa: E731 - levels on both sides of the price (gaps)
        tech = _tech(last_close=lv(), atr14=atr, supports=tuple(lv() for _ in range(3)), resistances=tuple(lv() for _ in range(3)),
                     sma20=lv(), sma50=lv(), sma200=lv(), anchored_vwap=lv(), high_52w=lv())
        p = build_entry_plan(price, tech, EntryConfig())
        if p is None:
            continue
        produced += 1
        assert p.stop < p.current_price < p.target1 < p.target2 and p.stop < p.max_buy < p.target1
    assert produced > 1000


def test_plan_with_stop_above_price_can_never_become_a_buy():
    bad = plan(price=100.0, max_buy=102.0, stop=105.0, t1=120.0)  # stale plan: price already below the stop
    for sc in (74.0, 85.0):
        assert decide(card(sc), bad, ctx()).action not in (Action.BUY, Action.BUY_SMALL, Action.ADD)


def test_prior_stop_breach_forces_exit_and_blocks_new_buy(cfg):
    """Stop semantics (one meaning everywhere): a session CLOSING at/below the previous stop exits a holder
    and blocks new buys; an intraday quote below it only blocks new buys (holders wait for the close)."""
    inp = golden()
    r0 = run_analysis(inp, cfg)
    last_close = inp.bars[-1].close
    earlier = r0.digest.as_of - timedelta(days=7)
    closed_below = replace(r0.digest, action="BUY", stop=last_close * 1.02, as_of=earlier)  # the last close is below it
    held = run_analysis(replace(inp, previous=closed_below, previous_action=Action.BUY, held=True), cfg)
    assert held.decision.action == Action.SELL and any("종가 기준 이탈" in x for x in held.decision.reasons)
    assert any("손절" in c.text for c in held.changes)
    flat = run_analysis(replace(inp, previous=closed_below, previous_action=Action.BUY, held=False), cfg)
    assert flat.decision.action not in (Action.BUY, Action.BUY_SMALL, Action.ADD)
    # intraday only: current price below the stop, last close above it
    px = r0.price or last_close
    if px < last_close:
        intraday = replace(r0.digest, action="BUY", stop=(px + last_close) / 2, as_of=earlier)
        h2 = run_analysis(replace(inp, previous=intraday, previous_action=Action.BUY, held=True), cfg)
        assert h2.decision.action != Action.SELL
        f2 = run_analysis(replace(inp, previous=intraday, previous_action=Action.BUY, held=False), cfg)
        assert f2.decision.action not in (Action.BUY, Action.BUY_SMALL, Action.ADD)


def test_cumulative_small_changes_are_caught_by_the_material_gate():
    from tests.unit.test_router_codec import digest

    base = digest(score=76.0, baseline_score=76.0, action="WATCH")
    step = digest(score=79.5, baseline_score=76.0, action="WATCH")  # each run moves < 5 points …
    nxt = digest(score=81.5, baseline_score=76.0, action="WATCH")
    assert material_reasons(diff(base, step)) == ()
    assert any("누적" in m for m in material_reasons(diff(step, nxt)))  # … but 5.5 points since the last change is material


# ---------------------------------------------------------------- P0-D paper account


def _bars(start: date, closes: list[float]) -> list[Bar]:
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(d, c, c * 1.01, c * 0.99, c, 1e6))
        d += timedelta(days=1)
    return out


def _sig(t: str, ts: datetime, action: str = "BUY", stop: float = 90.0, t1: float = 130.0, t2: float = 140.0, max_buy: float | None = None) -> PaperSignal:
    return PaperSignal(t, ts, action, 80, 70, stop, t1, t2, "", "v", "r", "s", None, max_buy)


def test_paper_account_dedupes_repeated_buys_requires_cash_and_caps_weight():
    ts = datetime(2026, 9, 21, 18, tzinfo=UTC)
    bars = {"A": _bars(date(2026, 9, 22), [100.0] * 10), "B": _bars(date(2026, 9, 22), [50.0] * 10)}
    items = [
        AccountItem("1", _sig("A", ts)),
        AccountItem("2", _sig("A", ts + timedelta(days=1))),  # repeated BUY while holding → skipped
        AccountItem("3", _sig("B", ts + timedelta(days=1), action="ADD", stop=40.0, t1=70.0, t2=80.0)),  # ADD without a position → skipped
        AccountItem("4", _sig("A", ts + timedelta(days=2), action="ADD")),  # ADD on top of 10% → cap breach
    ]
    acct = simulate_account(items, bars, PaperConfig(slippage_bps=0, default_half_spread_bps=0, starting_capital=100_000, position_notional=10_000))
    reasons = dict(acct.skipped)
    assert [k for k, _ in acct.trades] == ["1"]
    assert "반복 매수" in reasons["2"] and "보유 중인 모의 포지션이 없음" in reasons["3"] and "비중" in reasons["4"]
    small = simulate_account([AccountItem("1", _sig("A", ts))], bars, PaperConfig(starting_capital=5_000, position_notional=10_000))
    assert small.trades == () and "현금 부족" in small.skipped[0][1]


def test_paper_equity_is_marked_to_market_daily_with_open_losses():
    ts = datetime(2026, 9, 21, 18, tzinfo=UTC)
    closes = [100, 95, 92, 91, 99, 105]
    acct = simulate_account([AccountItem("1", _sig("A", ts, stop=80.0))], {"A": _bars(date(2026, 9, 22), closes)}, PaperConfig(slippage_bps=0, default_half_spread_bps=0, starting_capital=100_000, position_notional=10_000))
    eq = [v for _, v in acct.equity]
    assert min(eq) == pytest.approx(100_000 - 100 * 9, abs=1)  # 100 shares, worst close 91 → −$900 unrealised
    assert acct.max_drawdown is not None and acct.max_drawdown < 0
    assert acct.unrealized_pnl == pytest.approx(500, abs=1)


def test_buy_small_uses_half_size_and_max_buy_gap_up_is_skipped():
    ts = datetime(2026, 9, 21, 18, tzinfo=UTC)
    bars = {"A": _bars(date(2026, 9, 22), [100.0] * 5)}
    acct = simulate_account([AccountItem("1", _sig("A", ts, action="BUY SMALL"))], bars, PaperConfig(slippage_bps=0, default_half_spread_bps=0, position_notional=10_000))
    assert acct.trades[0][1].entry.quantity == pytest.approx(50)  # $5,000 / $100
    gap = simulate_account([AccountItem("1", _sig("A", ts, max_buy=99.0))], bars, PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    assert gap.trades == () and "최대 매수가" in gap.skipped[0][1]
