"""Semantic verification of numeric claims in AI committee output.

A number in AI text is accepted only if an evidence item supports it on ALL of:
- entity  : the company the sentence talks about (the analysed ticker, or market-wide data for macro);
- metric  : the metric named next to the number (price, EPS, P/E, margin, …) must match the evidence metric;
- unit    : $ amounts only match USD evidence, % only fraction/percent evidence, "x" only multiples, …;
- scale   : K/M/B/T, thousand/million/billion, 만/억/조 are applied before comparing;
- sign    : an explicit minus (or a direction word such as "declined"/"감소") must agree with the evidence;
- value   : within the precision the text printed (half a unit of the last digit) or 0.5%.

Matching "any number that appears somewhere in the evidence" is NOT enough: "Price is $100" is rejected
even when some unrelated evidence equals 100, and "EPS is -125.5" is rejected when the evidence is +125.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from marketlens.application.evidence import Evidence

# ------------------------------------------------------------------------------------ metric classes

_EXACT_CLASS: dict[str, str] = {
    "price.current": "price", "val.price": "price",
    "entry.max_buy": "max_buy", "entry.stop": "stop", "entry.target1": "target", "analyst.target_price_consensus": "target",
    "entry.rr": "rr", "fund.eps_ttm": "eps", "analyst.forward_eps": "eps",
    "earnings.last": "surprise", "earnings.revenue_surprise": "surprise", "earnings.guide_rev_vs_cons": "guidance",
    "fund.revenue_ttm": "revenue", "analyst.forward_revenue": "revenue",
    "val.trailing_pe": "pe", "val.forward_pe": "pe", "val.peg": "peg",
    "val.market_cap": "market_cap", "val.enterprise_value": "ev",
    "tech.rsi14": "rsi", "tech.atr14": "atr", "tech.volatility_20d": "volatility", "tech.rs_6m": "relative_strength",
    "tech.avg_dollar_volume_20d": "dollar_volume", "tech.volume_ratio": "volume",
    "dq.completeness": "completeness", "macro.VIX": "vix", "macro.WTI": "oil", "macro.BRENT": "oil", "macro.USDKRW": "fx",
    "ownership.short_interest": "short_interest", "ownership.insider_net_90d": "insider",
    "options.expected_move": "expected_move", "options.iv_rank": "iv", "issues.net_swing": "issue_score",
    "committee.consensus": "consensus", "decision.confidence": "confidence",
}
_RATE_SERIES = {"macro.US2Y", "macro.US10Y", "macro.US30Y", "macro.FED_FUNDS", "macro.HY_SPREAD"}
_INFLATION_SERIES = {"macro.CPI_YOY", "macro.CORE_CPI_YOY", "macro.PCE_YOY", "macro.CORE_PCE_YOY"}


def metric_class(key: str) -> str:
    if key in _EXACT_CLASS:
        return _EXACT_CLASS[key]
    if key in _RATE_SERIES:
        return "rate"
    if key in _INFLATION_SERIES:
        return "inflation"
    head, _, last = key.partition(".")
    if head == "score":
        return "score"
    if head == "portfolio":
        return "portfolio"
    if head == "tech":
        return "price_level"  # sma / vwap / 52w high-low
    if head == "val":
        return "yield" if "yield" in last else "multiple"
    if head == "analyst" and "revision" in last:
        return "revision"
    if head == "fund":
        if "margin" in last:
            return "margin"
        if "growth" in last or last.endswith("_yoy"):
            return "growth"
    if head == "macro":
        return "macro"
    return "other"


# text keyword → claimed metric class (English + Korean). Longer phrases are listed first.
_KEYWORDS: list[tuple[str, str]] = [
    ("earnings per share", "eps"), ("주당순이익", "eps"), ("eps", "eps"),
    ("price-to-earnings", "pe"), ("p/e", "pe"), ("pe ratio", "pe"), ("per ", "pe"), ("주가수익비율", "pe"),
    ("ev/ebitda", "multiple"), ("ev/sales", "multiple"), ("p/s", "multiple"), ("p/b", "multiple"), ("pbr", "multiple"), ("psr", "multiple"), ("p/fcf", "multiple"), ("p/ffo", "multiple"), ("멀티플", "multiple"), ("배수", "multiple"),
    ("peg", "peg"),
    ("market cap", "market_cap"), ("market capitalization", "market_cap"), ("시가총액", "market_cap"), ("시총", "market_cap"),
    ("enterprise value", "ev"),
    ("maximum buy", "max_buy"), ("max buy", "max_buy"), ("최대 매수", "max_buy"),
    ("stop-loss", "stop"), ("stop", "stop"), ("손절", "stop"),
    ("price target", "target"), ("target", "target"), ("목표", "target"),
    ("risk/reward", "rr"), ("reward/risk", "rr"), ("r/r", "rr"), ("손익비", "rr"),
    ("gross margin", "margin"), ("operating margin", "margin"), ("margin", "margin"), ("마진", "margin"), ("이익률", "margin"),
    ("revision", "revision"), ("리비전", "revision"), ("추정치 변화", "revision"),
    ("surprise", "surprise"), ("서프라이즈", "surprise"),
    ("guidance", "guidance"), ("가이던스", "guidance"),
    ("growth", "growth"), ("성장률", "growth"), ("성장", "growth"), ("증가율", "growth"), ("year-over-year", "growth"), ("yoy", "growth"),
    ("revenue", "revenue"), ("sales", "revenue"), ("매출", "revenue"),
    ("rsi", "rsi"), ("atr", "atr"),
    ("moving average", "price_level"), ("sma", "price_level"), ("vwap", "price_level"), ("52-week", "price_level"), ("52주", "price_level"), ("이동평균", "price_level"),
    ("volatility", "volatility"), ("변동성", "volatility"),
    ("relative strength", "relative_strength"), ("상대강도", "relative_strength"),
    ("dollar volume", "dollar_volume"), ("거래대금", "dollar_volume"),
    ("short interest", "short_interest"), ("공매도", "short_interest"),
    ("insider", "insider"), ("내부자", "insider"),
    ("expected move", "expected_move"), ("예상 변동", "expected_move"), ("implied volatility", "iv"), ("iv rank", "iv"),
    ("treasury", "rate"), ("yield", "rate"), ("금리", "rate"), ("국채", "rate"), ("fed funds", "rate"),
    ("10y", "rate"), ("2y", "rate"), ("30y", "rate"), ("10-year", "rate"), ("2-year", "rate"), ("30-year", "rate"), ("10년물", "rate"), ("2년물", "rate"), ("30년물", "rate"),
    ("cpi", "inflation"), ("pce", "inflation"), ("inflation", "inflation"), ("물가", "inflation"), ("인플레이션", "inflation"),
    ("vix", "vix"), ("wti", "oil"), ("brent", "oil"), ("oil", "oil"), ("유가", "oil"), ("원/달러", "fx"), ("환율", "fx"),
    ("consensus", "consensus"), ("합의", "consensus"),
    ("confidence", "confidence"), ("신뢰도", "confidence"),
    ("completeness", "completeness"), ("완결성", "completeness"),
    ("score", "score"), ("점수", "score"),
    ("share price", "price"), ("stock price", "price"), ("trading at", "price"), ("trades at", "price"), ("closed at", "price"),
    ("price", "price"), ("주가", "price"), ("현재가", "price"), ("종가", "price"), ("가격", "price"),
]
# claimed class → evidence classes that can support it
_COMPATIBLE: dict[str, frozenset[str]] = {
    "price": frozenset({"price"}),
    "pe": frozenset({"pe"}), "multiple": frozenset({"multiple", "pe"}), "peg": frozenset({"peg"}),
    "growth": frozenset({"growth", "revision"}), "rate": frozenset({"rate", "yield"}), "confidence": frozenset({"confidence"}),
}
_TIME_WORDS = re.compile(r"^\s*[-\s]?(day|days|week|weeks|month|months|year|years|quarter|quarters|session|sessions|round|rounds|일|주|개월|년|분기|거래일|라운드|단계)", re.I)
_NEG_WORDS = ("decline", "declined", "fell", "fall", "drop", "dropped", "down", "decrease", "decreased", "shrank", "contract", "감소", "하락", "하회", "악화", "축소", "마이너스")
_POS_WORDS = ("rose", "rise", "increase", "increased", "grew", "up ", "improved", "expanded", "증가", "상승", "상회", "개선", "확대", "플러스")

NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<sign>[-−+])?\s?(?P<cur>US\$|\$|USD\s?)?"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<mag>\s?(?:thousand|million|billion|trillion|[KMBT](?![A-Za-z])|만|억|조))?"
    r"(?P<unit>\s?(?:%p|%|percent|퍼센트|bps|bp|x(?![A-Za-z])|배|달러|dollars?|원|포인트|points?|pts))?"
    r"(?![A-Za-z0-9])"  # "10Y", "30d", "Q3" style identifiers are labels, not data claims
)
_MAG = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9, "trillion": 1e12, "t": 1e12, "만": 1e4, "억": 1e8, "조": 1e12}
SAFE_SMALL_INTS = set(range(0, 11)) | {12, 14, 20, 30, 50, 52, 60, 90, 100, 120, 200}


@dataclass(frozen=True, slots=True)
class NumericClaim:
    raw: str
    value: float  # magnitude-applied, sign-applied
    explicit_sign: bool
    kind: str  # usd | krw | percent | bp | multiple | points | plain
    decimals: int
    scale: float
    claimed_class: str  # from nearby keywords ("other" when none)
    entity: str | None  # ticker named nearest before the number (None = not named)
    direction: int  # -1 / +1 from direction words near the number, 0 = none
    basis: str | None = None  # ACTUAL | FORECAST when the sentence says so ("forward", "trailing", "예상", …)


# words that say whether a number is a reported value or an estimate/forecast (longest first)
_BASIS_WORDS: list[tuple[str, str]] = [
    ("next fiscal year", "FORECAST"), ("next quarter", "FORECAST"), ("next year", "FORECAST"), ("forward", "FORECAST"),
    ("expected", "FORECAST"), ("expects", "FORECAST"), ("estimate", "FORECAST"), ("estimated", "FORECAST"), ("consensus", "FORECAST"),
    ("projected", "FORECAST"), ("forecast", "FORECAST"), ("guidance", "FORECAST"), ("outlook", "FORECAST"), ("fy+1", "FORECAST"),
    ("선행", "FORECAST"), ("예상", "FORECAST"), ("추정", "FORECAST"), ("컨센서스", "FORECAST"), ("전망", "FORECAST"), ("가이던스", "FORECAST"), ("내년", "FORECAST"), ("다음 분기", "FORECAST"),
    ("trailing", "ACTUAL"), ("ttm", "ACTUAL"), ("reported", "ACTUAL"), ("actual", "ACTUAL"), ("last quarter", "ACTUAL"), ("last year", "ACTUAL"), ("past 12 months", "ACTUAL"),
    ("최근 12개월", "ACTUAL"), ("지난 12개월", "ACTUAL"), ("발표한", "ACTUAL"), ("실제", "ACTUAL"), ("지난 분기", "ACTUAL"), ("과거", "ACTUAL"),
]


def _basis(before: str, after: str) -> str | None:
    low = before[-40:].lower()
    best, pos = None, -1
    for w, b in _BASIS_WORDS:
        i = low.rfind(w)
        if i > pos:
            best, pos = b, i
    if best is None:
        tail = after[:14].lower()
        for w, b in _BASIS_WORDS:
            if w in tail:
                return b
    return best


def _nearest_keyword(before: str) -> str:
    low = before.lower()
    best, pos = "other", -1
    for kw, cls in _KEYWORDS:
        i = low.rfind(kw)
        if i > pos:
            best, pos = cls, i
    return best


def extract_claims(text: str, known_tickers: Iterable[str] = ()) -> list[NumericClaim]:
    tickers = sorted({t for t in known_tickers if t}, key=len, reverse=True)
    tick_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in tickers) + r")(?![A-Za-z0-9])") if tickers else None
    out: list[NumericClaim] = []
    for m in NUM_RE.finditer(text):
        num = m.group("num")
        raw = m.group(0).strip()
        if not num:
            continue
        sent_start = max(text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start()), text.rfind("다. ", 0, m.start()))
        before = text[max(0, sent_start + 1, m.start() - 48) : m.start()]
        after = text[m.end() : m.end() + 20]
        if _TIME_WORDS.match(after) and not (m.group("cur") or m.group("unit")):
            continue  # "20-day", "52주", "2 rounds" are windows, not data claims
        value = float(num.replace(",", ""))
        dec = len(num.split(".")[1]) if "." in num else 0
        mag_s = (m.group("mag") or "").strip().lower()
        scale = _MAG.get(mag_s, 1.0)
        unit = (m.group("unit") or "").strip().lower()
        cur = m.group("cur")
        if cur or unit in ("달러", "dollar", "dollars"):
            kind = "usd"
        elif unit == "원":
            kind = "krw"
        elif unit in ("%", "percent", "퍼센트", "%p"):
            kind = "percent"
        elif unit in ("bp", "bps"):
            kind = "bp"
        elif unit in ("x", "배"):
            kind = "multiple"
        elif unit in ("포인트", "point", "points", "pts"):
            kind = "points"
        else:
            kind = "plain"
        sign = m.group("sign") or ""
        neg = sign in ("-", "−")
        v = -value * scale if neg else value * scale
        claimed = _nearest_keyword(before)
        if claimed == "other":
            claimed = _nearest_keyword(after[:8])  # Korean often puts the noun after the number ("84점")
        entity = None
        if tick_re is not None:
            hits = list(tick_re.finditer(before))
            if hits:
                entity = hits[-1].group(1)
        low = (before[-30:] + " " + after).lower()
        direction = -1 if any(w in low for w in _NEG_WORDS) else 1 if any(w in low for w in _POS_WORDS) else 0
        out.append(NumericClaim(raw, v, bool(sign), kind, dec, scale, claimed, entity, direction, _basis(before, after)))
    return out


def _candidates(c: NumericClaim, e: Evidence) -> list[float]:
    """Values of evidence ``e`` expressed in the claim's unit (empty = incompatible units)."""
    if not isinstance(e.value, (int, float)) or isinstance(e.value, bool):
        return []
    v = float(e.value)
    u = e.unit
    if c.kind == "usd":
        return [v] if u == "USD" else []
    if c.kind == "krw":
        return [v] if u == "KRW" else []
    if c.kind == "percent":
        return [v * 100] if u == "fraction" else [v] if u in ("pct", "points") else []
    if c.kind == "bp":
        return [v * 10_000] if u == "fraction" else [v * 100] if u == "pct" else []
    if c.kind == "multiple":
        return [v] if u == "x" else []
    if c.kind == "points":
        return [v] if u in ("points", "index", "pct") else []
    # plain number: same-unit evidence, or a percent written without the % sign
    if u == "fraction":
        return [v * 100, v]
    return [v]


