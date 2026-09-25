from datetime import date, datetime, timezone

from marketlens.domain.enums import ConfirmedStatus, EdgeType, Horizon, IssueCategory, NodeType
from marketlens.domain.exposure_graph import Edge, ExposureGraph, HopDecay, Node
from marketlens.domain.issues import Issue, IssueEffect, aggregate_issue_score, compute_issue_impacts
from marketlens.domain.macro import (
    SOX, SPX, US10Y, VIX, WTI, MacroExposure, MacroSeries, MacroSnapshot, detect_regimes, factor_moves, macro_impact, primary_regime,
)
from marketlens.domain.facts import Fact
from marketlens.domain.priced_in import PricedInInputs, estimate_priced_in

T = datetime(2026, 9, 24, tzinfo=timezone.utc)
D = date(2026, 9, 1)


def graph() -> ExposureGraph:
    nodes = [Node(t, NodeType.COMPANY, t) for t in ("MSFT", "NVDA", "TSM", "AMD", "VRT")]
    edges = [
        Edge("NVDA", "MSFT", EdgeType.SUPPLIER_OF, 0.8, 0.9, "s", D),
        Edge("TSM", "NVDA", EdgeType.SUPPLIER_OF, 0.9, 0.9, "s", D),
        Edge("VRT", "MSFT", EdgeType.SUPPLIER_OF, 0.5, 0.8, "s", D),
        Edge("AMD", "NVDA", EdgeType.COMPETITOR_OF, 0.6, 0.9, "s", D),
    ]
    return ExposureGraph(nodes, edges)


def issue(effects, cat=IssueCategory.AI, status=ConfirmedStatus.CONFIRMED) -> Issue:
    return Issue("I1", "t", cat, "s", T, T, ("src",), 0.9, status, (), tuple(effects), 0.8, 0.5, 0.2, 0.9)


def test_indirect_impact_without_ticker_mention():
    imps = {i.ticker: i for i in compute_issue_impacts(issue([IssueEffect("MSFT", 1.0, ("capex ↑",), origin_impact=False)]), graph())}
    assert "MSFT" not in imps  # the spender is only the transmitter
    assert imps["NVDA"].hops == 1 and imps["TSM"].hops == 2 and imps["VRT"].hops == 1
    assert imps["NVDA"].at(Horizon.SWING).impact_score > imps["TSM"].at(Horizon.SWING).impact_score > 0
    assert any("SUPPLIER_OF" in m for m in imps["TSM"].at(Horizon.SWING).mechanism)


def test_competitor_gets_opposite_sign():
    imps = {i.ticker: i for i in compute_issue_impacts(issue([IssueEffect("NVDA", 1.0, ("x",))]), graph())}
    assert imps["NVDA"].at(Horizon.SWING).impact_score > 0
    assert imps["AMD"].at(Horizon.SWING).impact_score < 0


def test_max_hops_respected():
    from marketlens.domain.issues import ImpactConfig

    imps = {i.ticker for i in compute_issue_impacts(issue([IssueEffect("MSFT", 1.0, ("x",), origin_impact=False)]), graph(), cfg=ImpactConfig(max_hops=1))}
    assert "TSM" not in imps and "NVDA" in imps


def test_hop_decay_values():
    d = HopDecay()
    assert (d.for_hops(0), d.for_hops(1), d.for_hops(2), d.for_hops(3)) == (1.0, 0.65, 0.35, 0.0)


def test_horizons_are_separate_and_priced_in_dampens_short_term():
    eff = [IssueEffect("NVDA", -1.0, ("China TAM ↓", "revenue ↓", "EPS revision risk ↑", "valuation pressure"))]
    base = {i.ticker: i for i in compute_issue_impacts(issue(eff, IssueCategory.EXPORT_CONTROL), graph())}["NVDA"]
    priced = {i.ticker: i for i in compute_issue_impacts(issue(eff, IssueCategory.EXPORT_CONTROL), graph(), {"NVDA": 90.0})}["NVDA"]
    assert {h.horizon for h in base.horizons} == set(Horizon)
    assert abs(priced.at(Horizon.IMMEDIATE).impact_score) < abs(base.at(Horizon.IMMEDIATE).impact_score) * 0.3
    assert abs(priced.at(Horizon.FUNDAMENTAL).impact_score) > abs(base.at(Horizon.FUNDAMENTAL).impact_score) * 0.85
    assert base.at(Horizon.FUNDAMENTAL).mechanism[0] == "China TAM ↓"


