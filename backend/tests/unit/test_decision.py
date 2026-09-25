from marketlens.domain.decision import DecisionThresholds, apply_downgrade, bounded_confidence, decide
from marketlens.domain.enums import Action, DataQuality, HardVeto
from marketlens.domain.facts import DataQualityReport
from tests.helpers import card, ctx, plan

TH = DecisionThresholds()


def test_buy_when_score_high_and_in_zone():
    d = decide(card(85), plan(), ctx(), TH)
    assert d.action == Action.BUY and not d.vetoes


def test_wait_when_score_high_but_price_above_max_buy():
    d = decide(card(85), plan(price=110, max_buy=102), ctx(), TH)
    assert d.action == Action.WAIT


def test_buy_small_band():
    assert decide(card(74), plan(), ctx(), TH).action == Action.BUY_SMALL
    assert decide(card(60), plan(), ctx(), TH).action == Action.WATCH


def test_stale_price_blocks_buy():
    d = decide(card(95), plan(), ctx(price_quality=DataQuality.STALE), TH)
    assert d.action == Action.DATA_INSUFFICIENT and HardVeto.STALE_PRICE in d.vetoes


def test_missing_core_data_blocks_buy():
    dq = DataQualityReport(fields=(("price", DataQuality.FRESH), ("fundamentals", DataQuality.MISSING)), core_missing=("fundamentals",))
    assert decide(card(95), plan(), ctx(data_quality=dq), TH).action == Action.DATA_INSUFFICIENT


def test_severe_conflict_blocks_buy():
    d = decide(card(95), plan(), ctx(severe_conflicts=("price: 100 vs 120",)), TH)
    assert d.action == Action.DATA_INSUFFICIENT and HardVeto.SEVERE_DATA_CONFLICT in d.vetoes


def test_thesis_invalidated():
    assert decide(card(95), plan(), ctx(thesis_invalidated=True), TH).action == Action.WAIT
    assert decide(card(95), plan(), ctx(thesis_invalidated=True, held=True), TH).action == Action.SELL


def test_liquidity_veto():
    assert decide(card(95), plan(), ctx(avg_dollar_volume=1e6), TH).action == Action.WAIT


def test_extreme_event_risk_caps_to_buy_small():
    d = decide(card(95), plan(), ctx(event_risk_level="EXTREME"), TH)
    assert d.action == Action.BUY_SMALL and d.size_limit == "SMALL"


def test_hysteresis_keeps_buy_between_exit_and_enter():
    # 78 is below BUY enter (80) but above BUY exit (76)
    assert decide(card(78), plan(), ctx(previous_action=None), TH).action == Action.BUY_SMALL
    assert decide(card(78), plan(), ctx(previous_action=Action.BUY, material_changes=("x",)), TH).action == Action.BUY
    assert decide(card(75), plan(), ctx(previous_action=Action.BUY, material_changes=("x",)), TH).action == Action.BUY_SMALL


def test_no_flip_flop_without_material_change():
    d = decide(card(66), plan(), ctx(previous_action=Action.BUY_SMALL, material_changes=()), TH)
    assert d.action == Action.BUY_SMALL and d.suppressed_change
    d2 = decide(card(66), plan(), ctx(previous_action=Action.BUY_SMALL, material_changes=("price left the buy zone",)), TH)
    assert d2.action == Action.WATCH and not d2.suppressed_change


def test_veto_overrides_suppression():
    d = decide(card(90), plan(), ctx(previous_action=Action.BUY, material_changes=(), price_quality=DataQuality.STALE), TH)
    assert d.action == Action.DATA_INSUFFICIENT


def test_held_positions():
    assert decide(card(40), plan(), ctx(held=True), TH).action == Action.SELL
    assert decide(card(50), plan(), ctx(held=True), TH).action == Action.REDUCE
    assert decide(card(65), plan(), ctx(held=True), TH).action == Action.HOLD
    assert decide(card(85), plan(price=96, max_buy=102, add=(95, 98)), ctx(held=True), TH).action == Action.ADD


def test_portfolio_cap_downgrades_buy():
    assert decide(card(90), plan(), ctx(portfolio_size_cap="SMALL"), TH).action == Action.BUY_SMALL


def test_downgrade_only():
    assert apply_downgrade(Action.BUY, Action.BUY_SMALL) == (Action.BUY_SMALL, True)
    assert apply_downgrade(Action.BUY, Action.WAIT) == (Action.WAIT, True)
    assert apply_downgrade(Action.WAIT, Action.BUY) == (Action.WAIT, False)
    assert apply_downgrade(Action.DATA_INSUFFICIENT, Action.BUY) == (Action.DATA_INSUFFICIENT, False)
    assert apply_downgrade(Action.BUY_SMALL, Action.BUY) == (Action.BUY_SMALL, False)
    assert apply_downgrade(Action.BUY, None) == (Action.BUY, False)


def test_confidence_bounded():
    assert bounded_confidence(70, 50, 10) == 80
    assert bounded_confidence(70, -50, 10) == 60
    assert bounded_confidence(95, 10, 10) == 100


def test_data_insufficient_confidence_low():
    assert decide(card(95), plan(), ctx(price_quality=DataQuality.MISSING), TH).confidence <= 30