def _close(claim: float, ev: float, decimals: int, scale: float) -> bool:
    tol = max(0.5 * 10 ** (-decimals) * scale, abs(ev) * 0.005)
    return abs(claim - ev) <= tol + 1e-12


def supported(c: NumericClaim, evidence: Sequence[Evidence], ticker: str) -> tuple[bool, str]:
    if c.entity is not None and c.entity != ticker:
        pool = [e for e in evidence if e.ticker == c.entity]
        if not pool:
            return False, f"다른 종목({c.entity})에 대한 수치 — 근거 없음"
    else:
        pool = [e for e in evidence if e.ticker in (ticker, None)]
    if c.claimed_class != "other":
        if c.kind in ("percent", "bp") and c.claimed_class in ("eps", "revenue"):
            # "EPS rose 12%" is a change of EPS: only EPS growth / revision / surprise evidence can support it
            stem = "eps" if c.claimed_class == "eps" else "rev"
            pool = [e for e in pool if stem in e.metric.split(".")[-1] or (stem == "eps" and e.metric == "earnings.last")]
        else:
            allowed = _COMPATIBLE.get(c.claimed_class, frozenset({c.claimed_class}))
            pool = [e for e in pool if metric_class(e.metric) in allowed]
        if not pool:
            return False, f"'{c.claimed_class}' 지표 근거 없음"
    if c.basis is not None:
        # "forward EPS $5" cannot be supported by trailing EPS = 5, and "reported revenue" not by a consensus
        same = [e for e in pool if e.basis in (c.basis, None)]
        if not same:
            return False, f"{'예상치' if c.basis == 'FORECAST' else '실적치'} 근거 없음 (실적/예상 구분 불일치)"
        pool = same
    unit_ok = False
    for e in pool:
        for ev in _candidates(c, e):
            unit_ok = True
            mag_match = _close(abs(c.value), abs(ev), c.decimals, c.scale)
            if not mag_match:
                continue
            if c.explicit_sign and (c.value < 0) != (ev < 0) and abs(ev) > 1e-12:
                continue  # explicit sign disagrees with the evidence
            if not c.explicit_sign and c.direction != 0 and ev != 0 and (ev < 0) != (c.direction < 0) and metric_class(e.metric) in _CHANGE_CLASSES:
                continue  # "EPS declined 12%" vs evidence +12%
            return True, e.evidence_id
    return False, ("단위가 맞는 근거 없음" if not unit_ok else "값·부호가 근거와 불일치")


_CHANGE_CLASSES = frozenset({"growth", "revision", "surprise", "guidance", "relative_strength", "issue_score"})


def is_trivial(c: NumericClaim) -> bool:
    """Years, and small counts/windows without any unit, are not data claims."""
    if c.kind != "plain" or c.explicit_sign:
        return False
    v = abs(c.value)
    if v.is_integer() and 1990 <= v <= 2100 and c.decimals == 0 and c.scale == 1.0:
        return True  # calendar / fiscal years
    return c.claimed_class == "other" and v.is_integer() and int(v) in SAFE_SMALL_INTS


def unverified(text: str, evidence: Sequence[Evidence], ticker: str, known_tickers: Iterable[str] = ()) -> list[str]:
    bad: list[str] = []
    for c in extract_claims(text, known_tickers):
        if is_trivial(c):
            continue
        ok, why = supported(c, evidence, ticker)
        if not ok:
            bad.append(f"{c.raw} ({why})")
    return bad