def test_rumor_weaker_than_confirmed():
    eff = [IssueEffect("NVDA", 1.0, ("x",))]
    c = compute_issue_impacts(issue(eff), graph())[0].at(Horizon.SWING).impact_score
    r = compute_issue_impacts(issue(eff, status=ConfirmedStatus.RUMOR), graph())[0].at(Horizon.SWING).impact_score
    assert abs(r) < abs(c)


def test_aggregate_bounded():
    eff = [IssueEffect("NVDA", 1.0, ("x",))]
    imps = compute_issue_impacts(issue(eff), graph()) * 20
    assert -100 <= aggregate_issue_score(imps) <= 100


def test_priced_in_estimate_and_confidence():
    full = estimate_priced_in(PricedInInputs(1, pre_event_return=0.12, daily_volatility=0.02, abnormal_volume_ratio=2.5, gap_reaction=0.08,
                                             implied_move=0.06, iv_rank=0.8, revision_already_in_direction=0.04, news_repetition=15, days_since_first_report=8))
    none = estimate_priced_in(PricedInInputs(1))
    low = estimate_priced_in(PricedInInputs(1, pre_event_return=-0.05, daily_volatility=0.02, abnormal_volume_ratio=1.0))
    assert full.value > 70 and full.confidence == 1.0 and full.label == "estimate"
    assert none.value is None and none.confidence == 0
    assert low.value < full.value and low.confidence < full.confidence


def snap(d10=0.4, oil=0.0, vix=30.0, sox=0.0, spx=-0.05, above=False) -> MacroSnapshot:
    f = lambda v: Fact(v, "t")  # noqa: E731
    return MacroSnapshot(T, {US10Y: MacroSeries(US10Y, f(4.5), change_20d=d10), WTI: MacroSeries(WTI, f(80), pct_change_20d=oil),
                             VIX: MacroSeries(VIX, f(vix)), SOX: MacroSeries(SOX, f(5000), pct_change_20d=sox),
                             SPX: MacroSeries(SPX, f(6000), pct_change_20d=spx, above_200d=above)})


def test_regimes_have_score_confidence_evidence():
    regs = detect_regimes(snap())
    assert len(regs) == 12
    ro = next(r for r in regs if r.regime == "Risk Off")
    assert ro.active and ro.evidence and 0 < ro.confidence <= 1
    assert primary_regime(regs) != "Neutral"
    assert all(r.confidence == 0 for r in detect_regimes(MacroSnapshot(T, {})))


def test_macro_transmits_through_exposure():
    moves = factor_moves(snap(d10=0.4, oil=0.2))
    growth = macro_impact(MacroExposure(rates=-0.6), moves)
    bank = macro_impact(MacroExposure(rates=0.4), moves)
    energy = macro_impact(MacroExposure(oil=0.9), moves)
    assert growth.net < 0 < bank.net and energy.net > 0
    assert any("역풍" in c[2] for c in growth.contributions)  # rates up = headwind for long-duration growth


def test_supplier_problem_barely_moves_customer():
    imps = {i.ticker: i for i in compute_issue_impacts(issue([IssueEffect("NVDA", -1.0, ("x",))], IssueCategory.EXPORT_CONTROL), graph())}
    nvda = imps["NVDA"].at(Horizon.SWING).impact_score
    tsm = imps["TSM"].at(Horizon.SWING).impact_score  # supplier of NVDA: demand shock transmits fully
    msft = imps.get("MSFT")  # customer of NVDA: weak transmission
    assert tsm < 0 and abs(tsm) > abs(nvda) * 0.5
    assert msft is None or abs(msft.at(Horizon.SWING).impact_score) < abs(nvda) * 0.2


def test_macro_factor_flows_only_to_companies():
    g = ExposureGraph([Node("XOM", NodeType.COMPANY, "x"), Node("COMMODITY:OIL", NodeType.COMMODITY, "oil")],
                      [Edge("XOM", "COMMODITY:OIL", EdgeType.EXPOSED_TO_COMMODITY, 0.9, 1.0, "s", D)])
    assert "COMMODITY:OIL" not in g.propagate(["XOM"]) or abs(g.propagate(["XOM"])["COMMODITY:OIL"].multiplier) == 0
    assert g.propagate(["COMMODITY:OIL"])["XOM"].multiplier > 0
