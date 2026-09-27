"""Invariant: a stock split changes nothing economic. The same market expressed after a 2-for-1, a 10-for-1 or a 1-for-20
reverse split — and whenever the store learned about the split (before the previous analysis, on the execution day
before that day's sync, after it, the next day) — gives the same decision, the same stop check, the same dollar amount
to buy, the same holding value and weight, the same paper-trading result and the same stored-recommendation levels
(per-share levels scaled by the ratio).

Every path converts through corporate_actions.share_multiplier; these tests fail if any path guesses the basis."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from marketlens.domain.corporate_actions import SplitEvent, split_key
from marketlens.domain.enums import Action
from marketlens.domain.market import Bar

UTC = timezone.utc
RATIOS = ((1, 2), (1, 10), (20, 1))  # 2-for-1, 10-for-1, 1-for-20 reverse
TIMINGS = ("known_before_previous", "execution_day_before_sync", "execution_day_after_sync", "next_day")


def _scale_bars(bars, r: float):  # noqa: ANN001, ANN202
    return tuple(Bar(b.day, b.open / r, b.high / r, b.low / r, b.close / r, b.volume * r) for b in bars)


# ---------------------------------------------------------------- the analysis: decision, stop check, plan
_base_cache: dict[str, object] = {}


def _fixture():  # noqa: ANN202
    if "nvda" not in _base_cache:
        from tests.fixtures import analysis

        _base_cache["nvda"] = analysis("NVDA")
    return _base_cache["nvda"]


def _world(ratio: tuple[int, int] | None, timing: str, previous_action: str, held: bool):  # noqa: ANN202
    """The previous analysis ran on day P (the last stored session) at 09:00 New York; now is the fixture's clock."""
    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.market_calendar import NY

    res, inp = _fixture()
    p_day = inp.bars[-1].day
    prev = replace(res.digest, as_of=datetime.combine(p_day, time(9, 0), tzinfo=NY), action=previous_action, stop=res.entry.stop,
                   guard_stop=res.entry.stop if held else None, max_buy=res.entry.max_buy, price=res.price, splits_applied=())
    kw: dict = dict(previous=prev, previous_action=Action(previous_action), held=held)
    if ratio is None:
        return run_analysis(replace(inp, **kw), load_model_config())
    frm, to = ratio
    r = to / frm
    exec_day = {"known_before_previous": p_day - timedelta(days=7), "execution_day_before_sync": p_day,
                "execution_day_after_sync": p_day, "next_day": p_day + timedelta(days=1)}[timing]
    if exec_day > inp.as_of.date():
        pytest.skip("the next day is after the analysis clock")
    split = SplitEvent("NVDA", exec_day, frm, to, "polygon")
    prev_knew = timing in ("known_before_previous", "execution_day_after_sync")
    if prev_knew:  # its bars were already adjusted: its levels are on the new basis and it recorded the split
        prev = replace(prev, stop=prev.stop / r, guard_stop=prev.guard_stop / r if prev.guard_stop else None, max_buy=prev.max_buy / r,
                       price=prev.price / r, atr=prev.atr / r if prev.atr else None, splits_applied=(split_key(split),))
    kw.update(previous=prev, bars=_scale_bars(inp.bars, r), quote=replace(inp.quote, price=inp.quote.price / r), splits=(split,))
    if inp.analyst is not None:
        a = inp.analyst
        kw["analyst"] = replace(a, forward_eps=a.forward_eps / r if a.forward_eps else a.forward_eps)
    return run_analysis(replace(inp, **kw), load_model_config())


@pytest.mark.parametrize("ratio,timing,previous_action,held",
                         list(itertools.product(RATIOS, TIMINGS, ("BUY", "HOLD"), (True, False))))
