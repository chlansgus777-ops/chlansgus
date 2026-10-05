"""Buy-zone alerts of a name at most once per 5 minutes (owner 2026-10-05: "같은 종목은 5분안에는 다시 안뜨게";
independent review F04) — stop and target alerts are never held back."""

from datetime import datetime, timedelta, timezone

from marketlens.application.live_judge import BUY_ALERT_COOLDOWN, LiveJudge, LivePlan

T0 = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def _plan(**kw):
    return LivePlan(**({"ticker": "TEST", "rec_id": 1, "as_of": T0 - timedelta(minutes=30), "action": "BUY", "bullish": True, "data_ok": True,
                        "rec_price": 100.0, "max_buy": 102.0, "stop": 92.0, "target1": 125.0, "ideal_entry": 99.0, "min_rr": 2.0} | kw))


def test_a_wobble_at_the_max_buy_says_buy_once_then_again_after_five_minutes():
    clock = {"t": T0}
    j = LiveJudge(now=lambda: clock["t"])
    j.set_plans([_plan()])
    for px in (102.01, 101.99, 102.01, 101.99):
        j.observe("TEST", px, clock["t"])
    kinds = [a["kind"] for a in j.alerts()]
    assert kinds.count("BUY_ZONE") == 1 and kinds.count("LEFT_BUY_ZONE") == 0
    clock["t"] = T0 + BUY_ALERT_COOLDOWN + timedelta(seconds=1)
    j.observe("TEST", 102.01, clock["t"])
    j.observe("TEST", 101.99, clock["t"])
    assert [a["kind"] for a in j.alerts()].count("BUY_ZONE") + [a["kind"] for a in j.alerts()].count("LEFT_BUY_ZONE") == 2


def test_a_stop_is_never_held_back_by_the_buy_cooldown():
    j = LiveJudge(now=lambda: T0)
    j.set_plans([_plan(held=True)])
    j.observe("TEST", 101.0, T0)
    j.observe("TEST", 91.0, T0)
    assert "STOP_HIT" in [a["kind"] for a in j.alerts()]


def test_a_big_move_asks_for_one_reanalysis_per_analysis():
    asked = []
    j = LiveJudge(now=lambda: T0)
    j.on_reanalyze = lambda t, why: asked.append(why)
    j.set_plans([_plan()])
    for px in (100.0, 94.0, 94.5, 93.9):
        j.observe("TEST", px, T0)
    assert asked == ["BIG_MOVE"]
