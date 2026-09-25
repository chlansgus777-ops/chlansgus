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
