"""Per-data-type freshness policy.

Every input type has its own clock. Freshness is measured from the moment a value became *effective*
(bar session, fiscal period end, estimate snapshot, short-interest settlement date, news fetch) to the
analysis ``as_of`` — never by counting rows. Twelve quarters that ended three years ago are STALE, not
FRESH, and a year of daily bars whose last session is a month old is STALE as well.

Qualities:
- FRESH    — as recent as the source can normally be.
- DELAYED  — older than normal but still usable; confidence is reduced by the completeness term.
- STALE    — too old to support a decision; a stale *core* input vetoes any recommendation.
- MISSING  — not available (or not enough history to compute the metric).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Mapping

from marketlens.domain.enums import DataQuality
from marketlens.domain.market_calendar import last_completed_session, market_active_between, to_ny, trading_days_between

SESSIONS = "sessions"
DAYS = "days"


@dataclass(frozen=True, slots=True)
class FreshnessRule:
    data_type: str
    unit: str  # "sessions" (NYSE trading sessions) or "days" (calendar days)
    fresh_max: float
    usable_max: float
    label_ko: str
    basis_ko: str  # what the age is measured from


# Defaults. Rationale (SEC deadlines): a 10-Q is due 40–45 days after quarter end and a 10-K 60–90 days
# after fiscal year end, so right before a 10-K is filed the newest public quarter (Q3) is ~181 days old.
DEFAULT_RULES: Mapping[str, FreshnessRule] = {
    "price_history": FreshnessRule("price_history", SESSIONS, 1, 3, "일봉 가격 이력", "마지막 일봉 거래일"),
    "fundamentals": FreshnessRule("fundamentals", DAYS, 190, 280, "재무제표(분기)", "최신 공시 분기의 결산일"),
    "earnings": FreshnessRule("earnings", DAYS, 110, 200, "실적 발표 이력", "최근 실적 발표일"),
    "analyst": FreshnessRule("analyst", DAYS, 7, 30, "애널리스트 추정치", "추정치 스냅샷 날짜"),
    "options": FreshnessRule("options", SESSIONS, 1, 2, "옵션 데이터", "옵션 스냅샷 거래일"),
    "short_interest": FreshnessRule("short_interest", DAYS, 30, 45, "공매도 잔고(FINRA)", "결제 기준일"),
    "news": FreshnessRule("news", DAYS, 1, 3, "뉴스", "뉴스 수집 시각"),
}


@dataclass(frozen=True, slots=True)
class FreshnessCheck:
    data_type: str
    quality: DataQuality
    effective: str | None  # ISO date/datetime the latest value refers to
    published: str | None  # ISO date/datetime the value became public (filing / release), if known
    age: float | None
    unit: str
    fresh_max: float
    usable_max: float
    reason_ko: str

    @property
    def usable(self) -> bool:
        return self.quality in (DataQuality.FRESH, DataQuality.DELAYED)


def _iso(x: date | datetime | None) -> str | None:
    return None if x is None else x.isoformat()


def _classify(age: float, rule: FreshnessRule) -> DataQuality:
    if age <= rule.fresh_max:
        return DataQuality.FRESH
    if age <= rule.usable_max:
        return DataQuality.DELAYED
    return DataQuality.STALE


def _reason(rule: FreshnessRule, q: DataQuality, age: float) -> str:
    unit = "거래일" if rule.unit == SESSIONS else "일"
    base = f"{rule.label_ko}: {rule.basis_ko} 기준 {age:g}{unit} 경과"
    if q == DataQuality.FRESH:
        return f"{base} (최신, 기준 {rule.fresh_max:g}{unit} 이내)"
    if q == DataQuality.DELAYED:
        return f"{base} (지연, 사용 가능 한도 {rule.usable_max:g}{unit} 이내 — 신뢰도 하향)"
    return f"{base} (오래됨, 한도 {rule.usable_max:g}{unit} 초과 — 판단에 사용 불가)"


def missing(data_type: str, reason_ko: str, rules: Mapping[str, FreshnessRule] = DEFAULT_RULES) -> FreshnessCheck:
    r = rules.get(data_type) or FreshnessRule(data_type, DAYS, 0, 0, data_type, "-")
    return FreshnessCheck(data_type, DataQuality.MISSING, None, None, None, r.unit, r.fresh_max, r.usable_max, reason_ko)


def check_age(
    data_type: str,
    effective: date | datetime | None,
    as_of: datetime,
    published: date | datetime | None = None,
    rules: Mapping[str, FreshnessRule] = DEFAULT_RULES,
) -> FreshnessCheck:
    """Classify one input. ``effective`` is when the newest value applies (bar day, period end, …)."""
    rule = rules[data_type]
    if effective is None:
        return missing(data_type, f"{rule.label_ko}: 데이터 없음", rules)
    eff_day = to_ny(effective).date() if isinstance(effective, datetime) else effective
    ref_day = to_ny(as_of).date()
    if eff_day > ref_day:
        # a value dated after the analysis time is corrupt or look-ahead data
        return FreshnessCheck(data_type, DataQuality.CONFLICTING, _iso(effective), _iso(published), None, rule.unit, rule.fresh_max, rule.usable_max, f"{rule.label_ko}: 분석 시점({ref_day.isoformat()}) 이후 날짜의 데이터 — 사용 불가")
    if rule.unit == SESSIONS:
        age = float(trading_days_between(eff_day, last_completed_session(as_of)))
    else:
        age = float((ref_day - eff_day).days)
    q = _classify(age, rule)
    return FreshnessCheck(data_type, q, _iso(effective), _iso(published), age, rule.unit, rule.fresh_max, rule.usable_max, _reason(rule, q, age))


def rules_from_config(raw: Mapping[str, Mapping[str, float]] | None) -> Mapping[str, FreshnessRule]:
    """Override thresholds from ``[freshness.rules.<type>]`` in scoring_model.toml (labels stay)."""
    if not raw:
        return DEFAULT_RULES
    out = dict(DEFAULT_RULES)
    for k, v in raw.items():
        if k in out:
            base = out[k]
            out[k] = FreshnessRule(base.data_type, base.unit, float(v.get("fresh_max", base.fresh_max)), float(v.get("usable_max", base.usable_max)), base.label_ko, base.basis_ko)
    return out


# --- stored recommendations: "was FRESH when made" vs "is still current now" -------------------------


@dataclass(frozen=True, slots=True)
class RecommendationFreshness:
    status: str  # CURRENT | NEEDS_REVALIDATION | PLAN_INVALIDATED | AGING | EXPIRED
    at_creation: str  # data quality recorded when the recommendation was made
    sessions_since: int
    reason_ko: str
    problems: tuple[str, ...] = field(default_factory=tuple)
    revalidated_price: float | None = None

    @property
    def actionable(self) -> bool:
        return self.status == "CURRENT"


@dataclass(frozen=True, slots=True)
class PlanCheck:
    """The price plan of a stored recommendation, re-checked against a *current* quote."""

    rec_price: float | None
    max_buy: float | None
    stop: float | None
    target1: float | None
    min_rr: float = 2.0
    bullish: bool = False


@dataclass(frozen=True, slots=True)
class RevalidationPolicy:
    max_intraday_age: timedelta = timedelta(minutes=60)  # unused since decision-3.2.0 (kept for config compatibility)
    max_quote_age: timedelta = timedelta(minutes=20)  # a quote older than this cannot re-validate anything
    max_move: float = 0.03  # a price move larger than this since the analysis → the analysis is out of date


STATUS_KO = {
    "CURRENT": "현재 유효", "NEEDS_REVALIDATION": "현재가 재확인 필요", "PLAN_INVALIDATED": "가격 조건 이탈",
    "AGING": "오래됨", "EXPIRED": "만료",
}


def _revalidate(plan: PlanCheck, price: float, pol: RevalidationPolicy) -> list[str]:
    problems: list[str] = []
    if plan.rec_price and abs(price / plan.rec_price - 1) > pol.max_move:
        problems.append(f"분석 이후 가격이 {price / plan.rec_price - 1:+.1%} 움직임(허용 ±{pol.max_move:.0%})")
    if plan.bullish:
        if plan.max_buy is not None and price > plan.max_buy:
            problems.append(f"현재가 ${price:,.2f} > 최대 매수가 ${plan.max_buy:,.2f}")
        if plan.stop is not None and price <= plan.stop:
            problems.append(f"현재가 ${price:,.2f} ≤ 손절 기준 ${plan.stop:,.2f}")
        elif plan.stop is not None and plan.target1 is not None and price > plan.stop:
            rr = (plan.target1 - price) / (price - plan.stop)
            if rr < plan.min_rr - 1e-9:
                problems.append(f"현재가 기준 손익비 {rr:.2f} < {plan.min_rr:g}")
    return problems


def recommendation_freshness(
    as_of: datetime,
    recorded_quality: str,
    now: datetime,
    current_max_sessions: int = 0,
    aging_max_sessions: int = 2,
    *,
    plan: PlanCheck | None = None,
    quote_price: float | None = None,
    quote_ts: datetime | None = None,
    new_major_events: tuple[str, ...] = (),
    policy: RevalidationPolicy | None = None,
) -> RecommendationFreshness:
    """A recommendation is a statement about prices *at* ``as_of``.

    - After a new regular session has closed it is AGING, and after ``aging_max_sessions`` EXPIRED.
    - Within the same session it stays CURRENT without a new quote only while its own analysis price is as
      fresh as a quote may be (``max_quote_age``) or while nothing could have moved it (market closed since).
      Otherwise it must be re-checked against a *current* quote: max buy, stop, reward/risk and the size of
      the move since the analysis. Without a fresh quote it is NEEDS_REVALIDATION; if the quote breaks the
      plan it is PLAN_INVALIDATED.
    - A major new issue since the analysis always requires re-analysis.
    - Whenever a fresh quote newer than the analysis is available, the plan is checked against it first,
      whatever the age: a young recommendation whose price already broke the stop / max buy / R:R is
      PLAN_INVALIDATED, not CURRENT.
    Only CURRENT may be shown as actionable.
    """
    pol = policy or RevalidationPolicy()
    n = trading_days_between(last_completed_session(as_of), last_completed_session(now))
    rec_ok = recorded_quality in (DataQuality.FRESH.value, DataQuality.DELAYED.value)
    when = "추천 당시 데이터 " + ("최신" if recorded_quality == DataQuality.FRESH.value else "지연" if recorded_quality == DataQuality.DELAYED.value else "불충분/오래됨")
    if n > aging_max_sessions:
        return RecommendationFreshness("EXPIRED", recorded_quality, n, f"{when}; 이후 {n}거래일 경과 — 만료된 추천(현재 판단 근거로 사용 금지)")
    if n > current_max_sessions:
        return RecommendationFreshness("AGING", recorded_quality, n, f"{when}; 이후 {n}거래일 경과 — 현재 가격 기준으로는 재분석 필요")
    if not rec_ok:
        return RecommendationFreshness("EXPIRED", recorded_quality, n, f"{when}; 데이터가 불충분해 실행 근거가 될 수 없음")
    if new_major_events:
        return RecommendationFreshness("NEEDS_REVALIDATION", recorded_quality, n, f"{when}; 분석 이후 중요한 새 이슈 발생({', '.join(new_major_events[:3])}) — 재분석 필요")
    age = now - as_of
    minutes = int(age.total_seconds() // 60)
    quote_ok = quote_price is not None and quote_ts is not None and timedelta(0) <= now - quote_ts <= pol.max_quote_age
    # A fresh quote newer than the analysis is always checked against the plan — even for a young
    # recommendation: ten minutes are enough for a price to fall through the stop.
    if plan is not None and quote_ok and quote_ts is not None and quote_ts > as_of:
        problems = _revalidate(plan, quote_price, pol)  # type: ignore[arg-type]
        if problems:
            return RecommendationFreshness("PLAN_INVALIDATED", recorded_quality, n, f"분석 후 {minutes}분 경과, 현재가 기준 조건 이탈: " + "; ".join(problems), problems=tuple(problems))
    # the analysis price is itself a quote taken at ``as_of``: it proves the plan only as long as any quote would
    # (``max_quote_age``). After that, while the market has traded, a newer quote is required — listings that
    # show only cached quotes must say "check the price" instead of calling an unchecked BUY actionable.
    if not market_active_between(as_of, now) or age <= pol.max_quote_age:
        return RecommendationFreshness("CURRENT", recorded_quality, n, f"{when}; 분석 {int(age.total_seconds() // 60)}분 경과, 이후 가격 변동 가능 시간 없음" if age > pol.max_intraday_age else f"{when}; 분석 {int(age.total_seconds() // 60)}분 경과")
    if plan is None or not quote_ok:
        return RecommendationFreshness("NEEDS_REVALIDATION", recorded_quality, n, f"{when}; 장중 분석 후 {minutes}분 경과 — 가격이 바뀌었을 수 있어 현재가 확인 전에는 실행 불가")
    problems = _revalidate(plan, quote_price, pol)  # type: ignore[arg-type]
    if problems:
        return RecommendationFreshness("PLAN_INVALIDATED", recorded_quality, n, f"분석 후 {minutes}분 경과, 현재가 기준 조건 이탈: " + "; ".join(problems), problems=tuple(problems))
    return RecommendationFreshness("CURRENT", recorded_quality, n, f"{when}; 분석 후 {minutes}분 경과, 현재가 ${quote_price:,.2f}로 가격 조건 재확인 통과", revalidated_price=quote_price)
