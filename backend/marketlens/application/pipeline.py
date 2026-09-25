"""Pure per-ticker analysis pipeline.

``run_analysis(inputs, cfg)`` is deterministic and side-effect free: no network, no database, no LLM.
``AnalysisInputs`` is the point-in-time snapshot persisted with every recommendation so that the
decision can be replayed exactly later (see application/replay.py).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping

from marketlens.application.codec import encode
from marketlens.application.evidence import Evidence, EvidenceBuilder
from marketlens.config import ModelConfig
from marketlens.domain.catalysts import CatalystEvent, CatalystType, EventRisk, assess_event_risk
from marketlens.domain.decision import Decision, DecisionContext, decide
from marketlens.domain.earnings import AnalystSnapshot, EarningsAssessment, EarningsReport, RevisionAssessment, assess_earnings, assess_revisions
from marketlens.domain.entry import EntryPlan, ThesisCondition, build_entry_plan, evaluate_thesis
from marketlens.domain.enums import Action, DataMode, DataQuality, Horizon
from marketlens.domain.exposure_graph import Edge, ExposureGraph, Node
from marketlens.domain.facts import DataQualityReport, Fact, build_quality_report
from marketlens.domain.fundamentals import FundamentalMetrics, QuarterlyFinancials, as_of, compute_metrics
from marketlens.domain.indicators import TechnicalSnapshot, compute_technicals
from marketlens.domain.issues import CompanyIssueImpact, Issue, aggregate_issue_score, compute_issue_impacts
from marketlens.domain.macro import US10Y, MacroExposure, MacroImpact, MacroSnapshot, RegimeReading, detect_regimes, factor_moves, macro_impact, primary_regime
from marketlens.domain.market import Bar, Quote, Security, assess_price_quality
from marketlens.domain.market_calendar import trading_days_between
from marketlens.domain.options import OptionsMetrics, OptionsSnapshot, OwnershipSnapshot, compute_options_metrics
from marketlens.domain.portfolio import PortfolioReview
from marketlens.domain.priced_in import PricedInEstimate, PricedInInputs, estimate_priced_in
from marketlens.domain.scenario import Scenario, build_scenarios
from marketlens.domain.scoring import ScoreCard, ScoringInputs, score
from marketlens.domain.sector_models import RuleScore, score_rules, select_sector_model
from marketlens.domain.valuation import RelativeValuation, ValuationMultiples, compute_multiples, fundamental_features, relative_valuation
from marketlens.domain.what_changed import AnalysisDigest, ChangeItem, diff, material_reasons

CORE_FIELDS = ("price", "price_history", "fundamentals")


@dataclass(frozen=True)
class AnalysisInputs:
    ticker: str
    as_of: datetime
    mode: DataMode
    security: Security
    quote: Quote | None
    bars: tuple[Bar, ...]
    benchmark_closes: tuple[float, ...]
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
    horizon_view: Mapping[str, float]
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


def _beta(bars: tuple[Bar, ...], bench: tuple[float, ...], n: int = 120) -> float | None:
    closes = [b.close for b in bars]
    m = min(len(closes), len(bench), n + 1)
    if m < 40:
        return None
    a = closes[-m:]
    b = bench[-m:]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, m)]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, m)]
    mb = sum(rb) / len(rb)
    ma = sum(ra) / len(ra)
    var = sum((x - mb) ** 2 for x in rb)
    if var <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / var


def _priced_in_for(issue: Issue, direction: int, bars: tuple[Bar, ...], analyst: AnalystSnapshot | None, opt: OptionsMetrics | None, as_of: datetime) -> PricedInEstimate:
    ev_day = issue.publish_time.date()
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
            days_since_first_report=trading_days_between(ev_day, as_of.date()),
        )
    )


def _catalyst_bias(events: list[CatalystEvent], ticker: str, rev: RevisionAssessment | None, er: EarningsAssessment | None, today: datetime) -> float | None:
    upcoming = [e for e in events if ticker in e.affected and e.event_type == CatalystType.EARNINGS and 0 <= e.days_until(today.date()) <= 30]
    if not upcoming:
        return 0.0 if events else None
    if rev is None:
        return 0.0
    bias = 0.5 * rev.eps_direction
    if er is not None and er.expectation_bar.value == "HIGH":
        bias -= 0.25
    return max(-1.0, min(1.0, bias))


def run_analysis(inp: AnalysisInputs, cfg: ModelConfig) -> AnalysisResult:
    t = inp.ticker
    eb = EvidenceBuilder(t, inp.as_of)
    src = inp.source_map

    # ---------------------------------------------------------------- price & technicals
    pq = assess_price_quality(inp.quote, inp.as_of, cfg.freshness)
    price = inp.quote.price if inp.quote is not None and pq in (DataQuality.FRESH, DataQuality.DELAYED) else None
    if inp.quote is not None:
        eb.add("price.current", "price", f"current price ({inp.quote.session.value})", inp.quote.price, inp.quote.source, inp.quote.timestamp, pq.value)
    bars = tuple(b for b in inp.bars if b.day <= inp.as_of.date())
    tech = compute_technicals(list(bars), list(inp.benchmark_closes) or None) if len(bars) >= 30 else None
    if tech is not None:
        for k in ("sma20", "sma50", "sma200", "rsi14", "atr14", "rs_6m", "volatility_20d", "high_52w", "low_52w", "avg_dollar_volume_20d", "anchored_vwap", "volume_ratio"):
            eb.add(f"tech.{k}", "technical", k, getattr(tech, k), src.get("bars", "calc"))
    beta = _beta(bars, inp.benchmark_closes)

    # ---------------------------------------------------------------- fundamentals (point-in-time)
    pit_quarters = as_of(list(inp.quarters), inp.as_of.date())
    metrics = compute_metrics(pit_quarters) if pit_quarters else None
    features: dict[str, float | None] = fundamental_features(metrics, dict(inp.extras)) if metrics else dict(inp.extras)
    for k, v in features.items():
        if v is not None:
            eb.add(f"fund.{k}", "fundamental", k, v, src.get("fundamentals", "calc"))

    # ---------------------------------------------------------------- earnings & revisions
    er_hist = [r for r in inp.earnings if r.report_date <= inp.as_of.date()]
    earnings = assess_earnings(er_hist)
    revisions = assess_revisions(inp.analyst)
    if inp.analyst is not None:
        for k in ("eps_revision_7d", "eps_revision_30d", "eps_revision_90d", "revenue_revision_30d", "revenue_revision_90d", "analyst_count", "estimate_dispersion", "forward_eps", "forward_revenue", "target_price_consensus"):
            eb.add(f"analyst.{k}", "analyst", k, getattr(inp.analyst, k), inp.analyst.source)
    if earnings is not None:
        last = sorted(er_hist, key=lambda r: r.report_date)[-1]
        eb.add("earnings.last", "earnings", f"{last.fiscal_label} result: {earnings.result_quality.value}", earnings.eps_surprise, last.source)
        eb.add("earnings.revenue_surprise", "earnings", "revenue surprise", earnings.revenue_surprise, last.source)
        eb.add("earnings.guide_rev_vs_cons", "earnings", "next-Q revenue guide vs consensus", earnings.guide_rev_vs_cons, last.source)
        eb.add("earnings.expectation_bar", "earnings", "expectation bar", earnings.expectation_bar.value, "calc")

    # ---------------------------------------------------------------- sector model & valuation
    model, reason = select_sector_model(inp.security, cfg.sector_models)
    fund_rules = score_rules(model.fundamental_rules, features, model.min_coverage) if features else None
    multiples = compute_multiples(price, metrics, inp.analyst, inp.extras) if metrics is not None else None
    val_inputs: dict[str, float | None] = multiples.as_dict() if multiples else {}
    val_rules = score_rules(model.valuation_rules, val_inputs, model.min_coverage) if multiples else None
    us10y_pct = inp.macro.value(US10Y) if inp.macro else None
    rel = relative_valuation(multiples, model.primary_multiple, inp.valuation_history, inp.peer_multiples, us10y_pct / 100 if us10y_pct is not None else None) if multiples else None
    if multiples is not None:
        for k, v in multiples.as_dict().items():
            if v is not None:
                eb.add(f"val.{k}", "valuation", k, v, "calc")
    if rel is not None:
        eb.add("val.history", "valuation", f"{rel.primary_multiple} percentile vs own history", rel.history_percentile, src.get("valuation_history", "calc"))
        eb.add("val.peers", "valuation", f"{rel.primary_multiple} premium vs peers", rel.premium_to_peers, "calc")
        eb.add("val.rate_spread", "valuation", "forward earnings yield − 10Y", rel.equity_risk_spread, "calc")
        eb.add("val.peg", "valuation", "PEG", rel.growth_adjusted, "calc")

    # ---------------------------------------------------------------- macro
    regimes = tuple(detect_regimes(inp.macro)) if inp.macro else ()
    prim = primary_regime(list(regimes)) if regimes else "Unknown"
    mimpact = macro_impact(inp.macro_exposure, factor_moves(inp.macro)) if inp.macro else None
    if inp.macro is not None:
        for sid, s in inp.macro.series.items():
            eb.add(f"macro.{sid}", "macro", sid, s.latest.value, s.latest.source, s.latest.source_ts, s.latest.quality.value, ticker_scoped=False)
        for f, c, expl in (mimpact.contributions if mimpact else ()):
            eb.add(f"macro.{f}", "macro", expl, c, "calc")
        eb.add("macro.regime", "macro", f"primary regime {prim}", prim, "calc", ticker_scoped=False)

    # ---------------------------------------------------------------- issues (graph propagation + priced-in)
    graph = ExposureGraph(list(inp.graph_nodes), list(inp.graph_edges))
    opt = compute_options_metrics(inp.options)
    impacts: list[CompanyIssueImpact] = []
    priced: dict[str, PricedInEstimate] = {}
    for iss in inp.issues:
        raw = [i for i in compute_issue_impacts(iss, graph, None, cfg.impact) if i.ticker == t]
        if not raw:
            continue
        direction = 1 if raw[0].at(Horizon.SWING).impact_score >= 0 else -1
        est = _priced_in_for(iss, direction, bars, inp.analyst, opt, inp.as_of)
        priced[iss.issue_id] = est
        adj = [i for i in compute_issue_impacts(iss, graph, {t: est.value} if est.value is not None else None, cfg.impact) if i.ticker == t]
        impacts.extend(adj)
        eb.add(f"issue.{iss.issue_id}", "issue", iss.title, adj[0].at(Horizon.SWING).impact_score if adj else None, ",".join(iss.sources), iss.publish_time, explicit_id=iss.evidence_id or iss.issue_id)
    issue_swing = aggregate_issue_score(impacts, Horizon.SWING) if impacts else (None if "news" in inp.missing_reasons else 0.0)
    horizon_view = {h.value: round(aggregate_issue_score(impacts, h), 2) for h in Horizon} if impacts else {h.value: 0.0 for h in Horizon}
    eb.add("issues.net_swing", "issue", "net issue impact 2-6W", issue_swing, "calc")

    # ---------------------------------------------------------------- catalysts & risk
    ev_risk = assess_event_risk(t, list(inp.events), inp.as_of.date(), cfg.event_risk)
    upcoming = tuple(sorted((e for e in inp.events if (t in e.affected or not e.affected) and e.event_date >= inp.as_of.date()), key=lambda e: e.event_date)[:8])
    eb.add("calendar.event_risk", "catalyst", f"event risk {ev_risk.level}", ev_risk.level, "calc")
    if ev_risk.nearest is not None:
        eb.add("calendar.next", "catalyst", ev_risk.nearest.title, ev_risk.nearest.event_date.isoformat(), ev_risk.nearest.source)
    cat_bias = _catalyst_bias(list(inp.events), t, revisions, earnings, inp.as_of)
    if opt is not None:
        eb.add("options.expected_move", "options", "options-implied move", opt.expected_move, src.get("options", "calc"))
        eb.add("options.iv_rank", "options", "IV rank", opt.iv_rank, src.get("options", "calc"))
    if inp.ownership is not None:
        eb.add("ownership.short_interest", "ownership", "short interest % float", inp.ownership.short_interest_pct_float, inp.ownership.source)

    # ---------------------------------------------------------------- entry & thesis
    entry = build_entry_plan(price, tech, cfg.entry, opt.expected_move if opt else None) if (price is not None and tech is not None) else None
    if entry is not None:
        eb.add("entry.rr", "entry", "R/R at current price", entry.rr_at_current, "calc")
        eb.add("entry.max_buy", "entry", "maximum buy price", entry.max_buy, "calc")
        eb.add("entry.stop", "entry", "price stop", entry.stop, "calc")
        eb.add("entry.target1", "entry", "target 1", entry.target1, "calc")
    flags = set(inp.thesis_flags)
    cat_by_issue = {i.issue_id: i.category.value for i in inp.issues}
    for c in inp.thesis_conditions:
        if c.issue_category and c.issue_threshold is not None:
            for imp in impacts:
                if cat_by_issue.get(imp.issue_id) == c.issue_category and imp.at(Horizon.FUNDAMENTAL).impact_score <= c.issue_threshold:
                    flags.add(c.condition_id)
    thesis_bad, breaches = evaluate_thesis(list(inp.thesis_conditions), features, flags)

    # ---------------------------------------------------------------- data quality
    fact_map: dict[str, Fact | None] = {
        "price": Fact(price, inp.quote.source if inp.quote else "none", quality=pq) if inp.quote else None,
        "price_history": Fact(float(len(bars)), src.get("bars", "none"), quality=DataQuality.FRESH if len(bars) >= 60 else DataQuality.MISSING),
        "fundamentals": Fact(float(len(pit_quarters)), src.get("fundamentals", "none"), quality=DataQuality.FRESH if len(pit_quarters) >= 4 else DataQuality.MISSING),
        "analyst": Fact(1.0, src.get("analyst", "none")) if inp.analyst else None,
        "earnings": Fact(float(len(er_hist)), src.get("earnings", "none")) if er_hist else None,
        "macro": Fact(1.0, src.get("macro", "none")) if inp.macro and inp.macro.series else None,
        "options": Fact(1.0, src.get("options", "none")) if inp.options else None,
        "ownership": Fact(1.0, src.get("ownership", "none")) if inp.ownership else None,
    }
    for c in inp.provider_conflicts:
        name = c.split(":")[0]
        fact_map[name] = Fact(None, "conflict", quality=DataQuality.CONFLICTING, note=c)
    dq = build_quality_report(fact_map, CORE_FIELDS)
    severe = tuple(c for c in inp.provider_conflicts if c.split(":")[0] in CORE_FIELDS)

    # ---------------------------------------------------------------- score (facts only — no action input)
    net_debt_ebitda = features.get("net_debt_to_ebitda")
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
        net_debt_to_ebitda=net_debt_ebitda,
        avg_dollar_volume=tech.avg_dollar_volume_20d if tech else None,
        short_interest_pct=inp.ownership.short_interest_pct_float if inp.ownership else None,
        data_completeness=dq.completeness,
    )
    card = score(si, cfg.scoring_model)

    # ---------------------------------------------------------------- what changed + decision
    last_er = max((r.report_date for r in er_hist), default=None)
    guidance_sig = None
    if er_hist:
        g = sorted(er_hist, key=lambda r: r.report_date)[-1].guidance
        guidance_sig = hashlib.sha1(json.dumps(encode(g), sort_keys=True).encode()).hexdigest()[:10]
    major = tuple(sorted(i.issue_id for i in inp.issues if i.importance >= cfg.major_issue_importance and i.issue_id in priced))
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
        eps_revision_30d=inp.analyst.eps_revision_30d if inp.analyst else None,
        revenue_revision_30d=inp.analyst.revenue_revision_30d if inp.analyst else None,
        last_earnings_date=last_er,
        guidance_signature=guidance_sig,
        issue_ids=tuple(sorted(priced)),
        major_issue_ids=major,
        regime=prim,
        us10y=us10y_pct,
        thesis_invalidated=thesis_bad,
    )
    changes = diff(inp.previous, digest, cfg.decision)
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
    )
    decision = decide(card, entry, ctx, cfg.decision)
    digest = _with_action(digest, decision.action)
    if inp.previous is not None and inp.previous.action and inp.previous.action != decision.action.value:
        changes = changes + [ChangeItem("action", f"recommendation {inp.previous.action} → {decision.action.value}", False)]

    scenarios: tuple[Scenario, ...] = ()
    if entry is not None and tech is not None and tech.atr14:
        bull = "estimate revisions continue upward" if (revisions and revisions.eps_direction >= 0) else "revisions stabilise"
        bear = "negative estimate revisions / issue escalation"
        inv = "; ".join(c.description for c in inp.thesis_conditions[:2]) or "price closes below stop"
        scenarios = tuple(build_scenarios(entry, tech.atr14, bull, bear, inv))

    eb.add("score.total", "score", "deterministic score (0-100)", card.total, "calc")
    for c in card.components:
        eb.add(f"score.{c.name}", "score", f"{c.name} points {c.points}/{c.weight:g}", c.points, "calc")
    eb.add("decision.action", "score", "deterministic action", decision.action.value, "calc")
    eb.add("dq.completeness", "risk", "data completeness", dq.completeness, "calc")
    if inp.portfolio_review is not None:
        pr = inp.portfolio_review
        eb.add("portfolio.sector_after", "portfolio", f"{inp.security.sector} weight after a full position", pr.candidate_sector_weight_after, "calc")
        eb.add("portfolio.size_cap", "portfolio", "deterministic size cap", pr.size_cap.value, "calc")
        eb.add("portfolio.hhi", "portfolio", "portfolio HHI", pr.hhi, "calc")
        if pr.max_correlation:
            eb.add("portfolio.max_correlation", "portfolio", f"max correlation (with {pr.max_correlation[0]})", pr.max_correlation[1], "calc")
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
        analyst=inp.analyst,
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
        ownership=inp.ownership,
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
    )


def _with_action(d: AnalysisDigest, action: Action) -> AnalysisDigest:
    from dataclasses import replace

    return replace(d, action=action.value)
