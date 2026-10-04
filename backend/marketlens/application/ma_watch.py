"""이동평균선 닿음 알림 (owner 2026-10-05: "200일선에 닿았다 몇일선에 닿았다 … 닿았을때 알람"): every live price of a
held or watched name is checked against the 20 / 50 / 200-day moving averages of its newest analysis (on today's share
basis), and a touch becomes an alert in the app's alert center — once per name, line and New York trading day, never on
every tick. Only a display and an alert: nothing here changes a score or a decision.

A price "touches" a line when it is within a quarter of the daily ATR of it (at least 0.5 % of the line), so a quiet
large-cap and a volatile small-cap are held to the same idea of "at the line". The lines are daily values from the
analysis (they move little within a day); an analysis older than ``MAX_AGE`` is not used.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from marketlens.domain.market_calendar import to_ny

log = logging.getLogger(__name__)

LINES: tuple[tuple[int, str], ...] = ((20, "20일선"), (50, "50일선"), (200, "200일선"))
TOUCH_ATR = 0.25  # within a quarter of the daily range of the line …
TOUCH_MIN_PCT = 0.005  # … but never tighter than 0.5 % of it
STALE_QUOTE = timedelta(minutes=20)  # an older price (yesterday's close after a restart) announces nothing
MAX_AGE = timedelta(days=7)  # moving averages older than a week are not "today's" lines


@dataclass(frozen=True, slots=True)
class MaLevels:
    symbol: str
    sma: dict[int, float]  # window → value, on today's share basis
    atr: float | None
    as_of: datetime


def band(line: float, atr: float | None) -> float:
    return max(TOUCH_ATR * atr if atr else 0.0, TOUCH_MIN_PCT * line)


def state(price: float, line: float, atr: float | None) -> str:
    """AT within the touch band, else ABOVE / BELOW the line."""
    if abs(price - line) <= band(line, atr):
        return "AT"
    return "ABOVE" if price > line else "BELOW"


def levels_from_result(symbol: str, result: dict[str, Any] | None, as_of: datetime, split_factor: float = 1.0) -> MaLevels | None:
    t = (result or {}).get("technicals") or {}
    f = split_factor if split_factor and split_factor > 0 else 1.0
    sma = {n: float(t[f"sma{n}"]) / f for n, _ in LINES if isinstance(t.get(f"sma{n}"), (int, float)) and t[f"sma{n}"] > 0}
    if not sma:
        return None
    atr = t.get("atr14")
    return MaLevels(symbol.upper(), sma, float(atr) / f if isinstance(atr, (int, float)) and atr > 0 else None, as_of)


class MaWatch:
    def __init__(self, add: Callable[..., Any], now: Callable[[], datetime]) -> None:
        self._add = add
        self._now = now
        self._lock = threading.Lock()
        self._levels: dict[str, MaLevels] = {}
        self._last: dict[tuple[str, int], str] = {}  # (symbol, window) → last state seen
        self._sent: dict[tuple[str, int], date] = {}  # (symbol, window) → NY day of the last alert
        self.loader: Callable[[], list[MaLevels]] | None = None

    def load(self) -> int:
        """Reload the lines of the held and watched names (background; a failure keeps the previous ones)."""
        if self.loader is None:
            return 0
        try:
            levels = self.loader()
        except Exception as e:  # noqa: BLE001 - alerts are a convenience; the stock page still shows the lines
            log.warning("ma watch: load failed: %s", type(e).__name__)
            return len(self._levels)
        cutoff = self._now() - MAX_AGE
        with self._lock:
            self._levels = {lv.symbol: lv for lv in levels if lv.as_of >= cutoff}
            keep = set(self._levels)
            self._last = {k: v for k, v in self._last.items() if k[0] in keep}
        return len(self._levels)

    def symbols(self) -> list[str]:
        with self._lock:
            return list(self._levels)

    def observe(self, symbol: str, price: float | None, ts: datetime | None) -> list[dict[str, Any]]:
        """A live price of ``symbol``: an alert for each line it has just come to touch (first time today)."""
        sym = symbol.upper()
        now = self._now()
        if price is None or price <= 0 or ts is None or now - ts > STALE_QUOTE:
            return []
        out: list[dict[str, Any]] = []
        day = to_ny(now).date()
        with self._lock:
            lv = self._levels.get(sym)
            if lv is None:
                return []
            for n, label in LINES:
                line = lv.sma.get(n)
                if line is None:
                    continue
                st = state(price, line, lv.atr)
                prev = self._last.get((sym, n))
                self._last[(sym, n)] = st
                if st != "AT" or prev == "AT" or self._sent.get((sym, n)) == day:
                    continue
                self._sent[(sym, n)] = day
                how = " · 위에서 내려와 닿음(지지 시험)" if prev == "ABOVE" else " · 아래에서 올라와 닿음(저항 시험)" if prev == "BELOW" else ""
                out.append({"window": n, "label": label, "line": line, "from": prev,
                            "text": f"{sym} {label}에 닿음 — 현재가 ${price:,.2f}, {label} ${line:,.2f} ({price / line - 1:+.1%}){how}"})
        for a in out:
            self._add(sym, f"MA_TOUCH_{a['window']}", "info", a["text"], price, None, source="ma", window=a["window"])
        return out
