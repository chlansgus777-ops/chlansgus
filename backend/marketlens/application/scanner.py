"""Multi-stage whole-market opportunity scanner (cheap → expensive).

Data path: external providers → local point-in-time store (sync) → scanner. Stages 1–2 read only local /
bulk data (one query for all bars), so a 5,000-name universe costs no per-ticker API calls there.

Stage 1  Eligibility      listing status → type → market cap → (bulk bars) freshness, price, dollar volume
Stage 2  Cheap quant      cross-sectional percentile ranks (average rank on ties), fundamentals-first → ~400
Stage 3  Fundamental      sector model + valuation (last close basis) + revisions, no quotes     → ~150
Stage 4  Event / issue    market issues through the exposure graph for every stage-3 name       → ~60
                          + company news for those names
Stage 5  Final ranking    full deterministic analysis (live quote, events, options, ownership)  → ~40
AI committee runs only on the top ``ai_committee_top_n`` of stage 5 (never on the whole market).
Ordering is deterministic: ties are broken by ticker.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Mapping

from marketlens.application.data_access import DataAccess
from marketlens.application.graph_seed import GraphSeed
from marketlens.application.issue_engine import IssueBuildResult, build_issues
from marketlens.application.pipeline import AnalysisInputs, AnalysisResult, filing_visibility_day, run_analysis
from marketlens.application.theses import ThesisBook
from marketlens.config import ModelConfig
from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.enums import Action, DataMode, Horizon
from marketlens.domain.exposure_graph import ExposureGraph
from marketlens.domain.freshness import check_age, rules_from_config
from marketlens.domain.fundamentals import as_of as pit_quarters
from marketlens.domain.fundamentals import compute_metrics
from marketlens.domain.indicators import compute_technicals
from marketlens.domain.issues import Issue, aggregate_issue_score, compute_issue_impacts
from marketlens.domain.macro import MacroSnapshot
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import last_completed_session, to_ny
from marketlens.domain.portfolio import common_valuation, CandidateProfile, Portfolio, review_candidate
from marketlens.domain.sector_models import select_sector_model
from marketlens.domain.valuation import compute_multiples
from marketlens.domain.what_changed import AnalysisDigest
from marketlens.infrastructure.logging import Event, log_event

log = logging.getLogger("marketlens.scanner")
BENCHMARK = "SPY"
HISTORY_CALENDAR_DAYS = 420
NEWS_LOOKBACK = timedelta(days=5)
ISSUE_RANK_POINTS = 10.0  # stage 4: max ± points an issue swing of ±100 adds to the deep score

PreviousLookup = Callable[[str, datetime], tuple[AnalysisDigest | None, Action | None]]


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
    benchmark_bars: tuple[Bar, ...]
    securities: dict[str, Security]
    company_news_missing: dict[str, str] = field(default_factory=dict)
    estimate_fetch: dict[str, str] = field(default_factory=dict)  # Alpha Vantage prefetch outcome per ticker
    guidance_fetch: dict[str, str] = field(default_factory=dict)  # SEC 8-K guidance extraction outcome per ticker


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
    """Percentile ranks in (0, 1]; tied values share the average rank (order-independent)."""
    items = sorted(((k, v) for k, v in values.items() if v is not None), key=lambda kv: (kv[1] if higher_better else -kv[1], kv[0]))
    n = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            out[items[k][0]] = avg / n
        i = j + 1
    return out


def _returns_by_date(bars: list[Bar] | tuple[Bar, ...], n: int = 120) -> dict[date, float]:
    out = {bars[i].day: bars[i].close / bars[i - 1].close - 1 for i in range(1, len(bars)) if bars[i - 1].close > 0}
    days = sorted(out)[-n:]
    return {d: out[d] for d in days}


class Scanner:
    def __init__(
        self,
        data: DataAccess,
        cfg: ModelConfig,
        graph_seed: GraphSeed,
        theses: ThesisBook,
        previous_lookup: PreviousLookup = lambda _t, _ts: (None, None),
    ) -> None:
        self.data = data
        self.cfg = cfg
        fr = cfg.raw.get("freshness")
        self._fresh_rules = rules_from_config(fr.get("rules") if isinstance(fr, dict) else None)
        self.seed = graph_seed
        self.theses = theses
        self.previous_lookup = previous_lookup

    # ------------------------------------------------------------------ context
    def build_context(self, as_of: datetime) -> ScanContext:
        today = to_ny(as_of).date()
        secs_f = self.data.securities(today)
        securities = {s.ticker: s for s in (secs_f.value or [])}
        macro, macro_missing = self.data.macro_snapshot(as_of)
        ev_f = self.data.events(today - timedelta(days=1), today + timedelta(days=90))
        news_f = self.data.news(as_of - NEWS_LOOKBACK, None)  # market-wide news path
        names = {t: s.company_name for t, s in securities.items()}
        issues = build_issues(news_f.value, as_of, names) if news_f.value is not None else None
        if issues:
            for nid, flags in issues.injection_flags.items():
                log_event(log, Event.PROMPT_INJECTION_DETECTED, level=logging.WARNING, news_id=nid, patterns=len(flags))
            for i in issues.issues:
                log_event(log, Event.ISSUE_CREATED, issue_id=i.issue_id, category=i.category.value)
        bench = self.data.bars(BENCHMARK, today - timedelta(days=HISTORY_CALENDAR_DAYS), today)
        return ScanContext(
            as_of=as_of,
            mode=self.data.reg.mode,
            macro=macro,
            macro_missing=macro_missing,
            events=list(ev_f.value or []),
            issues=issues,
            news_missing=None if news_f.value is not None else (news_f.error or "뉴스 제공자 응답 없음"),
            graph=self.seed.graph(list(securities.values())),
            benchmark_bars=tuple(bench.value or ()),
            securities=securities,
        )

    def add_company_news(self, ctx: ScanContext, tickers: list[str]) -> None:
        """Company news for the stage-4 names (per-ticker endpoint), merged into the context issues."""
        if not tickers:
            return
        names = {t: ctx.securities[t].company_name for t in tickers if t in ctx.securities}
        items = []
        for t in tickers:
            f = self.data.news(ctx.as_of - NEWS_LOOKBACK, [t])
            if f.value is None:
                ctx.company_news_missing[t] = f.error or "기업 뉴스 수집 실패"
            else:
                items.extend(f.value)
        if not items:
            return
        company = build_issues(items, ctx.as_of, names)
        ctx.issues = company if ctx.issues is None else ctx.issues.merged(company)

    # ------------------------------------------------------------------ inputs
    def gather_inputs(self, ctx: ScanContext, sec: Security, portfolio: Portfolio | None, peer_multiples: tuple[float, ...] = (), full: bool = True) -> AnalysisInputs:
        """``full=False`` (stage 3) skips per-ticker live calls: quote, earnings, events, options, ownership."""
        t = sec.ticker
        d = to_ny(ctx.as_of).date()
        missing: dict[str, str] = {}
        src: dict[str, str] = {}
        conflicts: list[str] = []

        def take(name: str, f):  # type: ignore[no-untyped-def]
            if f.value is None:
                missing[name] = f.error or "제공자 없음/응답 없음"
            else:
                src[name] = f.provider or ""
            for c in f.conflicts:
                conflicts.append(f"{name}: {c}")
            return f.value

        quote = take("price", self.data.quote(t)) if full else None
        if not full:
            missing["price"] = "3단계(펀더멘털 선별)는 실시간 시세를 조회하지 않음"
        bars = take("bars", self.data.bars(t, d - timedelta(days=HISTORY_CALENDAR_DAYS), d)) or []
        quarters = take("fundamentals", self.data.quarters(t)) or []
        annuals = []
        if not quarters and full:  # foreign private issuer (20-F, IFRS): annual statements only
            af = self.data.annuals(t)
            annuals = af.value or []
        extras_obj = take("extras", self.data.extras(t))
        analyst = take("analyst", self.data.estimates(t, d))
        earnings = (take("earnings", self.data.earnings(t)) or []) if full else []
        if full and earnings and self.data.store is not None:  # LIVE: SEC 8-K guidance vs the pre-release consensus
            from marketlens.application.estimate_book import attach_guidance

            day = ctx.as_of.date()
            earnings = attach_guidance(earnings, self.data.store.guidance(t, ctx.as_of), self.data.store.estimate_history(t, day), day)
        model, _ = select_sector_model(sec, self.cfg.sector_models)
        vh = take("valuation_history", self.data.valuation_history(t, model.primary_multiple))
        options = take("options", self.data.options(t)) if full else None
        own = take("ownership", self.data.short_interest(t)) if full else None
        insider = take("insider", self.data.insider(t)) if full else None
        if ctx.macro is None:
            missing["macro"] = ctx.macro_missing or "거시 데이터 없음"
        else:
            src["macro"] = "macro"
        if ctx.news_missing:
            missing["news"] = ctx.news_missing
        elif t in ctx.company_news_missing:
            missing["news"] = f"기업 뉴스: {ctx.company_news_missing[t]}"

        issues: tuple[Issue, ...] = ()
        nodes, edges = self.seed.subgraph(ctx.graph, t, self.cfg.impact.max_hops)
        if ctx.issues is not None:
            reach = {n.node_id for n in nodes}
            issues = tuple(i for i in ctx.issues.issues if any(e.node_id in reach for e in i.primary_effects))

        held = False
        review = None
        if portfolio is not None:
            held = any(h.ticker == t for h in portfolio.holdings)
            hold_rets: dict[str, dict[date, float]] = {}
            closes: dict[str, dict[date, float]] = {}
            for h in portfolio.holdings:
                hb = self.data.bars(h.ticker, d - timedelta(days=200), d).value or []
                hb = [b for b in hb if b.day <= last_completed_session(ctx.as_of)]
                closes[h.ticker] = {b.day: b.close for b in hb}
                hold_rets[h.ticker] = _returns_by_date(hb)
            # same policy as the portfolio page: one common valuation session, missing prices never = cost
            val_day, prices, _missing = common_valuation(closes, [h.ticker for h in portfolio.holdings])
            exp = self.seed.macro_exposure(sec)
            themes = tuple(k for k, v in (("AI", exp.ai),) if v >= 0.4)
            review = review_candidate(portfolio, prices, CandidateProfile(t, sec.sector, themes, exp.rates, _returns_by_date(bars)), hold_rets, self.cfg.portfolio, valuation_day=val_day)

        prev_digest, prev_action = self.previous_lookup(t, ctx.as_of)
        return AnalysisInputs(
            ticker=t,
            as_of=ctx.as_of,
            mode=ctx.mode,
            security=sec,
            quote=quote,
            bars=tuple(bars),
            benchmark_bars=ctx.benchmark_bars,
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
            events=tuple(e for e in ctx.events if t in e.affected or not e.affected) if full else (),
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
            insider=insider,
            splits=tuple(self.data.splits(t)),
            annuals=tuple(annuals),
        )

    # ------------------------------------------------------------------ stages
    def stage1(self, ctx: ScanContext, excluded: dict[str, str], only: list[str] | None = None) -> dict[str, list[Bar]]:
        sc = self.cfg.scanner
        d = to_ny(ctx.as_of).date()
        last = last_completed_session(ctx.as_of)
        pending: list[str] = []
        for t, s in sorted(ctx.securities.items()):
            if only and t not in only:
                continue
            if not s.active or not s.was_listed_on(d):
                excluded[t] = "상장폐지/비활성"
            elif s.is_etf and not sc.include_etfs:
                excluded[t] = "ETF 제외(설정)"
            elif s.market_cap is None:
                excluded[t] = "시가총액 미상(발행주식수 또는 종가 없음)"
            elif s.market_cap < sc.min_market_cap:
                excluded[t] = "시가총액 기준 미달"
            else:
                pending.append(t)
        bars_by = self.data.bars_bulk(pending, d - timedelta(days=HISTORY_CALENDAR_DAYS), d)
        out: dict[str, list[Bar]] = {}
        for t in pending:
            bars = [b for b in bars_by.get(t, []) if b.day <= last]
            if len(bars) < 60:
                excluded[t] = "가격 이력 부족(60거래일 미만)"
                continue
            fr = check_age("price_history", bars[-1].day, ctx.as_of, rules=self._fresh_rules)
            if not fr.usable:
                excluded[t] = f"가격 이력이 오래됨(마지막 {bars[-1].day.isoformat()})"
                continue
            px = bars[-1].close
            adv = sum(b.close * b.volume for b in bars[-20:]) / 20
            if px < sc.min_price:
                excluded[t] = f"주가 {px:.2f} < {sc.min_price}"
                continue
            if adv < sc.min_avg_dollar_volume:
                excluded[t] = "평균 거래대금 기준 미달"
                continue
            out[t] = bars
        return out

    def stage2(self, ctx: ScanContext, eligible: dict[str, list[Bar]]) -> list[str]:
        w = self.cfg.scanner.stage2_weights
        raw: dict[str, dict[str, float | None]] = {k: {} for k in w}
        d = to_ny(ctx.as_of).date()
        for t, bars in sorted(eligible.items()):
            q = self.data.quarters(t).value or []
            pq = pit_quarters(q, filing_visibility_day(ctx.as_of)) if q else []
            m = compute_metrics(pq) if pq else None
            est = self.data.estimates(t, d).value
            tech = compute_technicals(bars, list(ctx.benchmark_bars) or None)
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
        return [t for t, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))][: self.cfg.scanner.stage2_keep]

    def _issue_swing(self, ctx: ScanContext, t: str) -> float:
        if ctx.issues is None:
            return 0.0
        impacts = []
        for iss in ctx.issues.issues:
            impacts.extend(i for i in compute_issue_impacts(iss, ctx.graph, None, self.cfg.impact) if i.ticker == t)
        return aggregate_issue_score(impacts, Horizon.SWING) if impacts else 0.0

    def run(self, as_of: datetime, portfolio: Portfolio | None = None, only: list[str] | None = None) -> ScanResult:
        log_event(log, Event.SCAN_STARTED, as_of=as_of.isoformat(), mode=self.data.reg.mode.value)
        ctx = self.build_context(as_of)
        excluded: dict[str, str] = {}
        stages: list[StageStats] = []
        sc = self.cfg.scanner

        eligible = self.stage1(ctx, excluded, only)
        reasons = Counter(excluded.values())
        top = ", ".join(f"{k} {v}" for k, v in reasons.most_common(4))
        stages.append(StageStats("1-eligibility", len(only) if only else len(ctx.securities), len(eligible), f"주가≥{sc.min_price}, 시총≥{sc.min_market_cap:.0e}, 거래대금≥{sc.min_avg_dollar_volume:.0e}; 제외: {top or '없음'}"))

        s2 = self.stage2(ctx, eligible)
        stages.append(StageStats("2-cheap-quant", len(eligible), len(s2), "펀더멘털 우선 백분위 순위(동점 평균 순위)"))

        # stage 3: sector-model deep filter (no live quotes) — also collects peer multiples
        lite: dict[str, AnalysisResult] = {}
        for t in s2:
            lite[t] = run_analysis(self.gather_inputs(ctx, ctx.securities[t], None, (), full=False), self.cfg)
        peer_values: dict[str, list[float]] = {}
        for r in lite.values():
            if r.relative_valuation and r.relative_valuation.primary_value is not None:
                peer_values.setdefault(r.sector_model_id, []).append(r.relative_valuation.primary_value)

        def deep_score(r: AnalysisResult) -> float:
            c = r.scorecard
            return sum(c.component(n).points for n in ("fundamental", "valuation", "earnings_revision")) + 0.3 * c.component("risk").points

        deep = {t: deep_score(r) for t, r in lite.items()}
        s3 = sorted(lite, key=lambda t: (-deep[t], t))[: sc.stage3_keep]
        stages.append(StageStats("3-fundamental-deep", len(s2), len(s3), "업종별 모델, 밸류에이션(직전 종가 기준), 추정치 리비전"))

        # stage 4: every stage-3 name gets the market-issue impact through the exposure graph
        combined = {t: deep[t] + ISSUE_RANK_POINTS * self._issue_swing(ctx, t) / 100 for t in s3}
        s4 = sorted(s3, key=lambda t: (-combined[t], t))[: sc.stage4_keep]
        self.add_company_news(ctx, s4)
        stages.append(StageStats("4-event-issue", len(s3), len(s4), f"이슈 {len(ctx.issues.issues) if ctx.issues else 0}건, 그래프 ≤{self.cfg.impact.max_hops}단계, 기업 뉴스 {len(s4)}종목"))

        # free consensus with provider revision history, only for the best-ranked names (daily-limited)
        ctx.estimate_fetch = self.data.prefetch_estimates(s4[: sc.estimate_top_n], last_completed_session(ctx.as_of), sc.estimate_daily_budget, sc.estimate_ttl_days)
        ctx.guidance_fetch = self.data.prefetch_guidance(s4[: sc.estimate_top_n], last_completed_session(ctx.as_of))
        full: dict[str, AnalysisResult] = {}
        inputs: dict[str, AnalysisInputs] = {}
        for t in s4:
            mid = lite[t].sector_model_id
            own_val = lite[t].relative_valuation.primary_value if lite[t].relative_valuation else None
            peers = tuple(v for v in peer_values.get(mid, []) if v != own_val)
            inp = self.gather_inputs(ctx, ctx.securities[t], portfolio, peers, full=True)
            inputs[t] = inp
            full[t] = run_analysis(inp, self.cfg)
            log_event(log, Event.SCORE_CREATED, ticker=t, score=full[t].scorecard.total)

        ranked = sorted(full.values(), key=lambda r: (r.decision.action == Action.DATA_INSUFFICIENT, -r.scorecard.total, r.ticker))[: sc.final_candidates]
        stages.append(StageStats("5-final-ranking", len(full), len(ranked), f"AI 위원회는 상위 {sc.ai_committee_top_n}종목만"))
        log_event(log, Event.SCAN_FINISHED, candidates=len(ranked), eligible=len(eligible))
        return ScanResult(as_of, ctx.mode, stages, ranked, {r.ticker: inputs[r.ticker] for r in ranked}, ctx, excluded)

    def analyze_single(self, ticker: str, as_of: datetime, portfolio: Portfolio | None = None, ctx: ScanContext | None = None) -> tuple[AnalysisResult, AnalysisInputs]:
        ctx = ctx or self.build_context(as_of)
        sec = ctx.securities.get(ticker)
        if sec is None:
            raise KeyError(f"{ticker}: 분석 시점의 유니버스에 없음")
        self.add_company_news(ctx, [ticker])
        sc = self.cfg.scanner
        self.data.prefetch_estimates([ticker], last_completed_session(ctx.as_of), sc.estimate_daily_budget + 3, sc.estimate_ttl_days)  # user-requested page may use the reserve
        self.data.prefetch_guidance([ticker], last_completed_session(ctx.as_of))
        inp = self.gather_inputs(ctx, sec, portfolio, (), full=True)
        return run_analysis(inp, self.cfg), inp