def test_the_analysis_is_the_same_after_a_split(ratio, timing, previous_action, held):
    base = _world(None, timing, previous_action, held)
    out = _world(ratio, timing, previous_action, held)
    r = ratio[1] / ratio[0]
    assert out.decision.action == base.decision.action, (out.decision.action, base.decision.action, out.decision.reasons)
    stop_words = ("종가 기준 이탈", "장중 손절")
    assert [x for x in out.decision.reasons if any(w in x for w in stop_words)] == [x for x in base.decision.reasons if any(w in x for w in stop_words)]
    assert out.scorecard.total == pytest.approx(base.scorecard.total, abs=1e-6)
    cents = 0.006 * max(1.0, r)  # levels are rounded to cents on each basis
    assert out.entry.stop * r == pytest.approx(base.entry.stop, abs=cents)
    assert out.entry.max_buy * r == pytest.approx(base.entry.max_buy, abs=cents)


# ---------------------------------------------------------------- the dollar amount to buy
@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("action", ("BUY", "BUY SMALL", "ADD"))
def test_the_dollar_amount_is_the_same_after_a_split(ratio, action):
    from marketlens.domain.portfolio import position_plan

    r = ratio[1] / ratio[0]
    price, stop, nav = 76.51, 70.0, 100_000.0
    a = position_plan(action, None, nav, price, stop, 3_000.0)
    b = position_plan(action, None, nav, price / r, stop / r, 3_000.0)
    assert a is not None and b is not None
    # whole shares: the amounts differ by less than one share at the coarser of the two prices
    assert abs(a.amount - b.amount) <= max(price, price / r) + 1e-6
    assert abs((a.risk_amount or 0) - (b.risk_amount or 0)) <= max(price - stop, (price - stop) / r) + 1e-6


# ---------------------------------------------------------------- holding value and weight
def _live(tmp_path):  # noqa: ANN001, ANN202
    from tests.regression.test_eval9_followups import _live as live

    return live(tmp_path)


@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("entered", ("before", "execution_day", "after"))
def test_a_holding_is_valued_the_same_after_a_split(tmp_path, ratio, entered):
    """The user types what the broker shows on the day they enter it: before the execution day the old quantity, from
    the execution day on the new one."""
    from marketlens.domain.market_calendar import is_trading_day
    from marketlens.domain.portfolio import portfolio_snapshot
    from marketlens.infrastructure.db import repository as repo
    from marketlens.infrastructure.db.models import HoldingRow

    frm, to = ratio
    r = to / frm
    exec_day = date(2026, 9, 22)
    enter = {"before": date(2026, 9, 1), "execution_day": exec_day, "after": date(2026, 9, 23)}[entered]
    qty, cost = (100.0, 180.0) if entered == "before" else (100.0 * r, 180.0 / r)
    svc = _live(tmp_path)
    days = [d for d in (date(2026, 8, 1) + timedelta(days=i) for i in range(60)) if is_trading_day(d) and d <= date(2026, 9, 24)]
    svc.store.save_bars("NVDA", [Bar(d, 200 / r, 200 / r, 200 / r, 200 / r, 1e6) for d in days], "polygon")  # post-split basis
    svc.store.save_splits([SplitEvent("NVDA", exec_day, frm, to, "polygon")])
    with svc.sf() as s:
        repo.set_setting(s, "portfolio_cash", "10000")
        repo.upsert_holding(s, "NVDA", qty, cost)
        s.get(HoldingRow, "NVDA").updated_at = datetime.combine(enter, time(15, 0), tzinfo=UTC)
        s.commit()
        pf = svc.portfolio(s)
    snap = portfolio_snapshot(pf, {"NVDA": {d: 200 / r for d in days}})
    (h,) = snap.holdings
    assert h.market_value == pytest.approx(20_000.0) and h.unrealized_pct == pytest.approx(200 / 180 - 1)
    assert h.weight == pytest.approx(20_000 / 30_000, abs=1e-4)


