"""Deterministic technical indicators on daily bars (oldest → newest)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from marketlens.domain.market import Bar


def sma(values: Sequence[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def ema(values: Sequence[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder RSI."""
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l_ in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l_) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: Sequence[Bar], n: int = 14) -> float | None:
    """Wilder ATR."""
    if len(bars) < n + 1:
        return None
    trs = true_ranges(bars)[1:]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def anchored_vwap(bars: Sequence[Bar], anchor: date) -> float | None:
    """VWAP of typical price from ``anchor`` (inclusive) to the last bar, using daily bars."""
    pv = 0.0
    vol = 0.0
    for b in bars:
        if b.day < anchor:
            continue
        tp = (b.high + b.low + b.close) / 3
        pv += tp * b.volume
        vol += b.volume
    if vol <= 0:
        return None
    return pv / vol


def period_return(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n + 1 or closes[-n - 1] <= 0:
        return None
    return closes[-1] / closes[-n - 1] - 1


def relative_strength(closes: Sequence[float], bench: Sequence[float], n: int) -> float | None:
    """Excess return vs benchmark over ``n`` bars (e.g. 0.05 = +5pp)."""
    r = period_return(closes, n)
    rb = period_return(bench, n)
    if r is None or rb is None:
        return None
    return r - rb


def volume_ratio(volumes: Sequence[float], n: int = 20) -> float | None:
    if len(volumes) < n + 1:
        return None
    base = sum(volumes[-n - 1 : -1]) / n
    if base <= 0:
        return None
    return volumes[-1] / base


def swing_levels(bars: Sequence[Bar], window: int = 3) -> tuple[list[float], list[float]]:
    """Swing lows (supports) and swing highs (resistances) using a symmetric fractal window."""
    lows: list[float] = []
    highs: list[float] = []
    for i in range(window, len(bars) - window):
        seg = bars[i - window : i + window + 1]
        if bars[i].low == min(b.low for b in seg):
            lows.append(bars[i].low)
        if bars[i].high == max(b.high for b in seg):
            highs.append(bars[i].high)
    return lows, highs


def cluster_levels(levels: Sequence[float], tolerance_pct: float) -> list[float]:
    """Merge levels within ``tolerance_pct`` of each other (average of the cluster)."""
    if not levels:
        return []
    srt = sorted(levels)
    clusters: list[list[float]] = [[srt[0]]]
    for lv in srt[1:]:
        ref = clusters[-1][-1]
        if ref > 0 and (lv - ref) / ref * 100 <= tolerance_pct:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    return [sum(c) / len(c) for c in clusters]


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    last_close: float | None
    sma20: float | None
    sma50: float | None
    sma100: float | None
    sma200: float | None
    ema20: float | None
    ema50: float | None
    rsi14: float | None
    atr14: float | None
    high_20d: float | None
    low_20d: float | None
    high_52w: float | None
    low_52w: float | None
    volume_ratio: float | None
    avg_volume_20d: float | None
    avg_dollar_volume_20d: float | None
    gap_pct: float | None
    anchored_vwap: float | None
    anchor_date: date | None
    rs_3m: float | None
    rs_6m: float | None
    return_1m: float | None
    return_3m: float | None
    volatility_20d: float | None
    supports: tuple[float, ...]
    resistances: tuple[float, ...]

    @property
    def distance_from_52w_high(self) -> float | None:
        if self.last_close is None or not self.high_52w:
            return None
        return self.last_close / self.high_52w - 1


def _stdev(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def compute_technicals(
    bars: Sequence[Bar],
    benchmark_closes: Sequence[float] | None = None,
    anchor: date | None = None,
    level_tolerance_pct: float = 1.5,
) -> TechnicalSnapshot:
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    last = closes[-1] if closes else None
    w52 = bars[-252:]
    w20 = bars[-20:]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 20), len(closes)) if closes[i - 1] > 0]
    sd = _stdev(rets)
    lows, highs = swing_levels(bars[-120:])
    supports = tuple(sorted((lv for lv in cluster_levels(lows, level_tolerance_pct) if last and lv < last), reverse=True))
    resistances = tuple(sorted(lv for lv in cluster_levels(highs, level_tolerance_pct) if last and lv > last))
    if anchor is None and w52:
        # default anchor: the 52-week low (a common "fresh-start" anchor)
        anchor = min(w52, key=lambda b: b.low).day
    gap = None
    if len(bars) >= 2 and bars[-2].close > 0:
        gap = bars[-1].open / bars[-2].close - 1
    avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else None
    avg_dv = sum(b.close * b.volume for b in bars[-20:]) / 20 if len(bars) >= 20 else None
    bench = list(benchmark_closes) if benchmark_closes else None
    return TechnicalSnapshot(
        last_close=last,
        sma20=sma(closes, 20),
        sma50=sma(closes, 50),
        sma100=sma(closes, 100),
        sma200=sma(closes, 200),
        ema20=ema(closes, 20),
        ema50=ema(closes, 50),
        rsi14=rsi(closes, 14),
        atr14=atr(bars, 14),
        high_20d=max((b.high for b in w20), default=None),
        low_20d=min((b.low for b in w20), default=None),
        high_52w=max((b.high for b in w52), default=None),
        low_52w=min((b.low for b in w52), default=None),
        volume_ratio=volume_ratio(vols, 20),
        avg_volume_20d=avg_vol,
        avg_dollar_volume_20d=avg_dv,
        gap_pct=gap,
        anchored_vwap=anchored_vwap(bars, anchor) if anchor else None,
        anchor_date=anchor,
        rs_3m=relative_strength(closes, bench, 63) if bench else None,
        rs_6m=relative_strength(closes, bench, 126) if bench else None,
        return_1m=period_return(closes, 21),
        return_3m=period_return(closes, 63),
        volatility_20d=(sd * (252**0.5)) if sd is not None else None,
        supports=supports,
        resistances=resistances,
    )
