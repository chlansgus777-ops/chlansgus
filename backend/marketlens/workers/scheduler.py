"""Simple in-process scheduler: periodic scans when a scan can give a verdict, daily evaluation after the close."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from marketlens.application.evaluation_service import EvaluationService
from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session, last_completed_session, session_close_utc, to_ny

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
        live = getattr(self.svc, "store", None) is not None
        if live:
            # LIVE (free quotes): a scan gives a verdict only with a current price — in the regular session, or once the
            # market is fully closed (the last close is then the current price). A pre-market / after-hours scan
            # holds every name for a stale quote AND replaces the last good scan (owner report 2026-09-29: the home
            # screen showed a 07:00 ET scan, every row "data insufficient", all day long).
            due = session == TradingSession.REGULAR and (self._last_scan is None or (now - self._last_scan).total_seconds() >= interval)
            if session == TradingSession.CLOSED:
                closed_at = session_close_utc(last_completed_session(now))
                due = self._last_scan is None or self._last_scan < closed_at  # one scan on the final close per session
        else:  # MOCK prices are generated for any time
            due = session in (TradingSession.PREMARKET, TradingSession.REGULAR, TradingSession.AFTER_HOURS) and (
                self._last_scan is None or (now - self._last_scan).total_seconds() >= interval)
        if due:
            # the AI committee costs money on a paid provider: automatic scans skip it unless the user opted in
            self.svc.run_scan(run_committee=bool(getattr(self.svc.settings, "ai_committee_on_schedule", False)))
            self._last_scan = now
        day = to_ny(now).date()
        if session == TradingSession.AFTER_HOURS and self._last_eval_day != day:
            if getattr(self.svc, "store", None) is not None:
                self.svc.sync_market()  # LIVE: pull today's grouped daily bars / shares before evaluating
            ev = EvaluationService(self.svc)
            ev.update_outcomes(now)
            ev.update_paper(now)
            self._last_eval_day = day
