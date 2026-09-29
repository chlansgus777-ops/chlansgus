from datetime import datetime, timezone

from marketlens.workers.scheduler import BackgroundScheduler


class FakeSvc:
    def __init__(self):
        self.scans = 0
        self.settings = type("S", (), {"scan_interval_minutes": 60})()

    def run_scan(self, run_committee=True):
        self.scans += 1

    def now(self):
        return datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def test_scheduler_scans_only_in_sessions_with_interval():
    svc = FakeSvc()
    s = BackgroundScheduler(svc)
    s.step(datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc))
    s.step(datetime(2026, 9, 25, 15, 30, tzinfo=timezone.utc))  # within interval
    s.step(datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc))  # Saturday: closed
    assert svc.scans == 1
    s.step(datetime(2026, 9, 25, 16, 5, tzinfo=timezone.utc))
    assert svc.scans == 2


def test_cli_help_runs():
    import pytest

    from marketlens.workers.cli import main

    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


def test_live_schedule_scans_only_when_a_scan_can_give_a_verdict():
    """Owner report 2026-09-29: with free quotes a pre-market / after-hours scan holds every name for a stale price
    and replaced the last good scan — the home screen showed a 07:00 ET scan, all "data insufficient", all day.
    LIVE: interval scans in the regular session, one scan on the final close once the market is fully closed."""
    svc = FakeSvc()
    svc.store = object()  # LIVE
    svc.ready = "NOT READY"
    svc.readiness_view = lambda wait=0.0: {"recommendation_readiness": svc.ready}
    s = BackgroundScheduler(svc)
    s.step(datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc))  # regular session, but the data is not prepared yet
    assert svc.scans == 0
    svc.ready = "LIMITED"
    s.step(datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc))  # 07:00 ET pre-market
    assert svc.scans == 0
    s.step(datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc))  # 10:00 ET regular
    s.step(datetime(2026, 9, 25, 15, 5, tzinfo=timezone.utc))  # 11:05 ET, interval passed
    assert svc.scans == 2
    from datetime import date
    s._last_eval_day = date(2026, 9, 25)  # the after-hours evaluation is not what this test is about
    s.step(datetime(2026, 9, 25, 21, 30, tzinfo=timezone.utc))  # 17:30 ET after-hours
    assert svc.scans == 2
    s.step(datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc))  # 20:30 ET closed: one scan on the final close
    s.step(datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc))  # still closed: not again
    s.step(datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc))  # Saturday: the same close — not again
    assert svc.scans == 3


def test_with_the_toss_feed_pre_market_and_after_hours_scan_too():
    """Owner 2026-09-29 "정규장에서만 실시간으로 보인다는거야?": with 토스 prices every second the extended sessions have
    a real current price, so the automatic scan runs there as well; without the feed the free-quote rule stays."""
    svc = FakeSvc()
    svc.store = object()  # LIVE
    svc.readiness_view = lambda wait=0.0: {"recommendation_readiness": "LIMITED"}
    feed = {"live": True}
    svc.toss_feed = type("F", (), {"status": lambda self: dict(feed)})()
    s = BackgroundScheduler(svc)
    s.step(datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc))  # 07:00 ET pre-market
    assert svc.scans == 1 and "토스" in svc.schedule_state["why"]
    feed["live"] = False  # the feed stopped: back to the free-quote rule
    s.step(datetime(2026, 9, 25, 12, 5, tzinfo=timezone.utc))
    assert svc.scans == 1 and "토스증권을 연결하면" in svc.schedule_state["why"]
