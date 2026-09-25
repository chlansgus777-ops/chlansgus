"""Macro snapshot, market regime engine and macro → company transmission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from marketlens.domain.enums import EdgeType
from marketlens.domain.facts import Fact

# Canonical macro series ids (provider-independent)
FED_FUNDS = "FED_FUNDS"
US2Y = "US2Y"
US10Y = "US10Y"
US30Y = "US30Y"
CPI_YOY = "CPI_YOY"
CORE_CPI_YOY = "CORE_CPI_YOY"
PCE_YOY = "PCE_YOY"
CORE_PCE_YOY = "CORE_PCE_YOY"
PAYROLLS_CHG = "PAYROLLS_CHG"
UNEMPLOYMENT = "UNEMPLOYMENT"
GDP_QOQ_SAAR = "GDP_QOQ_SAAR"
USD_INDEX = "USD_INDEX"
WTI = "WTI"
BRENT = "BRENT"
GOLD = "GOLD"
VIX = "VIX"
HY_SPREAD = "HY_SPREAD"
SPX = "SPX"
NASDAQ_COMP = "NASDAQ_COMP"
NDX = "NDX"
RUT = "RUT"
SOX = "SOX"
BREADTH_ABOVE_200D = "BREADTH_ABOVE_200D"

ALL_SERIES = (
    FED_FUNDS, US2Y, US10Y, US30Y, CPI_YOY, CORE_CPI_YOY, PCE_YOY, CORE_PCE_YOY, PAYROLLS_CHG,
    UNEMPLOYMENT, GDP_QOQ_SAAR, USD_INDEX, WTI, BRENT, GOLD, VIX, HY_SPREAD, SPX, NASDAQ_COMP, NDX,
    RUT, SOX, BREADTH_ABOVE_200D,
)


@dataclass(frozen=True, slots=True)
class MacroSeries:
    series_id: str
    latest: Fact
    change_20d: float | None = None  # absolute change (for yields: in percentage points)
    pct_change_20d: float | None = None
    above_200d: bool | None = None


@dataclass(frozen=True, slots=True)
class MacroSnapshot:
    as_of: datetime
    series: Mapping[str, MacroSeries] = field(default_factory=dict)

    def value(self, sid: str) -> float | None:
        s = self.series.get(sid)
        return s.latest.value if s is not None and s.latest.is_usable else None

    def change(self, sid: str) -> float | None:
        s = self.series.get(sid)
        return s.change_20d if s is not None and s.latest.is_usable else None

    def pct_change(self, sid: str) -> float | None:
        s = self.series.get(sid)
        return s.pct_change_20d if s is not None and s.latest.is_usable else None

    @property
    def yield_curve_2s10s(self) -> float | None:
        a, b = self.value(US10Y), self.value(US2Y)
        return a - b if a is not None and b is not None else None


@dataclass(frozen=True, slots=True)
class RegimeReading:
    regime: str
    score: float  # 0..1 strength
    confidence: float  # 0..1 based on data availability
    active: bool
    evidence: tuple[str, ...]


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    activation: float = 0.55
    vix_calm: float = 16.0
    vix_stress: float = 25.0
    hy_stress: float = 4.5  # percentage points
    rate_shock_bp_20d: float = 0.30  # +30bp in 20 days
    oil_shock_pct_20d: float = 0.15
    cpi_hot: float = 3.5
    breadth_strong: float = 0.60
    breadth_weak: float = 0.40


def _regime(name: str, parts: list[tuple[float | None, str]], th: RegimeThresholds) -> RegimeReading:
    avail = [(s, e) for s, e in parts if s is not None]
    if not avail:
        return RegimeReading(name, 0.0, 0.0, False, ("insufficient macro data",))
    score = sum(s for s, _ in avail) / len(avail)
    conf = len(avail) / len(parts)
    ev = tuple(e for s, e in avail if s >= 0.5)
    return RegimeReading(name, round(score, 4), round(conf, 4), score >= th.activation and conf >= 0.5, ev)


def detect_regimes(m: MacroSnapshot, th: RegimeThresholds | None = None) -> list[RegimeReading]:
    th = th or RegimeThresholds()
    vix = m.value(VIX)
    hy = m.value(HY_SPREAD)
    d10 = m.change(US10Y)
    oil = m.pct_change(WTI)
    cpi = m.value(CORE_CPI_YOY)
    breadth = m.value(BREADTH_ABOVE_200D)
    spx = m.series.get(SPX)
    spx_trend = spx.above_200d if spx is not None else None
    sox = m.pct_change(SOX)
    spx_chg = m.pct_change(SPX)
    usd = m.pct_change(USD_INDEX)
    hy_chg = m.change(HY_SPREAD)

    def f(cond: float | None) -> float | None:
        return None if cond is None else _clip01(cond)

    risk_on = [
        (f(None if vix is None else (th.vix_stress - vix) / (th.vix_stress - th.vix_calm)), f"VIX {vix}"),
        (None if spx_trend is None else (1.0 if spx_trend else 0.0), "S&P 500 above 200D"),
        (f(None if breadth is None else (breadth - th.breadth_weak) / (th.breadth_strong - th.breadth_weak)), f"breadth {breadth}"),
        (f(None if hy is None else (th.hy_stress - hy) / 1.5), f"HY spread {hy}"),
    ]
    risk_off = [
        (f(None if vix is None else (vix - th.vix_calm) / (th.vix_stress - th.vix_calm)), f"VIX {vix}"),
        (None if spx_trend is None else (0.0 if spx_trend else 1.0), "S&P 500 below 200D"),
        (f(None if hy_chg is None else hy_chg / 0.75), f"HY spread 20d change {hy_chg}"),
    ]
    inflation = [
        (f(None if cpi is None else (cpi - 2.5) / (th.cpi_hot - 2.5)), f"core CPI {cpi}%"),
        (f(None if oil is None else oil / th.oil_shock_pct_20d), f"WTI 20d {oil}"),
        (f(None if d10 is None else d10 / th.rate_shock_bp_20d), f"10Y 20d change {d10}"),
    ]
    growth_scare = [
        (f(None if d10 is None else -d10 / th.rate_shock_bp_20d), f"10Y falling {d10}"),
        (f(None if spx_chg is None else -spx_chg / 0.08), f"S&P 20d {spx_chg}"),
        (f(None if m.change(UNEMPLOYMENT) is None else m.change(UNEMPLOYMENT) / 0.3), "unemployment rising"),
    ]
    liq_exp = [
        (f(None if d10 is None else -d10 / th.rate_shock_bp_20d), "yields falling"),
        (f(None if usd is None else -usd / 0.03), "USD weakening"),
        (f(None if hy_chg is None else -hy_chg / 0.5), "credit spreads tightening"),
    ]
    liq_tight = [
        (f(None if d10 is None else d10 / th.rate_shock_bp_20d), "yields rising"),
        (f(None if usd is None else usd / 0.03), "USD strengthening"),
        (f(None if hy_chg is None else hy_chg / 0.5), "credit spreads widening"),
    ]
    ai_mom = [
        (f(None if sox is None else sox / 0.10), f"SOX 20d {sox}"),
        (f(None if sox is None or spx_chg is None else (sox - spx_chg) / 0.06), "SOX outperforming S&P"),
    ]
    defensive = [
        (f(None if spx_chg is None else -spx_chg / 0.05), "equities weak"),
        (f(None if breadth is None else (th.breadth_strong - breadth) / 0.2), "narrow breadth"),
        (f(None if d10 is None else -d10 / 0.2), "bond bid"),
    ]
    commodity = [
        (f(None if oil is None else oil / th.oil_shock_pct_20d), "oil spike"),
        (f(None if m.pct_change(GOLD) is None else m.pct_change(GOLD) / 0.08), "gold spike"),
    ]
    credit = [
        (f(None if hy is None else (hy - 3.5) / (th.hy_stress + 1.5 - 3.5)), f"HY spread level {hy}"),
        (f(None if hy_chg is None else hy_chg / 0.75), "HY spread widening"),
    ]
    mult_exp = [
        (f(None if spx_chg is None else spx_chg / 0.06), "equities rising"),
        (f(None if d10 is None else -d10 / 0.25), "discount rate falling"),
    ]
    mult_comp = [
        (f(None if d10 is None else d10 / 0.25), "discount rate rising"),
        (f(None if spx_chg is None else -spx_chg / 0.06), "equities falling"),
    ]
    return [
        _regime("Risk On", risk_on, th),
        _regime("Risk Off", risk_off, th),
        _regime("Inflation Shock", inflation, th),
        _regime("Growth Scare", growth_scare, th),
        _regime("Liquidity Expansion", liq_exp, th),
        _regime("Liquidity Tightening", liq_tight, th),
        _regime("AI Momentum", ai_mom, th),
        _regime("Defensive Rotation", defensive, th),
        _regime("Commodity Shock", commodity, th),
        _regime("Credit Stress", credit, th),
        _regime("Multiple Expansion", mult_exp, th),
        _regime("Multiple Compression", mult_comp, th),
    ]


def primary_regime(readings: list[RegimeReading]) -> str:
    active = sorted((r for r in readings if r.active), key=lambda r: r.score * r.confidence, reverse=True)
    return active[0].regime if active else "Neutral"


# Macro factor moves, normalised to roughly [-1, 1]; positive = factor rising
@dataclass(frozen=True, slots=True)
class FactorMove:
    factor: str
    move: float
    evidence: str


FACTOR_EDGE = {
    "RATES": EdgeType.EXPOSED_TO_RATE,
    "OIL": EdgeType.EXPOSED_TO_COMMODITY,
    "USD": EdgeType.EXPOSED_TO_REGION,
    "AI": EdgeType.EXPOSED_TO_AI,
}


def factor_moves(m: MacroSnapshot) -> list[FactorMove]:
    out: list[FactorMove] = []
    d10 = m.change(US10Y)
    if d10 is not None:
        out.append(FactorMove("RATES", max(-1.0, min(1.0, d10 / 0.5)), f"US10Y 20d change {d10 * 100:+.0f}bp"))
    oil = m.pct_change(WTI)
    if oil is not None:
        out.append(FactorMove("OIL", max(-1.0, min(1.0, oil / 0.2)), f"WTI 20d {oil * 100:+.1f}%"))
    usd = m.pct_change(USD_INDEX)
    if usd is not None:
        out.append(FactorMove("USD", max(-1.0, min(1.0, usd / 0.04)), f"USD index 20d {usd * 100:+.1f}%"))
    sox = m.pct_change(SOX)
    spx = m.pct_change(SPX)
    if sox is not None and spx is not None:
        out.append(FactorMove("AI", max(-1.0, min(1.0, (sox - spx) / 0.08)), f"SOX vs S&P 20d {(sox - spx) * 100:+.1f}pp"))
    vix = m.value(VIX)
    if vix is not None:
        out.append(FactorMove("VOL", max(-1.0, min(1.0, (vix - 18.0) / 12.0)), f"VIX {vix:.1f}"))
    return out


@dataclass(frozen=True, slots=True)
class MacroExposure:
    """Company sensitivities in [-1, 1] per factor (from the exposure graph)."""

    rates: float = 0.0
    oil: float = 0.0
    usd: float = 0.0
    ai: float = 0.0
    beta: float = 1.0


@dataclass(frozen=True, slots=True)
class MacroImpact:
    net: float  # [-1, 1]
    contributions: tuple[tuple[str, float, str], ...]  # factor, contribution, explanation


def macro_impact(exposure: MacroExposure, moves: list[FactorMove]) -> MacroImpact:
    sens = {"RATES": exposure.rates, "OIL": exposure.oil, "USD": exposure.usd, "AI": exposure.ai}
    contribs: list[tuple[str, float, str]] = []
    for mv in moves:
        if mv.factor == "VOL":
            # higher volatility hurts high-beta names more
            c = -mv.move * max(0.0, exposure.beta - 0.6) * 0.5
            if abs(c) > 1e-9:
                contribs.append((mv.factor, round(c, 4), f"{mv.evidence} × beta {exposure.beta:.2f}"))
            continue
        s = sens.get(mv.factor, 0.0)
        if s == 0:
            continue
        c = s * mv.move
        direction = "tailwind" if c > 0 else "headwind"
        contribs.append((mv.factor, round(c, 4), f"{mv.evidence} × sensitivity {s:+.2f} → {direction}"))
    net = max(-1.0, min(1.0, sum(c for _, c, _ in contribs)))
    return MacroImpact(net=round(net, 4), contributions=tuple(contribs))
