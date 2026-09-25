"""Pure per-ticker analysis pipeline.

``run_analysis(inputs, cfg)`` is deterministic and side-effect free: no network, no database, no LLM.
``AnalysisInputs`` is the point-in-time snapshot persisted with every recommendation so that the
decision can be replayed exactly later (see application/replay.py).

Point-in-time rules applied here (defence in depth — providers and the store apply them too):
- daily bars after the last *completed* session at ``as_of`` are dropped (no partial/in-progress bars);
- SEC filings are visible from the filing date, and only after 17:30 ET on that date if ``as_of`` is on
  the filing date itself (EDGAR accepts filings until 17:30 ET); fields published later are masked;
- earnings reports dated on the analysis day are visible only after that day's regular close;
- news/issues published after ``as_of`` are ignored.
Every input also passes a per-type freshness policy (domain/freshness.py); stale core inputs veto.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Mapping

from marketlens.application.codec import encode
from marketlens.application.evidence import Evidence, EvidenceBuilder
from marketlens.config import ModelConfig
from marketlens.domain.catalysts import CatalystEvent, CatalystType, EventRisk, assess_event_risk
from marketlens.domain.decision import Decision, DecisionContext, decide
from marketlens.domain.earnings import RESULT_KO, AnalystSnapshot, EarningsAssessment, EarningsReport, RevisionAssessment, assess_earnings, assess_revisions
from marketlens.domain.entry import EntryPlan, ThesisCondition, build_entry_plan, evaluate_thesis
from marketlens.domain.enums import BULLISH_ACTIONS, Action, DataMode, DataQuality, Horizon
from marketlens.domain.exposure_graph import Edge, ExposureGraph, Node
from marketlens.domain.facts import DataQualityReport, Fact, build_quality_report
from marketlens.domain.freshness import FreshnessCheck, check_age, missing as fresh_missing, rules_from_config
from marketlens.domain.fundamentals import FundamentalMetrics, QuarterlyFinancials, as_of, compute_metrics
from marketlens.domain.indicators import TechnicalSnapshot, aligned_closes, compute_technicals
from marketlens.domain.issues import CompanyIssueImpact, Issue, aggregate_issue_score, compute_issue_impacts
from marketlens.domain.macro import US10Y, MacroExposure, MacroImpact, MacroSnapshot, RegimeReading, detect_regimes, factor_moves, macro_impact, primary_regime
from marketlens.domain.market import Bar, Quote, Security, assess_price_quality
from marketlens.domain.market_calendar import NY, last_completed_session, session_close_utc, to_ny, trading_days_between
from marketlens.domain.options import OptionsMetrics, OptionsSnapshot, OwnershipSnapshot, compute_options_metrics
from marketlens.domain.portfolio import PortfolioReview
from marketlens.domain.priced_in import PricedInEstimate, PricedInInputs, estimate_priced_in
from marketlens.domain.scenario import Scenario, build_scenarios
from marketlens.domain.scoring import ScoreCard, ScoringInputs, score
from marketlens.domain.sector_models import RuleScore, score_rules, select_sector_model
from marketlens.domain.valuation import RelativeValuation, ValuationMultiples, compute_multiples, fundamental_features, relative_valuation
from marketlens.domain.what_changed import AnalysisDigest, ChangeItem, diff, material_reasons

CORE_FIELDS = ("price", "price_history", "fundamentals")
MIN_BARS = 60
MIN_QUARTERS = 4
EDGAR_CUTOFF = time(17, 30)  # EDGAR filings accepted after 17:30 ET receive the next business day's date
UNKNOWN_SECTORS = ("", "Unknown", "N/A")


@dataclass(frozen=True)
class AnalysisInputs:
    ticker: str
    as_of: datetime
    mode: DataMode
    security: Security
    quote: Quote | None
    bars: tuple[Bar, ...]
    benchmark_bars: tuple[Bar, ...]
    quarters: tuple[QuarterlyFinancials, ...]
    extras: Mapping[str, float]
    analyst: AnalystSnapshot | None
    earnings: tuple[EarningsReport, ...]
    valuation_history: tuple[float, ...]
    peer_multiples: tuple[float, ...]
    macro: MacroSnapshot | None
    macro_exposure: MacroExposure
    issues: tuple[Issue, ...]
    graph_nodes: tuple[Node, ...]
    graph_edges: tuple[Edge, ...]
    events: tuple[CatalystEvent, ...]
    options: OptionsSnapshot | None
    ownership: OwnershipSnapshot | None
    held: bool
    portfolio_review: PortfolioReview | None
    previous: AnalysisDigest | None
    previous_action: Action | None
    thesis_conditions: tuple[ThesisCondition, ...]
    thesis_flags: tuple[str, ...]
    provider_conflicts: tuple[str, ...]
    source_map: Mapping[str, str] = field(default_factory=dict)  # field -> provider name
    missing_reasons: Mapping[str, str] = field(default_factory=dict)
    insider: OwnershipSnapshot | None = None  # SEC Form 4 aggregate (separate from short interest)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(_canonical(encode(self)), sort_keys=True).encode()).hexdigest()


def _canonical(x: object) -> object:
    """Normalise numerics so 1 and 1.0 hash identically (JSON round-trips must not change fingerprints)."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        f = float(x)
        return int(f) if f.is_integer() else repr(f)
    if isinstance(x, dict):
        return {k: _canonical(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_canonical(v) for v in x]
    return x


@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    as_of: datetime
    mode: DataMode
    security: Security
    price: float | None
    price_quality: DataQuality
    price_timestamp: datetime | None
    session: str | None
    price_source: str | None
    technicals: TechnicalSnapshot | None
    metrics: FundamentalMetrics | None
    features: Mapping[str, float | None]
    sector_model_id: str
    sector_model_name: str
    sector_model_reason: str
    fundamental_rules: RuleScore | None
    valuation_rules: RuleScore | None
    multiples: ValuationMultiples | None
    relative_valuation: RelativeValuation | None
    earnings: EarningsAssessment | None
    revisions: RevisionAssessment | None
    analyst: AnalystSnapshot | None
    regimes: tuple[RegimeReading, ...]
    primary_regime: str
    macro_impact: MacroImpact | None
    issue_impacts: tuple[CompanyIssueImpact, ...]
    priced_in: Mapping[str, PricedInEstimate]
    issue_score_swing: float | None
    horizon_view: Mapping[str, float | None]
    event_risk: EventRisk
    upcoming_events: tuple[CatalystEvent, ...]
    options: OptionsMetrics | None
    ownership: OwnershipSnapshot | None
    entry: EntryPlan | None
    thesis_conditions: tuple[ThesisCondition, ...]
    thesis_invalidated: bool
    thesis_breaches: tuple[str, ...]
    data_quality: DataQualityReport
    scorecard: ScoreCard
    decision: Decision
    changes: tuple[ChangeItem, ...]
    digest: AnalysisDigest
    scenarios: tuple[Scenario, ...]
    evidence: tuple[Evidence, ...]
    reason_evidence: Mapping[str, tuple[str, ...]]  # reason text -> evidence ids
    beta: float | None
    portfolio_review: PortfolioReview | None
    versions: Mapping[str, str]
    input_fingerprint: str
    sector_known: bool = True
    short_interest_pct: float | None = None  # short interest / shares outstanding (FINRA ÷ SEC cover page)
    valuation_price_basis: str = "현재가"


# ------------------------------------------------------------------------------------------------ helpers


def _beta(bars: tuple[Bar, ...], bench: tuple[Bar, ...], n: int = 120) -> float | None:
    """OLS beta on *date-aligned* daily returns (inner join on trading date, never by position)."""
    a_all, b_all = aligned_closes(bars, bench)
    m = min(len(a_all), n + 1)
    if m < 40:
        return None
    a, b = a_all[-m:], b_all[-m:]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, m)]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, m)]
    mb = sum(rb) / len(rb)
    ma = sum(ra) / len(ra)
    var = sum((x - mb) ** 2 for x in rb)
    if var <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / var


