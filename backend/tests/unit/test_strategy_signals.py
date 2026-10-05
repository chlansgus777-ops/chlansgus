"""The strategies in the app (application/strategy_signals.py): confirmed at a completed close only, preliminary at the
live price (never recorded), held with a reason when data is missing, and the forward paper log filled at the next
open with the strategy's own exit — the same rules as the backtest."""

import json
from datetime import date, datetime, timedelta, timezone

from marketlens.application.strategy_signals import StrategySignals, strategy_status
from marketlens.domain.market import Bar


def _sessions(n: int, end: date) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _bars(closes, days, vol=2e6):
    return [Bar(d, c, c * 1.002, c * 0.998, c, vol) for d, c in zip(days, closes)]


def _pullback(n):
    closes = [40 + 0.15 * i for i in range(n)]
    closes[-2] = closes[-3] * 0.97
    closes[-1] = closes[-2] * 0.97
    return closes


SESSION = date(2026, 9, 24)  # a Thursday; "now" is the next morning before the open
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _svc(tmp_path, data, now=NOW, results=None):
    rp = tmp_path / "strategy_results.json"
    if results is not None:
        rp.write_text(json.dumps(results))
    return StrategySignals(lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in data.items()}, lambda: now, tmp_path, results_path=rp)


def test_a_confirmed_signal_is_recorded_once_and_carries_its_rules_and_time(tmp_path):
    days = _sessions(300, SESSION)
    data = {"PULL": _bars(_pullback(300), days), "SPY": _bars([400 + i for i in range(300)], days, 1e8)}
    s = _svc(tmp_path, data)
    r = s.scan()
    sig = r["confirmed"][0]
    assert sig["ticker"] == "PULL" and sig["strategy"] == "A" and sig["kind_ko"] == "확정 신호" and sig["version"] == "A-1.0"
    assert sig["execute_at"].startswith("2026-09-25") and all(c["ok"] for c in sig["conditions"])
    assert sig["validation"]["status_ko"] == "검증 중"
    s.scan()
    assert len((tmp_path / "strategy_forward.jsonl").read_text().splitlines()) == 1  # never re-issued


def test_a_preliminary_signal_is_shown_but_never_recorded(tmp_path):
    days = _sessions(300, SESSION)
    closes = [40 + 0.15 * i for i in range(300)]
    closes[-1] = closes[-2] * 0.97  # one fall at the close; a second one only in today's live price
    data = {"P": _bars(closes, days), "SPY": _bars([400 + i for i in range(300)], days, 1e8)}
    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    s = _svc(tmp_path, data, now=now)
    r = s.scan(live=lambda t: (closes[-1] * 0.9, now) if t == "P" else None)
    assert not r["confirmed"] and r["preliminary"][0]["kind_ko"] == "예비 신호"
    assert not (tmp_path / "strategy_forward.jsonl").exists()


def test_missing_data_holds_the_strategy_with_the_reason(tmp_path):
    days = _sessions(120, SESSION)
    data = {"NEW": _bars([20 + i * 0.1 for i in range(120)], days), "SPY": _bars([400 + i for i in range(120)], days, 1e8)}
    t = _svc(tmp_path, data).ticker("NEW")
    a = next(x for x in t["strategies"] if x["strategy"] == "A")
    c = next(x for x in t["strategies"] if x["strategy"] == "C")
    assert a["state"] == "HELD" and "252거래일" in a["reasons"][0]
    assert c["state"] == "HELD" and any("SPY" in w for w in c["reasons"])


def test_the_forward_log_fills_at_the_next_open_and_exits_by_the_strategy_rule(tmp_path):
    days = _sessions(300, SESSION)
    closes = _pullback(300)
    data = {"PULL": _bars(closes, days), "SPY": _bars([400 + i for i in range(300)], days, 1e8)}
    s = _svc(tmp_path, data)
    s.scan()
    # three more sessions: the next open fills, a rebound above the 5-day line exits at the open after
    more = _sessions(4, date(2026, 9, 30))[-4:]
    rebound = [closes[-1] * 1.0, closes[-1] * 1.08, closes[-1] * 1.09, closes[-1] * 1.10]
    data["PULL"] += [Bar(d, c, c * 1.01, c * 0.99, c, 2e6) for d, c in zip(more, rebound)]
    s._now = lambda: datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    f = s.forward()
    t = f["trades"][0]
    assert t["state"] == "CLOSED" and t["entry_day"] == "2026-09-25" and t["reason"] == "rule"
    assert abs(t["ret"] - (t["exit"] * 0.9985 / (t["entry"] * 1.0015) - 1)) < 1e-12
    assert "실거래" in f["note"]


def test_the_status_follows_the_committed_backtest_verdict():
    st = strategy_status({"variants": {"A": {"verdict": {"passed": False, "checks": {}}, "full": {"trades": {}}}, "C": {"verdict": {"passed": True, "checks": {}}, "full": {"trades": {}}}}})
    assert st["A"]["status_ko"] == "미채택" and st["C"]["status_ko"] == "백테스트 통과 · 전진 모의운영 중"
    assert strategy_status(None)["A"]["status_ko"] == "검증 중"
