"""이동평균선 닿음 알림: a held / watched name's live price touching its 20 / 50 / 200-day line becomes one alert per
name, line and New York day — never on every tick, never from a stale price or an old analysis."""

from datetime import datetime, timedelta, timezone

from marketlens.application.ma_watch import MaLevels, MaWatch, band, levels_from_result, state

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)  # 11:00 New York


def watch(levels, now=NOW):
    sent = []
    w = MaWatch(add=lambda *a, **k: sent.append((a, k)), now=lambda: now)
    w.loader = lambda: levels
    w.load()
    return w, sent


def lv(sma, atr=4.0, as_of=NOW - timedelta(days=1)):
    return MaLevels("NVDA", sma, atr, as_of)


def test_touch_band_is_a_quarter_atr_but_never_under_half_a_percent():
    assert band(200.0, 4.0) == 1.0          # 0.25 × 4
    assert band(200.0, 0.4) == 1.0          # 0.5 % of 200 wins over 0.1
    assert band(200.0, None) == 1.0
    assert state(200.9, 200.0, 4.0) == "AT" and state(201.2, 200.0, 4.0) == "ABOVE" and state(198.8, 200.0, 4.0) == "BELOW"


def test_coming_down_to_the_200_day_line_alerts_once_with_its_direction():
    w, sent = watch([lv({20: 230.0, 50: 220.0, 200: 200.0})])
    ts = NOW - timedelta(seconds=5)
    assert w.observe("NVDA", 210.0, ts) == []           # above every line: nothing
    out = w.observe("NVDA", 200.6, ts)                   # within 1.0 of the 200-day line
    assert [a["window"] for a in out] == [200]
    (args, kw), = sent
    assert args[0] == "NVDA" and args[1] == "MA_TOUCH_200" and args[2] == "info" and args[4] == 200.6
    assert "200일선에 닿음" in args[3] and "위에서 내려와" in args[3] and "$200.00" in args[3]
    assert kw["window"] == 200
    assert w.observe("NVDA", 200.4, ts) == []            # still at the line: no repeat
    w.observe("NVDA", 205.0, ts)
    assert w.observe("NVDA", 200.2, ts) == []            # back again the same day: no repeat


def test_a_new_new_york_day_can_alert_again():
    w, sent = watch([lv({200: 200.0})])
    w.observe("NVDA", 200.1, NOW - timedelta(seconds=1))
    w._now = lambda: NOW + timedelta(days=1)  # type: ignore[method-assign]
    w.observe("NVDA", 210.0, NOW + timedelta(days=1))
    assert [a["window"] for a in w.observe("NVDA", 200.3, NOW + timedelta(days=1))] == [200]
    assert len(sent) == 2


def test_stale_prices_unknown_names_and_old_analyses_say_nothing():
    w, sent = watch([lv({50: 100.0})])
    assert w.observe("NVDA", 100.0, NOW - timedelta(hours=1)) == []   # yesterday's close after a restart
    assert w.observe("AAPL", 100.0, NOW) == []                         # not held / watched
    w2, _ = watch([lv({50: 100.0}, as_of=NOW - timedelta(days=30))])
    assert w2.symbols() == [] and w2.observe("NVDA", 100.0, NOW) == []
    assert sent == []


def test_lines_are_read_from_the_analysis_on_todays_share_basis():
    got = levels_from_result("nvda", {"technicals": {"sma20": 1000.0, "sma50": 900.0, "sma200": None, "atr14": 40.0}}, NOW, split_factor=10.0)
    assert got is not None and got.symbol == "NVDA" and got.sma == {20: 100.0, 50: 90.0} and got.atr == 4.0
    assert levels_from_result("X", {"technicals": {}}, NOW) is None
    assert levels_from_result("X", None, NOW) is None