def filing_visibility_day(ts: datetime) -> date:
    """Latest SEC filing date whose documents are certainly public at ``ts``."""
    local = to_ny(ts)
    return local.date() if local.time() >= EDGAR_CUTOFF else local.date() - timedelta(days=1)


def earnings_visible(r: EarningsReport, ts: datetime) -> bool:
    today = to_ny(ts).date()
    if r.report_date < today:
        return True
    # without an intraday release time, a report dated today is only trusted after the regular close
    return r.report_date == today and ts >= session_close_utc(today)


def _priced_in_for(issue: Issue, direction: int, bars: tuple[Bar, ...], analyst: AnalystSnapshot | None, opt: OptionsMetrics | None, as_of: datetime) -> PricedInEstimate:
    ev_day = to_ny(issue.publish_time).date()
    before = [b for b in bars if b.day < ev_day]
    after = [b for b in bars if b.day >= ev_day]
    pre_ret = None
    vol = None
    if len(before) >= 21:
        pre_ret = before[-1].close / before[-11].close - 1
        rets = [before[i].close / before[i - 1].close - 1 for i in range(len(before) - 20, len(before))]
        mu = sum(rets) / len(rets)
        vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    abn = None
    if len(before) >= 30 and after:
        base = sum(b.volume for b in before[-30:-10]) / 20
        recent = [b.volume for b in (before[-10:] + after)[-10:]]
        abn = (sum(recent) / len(recent)) / base if base > 0 else None
    gap = None
    if after and before:
        gap = after[0].open / before[-1].close - 1
    return estimate_priced_in(
        PricedInInputs(
            event_direction=direction,
            pre_event_return=pre_ret,
            daily_volatility=vol,
            abnormal_volume_ratio=abn,
            gap_reaction=gap,
            implied_move=opt.expected_move if opt else None,
            iv_rank=opt.iv_rank if opt else None,
            revision_already_in_direction=analyst.eps_revision_30d if analyst else None,
            news_repetition=int(round(issue.market_awareness * 20)),
            days_since_first_report=trading_days_between(ev_day, to_ny(as_of).date()),
        )
    )


