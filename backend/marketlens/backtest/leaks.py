"""Leak checks of the backtest (PREREGISTRATION §누수 검사).

1. Time audit (``audit_inputs``): every input of an analysis at t carries a public time, and none is later than t:
   - bars: the session (16:00 New York close) completed at t; the quote: stamped at or before t;
   - SEC fundamentals: filing date ≤ the filing visibility day of t, per field and per restatement (filing date is
     the EDGAR acceptance date — an acceptance after 17:30 gets the next day's date, so it is a safe upper bound);
   - splits: executed (00:00 New York of the execution day) on or before t;
   - macro: observation date ≤ t (ALFRED vintage ≤ t is enforced on the recorded request, see ``audit_vintages``);
   - inputs without a verifiable public time must be absent: consensus, earnings, events, news/issues, options,
     short interest, insider, valuation history, provider extras.
   Allowed exceptions (current values, documented): sector / industry and the ETF flag of the security.
   A violation raises ``LeakError`` and fails the run.
2. Truncated copy: the database cut at t gives the same analysis at t (``truncated_copy``).
3. Canary: a record published just after t does not change t; it shows up once public (tests + real-data report).
4. Network blocked: see offline.no_network / BlockedProvider.
5. Shuffled returns: IC ≈ 0 (a check of the computation, not of leaks) — metrics.shuffle_ic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from marketlens.domain.market_calendar import last_completed_session, to_ny


class LeakError(AssertionError):
    pass


def _fail(t: datetime, ticker: str, what: str) -> None:
    raise LeakError(f"time audit failed at {t.isoformat()} {ticker}: {what}")


def audit_inputs(inp: Any, t: datetime, store: Any = None) -> None:
    from marketlens.application.pipeline import filing_visibility_day

    tk = inp.ticker
    if inp.as_of != t:
        _fail(t, tk, f"analysis time {inp.as_of} ≠ t")
    session = last_completed_session(t)
    vis = filing_visibility_day(t)
    for b in inp.bars:
        if b.day > session:
            _fail(t, tk, f"bar {b.day} after the session completed at t ({session})")
    for b in inp.benchmark_bars:
        if b.day > session:
            _fail(t, tk, f"benchmark bar {b.day} after {session}")
    if inp.quote is not None and inp.quote.timestamp > t:
        _fail(t, tk, f"quote stamped {inp.quote.timestamp} after t")
    for q in inp.quarters:
        if q.filed_date > vis:
            _fail(t, tk, f"quarter {q.period_end} filed {q.filed_date} after the visibility day {vis}")
        for k, fd in q.field_filed.items():
            if fd > vis:
                _fail(t, tk, f"{q.period_end} {k} first filed {fd} after {vis}")
        for k, obs in q.revisions.items():
            for fd, _v in obs:
                if fd > vis:
                    _fail(t, tk, f"{q.period_end} {k} restated {fd} after {vis}")
    for y in inp.annuals:
        if y.filed_date > vis:
            _fail(t, tk, f"annual {y.period_end} filed {y.filed_date} after {vis}")
    today = to_ny(t).date()
    for s in inp.splits:
        if s.execution_date > today:
            _fail(t, tk, f"split executed {s.execution_date} after t")
    if inp.macro is not None:
        for sid, s in inp.macro.series.items():
            ts = s.latest.source_ts
            if ts is not None and ts.date() > today:
                _fail(t, tk, f"macro {sid} observation {ts.date()} after t")
    # inputs whose public time cannot be verified in the past are never used
    for name in ("analyst", "options", "ownership", "insider", "portfolio_review"):
        if getattr(inp, name) is not None:
            _fail(t, tk, f"{name} present although it has no verifiable public time")
    for name in ("earnings", "events", "issues", "valuation_history"):
        if getattr(inp, name):
            _fail(t, tk, f"{name} present although it has no verifiable public time")
    if inp.extras:
        _fail(t, tk, "provider extras present although they have no verifiable public time")
    if store is not None and getattr(store, "t", t) != t:
        _fail(t, tk, f"store bound to {store.t}, not t")


def audit_vintages(urls: list[str], t: datetime) -> None:
    """Every recorded ALFRED request served at t asked for a vintage (realtime_end) and observations on or before t."""
    today = to_ny(t).date()
    for u in urls:
        q = dict(parse_qsl(urlsplit("//" + u).query))
        for k in ("realtime_end", "realtime_start", "observation_end"):
            if k in q and date.fromisoformat(q[k]) > today:
                raise LeakError(f"FRED request at {t.isoformat()} asked {k}={q[k]} after t")


# ------------------------------------------------------------------------------------------------ checks 2 and 3
def truncated(data: Any, t: datetime) -> Any:
    """A copy of the loaded backtest data holding only what existed at t: bars of sessions completed at t, splits and
    dividends executed by t, fundamentals as known on t's filing visibility day (later periods, fields and restatements
    removed). The analysis at t must not change on it (leak check 2)."""
    import copy
    from bisect import bisect_right

    from marketlens.application.pipeline import filing_visibility_day
    from marketlens.domain.fundamentals import known_on

    session = last_completed_session(t)
    today = to_ny(t).date()
    vis = filing_visibility_day(t)
    cut = copy.copy(data)
    cut.lineages = []
    for ln in data.lineages:
        n = bisect_right(ln.days, session)
        c = copy.copy(ln)
        c.days, c.rows, c.labels = ln.days[:n], ln.rows[:n], ln.labels[:n]
        c.splits = [s for s in ln.splits if s.execution_date <= today]
        c.dividends = [x for x in ln.dividends if x[0] <= today]
        if c.days:
            cut.lineages.append(c)
    cut.by_key = {ln.key: ln for ln in cut.lineages}
    cut._by_listing = {}
    for ln in cut.lineages:
        for tk in set(ln.labels):
            cut._by_listing.setdefault(tk, []).append(ln)
    full_q = data.quarters
    cut._quarters = {}
    cut.quarters = lambda cik: known_on(full_q(cik), vis)  # type: ignore[method-assign]
    return cut


def stateless_rows(data: Any, eng: Any, cfg: Any, t: datetime) -> dict[str, str]:
    """{key: payload} of one analysis week with no carried state (the comparison unit of checks 2 and 3)."""
    from marketlens.backtest.engine import Engine
    from marketlens.backtest.offline import build_offline_registry
    from marketlens.backtest.store import BacktestStore

    store = BacktestStore(data)
    reg, _replay, _blocked = build_offline_registry(eng, store)
    e = Engine(store, reg, cfg, ":memory:")
    e.step(t)
    from sqlalchemy import select

    from marketlens.backtest.engine import bt_rows

    with e.out.connect() as c:
        return {m["key"]: f"{m['eligible']}|{m['excluded']}|{m['payload']}" for m in (r._mapping for r in c.execute(select(bt_rows)))}


def with_canary(data: Any, key: str, after: datetime, bar_factor: float = 2.0, revenue_factor: float = 10.0) -> Any:
    """A copy with a fake bar on the first session after ``after`` (price × bar_factor) and a fake quarter filed the day
    after ``after``'s filing visibility day (revenue × revenue_factor) for security ``key`` (leak check 3)."""
    import copy
    from bisect import bisect_right
    from dataclasses import replace

    from marketlens.application.pipeline import filing_visibility_day
    from marketlens.domain.market_calendar import next_trading_day

    out = copy.copy(data)
    out.lineages = []
    cik = data.by_key[key].cik
    for ln in data.lineages:
        if ln.key != key:
            out.lineages.append(ln)
            continue
        c = copy.copy(ln)
        day = next_trading_day(last_completed_session(after))
        i = bisect_right(ln.days, day) - 1
        o, h, lo, cl, v = ln.rows[i]
        fake = (o * bar_factor, h * bar_factor, lo * bar_factor, cl * bar_factor, v)
        c.days, c.rows, c.labels = list(ln.days), list(ln.rows), list(ln.labels)
        if i >= 0 and ln.days[i] == day:
            c.rows[i] = fake
        else:
            c.days.insert(i + 1, day)
            c.rows.insert(i + 1, fake)
            c.labels.insert(i + 1, ln.labels[max(i, 0)])
        out.lineages.append(c)
    out.by_key = {ln.key: ln for ln in out.lineages}
    out._by_listing = {}
    for ln in out.lineages:
        for tk in set(ln.labels):
            out._by_listing.setdefault(tk, []).append(ln)
    filed = filing_visibility_day(after) + timedelta(days=1)  # filed the day after the last filing day visible at ``after``
    base_q = data.quarters

    def q(c: int) -> list[Any]:
        qs = list(base_q(c))
        if c == cik and qs:
            last = qs[-1]
            qs.append(replace(last, period_end=last.period_end + timedelta(days=91), filed_date=filed, field_filed={}, revisions={},
                              revenue=(last.revenue or 1.0) * revenue_factor))
        return qs

    out._quarters = {}
    out.quarters = q  # type: ignore[method-assign]
    return out