# ---------------------------------------------------------------- paper trading
@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("timing", ("execution_day_before_sync", "execution_day_after_sync", "next_day", "later"))
def test_the_paper_result_is_the_same_after_a_split(ratio, timing):
    from marketlens.application.evaluation_service import EvaluationService
    from marketlens.domain.market_calendar import NY, is_trading_day
    from marketlens.domain.paper import AccountItem, PaperConfig, simulate_account

    frm, to = ratio
    r = to / frm
    days = [d for d in (date(2026, 8, 3) + timedelta(days=i) for i in range(50)) if is_trading_day(d)]
    rec_day = days[5]
    closes = [100 + 0.8 * i for i in range(len(days))]
    raw = [Bar(d, c, c + 1, c - 1, c, 1e6) for d, c in zip(days, closes)]
    exec_day = {"execution_day_before_sync": rec_day, "execution_day_after_sync": rec_day, "next_day": days[6], "later": days[20]}[timing]
    split = SplitEvent("T", exec_day, frm, to, "polygon")
    knew = timing == "execution_day_after_sync"
    k = r if knew else 1.0  # the recommendation's levels are on the basis of the bars it saw

    def pos(k: float):  # noqa: ANN202
        return SimpleNamespace(ticker="T", recommended_at=datetime.combine(rec_day, time(10, 0), tzinfo=NY), action="BUY", score=80.0,
                               confidence=70.0, stop=90.0 / k, target1=130.0 / k, target2=150.0 / k, thesis="", model_version="m",
                               regime="x", sector="Tech", max_buy=112.0 / k)

    base_svc = SimpleNamespace(data=SimpleNamespace(splits=lambda t: []))
    split_svc = SimpleNamespace(data=SimpleNamespace(splits=lambda t: [split]))
    today = days[-1]
    base_sig = EvaluationService(base_svc)._signal(pos(1.0), None, today, "T", frozenset())  # type: ignore[arg-type]
    split_sig = EvaluationService(split_svc)._signal(pos(k), None, today, "T", frozenset({split_key(split)}) if knew else frozenset())  # type: ignore[arg-type]
    adj = [Bar(b.day, b.open / r, b.high / r, b.low / r, b.close / r, b.volume * r) for b in raw]  # the store rescales the whole history
    cfg = PaperConfig()
    a = simulate_account([AccountItem("1", base_sig)], {"T": raw}, cfg, today)
    b = simulate_account([AccountItem("1", split_sig)], {"T": adj}, cfg, today)
    ta, tb = a.trades[0][1], b.trades[0][1]
    assert tb.return_pct == pytest.approx(ta.return_pct, abs=1e-9)
    assert [e.reason for e in tb.exits] == [e.reason for e in ta.exits]
    assert b.realized_pnl + b.unrealized_pnl == pytest.approx(a.realized_pnl + a.unrealized_pnl, rel=1e-6, abs=0.05 * max(1, r))


# ---------------------------------------------------------------- a stored recommendation on today's basis
@pytest.mark.parametrize("ratio", RATIOS)
@pytest.mark.parametrize("timing", ("execution_day_before_sync", "execution_day_after_sync", "next_day"))
def test_stored_levels_are_the_same_after_a_split(tmp_path, ratio, timing):
    from marketlens.domain.market_calendar import NY
    from marketlens.infrastructure.db.models import RecommendationRow

    frm, to = ratio
    r = to / frm
    svc = _live(tmp_path)
    rec_day = date(2026, 9, 23)
    exec_day = rec_day if timing != "next_day" else date(2026, 9, 24)
    split = SplitEvent("NVDA", exec_day, frm, to, "polygon")
    svc.store.save_splits([split])
    knew = timing == "execution_day_after_sync"
    k = r if knew else 1.0
    known = [{"execution_date": exec_day.isoformat(), "split_from": frm, "split_to": to}] if knew else []
    row = RecommendationRow(ticker="NVDA", as_of=datetime.combine(rec_day, time(8, 0), tzinfo=NY), price=366.0 / k, final_action="BUY",
                            result={"entry": {"ideal_entry": 360.0 / k, "max_buy": 372.0 / k, "stop": 350.0 / k, "target1": 400.0 / k}},
                            inputs={"splits": known})
    lv = svc.levels_now(row)
    assert (lv["price"] * r, lv["stop"] * r, lv["max_buy"] * r, lv["target1"] * r) == pytest.approx((366.0, 350.0, 372.0, 400.0))
