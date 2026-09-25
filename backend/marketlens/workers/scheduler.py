"""Simple in-process scheduler: periodic scans on trading days, daily evaluation after the close."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from marketlens.application.evaluation_service import EvaluationService
from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session, to_ny

log = logging.getLogger("marketlens.scheduler")


class BackgroundScheduler:
    def __init__(self, service, tick_seconds: float = 60.0) -> None:  # type: ignore[no-untyped-def]
        self.svc = service
        self.tick = tick_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="marketlens-scheduler", daemon=True)
        self._last_scan: datetime | None = None
        self._last_eval_day = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.step(self.svc.now())
            except Exception:  # keep the scheduler alive; the failure is logged with traceback
                log.exception("scheduled job failed")

    def step(self, now: datetime) -> None:
        session = classify_session(now)
        interval = self.svc.settings.scan_interval_minutes * 60
        if session in (TradingSession.PREMARKET, TradingSession.REGULAR, TradingSession.AFTER_HOURS):
            if self._last_scan is None or (now - self._last_scan).total_seconds() >= interval:
                self.svc.run_scan(run_committee=True)
                self._last_scan = now
        day = to_ny(now).date()
        if session == TradingSession.AFTER_HOURS and self._last_eval_day != day:
            ev = EvaluationService(self.svc)
            ev.update_outcomes(now)
            ev.update_paper(now)
            self._last_eval_day = day