def _catalyst_bias(events: list[CatalystEvent], ticker: str, rev: RevisionAssessment | None, er: EarningsAssessment | None, today: date) -> float | None:
    upcoming = [e for e in events if ticker in e.affected and e.event_type == CatalystType.EARNINGS and 0 <= e.days_until(today) <= 30]
    if not upcoming:
        return 0.0 if events else None
    if rev is None:
        return 0.0
    bias = 0.5 * rev.eps_direction
    if er is not None and er.expectation_bar.value == "HIGH":
        bias -= 0.25
    return max(-1.0, min(1.0, bias))


_PRICE_REASON = {
    DataQuality.FRESH: "현재가: 최신 시세",
    DataQuality.DELAYED: "현재가: 지연 시세(실시간 아님) — 사용 가능하나 신뢰도 하향",
    DataQuality.STALE: "현재가: 오래된 시세 — 현재가로 사용할 수 없음",
    DataQuality.CONFLICTING: "현재가: 분석 시점 이후 시각의 시세 — 데이터 오류",
    DataQuality.MISSING: "현재가: 시세 없음",
}


def _price_check(quote: Quote | None, pq: DataQuality, as_of_ts: datetime, cfg: ModelConfig) -> FreshnessCheck:
    if quote is None:
        return fresh_missing("price", _PRICE_REASON[DataQuality.MISSING])
    age = round((as_of_ts - quote.timestamp).total_seconds() / 60, 1)
    reason = _PRICE_REASON[pq] + ("" if quote.is_realtime else " (제공자가 실시간이 아니라고 표시)")
    return FreshnessCheck("price", pq, quote.timestamp.isoformat(), None, age, "minutes", cfg.freshness.realtime_max_age.total_seconds() / 60, cfg.freshness.delayed_max_age.total_seconds() / 60, reason)


def _macro_check(macro: MacroSnapshot | None, missing_reason: str | None) -> FreshnessCheck:
    if macro is None or not macro.series:
        return fresh_missing("macro", f"거시 데이터: {missing_reason or '없음'}")
    ok = [sid for sid, s in macro.series.items() if s.latest.value is not None and s.latest.quality in (DataQuality.FRESH, DataQuality.DELAYED)]
    bad = sorted(set(macro.series) - set(ok))
    newest = max((s.latest.source_ts for s in macro.series.values() if s.latest.source_ts is not None), default=None)
    if not ok:
        return FreshnessCheck("macro", DataQuality.STALE, newest.isoformat() if newest else None, None, None, "series", 0, 0, "거시 데이터: 사용 가능한 최신 시계열 없음")
    q = DataQuality.FRESH if not bad else DataQuality.DELAYED
    txt = f"거시 데이터: {len(ok)}/{len(macro.series)}개 시계열 사용 가능" + (f" (오래됨/누락: {', '.join(bad[:6])})" if bad else "")
    return FreshnessCheck("macro", q, newest.isoformat() if newest else None, None, float(len(bad)), "series", 0, float(len(macro.series)), txt)


def _news_check(missing_reason: str | None, as_of_ts: datetime) -> FreshnessCheck:
    if missing_reason is not None:
        # a failed news request is NOT the same as "no news": the issue component becomes unavailable
        return fresh_missing("news", f"뉴스: 수집 실패 — {missing_reason} (뉴스 없음과 다름)")
    return FreshnessCheck("news", DataQuality.FRESH, as_of_ts.isoformat(), None, 0.0, "days", 1, 3, "뉴스: 분석 시점에 수집 성공 (관련 기사가 0건일 수 있음)")


def _check_or_missing(name: str, present: bool, eff: date | datetime | None, as_of_ts: datetime, rules: Mapping, label: str, published: date | datetime | None = None, missing_reason: str | None = None) -> FreshnessCheck:
    if not present:
        return fresh_missing(name, f"{label}: {missing_reason or '데이터 없음'}", rules)
    if eff is None:
        return FreshnessCheck(name, DataQuality.STALE, None, None, None, rules[name].unit, rules[name].fresh_max, rules[name].usable_max, f"{label}: 기준 시점 정보가 없어 신선도를 확인할 수 없음 — 사용하지 않음")
    return check_age(name, eff, as_of_ts, published, rules)


