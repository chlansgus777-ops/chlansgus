"""Simple in-process scheduler: periodic scans when a scan can give a verdict, daily evaluation after the close."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from marketlens.application.evaluation_service import EvaluationService
from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session, last_completed_session, session_close_utc

log = logging.getLogger("marketlens.scheduler")


class BackgroundScheduler:
    WANTED_GAP_S = 600.0  # an account change asks for a scan at most this often

    def __init__(self, service, tick_seconds: float = 60.0) -> None:  # type: ignore[no-untyped-def]
        self.svc = service
        self.tick = tick_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="marketlens-scheduler", daemon=True)
        self._last_scan: datetime | None = None
        self._last_wanted: datetime | None = None
        self._last_eval_day = None
        self._eval_failed_at: datetime | None = None

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

    def _toss_live(self) -> bool:
        """The 토스 price feed is answering: pre-market and after-hours prices are real, so those sessions can scan."""
        feed = getattr(self.svc, "toss_feed", None)
        try:
            return bool(feed is not None and feed.status().get("live"))
        except Exception:  # noqa: BLE001 - unknown: the free-quote rules apply
            return False

    def _publish(self, now: datetime, session: TradingSession, live: bool, interval: float) -> None:
        """When the next automatic scan is due, for the screens (``service.schedule_state``)."""
        from datetime import timedelta

        nxt: datetime | None = None
        why = ""
        extended = session in (TradingSession.PREMARKET, TradingSession.AFTER_HOURS)
        if session == TradingSession.REGULAR or (extended and (not live or self._toss_live())):
            nxt = now if self._last_scan is None else max(now, self._last_scan + timedelta(seconds=interval))
            why = "정규장 주기 스캔" if session == TradingSession.REGULAR else "토스 실시간 시세로 장전·시간외 주기 스캔" if live else "주기 스캔"
        elif live and session == TradingSession.CLOSED:
            closed_at = session_close_utc(last_completed_session(now))
            if self._last_scan is None or self._last_scan < closed_at:
                nxt, why = now, "종가 기준 스캔"
            else:
                why = "다음 정규장에 다시 스캔"
        else:
            why = "장전·시간외에는 판단용 현재가가 없어 정규장·장 마감 뒤에 스캔 (토스증권을 연결하면 장전·시간외에도 스캔)"
        self.svc.schedule_state = {"enabled": True, "interval_minutes": round(interval / 60), "last_auto_scan": self._last_scan.isoformat() if self._last_scan else None,
                                   "next_due": nxt.isoformat() if nxt else None, "why": why}

    EVAL_KEY = "evaluation_through"  # the last completed session the results and paper account include
    EVAL_RETRY_S = 600

    def _evaluated_through(self):  # type: ignore[no-untyped-def]
        from datetime import date

        from marketlens.infrastructure.db import repository as repo

        try:
            with self.svc.sf() as s:
                raw = repo.get_setting(s, self.EVAL_KEY)
            return date.fromisoformat(raw) if raw else None
        except Exception:  # noqa: BLE001 - unknown: evaluate (it is idempotent)
            return None

    def _mark_evaluated(self, day) -> None:  # type: ignore[no-untyped-def]
        from marketlens.infrastructure.db import repository as repo

        with self.svc.sf() as s:
            repo.set_setting(s, self.EVAL_KEY, day.isoformat())
            s.commit()

    def _data_ready(self) -> bool:
        try:
            return self.svc.readiness_view(wait=0.0).get("recommendation_readiness") != "NOT READY"
        except Exception:  # noqa: BLE001 - unknown readiness: do not scan on it
            return False

    def step(self, now: datetime) -> None:
        session = classify_session(now)
        interval = self.svc.settings.scan_interval_minutes * 60
        live = getattr(self.svc, "store", None) is not None
        if live:
            # LIVE (free quotes): a scan gives a verdict only with a current price — in the regular session, or once the
            # market is fully closed (the last close is then the current price). A pre-market / after-hours scan
            # holds every name for a stale quote AND replaces the last good scan (owner report 2026-09-29: the home
            # screen showed a 07:00 ET scan, every row "data insufficient", all day long).
            # with the 토스 feed, pre-market and after-hours prices are real (owner 2026-09-29): scan there too
            priced = session == TradingSession.REGULAR or (session in (TradingSession.PREMARKET, TradingSession.AFTER_HOURS) and self._toss_live())
            due = priced and (self._last_scan is None or (now - self._last_scan).total_seconds() >= interval)
            if session == TradingSession.CLOSED:
                closed_at = session_close_utc(last_completed_session(now))
                due = self._last_scan is None or self._last_scan < closed_at  # one scan on the final close per session
        else:  # MOCK prices are generated for any time
            due = session in (TradingSession.PREMARKET, TradingSession.REGULAR, TradingSession.AFTER_HOURS) and (
                self._last_scan is None or (now - self._last_scan).total_seconds() >= interval)
        # an account change (a trade, a deposit, a sale): the list is analysed again with the new account — the live
        # round re-judges only the names with a live price (owner 2026-10-03, review finding 2); at most every 10 min
        wanted = getattr(self.svc, "scan_wanted", None)
        if wanted and not due and (self._last_wanted is None or (now - self._last_wanted).total_seconds() >= self.WANTED_GAP_S):
            due = (session == TradingSession.CLOSED or session == TradingSession.REGULAR
                   or (session in (TradingSession.PREMARKET, TradingSession.AFTER_HOURS) and self._toss_live())) if live else True
        if due and live and not self._data_ready():
            due = False  # before the data is prepared a scan judges nothing and spends the free request limits
        self._publish(now, session, live, interval)
        if due:
            from marketlens.application.services import ScanRefused

            try:
                # the AI committee costs money on a paid provider: automatic scans skip it unless the user opted in
                self.svc.run_scan(run_committee=bool(getattr(self.svc.settings, "ai_committee_on_schedule", False)))
            except ScanRefused as e:  # a scan the user started, or data preparation, is running: try at the next tick
                log.info("scheduled scan skipped: %s", e)
                return
            self._last_scan = now
            if wanted:
                self.svc.scan_wanted = None  # this scan used the account as it is now
                self._last_wanted = now
            self._publish(now, session, live, interval)  # the next due time at once, not a minute later
        # results and the paper account follow every completed session — after today's close, and on the first
        # tick after a start when the app was off at that time (owner 2026-10-04: "왜 모의투자가 0건이야" — the
        # evaluation ran only while the app happened to be open after the US close, 05–09 KST)
        target = last_completed_session(now)
        if self._last_eval_day == target or (self._eval_failed_at is not None and (now - self._eval_failed_at).total_seconds() < self.EVAL_RETRY_S):
            return
        if self._evaluated_through() == target:
            self._last_eval_day = target
            return
        if live and not self._data_ready():
            return  # nothing to evaluate on before the data is prepared; asked again at the next tick
        try:
            if live:
                self.svc.sync_market()  # LIVE: pull the completed sessions' grouped daily bars / shares before evaluating
            ev = EvaluationService(self.svc)
            ev.update_outcomes(now)
            ev.update_paper(now)
            self._mark_evaluated(target)
        except Exception:  # noqa: BLE001 - a failed evaluation never stops the scans; it is tried again later
            log.exception("scheduled evaluation failed; retrying in %d min", self.EVAL_RETRY_S // 60)
            self._eval_failed_at = now
            return
        self._eval_failed_at = None
        self._last_eval_day = target
