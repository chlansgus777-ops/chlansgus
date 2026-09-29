"""토스증권 account sync (read-only): the owner's holdings, cash and executions, kept current without being asked.

- Connect: the key entered on the screen is tried FIRST (account + holdings); only a key that works is stored (OS
  keychain, else the private .env — config.save_setup) and used at once, without a restart.
- Sync: in the background, every minute while a US or Korean session is open, every 10 minutes otherwise, and at once
  on request. A failed sync keeps the last snapshot (with its time — never shown as fresh) and says why; a key or IP
  problem is retried every 10 minutes, not hammered.
- The snapshot is stored in the database (app_settings), so the portfolio is there right after a restart.
- A change of holdings bumps ``version``: the live verdicts, the quote subscriptions and the open screens follow.
Nothing here can place an order (providers.live.toss has no order endpoint).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from marketlens.config import keychain_backend
from marketlens.domain.broker import BrokerPosition
from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session
from marketlens.infrastructure.db import repository as repo
from marketlens.providers.live.toss import TossClient, TossError, TossFill

log = logging.getLogger("marketlens.broker")
KST = ZoneInfo("Asia/Seoul")
SNAP_KEY, FILLS_KEY, PREFS_KEY, FILL_FX_KEY = "broker.toss.snapshot", "broker.toss.fills", "broker.toss.prefs", "broker.toss.fill_fx"
CASH_SOURCES = ("toss_usd", "toss_usd_krw", "manual")
FILLS_EVERY_S = 600.0
FILLS_FIRST_DAYS = 365
FILLS_KEEP = 2000
FILL_FX_PER_SYNC = 30  # executions whose Toss rate is looked up per sync (once each, kept): a long history fills in over a few minutes
KEY_TROUBLE = ("NOT_CONFIGURED", "BAD_KEY", "IP_NOT_ALLOWED", "TOKEN_REVOKED", "NO_ACCOUNT")


def _s(d: Decimal | None) -> str | None:
    return None if d is None else format(d, "f")


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def kr_session_open(now: datetime) -> bool:
    k = now.astimezone(KST)
    return k.weekday() < 5 and (8, 30) <= (k.hour, k.minute) < (16, 0)


class BrokerSync:
    def __init__(self, sf: Callable[[], Any], now: Callable[[], datetime], client_id: str | None, client_secret: str | None, enabled: bool,
                 transport: Any = None, save_keys: Callable[[dict[str, str]], Any] | None = None, forget_keys: Callable[[tuple[str, ...]], None] | None = None) -> None:
        from marketlens.config import forget_setup, save_setup

        self.sf, self.now, self.enabled, self._transport = sf, now, enabled, transport
        self._save_keys, self._forget_keys = save_keys or save_setup, forget_keys or forget_setup
        self.client: TossClient | None = TossClient(client_id, client_secret, transport=transport) if client_id and client_secret else None
        self._lock = threading.Lock()  # one sync / connect at a time
        self.version = 0
        self.last_error: dict[str, Any] | None = None
        self.last_attempt: datetime | None = None
        self._fills_at = -1e9
        self.on_change: Callable[[], None] | None = None
        self._snap: dict[str, Any] | None = None
        self._fills: dict[str, Any] | None = None
        self._fill_fx: dict[str, str] = {}
        try:
            with self.sf() as s:
                raw = repo.get_setting(s, SNAP_KEY)
                self._snap = json.loads(raw) if raw else None
                raw = repo.get_setting(s, FILLS_KEY)
                self._fills = json.loads(raw) if raw else None
                raw = repo.get_setting(s, PREFS_KEY)
                self._prefs = json.loads(raw) if raw else {}
                raw = repo.get_setting(s, FILL_FX_KEY)
                self._fill_fx = json.loads(raw) if raw else {}
        except Exception as e:  # noqa: BLE001 - no table yet on a first start: nothing stored
            log.info("broker snapshot not loaded: %s", type(e).__name__)
            self._prefs = {}

    # ---------------------------------------------------------------- state
    @property
    def configured(self) -> bool:
        return self.client is not None and self.client.configured

    def active(self) -> bool:
        """The account decides the portfolio: enabled (LIVE), a key, and a snapshot taken with it."""
        return self.enabled and self.configured and self._snap is not None

    def snapshot(self) -> dict[str, Any] | None:
        return self._snap if self.active() else None

    def interval(self) -> float:
        now = self.now()
        return 60.0 if classify_session(now) in (TradingSession.PREMARKET, TradingSession.REGULAR, TradingSession.AFTER_HOURS) or kr_session_open(now) else 600.0

    def retry_after(self) -> float:
        return 600.0 if self.last_error and self.last_error.get("kind") in KEY_TROUBLE else 60.0

    def due(self) -> bool:
        """Time for a background sync: never tried, the snapshot is older than ``interval``, or after a failure once
        ``retry_after`` has passed since the attempt."""
        if not (self.enabled and self.configured):
            return False
        if self.last_attempt is None:
            return True
        since = (self.now() - self.last_attempt).total_seconds()
        if self.last_error:
            return since >= self.retry_after()
        age = self.age_s()
        return age is None or age >= self.interval()

    def age_s(self) -> float | None:
        if not self._snap:
            return None
        return max(0.0, (self.now() - datetime.fromisoformat(self._snap["taken_at"])).total_seconds())

    def prefs(self) -> dict[str, Any]:
        src = self._prefs.get("cash")
        return {"cash": src if src in CASH_SOURCES else "toss_usd"}

    def set_prefs(self, cash: str) -> dict[str, Any]:
        if cash not in CASH_SOURCES:
            raise ValueError("cash: toss_usd, toss_usd_krw, manual 중 하나")
        self._prefs = {**self._prefs, "cash": cash}
        with self.sf() as s:
            repo.set_setting(s, PREFS_KEY, json.dumps(self._prefs))
            s.commit()
        self._changed()
        return self.prefs()

    def positions(self) -> list[BrokerPosition]:
        snap = self.snapshot()
        return [BrokerPosition(h["symbol"], h["name"], h["market"], h["currency"], float(h["quantity"]), float(h["avg_price"])) for h in (snap or {}).get("holdings", [])]

    def cash(self, manual: float) -> tuple[float, str, list[str]]:
        """(cash for sizing, its source, notes). The account's cash buying power when chosen and known."""
        snap = self.snapshot()
        src = self.prefs()["cash"]
        if snap is None or src == "manual":
            return manual, "manual", []
        usd = _f(snap["cash"].get("USD")) or 0.0
        if src == "toss_usd_krw":
            krw, fx = _f(snap["cash"].get("KRW")), snap.get("fx")
            if krw and fx and _f(fx.get("rate")):
                return usd + krw / float(fx["rate"]), src, []
            return usd, "toss_usd", ["원화 예수금을 달러로 바꿀 환율이 없어 달러 예수금만 현금으로 씁니다"]
        return usd, src, []

    def status(self) -> dict[str, Any]:
        snap = self._snap
        age = self.age_s()
        return {
            "enabled": self.enabled, "configured": self.configured, "active": self.active(), "version": self.version,
            "account": (snap or {}).get("account"), "synced_at": (snap or {}).get("taken_at"), "age_s": None if age is None else round(age),
            "stale": age is not None and age > max(3 * self.interval(), 900), "error": self.last_error,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None, "interval_s": self.interval(), "prefs": self.prefs(),
            "key_store": "keychain" if keychain_backend() else "env_file",
            "fills": None if not self._fills else {"count": len(self._fills.get("items", [])), "complete": self._fills.get("complete", False),
                                                   "since": self._fills.get("since"), "synced_at": self._fills.get("synced_at")},
        }

    def view(self) -> dict[str, Any]:
        """What the portfolio screen shows of the account: status, the domestic (won) holdings apart, the account's
        own totals per currency and cash — every number as Toss computed it, with the time it was taken."""
        snap = self.snapshot()
        out = self.status()
        if snap is None:
            return out
        kr = [h for h in snap["holdings"] if h["market"] == "KR"]
        return out | {"domestic": kr, "totals": snap.get("totals"), "cash": snap.get("cash"), "fx": snap.get("fx"),
                      "other": [h for h in snap["holdings"] if h["market"] not in ("US", "KR")]}

    def fill_rates(self) -> dict[str, float]:
        """order id → the won per dollar Toss quoted (its buy rate) at the moment of that USD execution."""
        return {k: float(v) for k, v in self._fill_fx.items()} if self.active() else {}

    def fills(self, limit: int = 300) -> list[dict[str, Any]]:
        if not self.active() or not self._fills:
            return []
        return sorted(self._fills["items"], key=lambda f: f.get("filled_at") or f["ordered_at"], reverse=True)[:limit]

    # ---------------------------------------------------------------- connect / disconnect
    def connect(self, client_id: str, client_secret: str) -> dict[str, Any]:
        """Try the key, store it only when it works, then take the first snapshot. Raises TossError / ValueError."""
        from marketlens.config import validate_setup

        if not self.enabled:
            raise ValueError("토스증권 연동은 실데이터(LIVE) 모드에서만 켤 수 있습니다 — 모의 데이터의 가격으로 실제 계좌를 평가하면 틀린 숫자가 나옵니다")
        clean = validate_setup({"TOSS_CLIENT_ID": client_id, "TOSS_CLIENT_SECRET": client_secret})
        if len(clean) != 2:
            raise ValueError("client_id와 client_secret을 모두 입력하세요")
        trial = TossClient(clean["TOSS_CLIENT_ID"], clean["TOSS_CLIENT_SECRET"], transport=self._transport)
        acct = trial.brokerage_account()  # a wrong key / IP / account fails here, before anything is stored
        with self._lock:
            self._save_keys(clean)
            old = self.client
            self.client = trial
            if self._snap and (self._snap.get("account") or {}).get("seq") != acct.seq:
                self._clear()
            self.last_error = None
        if old is not None and old is not trial:
            old.close()
        return self.sync(full=True)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._forget_keys(("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"))
            if self.client is not None:
                self.client.close()
            self.client = None
            self._clear()
            self.last_error = None
        self._changed()
        return self.status()

    def _clear(self) -> None:
        self._snap, self._fills, self._fill_fx = None, None, {}
        with self.sf() as s:
            for k in (SNAP_KEY, FILLS_KEY, FILL_FX_KEY):
                repo.delete_setting(s, k)
            s.commit()

    # ---------------------------------------------------------------- sync
    def sync(self, full: bool = False) -> dict[str, Any]:
        """One sync. Raises TossError after recording it (the refresher backs off); the last snapshot stays."""
        if not self.enabled or self.client is None:
            raise TossError("NOT_CONFIGURED", "토스증권 키가 없습니다")
        with self._lock:
            self.last_attempt = self.now()
            try:
                changed = self._sync(self.client, full)
            except TossError as e:
                self.last_error = e.as_dict() | {"at": self.now().isoformat()}
                log.warning("toss sync failed: %s", e.kind)
                raise
            self.last_error = None
        if changed:
            self._changed()
        return self.status()

    def _sync(self, c: TossClient, full: bool) -> bool:
        acct = c.brokerage_account()
        hs = c.holdings(acct.seq)
        usd, krw = c.buying_power(acct.seq, "USD"), c.buying_power(acct.seq, "KRW")
        fx = (self._snap or {}).get("fx")
        try:
            rate, mid, at = c.usd_krw()
            fx = {"rate": _s(rate), "mid": _s(mid), "at": at.isoformat() if at else None}
        except TossError as e:
            if e.kind in KEY_TROUBLE:
                raise
        now = self.now()
        holdings = [{k: (_s(v) if isinstance(v, Decimal) else v) for k, v in asdict(h).items()} for h in hs]
        totals: dict[str, dict[str, str]] = {}
        for h in hs:
            t = totals.setdefault(h.currency, {"purchase": "0", "value": "0", "pnl": "0", "daily_pnl": "0"})
            for k, v in (("purchase", h.purchase_amount), ("value", h.market_value), ("pnl", h.pnl), ("daily_pnl", h.daily_pnl)):
                t[k] = _s(Decimal(t[k]) + v)  # type: ignore[assignment]
        snap = {"taken_at": now.isoformat(), "account": {"seq": acct.seq, "masked": acct.masked}, "holdings": holdings, "totals": totals,
                "cash": {"USD": _s(usd), "KRW": _s(krw)}, "fx": fx, "spec": "1.2.17"}
        sig = lambda x: sorted((h["symbol"], h["quantity"], h["avg_price"]) for h in (x or {}).get("holdings", []))  # noqa: E731
        changed = self._snap is None or sig(self._snap) != sig(snap) or self._snap.get("cash") != snap["cash"]
        fills = self._fills
        if full or fills is None or changed or time.monotonic() - self._fills_at >= FILLS_EVERY_S:
            fills = self._sync_fills(c, acct.seq, now, fills if not full else None)
            self._fills_at = time.monotonic()
        fill_fx = self._sync_fill_fx(c, fills) if not full else self._fill_fx  # connecting stays quick; the next sync looks them up
        with self.sf() as s:
            repo.set_setting(s, SNAP_KEY, json.dumps(snap))
            if fills is not self._fills:
                repo.set_setting(s, FILLS_KEY, json.dumps(fills))
            if fill_fx is not self._fill_fx:
                repo.set_setting(s, FILL_FX_KEY, json.dumps(fill_fx))
            s.commit()
        self._snap, self._fills = snap, fills
        self._fill_fx = fill_fx  # read per request by the won-based return: no new version (nothing held changed)
        return changed

    def _sync_fill_fx(self, c: TossClient, fills: dict[str, Any] | None) -> dict[str, str]:
        """The Toss rate at each USD execution not looked up yet (newest first, a few per sync). A failed lookup is
        tried again next sync; a key problem stops the sync as any other request would."""
        todo = [f for f in (fills or {}).get("items", []) if f.get("currency") == "USD" and f["order_id"] not in self._fill_fx]
        if not todo:
            return self._fill_fx
        out = dict(self._fill_fx)
        for f in todo[:FILL_FX_PER_SYNC]:
            when = datetime.fromisoformat(f.get("filled_at") or f["ordered_at"])
            try:
                rate, _mid, _at = c.usd_krw(when)
            except TossError as e:
                if e.kind in KEY_TROUBLE:
                    raise
                log.info("toss fill rate %s: %s", f["order_id"], e.kind)
                break
            if rate and rate > 0:
                out[f["order_id"]] = _s(rate)  # type: ignore[assignment]
        return out if out != self._fill_fx else self._fill_fx

    def _sync_fills(self, c: TossClient, seq: int, now: datetime, prev: dict[str, Any] | None) -> dict[str, Any]:
        today = now.astimezone(KST).date()
        items = {f["order_id"]: f for f in (prev or {}).get("items", [])}
        if prev and prev.get("complete"):
            last = max((date.fromisoformat(f["ordered_at"][:10]) for f in items.values()), default=today)
            since = min(last, today) - timedelta(days=3)  # re-read a few days: a late partial fill or a correction
            since = max(since, date.fromisoformat(prev["since"]))
            first = date.fromisoformat(prev["since"])
        else:
            since = first = today - timedelta(days=FILLS_FIRST_DAYS)
        got, complete = c.fills(seq, since, today)
        for f in got:
            items[f.order_id] = self._fill_dict(f)
        keep = sorted(items.values(), key=lambda f: f["ordered_at"], reverse=True)[:FILLS_KEEP]
        # complete: every execution since ``since`` is here — the first full read reached its end, and so did each later one
        return {"items": keep, "since": first.isoformat(), "complete": complete and (prev is None or bool(prev.get("complete"))), "synced_at": now.isoformat()}

    @staticmethod
    def _fill_dict(f: TossFill) -> dict[str, Any]:
        d = {k: (_s(v) if isinstance(v, Decimal) else v) for k, v in asdict(f).items()}
        d["ordered_at"] = f.ordered_at.isoformat()
        d["filled_at"] = f.filled_at.isoformat() if f.filled_at else None
        d["settlement"] = f.settlement.isoformat() if f.settlement else None
        return d

    def _changed(self) -> None:
        self.version += 1
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception as e:  # noqa: BLE001 - a listener never breaks the sync
                log.warning("broker change listener failed: %s", type(e).__name__)
