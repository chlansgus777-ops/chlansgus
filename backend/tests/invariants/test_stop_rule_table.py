"""Invariant: the close-based stop rule, over EVERY combination of
held × previous recommendation × carried stop × where the price is — the behaviour the stock screen promises:
"종가가 손절 기준가 아래로 마감하면 매도(보유 중)·매수 중단으로 판단합니다. 장중에만 내려간 경우는 신규 매수만 멈춥니다."

Which stop is watched (the one rule, application.pipeline.watched_stop):
  previous BUY / BUY SMALL / ADD            → that recommendation's stop
  previous HOLD / REDUCE / WAIT, and held   → the carried stop of the buy if there is one, else that analysis' own stop
  previous HOLD / REDUCE / WAIT, not held   → none (nothing to protect; a new buy uses the new plan)
  no previous recommendation                → none
Expected:
  close below the watched stop  → held: SELL;             not held: no buy
  only the quote below it       → held: not SELL, no add; not held: no buy
  above, or nothing watched     → no stop reason at all
The table runs on five names of the mock market whose own analysis (held) ends differently (HOLD, REDUCE, SELL, a
name that is a WAIT when not held, DATA INSUFFICIENT), so the rule is checked whatever the score says."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, time

import pytest

from marketlens.domain.enums import BULLISH_ACTIONS, Action

PREVIOUS = ("BUY", "BUY SMALL", "ADD", "HOLD", "REDUCE", "WAIT", None)
WHERE = ("above", "below", "intraday")
TICKERS = ("NVDA", "TSM", "AMD", "JPM", "AMZN")  # held: HOLD, REDUCE, SELL, WAIT-ish, DATA INSUFFICIENT

_cache: dict[str, object] = {}


def _base(ticker: str):  # noqa: ANN202
    if ticker not in _cache:
        from tests.fixtures import analysis

        _cache[ticker] = analysis(ticker)
    return _cache[ticker]


def watched(prev_action: str | None, held: bool, stop: float, guard: float | None) -> float | None:
    if prev_action is None:
        return None
    if prev_action in ("BUY", "BUY SMALL", "ADD"):
        return stop
    if held:
        return guard if guard is not None else stop
    return None


def _run(ticker: str, held: bool, prev_action: str | None, carried: bool, where: str):  # noqa: ANN202
    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.market_calendar import NY

    res, inp = _base(ticker)
    stop = round(res.entry.stop, 2) if res.entry else round(inp.quote.price * 0.95, 2)
    guard = round(stop * 0.97, 2) if carried else None
    levels = [x for x in (stop, guard) if x is not None]
    last = inp.bars[-1]
    close = max(levels) * 1.04 if where in ("above", "intraday") else min(levels) * 0.96
    quote = min(levels) * 0.96 if where == "intraday" else close
    bars = inp.bars[:-1] + (replace(last, open=close, high=max(close, last.high), low=min(close, quote), close=close),)
    prev = None
    if prev_action is not None:
        prev = replace(res.digest, as_of=datetime.combine(last.day, time(9, 0), tzinfo=NY), action=prev_action, stop=stop, guard_stop=guard,
                       price=close, splits_applied=())
    out = run_analysis(replace(inp, held=held, previous=prev, previous_action=Action(prev_action) if prev_action else None, bars=bars,
                               quote=replace(inp.quote, price=quote)), load_model_config())
    return out, watched(prev_action, held, stop, guard)


CASES = list(itertools.product(TICKERS, (True, False), PREVIOUS, (True, False), WHERE))


@pytest.mark.parametrize("ticker,held,prev_action,carried,where", CASES)
def test_the_stop_rule_table(ticker, held, prev_action, carried, where):
    out, w = _run(ticker, held, prev_action, carried, where)
    act = out.decision.action
    stop_reason = [r for r in out.decision.reasons if "종가 기준 이탈" in r]
    if w is None or where == "above":
        assert not stop_reason, (act, out.decision.reasons)
        return
    if where == "below":
        if held:
            assert act == Action.SELL, (act, out.decision.reasons, out.decision.notes)
        else:
            assert act not in BULLISH_ACTIONS, (act, out.decision.reasons)
        return
    # only the quote is below: a warning — never a stop sale (a SELL the score gives on its own is another matter);
    # no new or added buy
    assert not stop_reason, (act, out.decision.reasons)
    assert act not in BULLISH_ACTIONS, (act, out.decision.reasons)


@pytest.mark.parametrize("prev_action,held,breach", [
    ("BUY", True, True),     # a buy watches its own stop: the close is below it
    ("HOLD", True, False),   # a held HOLD watches the carried stop of the buy: the close is above that one
    ("REDUCE", True, False),
    ("WAIT", True, False),
])
def test_which_stop_is_watched_when_the_close_is_between_the_two(prev_action, held, breach):
    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.market_calendar import NY

    res, inp = _base("NVDA")
    stop = round(res.entry.stop, 2)
    guard = round(stop * 0.90, 2)
    close = (stop + guard) / 2
    last = inp.bars[-1]
    bars = inp.bars[:-1] + (replace(last, open=close, low=min(close, last.low), close=close),)
    prev = replace(res.digest, as_of=datetime.combine(last.day, time(9, 0), tzinfo=NY), action=prev_action, stop=stop, guard_stop=guard,
                   price=close, splits_applied=())
    out = run_analysis(replace(inp, held=held, previous=prev, previous_action=Action(prev_action), bars=bars, quote=replace(inp.quote, price=close)),
                       load_model_config())
    assert bool([r for r in out.decision.reasons if "종가 기준 이탈" in r]) == breach, out.decision.reasons
