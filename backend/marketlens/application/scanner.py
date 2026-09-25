"""Multi-stage whole-market opportunity scanner (cheap → expensive).

Stage 1  Eligibility            price / market cap / dollar volume / listing status   (all securities)
Stage 2  Cheap quant filter     cross-sectional percentile ranks, fundamentals-first   → ~400
Stage 3  Fundamental deep       sector model + valuation + earnings/revisions          → ~150
Stage 4  Event / issue          news → issues → exposure graph → impact, priced-in    → ~60
Stage 5  Final ranking          full deterministic analysis + decision                 → ~40
AI committee runs only on the top ``ai_committee_top_n`` of stage 5 (never on the whole market).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Mapping

from marketlens.application.data_access import DataAccess
from marketlens.application.graph_seed import GraphSeed
from marketlens.application.issue_engine import IssueBuildResult, build_issues
from marketlens.application.pipeline import AnalysisInputs, AnalysisResult, run_analysis
from marketlens.application.theses import ThesisBook
from marketlens.config import ModelConfig
from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.enums import Action, DataMode
from marketlens.domain.exposure_graph import ExposureGraph
from marketlens.domain.fundamentals import as_of as pit_quarters
from marketlens.domain.fundamentals import compute_metrics
from marketlens.domain.indicators import compute_technicals
from marketlens.domain.issues import Issue
from marketlens.domain.macro import MacroSnapshot
from marketlens.domain.market import Bar, Security
from marketlens.domain.portfolio import CandidateProfile, Portfolio, review_candidate
from marketlens.domain.sector_models import select_sector_model
from marketlens.domain.valuation import compute_multiples
from marketlens.domain.what_changed import AnalysisDigest
from marketlens.infrastructure.logging import Event, log_event

log = logging.getLogger("marketlens.scanner")
BENCHMARK = "SPY"
HISTORY_CALENDAR_DAYS = 420


@dataclass
class StageStats:
    stage: str
    input_count: int
    output_count: int
    note: str = ""


@dataclass
class ScanContext:
    as_of: datetime
    mode: DataMode
    macro: MacroSnapshot | None
    macro_missing: str | None
    events: list[CatalystEvent]
    issues: IssueBuildResult | None
    news_missing: str | None
    graph: ExposureGraph
    benchmark_closes: tuple[float, ...]
    securities: dict[str, Security]


@dataclass
class ScanResult:
    as_of: datetime
    mode: DataMode
    stages: list[StageStats]
    candidates: list[AnalysisResult]
    inputs: dict[str, AnalysisInputs]
    context: ScanContext
    excluded: dict[str, str] = field(default_factory=dict)


def _pct_ranks(values: Mapping[str, float | None], higher_better: bool = True) -> dict[str, float]:
    items = [(k, v) for k, v in values.items() if v is not None]
    items.sort(key=lambda kv: kv[1], reverse=not higher_better)
    n = len(items)
    out: dict[str, float] = {}
    for i, (k, _) in enumerate(items):
        out[k] = (i + 1) / n if n else 0.0
    return out


class Scanner:
    def __init__(
        self,
        data: DataAccess,
        cfg: ModelConfig,
        graph_seed: GraphSeed,
        theses: ThesisBook,
        previous_lookup: Callable[[str], tuple[AnalysisDigest | None, Action | None]] = lambda _t: (None, None),
    ) -> None:
        self.data = data
        self.cfg = cfg
        self.seed = graph_seed
        self.theses = theses
        self.previous_lookup = previous_lookup

    # ------------------------------------------------------------------ context
    def build_context(self, as_of: datetime) -> ScanContext:
        secs_f = self.data.securities(None)
        securities = {s.ticker: s for s in (secs_f.value or [])}
        macro, macro_missing = self.data.macro_snapshot(as_of)
        ev_f = self.data.events(as_of.date() - timedelta(days=1), as_of.date() + timedelta(days=90))
        news_f = self.data.news(as_of - timedelta(days=5), None)
        issues = build_issues(news_f.value, as_of) if news_f.value is not None else None
        if issues:
            for nid, flags in issues.injection_flags.items():
                log_event(log, Event.PROMPT_INJECTION_DETECTED, level=logging.WARNING, news_id=nid, patterns=len(flags))
            for i in issues.issues:
                log_event(log, Event.ISSUE_CREATED, issue_id=i.issue_id, category=i.category.value)
        bench = self.data.bars(BENCHMARK, as_of.date() - timedelta(days=HISTORY_CALENDAR_DAYS), as_of.date())
        bench_closes = tuple(b.close for b in (bench.value or []))
        return ScanContext(
            as_of=as_of,
            mode=self.data.reg.mode,
            macro=macro,
            macro_missing=macro_missing,
            events=list(ev_f.value or []),
            issues=issues,
            news_missing=None if news_f.value is not None else (news_f.error or "news unavailable"),
            graph=self.seed.graph(list(securities.values())),
            benchmark_closes=bench_closes,
            securities=securities,
        )

    # ------------------------------------------------------------------ inputs
    def gather_inputs(self, ctx: ScanContext, sec: Security, portfolio: Portfolio | None, peer_multiples: tuple[float, ...] = (), include_events: bool = True) -> AnalysisInputs:
        t = sec.ticker
        d = ctx.as_of.date()
        missing: dict[str, str] = {}
        src: dict[str, str] = {}
        conflicts: list[str] = []

        def take(name: str, f):  # type: ignore[no-untyped-def]
            if f.value is None:
                missing[name] = f.error or "unavailable"
            else:
                src[name] = f.provider or ""
            for c in f.conflicts:
                conflicts.append(f"{name}: {c}")
            return f.value

        quote = take("price", self.data.quote(t))
        bars = take("bars", self.data.bars(t, d - timedelta(days=HISTORY_CALENDAR_DAYS), d)) or []
        quarters = take("fundamentals", self.data.quarters(t)) or []
        extras_obj = take("extras", self.data.extras(t))
        analyst = take("analyst", self.data.estimates(t, d))
        earnings = take("earnings", self.data.earnings(t)) or []
        model, _ = select_sector_model(sec, self.cfg.sector_models)
        vh = take("valuation_history", self.data.valuation_history(t, model.primary_multiple))
        options = take("options", self.data.options(t)) if include_events else None
        own = take("ownership", self.data.short_interest(t)) if include_events else None
        if ctx.macro is None:
            missing["macro"] = ctx.macro_missing or "unavailable"
        else:
            src["macro"] = "macro"
        if ctx.news_missing:
            missing["news"] = ctx.news_missing

        issues: tuple[Issue, ...] = ()
        nodes, edges = self.seed.subgraph(ctx.graph, t, self.cfg.impact.max_hops)
        if include_events and ctx.issues is not None:
            reach = {n.node_id for n in nodes}
            issues = tuple(i for i in ctx.issues.issues if any(e.node_id in reach for e in i.primary_effects))

        held = False
        review = None
        if portfolio is not None:
            held = any(h.ticker == t for h in portfolio.holdings)
            closes = [b.close for b in bars]
            rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))][-120:]
            hold_rets: dict[str, list[float]] = {}
            prices: dict[str, float] = {}
            for h in portfolio.holdings:
                hb = self.data.bars(h.ticker, d - timedelta(days=200), d).value or []
                hc = [b.close for b in hb]
                if hc:
                    prices[h.ticker] = hc[-1]
                hold_rets[h.ticker] = [hc[i] / hc[i - 1] - 1 for i in range(1, len(hc))][-120:]
            exp = self.seed.macro_exposure(sec)
            themes = tuple(k for k, v in (("AI", exp.ai),) if v >= 0.4)
            review = review_candidate(portfolio, prices, CandidateProfile(t, sec.sector, themes, exp.rates, rets), hold_rets, self.cfg.portfolio)

        prev_digest, prev_action = self.previous_lookup(t)
        return AnalysisInputs(
            ticker=t,
            as_of=ctx.as_of,
            mode=ctx.mode,
            security=sec,
            quote=quote,
            bars=tuple(bars),
            benchmark_closes=ctx.benchmark_closes,
            quarters=tuple(quarters),
            extras=dict(extras_obj.values) if extras_obj is not None else {},
            analyst=analyst,
            earnings=tuple(earnings),
            valuation_history=tuple(vh.values) if vh is not None else (),
            peer_multiples=peer_multiples,
            macro=ctx.macro,
            macro_exposure=self.seed.macro_exposure(sec),
            issues=issues,
            graph_nodes=tuple(nodes),
            graph_edges=tuple(edges),
            events=tuple(e for e in ctx.events if t in e.affected or not e.affected) if include_events else (),
            options=options,
            ownership=own,
            held=held,
            portfolio_review=review,
            previous=prev_digest,
            previous_action=prev_action,
            thesis_conditions=self.theses.for_ticker(t),
            thesis_flags=(),
            provider_conflicts=tuple(conflicts),
            source_map=src,
            missing_reasons=missing,
        )

    # ------------------------------------------------------------------ stages
    def stage1(self, ctx: ScanContext, excluded: dict[str, str]) -> dict[str, list[Bar]]:
        sc = self.cfg.scanner
        d = ctx.as_of.date()
        out: dict[str, list[Bar]] = {}
        for t, s in ctx.securities.items():
            if not s.active or not s.was_listed_on(d):
                excluded[t] = "inactive/delisted"
                continue
            if s.is_etf and not sc.include_etfs:
                excluded[t] = "ETF excluded by config"
                continue
            if s.market_cap is not None and s.market_cap < sc.min_market_cap:
                excluded[t] = "market cap below minimum"
                continue
            bars = self.data.bars(t, d - timedelta(days=HISTORY_CALENDAR_DAYS), d).value or []
            if len(bars) < 60:
                excluded[t] = "insufficient price history"
                continue
            last = bars[-1].close
            adv = sum(b.close * b.volume for b in bars[-20:]) / 20
            if last < sc.min_price:
                excluded[t] = f"price {last:.2f} < {sc.min_price}"
                continue
            if adv < sc.min_avg_dollar_volume:
                excluded[t] = "average dollar volume below minimum"
                continue
            if s.market_cap is None:
                excluded[t] = "market cap unknown"
                continue
            out[t] = bars
        return out

    def stage2(self, ctx: ScanContext, eligible: dict[str, list[Bar]]) -> list[str]:
        w = self.cfg.scanner.stage2_weights
        raw: dict[str, dict[str, float | None]] = {k: {} for k in w}
        for t, bars in eligible.items():
            q = self.data.quarters(t).value or []
            m = compute_metrics(pit_quarters(q, ctx.as_of.date())) if q else None
            est = self.data.estimates(t, ctx.as_of.date()).value
            tech = compute_technicals(bars, list(ctx.benchmark_closes) or None)
            mult = compute_multiples(bars[-1].close, m, est, None) if m else None
            raw["revenue_growth"][t] = m.revenue_growth_yoy if m else None
            raw["eps_growth"][t] = m.eps_growth_yoy if m else None
            raw["eps_revision"][t] = est.eps_revision_30d if est else None
            raw["relative_strength"][t] = tech.rs_6m
            raw["volume_trend"][t] = tech.volume_ratio
            raw["distance_from_52w_high"][t] = tech.distance_from_52w_high
            raw["forward_pe"][t] = mult.forward_pe if mult else None
            raw["fcf_yield"][t] = mult.fcf_yield if mult else None
            raw["liquidity"][t] = tech.avg_dollar_volume_20d
            raw["volatility"][t] = tech.volatility_20d
        lower_better = {"forward_pe", "volatility"}
        ranks = {k: _pct_ranks(v, higher_better=k not in lower_better) for k, v in raw.items()}
        missing_rank = 0.3  # conservative: missing data is not rewarded
        scores = {t: sum(w[k] * ranks[k].get(t, missing_rank) for k in w) for t in eligible}
        return [t for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])][: self.cfg.scanner.stage2_keep]

    def run(self, as_of: datetime, portfolio: Portfolio | None = None, only: list[str] | None = None) -> ScanResult:
        log_event(log, Event.SCAN_STARTED, as_of=as_of.isoformat(), mode=self.data.reg.mode.value)
        ctx = self.build_context(as_of)
        excluded: dict[str, str] = {}
        stages: list[StageStats] = []
        sc = self.cfg.scanner

        eligible = self.stage1(ctx, excluded)
        if only:
            eligible = {t: b for t, b in eligible.items() if t in only}
        stages.append(StageStats("1-eligibility", len(ctx.securities), len(eligible), f"price≥{sc.min_price}, mcap≥{sc.min_market_cap:.0e}, ADV≥{sc.min_avg_dollar_volume:.0e}"))

        s2 = self.stage2(ctx, eligible)
        stages.append(StageStats("2-cheap-quant", len(eligible), len(s2), "fundamental-first percentile ranks"))

        # stage 3: sector-model deep filter (no events) — also collects peer multiples
        lite: dict[str, AnalysisResult] = {}
        for t in s2:
            inp = self.gather_inputs(ctx, ctx.securities[t], None, (), include_events=False)
            lite[t] = run_analysis(inp, self.cfg)
        peer_values: dict[str, list[float]] = {}
        for r in lite.values():
            if r.relative_valuation and r.relative_valuation.primary_value is not None:
                peer_values.setdefault(r.sector_model_id, []).append(r.relative_valuation.primary_value)

        def deep_score(r: AnalysisResult) -> float:
            c = r.scorecard
            return sum(c.component(n).points for n in ("fundamental", "valuation", "earnings_revision")) + 0.3 * c.component("risk").points

        s3 = sorted(lite, key=lambda t: -deep_score(lite[t]))[: sc.stage3_keep]
        stages.append(StageStats("3-fundamental-deep", len(s2), len(s3), "sector models, valuation, revisions"))

        s4_in = s3[: sc.stage4_keep]
        full: dict[str, AnalysisResult] = {}
        inputs: dict[str, AnalysisInputs] = {}
        for t in s4_in:
            mid = lite[t].sector_model_id
            peers = tuple(v for v in peer_values.get(mid, []) if v != (lite[t].relative_valuation.primary_value if lite[t].relative_valuation else None))
            inp = self.gather_inputs(ctx, ctx.securities[t], portfolio, peers, include_events=True)
            inputs[t] = inp
            full[t] = run_analysis(inp, self.cfg)
            log_event(log, Event.SCORE_CREATED, ticker=t, score=full[t].scorecard.total)
        stages.append(StageStats("4-event-issue", len(s3), len(full), f"{len(ctx.issues.issues) if ctx.issues else 0} issues, graph ≤{self.cfg.impact.max_hops} hops"))

        ranked = sorted(full.values(), key=lambda r: (-r.scorecard.total, r.ticker))[: sc.final_candidates]
        stages.append(StageStats("5-final-ranking", len(full), len(ranked), f"AI committee on top {sc.ai_committee_top_n}"))
        log_event(log, Event.SCAN_FINISHED, candidates=len(ranked), eligible=len(eligible))
        return ScanResult(as_of, ctx.mode, stages, ranked, {r.ticker: inputs[r.ticker] for r in ranked}, ctx, excluded)

    def analyze_single(self, ticker: str, as_of: datetime, portfolio: Portfolio | None = None, ctx: ScanContext | None = None) -> tuple[AnalysisResult, AnalysisInputs]:
        ctx = ctx or self.build_context(as_of)
        sec = ctx.securities.get(ticker)
        if sec is None:
            raise KeyError(f"{ticker} not in universe")
        inp = self.gather_inputs(ctx, sec, portfolio, (), include_events=True)
        return run_analysis(inp, self.cfg), inp