def _fact(check: FreshnessCheck, value: float | None, source: str) -> Fact:
    def ts(x: str | None) -> datetime | None:
        if x is None:
            return None
        if "T" in x:
            return datetime.fromisoformat(x)
        return datetime.combine(date.fromisoformat(x), time(0), tzinfo=NY)

    return Fact(value, source, source_ts=ts(check.effective), quality=check.quality, note=check.reason_ko, published_ts=ts(check.published))


# ------------------------------------------------------------------------------------------------ main


def run_analysis(inp: AnalysisInputs, cfg: ModelConfig) -> AnalysisResult:
    t = inp.ticker
    eb = EvidenceBuilder(t, inp.as_of)
    src = inp.source_map
    miss = inp.missing_reasons
    rules = rules_from_config(cfg.raw.get("freshness", {}).get("rules") if isinstance(cfg.raw.get("freshness"), dict) else None)
    ny_today = to_ny(inp.as_of).date()
    last_session = last_completed_session(inp.as_of)

    # ---------------------------------------------------------------- price & technicals
    pq = assess_price_quality(inp.quote, inp.as_of, cfg.freshness)
    price = inp.quote.price if inp.quote is not None and pq in (DataQuality.FRESH, DataQuality.DELAYED) else None
    if inp.quote is not None:
        eb.add("price.current", "price", f"현재가 ({inp.quote.session.value})", inp.quote.price, inp.quote.source, inp.quote.timestamp, pq.value)
    bars = tuple(b for b in inp.bars if b.day <= last_session)  # never a partial / in-progress session
    bench = tuple(b for b in inp.benchmark_bars if b.day <= last_session)
    bars_check = _check_or_missing("price_history", bool(bars), bars[-1].day if bars else None, inp.as_of, rules, "일봉 가격 이력", missing_reason=miss.get("bars"))
    if bars and len(bars) < MIN_BARS:
        bars_check = replace(bars_check, quality=DataQuality.MISSING, reason_ko=f"일봉 가격 이력: {len(bars)}개로 최소 {MIN_BARS}개 미만 — 기술 지표 계산 불가")
    tech = compute_technicals(list(bars), list(bench) or None) if len(bars) >= 30 else None
    if tech is not None:
        for k in ("sma20", "sma50", "sma200", "rsi14", "atr14", "rs_6m", "volatility_20d", "high_52w", "low_52w", "avg_dollar_volume_20d", "anchored_vwap", "volume_ratio"):
            eb.add(f"tech.{k}", "technical", k, getattr(tech, k), src.get("bars", "calc"), quality=bars_check.quality.value)
    beta = _beta(bars, bench)

    # ---------------------------------------------------------------- fundamentals (point-in-time)
    pit_quarters = as_of(list(inp.quarters), filing_visibility_day(inp.as_of))
    latest_q = pit_quarters[-1] if pit_quarters else None
    fund_check = _check_or_missing("fundamentals", bool(pit_quarters), latest_q.period_end if latest_q else None, inp.as_of, rules, "재무제표(분기)", latest_q.filed_date if latest_q else None, miss.get("fundamentals"))
    if pit_quarters and len(pit_quarters) < MIN_QUARTERS:
        fund_check = replace(fund_check, quality=DataQuality.MISSING, reason_ko=f"재무제표(분기): 분석 시점에 공개된 분기 {len(pit_quarters)}개로 최소 {MIN_QUARTERS}개 미만 — TTM 계산 불가")
    metrics = compute_metrics(pit_quarters) if pit_quarters else None
    features: dict[str, float | None] = fundamental_features(metrics, dict(inp.extras)) if metrics else dict(inp.extras)
    for k, v in features.items():
        if v is not None:
            eb.add(f"fund.{k}", "fundamental", k, v, src.get("fundamentals", "calc"), quality=fund_check.quality.value, period="TTM" if k.endswith("_ttm") else (latest_q.fiscal_label if latest_q else None))

    # ---------------------------------------------------------------- earnings & revisions (only if fresh enough)
    er_hist = [r for r in inp.earnings if earnings_visible(r, inp.as_of)]
    last_er = max(er_hist, key=lambda r: r.report_date) if er_hist else None
    er_check = _check_or_missing("earnings", bool(er_hist), last_er.report_date if last_er else None, inp.as_of, rules, "실적 발표 이력", missing_reason=miss.get("earnings"))
    earnings = assess_earnings(er_hist) if er_check.usable else None
    an_check = _check_or_missing("analyst", inp.analyst is not None, inp.analyst.as_of if inp.analyst else None, inp.as_of, rules, "애널리스트 추정치", missing_reason=miss.get("analyst"))
    analyst = inp.analyst if an_check.usable else None
    revisions = assess_revisions(analyst)
    if analyst is not None:
        for k in ("eps_revision_7d", "eps_revision_30d", "eps_revision_90d", "revenue_revision_30d", "revenue_revision_90d", "analyst_count", "estimate_dispersion", "forward_eps", "forward_revenue", "target_price_consensus"):
            eb.add(f"analyst.{k}", "analyst", k, getattr(analyst, k), analyst.source, quality=an_check.quality.value)
    if earnings is not None and last_er is not None:
        eb.add("earnings.last", "earnings", f"{last_er.fiscal_label} 실적: {RESULT_KO.get(earnings.result_quality, earnings.result_quality.value)}", earnings.eps_surprise, last_er.source, period=last_er.fiscal_label)
        eb.add("earnings.revenue_surprise", "earnings", "매출 서프라이즈", earnings.revenue_surprise, last_er.source, period=last_er.fiscal_label)
        eb.add("earnings.guide_rev_vs_cons", "earnings", "다음 분기 매출 가이던스 vs 컨센서스", earnings.guide_rev_vs_cons, last_er.source, period=last_er.fiscal_label)
        eb.add("earnings.expectation_bar", "earnings", "시장 기대 수준", earnings.expectation_bar.value, "calc")

    # ---------------------------------------------------------------- sector model & valuation
    sector_known = inp.security.sector not in UNKNOWN_SECTORS
    model, reason = select_sector_model(inp.security, cfg.sector_models)
    if not sector_known:
        reason = f"업종 분류 정보 없음 → 일반(generic) 모델 적용, 신뢰도 하향 ({reason})"
    fund_rules = score_rules(model.fundamental_rules, features, model.min_coverage) if features else None
    # valuation uses the current price when usable, else the last completed session's close (labelled)
    if price is not None:
        val_price, val_basis = price, "현재가"
    elif bars and bars_check.usable:
        val_price, val_basis = bars[-1].close, f"직전 종가({bars[-1].day.isoformat()})"
    else:
        val_price, val_basis = None, "가격 없음"
    multiples = compute_multiples(val_price, metrics, analyst, inp.extras) if metrics is not None else None
    val_inputs: dict[str, float | None] = multiples.as_dict() if multiples else {}
    val_rules = score_rules(model.valuation_rules, val_inputs, model.min_coverage) if multiples else None
    us10y_pct = inp.macro.value(US10Y) if inp.macro else None
    rel = relative_valuation(multiples, model.primary_multiple, inp.valuation_history, inp.peer_multiples, us10y_pct / 100 if us10y_pct is not None else None) if multiples else None
    if multiples is not None:
        for k, v in multiples.as_dict().items():
            if v is not None:
                eb.add(f"val.{k}", "valuation", k, v, "calc")
    if rel is not None:
        eb.add("val.history", "valuation", f"{rel.primary_multiple} 자기 과거 대비 백분위", rel.history_percentile, src.get("valuation_history", "calc"))
        eb.add("val.peers", "valuation", f"{rel.primary_multiple} 동종업계 대비 프리미엄", rel.premium_to_peers, "calc")
        eb.add("val.rate_spread", "valuation", "선행 이익수익률 − 미 10년물", rel.equity_risk_spread, "calc")
        eb.add("val.peg", "valuation", "PEG", rel.growth_adjusted, "calc")

    # ---------------------------------------------------------------- macro
    macro_check = _macro_check(inp.macro, miss.get("macro"))
    regimes = tuple(detect_regimes(inp.macro)) if inp.macro else ()
    prim = primary_regime(list(regimes)) if regimes else "Unknown"
    mimpact = macro_impact(inp.macro_exposure, factor_moves(inp.macro)) if inp.macro else None
    if inp.macro is not None:
        for sid, s in inp.macro.series.items():
            eb.add(f"macro.{sid}", "macro", sid, s.latest.value, s.latest.source, s.latest.source_ts, s.latest.quality.value, ticker_scoped=False)
        for f, c, expl in (mimpact.contributions if mimpact else ()):
            eb.add(f"macro.{f}", "macro", expl, c, "calc")
        eb.add("macro.regime", "macro", f"주요 시장 국면 {prim}", prim, "calc", ticker_scoped=False)

    # ---------------------------------------------------------------- options / ownership (auxiliary)
    opt_check = _check_or_missing("options", inp.options is not None, inp.options.as_of if inp.options else None, inp.as_of, rules, "옵션 데이터", missing_reason=miss.get("options"))
    opt = compute_options_metrics(inp.options) if opt_check.usable else None
    own = inp.ownership
    si_check = _check_or_missing("short_interest", own is not None and (own.short_interest_pct_float is not None or own.short_interest_shares is not None), own.short_interest_settlement if own else None, inp.as_of, rules, "공매도 잔고(FINRA)", missing_reason=miss.get("ownership"))
    si_pct: float | None = None
    if own is not None and si_check.usable:
        if own.short_interest_pct_float is not None:
            si_pct = own.short_interest_pct_float
        elif own.short_interest_shares is not None and metrics is not None and metrics.shares_outstanding:
            si_pct = own.short_interest_shares / metrics.shares_outstanding

    # ---------------------------------------------------------------- issues (graph propagation + priced-in)
    news_check = _news_check(miss.get("news"), inp.as_of)
    graph = ExposureGraph(list(inp.graph_nodes), list(inp.graph_edges))
    impacts: list[CompanyIssueImpact] = []
    priced: dict[str, PricedInEstimate] = {}
    for iss in inp.issues:
        if iss.publish_time > inp.as_of:
            continue  # look-ahead guard
        raw = [i for i in compute_issue_impacts(iss, graph, None, cfg.impact) if i.ticker == t]
        if not raw:
            continue
        direction = 1 if raw[0].at(Horizon.SWING).impact_score >= 0 else -1
        est = _priced_in_for(iss, direction, bars, analyst, opt, inp.as_of)
        priced[iss.issue_id] = est
        adj = [i for i in compute_issue_impacts(iss, graph, {t: est.value} if est.value is not None else None, cfg.impact) if i.ticker == t]
        impacts.extend(adj)
        eb.add(f"issue.{iss.issue_id}", "issue", iss.title, adj[0].at(Horizon.SWING).impact_score if adj else None, ",".join(iss.sources), iss.publish_time, explicit_id=iss.evidence_id or iss.issue_id)
    if impacts:
        issue_swing: float | None = aggregate_issue_score(impacts, Horizon.SWING)
        horizon_view: dict[str, float | None] = {h.value: round(aggregate_issue_score(impacts, h), 2) for h in Horizon}
    elif news_check.usable:
        issue_swing, horizon_view = 0.0, {h.value: 0.0 for h in Horizon}
    else:
        issue_swing, horizon_view = None, {h.value: None for h in Horizon}  # failure ≠ "no news"
    eb.add("issues.net_swing", "issue", "순 이슈 영향(2~6주)", issue_swing, "calc")

    # ---------------------------------------------------------------- catalysts & risk
    ev_risk = assess_event_risk(t, list(inp.events), ny_today, cfg.event_risk)
    upcoming = tuple(sorted((e for e in inp.events if (t in e.affected or not e.affected) and e.event_date >= ny_today), key=lambda e: e.event_date)[:8])
    eb.add("calendar.event_risk", "catalyst", f"이벤트 위험 {ev_risk.level}", ev_risk.level, "calc")
    if ev_risk.nearest is not None:
        eb.add("calendar.next", "catalyst", ev_risk.nearest.title, ev_risk.nearest.event_date.isoformat(), ev_risk.nearest.source)
    cat_bias = _catalyst_bias(list(inp.events), t, revisions, earnings, ny_today)
    if opt is not None:
        eb.add("options.expected_move", "options", "옵션 내재 예상 변동폭", opt.expected_move, src.get("options", "calc"))
        eb.add("options.iv_rank", "options", "IV 순위", opt.iv_rank, src.get("options", "calc"))
    if si_pct is not None and own is not None:
        basis = "유통주식 대비" if own.short_interest_pct_float is not None else "발행주식 대비"
        eb.add("ownership.short_interest", "ownership", f"공매도 잔고 비율({basis}, 결제일 {own.short_interest_settlement.isoformat() if own.short_interest_settlement else '미상'})", si_pct, own.source, quality=si_check.quality.value)
    ins = inp.insider
    if ins is not None and ins.insider_net_buy_value_90d is not None:
        eb.add("ownership.insider_net_90d", "ownership", "내부자 순매수 금액(90일, SEC Form 4)", ins.insider_net_buy_value_90d, ins.source)

    # ---------------------------------------------------------------- entry & thesis
    entry = build_entry_plan(price, tech, cfg.entry, opt.expected_move if opt else None) if (price is not None and tech is not None) else None
    if entry is not None:
        eb.add("entry.rr", "entry", "현재가 기준 손익비", entry.rr_at_current, "calc")
        eb.add("entry.max_buy", "entry", "최대 매수가", entry.max_buy, "calc")
        eb.add("entry.stop", "entry", "손절가", entry.stop, "calc")
        eb.add("entry.target1", "entry", "1차 목표가", entry.target1, "calc")
    flags = set(inp.thesis_flags)
    cat_by_issue = {i.issue_id: i.category.value for i in inp.issues}
    for c in inp.thesis_conditions:
        if c.issue_category and c.issue_threshold is not None:
            for imp in impacts:
                if cat_by_issue.get(imp.issue_id) == c.issue_category and imp.at(Horizon.FUNDAMENTAL).impact_score <= c.issue_threshold:
                    flags.add(c.condition_id)
    thesis_bad, breaches = evaluate_thesis(list(inp.thesis_conditions), features, flags)

    # ---------------------------------------------------------------- data quality (per-type freshness)
    price_check = _price_check(inp.quote, pq, inp.as_of, cfg)
    checks = (price_check, bars_check, fund_check, er_check, an_check, macro_check, news_check, opt_check, si_check)
    fact_map: dict[str, Fact | None] = {
        "price": _fact(price_check, price if price is not None else (inp.quote.price if inp.quote else None), inp.quote.source if inp.quote else "none"),
        "price_history": _fact(bars_check, float(len(bars)) if bars else None, src.get("bars", "none")),
        "fundamentals": _fact(fund_check, float(len(pit_quarters)) if pit_quarters else None, src.get("fundamentals", "none")),
        "earnings": _fact(er_check, float(len(er_hist)) if er_hist else None, src.get("earnings", "none")),
        "analyst": _fact(an_check, 1.0 if inp.analyst else None, src.get("analyst", "none")),
        "macro": _fact(macro_check, 1.0 if inp.macro and inp.macro.series else None, src.get("macro", "none")),
        "news": _fact(news_check, 1.0 if news_check.usable else None, src.get("news", "news")),
        "options": _fact(opt_check, 1.0 if inp.options else None, src.get("options", "none")),
        "short_interest": _fact(si_check, si_pct, own.source if own else "none"),
        "sector": Fact(1.0 if sector_known else None, "classification", quality=DataQuality.FRESH if sector_known else DataQuality.MISSING, note=None if sector_known else "업종 분류 불명확"),
    }
    for c in inp.provider_conflicts:
        name = c.split(":")[0]
        name = "price_history" if name == "bars" else name
        fact_map[name] = Fact(None, "conflict", quality=DataQuality.CONFLICTING, note=c)
    dq = build_quality_report(fact_map, CORE_FIELDS, checks)
    severe = tuple(c for c in inp.provider_conflicts if c.split(":")[0] in CORE_FIELDS + ("bars",))
    stale_core = tuple(ch.data_type for ch in (bars_check, fund_check) if ch.quality == DataQuality.STALE)
    eb.add("dq.completeness", "risk", "데이터 완결성", dq.completeness, "calc")

    # ---------------------------------------------------------------- score (facts only — no action input)
    si = ScoringInputs(
        ticker=t,
        sector_model_id=model.model_id,
        sector_model_reason=reason,
        fundamental=fund_rules,
        valuation_absolute=val_rules,
        relative_valuation=rel,
        revisions=revisions,
        earnings=earnings,
        issue_score_swing=issue_swing,
        upcoming_catalyst_bias=cat_bias,
        macro=mimpact,
        risk_off_active=any(r.active and r.regime == "Risk Off" for r in regimes),
        beta=beta,
        technicals=tech,
        entry=entry,
        event_risk=ev_risk,
        net_debt_to_ebitda=features.get("net_debt_to_ebitda"),
        avg_dollar_volume=tech.avg_dollar_volume_20d if tech else None,
        short_interest_pct=si_pct,
        data_completeness=dq.completeness,
    )
    card = score(si, cfg.scoring_model)

    # ---------------------------------------------------------------- what changed + decision
    guidance_sig = hashlib.sha1(json.dumps(encode(last_er.guidance), sort_keys=True).encode()).hexdigest()[:10] if last_er is not None else None
    major = tuple(sorted(i.issue_id for i in inp.issues if i.importance >= cfg.major_issue_importance and i.issue_id in priced))
    prev = inp.previous
    digest = AnalysisDigest(
        ticker=t,
        as_of=inp.as_of,
        score=card.total,
        components={c.name: c.subscore for c in card.components},
        action=None,
        price=price,
        in_buy_zone=entry.in_buy_zone if entry else None,
        stop_breached=entry.stop_breached if entry else None,
        rr=entry.rr_at_current if entry else None,
        eps_revision_30d=analyst.eps_revision_30d if analyst else None,
        revenue_revision_30d=analyst.revenue_revision_30d if analyst else None,
        last_earnings_date=last_er.report_date if last_er else None,
        guidance_signature=guidance_sig,
        issue_ids=tuple(sorted(priced)),
        major_issue_ids=major,
        regime=prim,
        us10y=us10y_pct,
        thesis_invalidated=thesis_bad,
        stop=entry.stop if entry else None,
        max_buy=entry.max_buy if entry else None,
        atr=tech.atr14 if tech else None,
    )
    changes = diff(prev, digest, cfg.decision)
    prev_bullish = prev is not None and prev.action in {a.value for a in BULLISH_ACTIONS}
    prior_stop_breached = bool(prev_bullish and prev is not None and prev.stop is not None and price is not None and price <= prev.stop)
    ctx = DecisionContext(
        held=inp.held,
        previous_action=inp.previous_action,
        price_quality=pq,
        data_quality=dq,
        severe_conflicts=severe,
        thesis_invalidated=thesis_bad,
        thesis_breaches=tuple(breaches),
        avg_dollar_volume=tech.avg_dollar_volume_20d if tech else None,
        event_risk_level=ev_risk.level,
        material_changes=material_reasons(changes),
        portfolio_size_cap=inp.portfolio_review.size_cap.value if inp.portfolio_review else None,
        stale_core=stale_core,
        binary_event=ev_risk.binary,
        prior_stop_breached=prior_stop_breached,
        sector_unknown=not sector_known,
    )
    decision = decide(card, entry, ctx, cfg.decision)
    unchanged = prev is not None and prev.action == decision.action.value
    baseline = (prev.baseline_score if prev.baseline_score is not None else prev.score) if (unchanged and prev is not None) else card.total
    digest = replace(digest, action=decision.action.value, baseline_score=baseline)
    if prev is not None and prev.action and prev.action != decision.action.value:
        changes = changes + [ChangeItem("action", f"추천 {prev.action} → {decision.action.value}", False)]

    scenarios: tuple[Scenario, ...] = ()
    if entry is not None and tech is not None and tech.atr14:
        bull = "이익 추정치 상향 지속" if (revisions and revisions.eps_direction >= 0) else "추정치 하향 멈춤"
        bear = "이익 추정치 하향 / 악재 이슈 확대"
        inv = "; ".join(c.description for c in inp.thesis_conditions[:2]) or "종가가 손절가 아래로 마감"
        scenarios = tuple(build_scenarios(entry, tech.atr14, bull, bear, inv))

    eb.add("score.total", "score", "결정론적 점수(0~100)", card.total, "calc")
    for c in card.components:
        eb.add(f"score.{c.name}", "score", f"{c.name} 점수 {c.points}/{c.weight:g}", c.points, "calc")
    eb.add("decision.action", "score", "결정론적 행동", decision.action.value, "calc")
    eb.add("decision.confidence", "score", "결정론적 신뢰도(보정된 확률 아님)", decision.confidence, "calc", unit="points")
    if inp.portfolio_review is not None:
        pr = inp.portfolio_review
        eb.add("portfolio.sector_after", "portfolio", f"{inp.security.sector} 비중(편입 후)", pr.candidate_sector_weight_after, "calc")
        eb.add("portfolio.size_cap", "portfolio", "포트폴리오 규칙상 최대 비중 등급", pr.size_cap.value, "calc")
        eb.add("portfolio.hhi", "portfolio", "포트폴리오 HHI", pr.hhi, "calc")
        if pr.max_correlation:
            eb.add("portfolio.max_correlation", "portfolio", f"최대 상관계수 ({pr.max_correlation[0]})", pr.max_correlation[1], "calc")
    reason_ev = {r.text: eb.ids_for(r.refs) for c in card.components for r in c.reasons}
    return AnalysisResult(
        ticker=t,
        as_of=inp.as_of,
        mode=inp.mode,
        security=inp.security,
        price=price,
        price_quality=pq,
        price_timestamp=inp.quote.timestamp if inp.quote else None,
        session=inp.quote.session.value if inp.quote else None,
        price_source=inp.quote.source if inp.quote else None,
        technicals=tech,
        metrics=metrics,
        features=features,
        sector_model_id=model.model_id,
        sector_model_name=model.name,
        sector_model_reason=reason,
        fundamental_rules=fund_rules,
        valuation_rules=val_rules,
        multiples=multiples,
        relative_valuation=rel,
        earnings=earnings,
        revisions=revisions,
        analyst=analyst,
        regimes=regimes,
        primary_regime=prim,
        macro_impact=mimpact,
        issue_impacts=tuple(impacts),
        priced_in=priced,
        issue_score_swing=issue_swing,
        horizon_view=horizon_view,
        event_risk=ev_risk,
        upcoming_events=upcoming,
        options=opt,
        ownership=own,
        entry=entry,
        thesis_conditions=inp.thesis_conditions,
        thesis_invalidated=thesis_bad,
        thesis_breaches=tuple(breaches),
        data_quality=dq,
        scorecard=card,
        decision=decision,
        changes=tuple(changes),
        digest=digest,
        scenarios=scenarios,
        evidence=eb.items(),
        reason_evidence=reason_ev,
        beta=beta,
        portfolio_review=inp.portfolio_review,
        versions={
            "scoring_model_version": cfg.scoring_model.version,
            "decision_model_version": cfg.decision_model_version,
            "config_version": cfg.config_version,
            "config_hash": cfg.config_hash,
            "sector_models_version": cfg.sector_models_version,
        },
        input_fingerprint=inp.fingerprint(),
        sector_known=sector_known,
        short_interest_pct=si_pct,
        valuation_price_basis=val_basis,
    )
