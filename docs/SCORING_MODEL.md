# Scoring Model (`domain/scoring.py`, `config/scoring_model.toml`)

Current version: **scoring-1.1.0** (1.1.0 changed exposure-graph transmission to be direction-aware).

## Components (points; normalised to 0–100)
| Component | Weight | Inputs |
|---|---|---|
| Fundamental | 25 | Sector-model fundamental rules (see SECTOR_MODELS.md) |
| Valuation | 15 | Sector valuation rules 45%, own-history percentile 20%, premium to peers 15%, forward earnings yield − 10Y 20% |
| Earnings & Revision | 15 | EPS revision breadth 45%, revenue revision direction 15%, last-report result quality 30%, expectation bar 10% |
| Catalyst | 10 | Net issue impact (2–6W) 60%, setup into the next earnings 40% |
| Macro | 10 | 0.5 + 0.5 × macro impact (factor moves × company sensitivities); −0.1 in Risk-Off for beta > 1.2 |
| Technical | 10 | Trend quality 40%, 6M relative strength 30%, RSI location 15%, volume trend 15% — timing only |
| Risk | 10 | Realised volatility 30%, leverage 20%, liquidity 15%, event risk 20%, short interest 10%, data completeness 5% (higher = lower risk) |
| Entry R/R | 15 | R/R at current price 70% (0.5→0, 3.0→1), position vs buy zone 30% |

Each component yields a sub-score in [0,1]; points = sub-score × weight; total = Σ points / Σ weights ×
100. Weights sum to 110 by design; normalisation makes the total 0–100. A component without data gets
the conservative sub-score `missing_component_subscore = 0.35` and is flagged `available=false` (and
lowers completeness).

Every component returns `reasons` (text, sign, evidence refs) that the UI shows next to the breakdown
and links to evidence IDs.

## Invariants (tested)
- `ScoringInputs` has no action/decision field; `scoring.py` never imports the decision engine.
- Deterministic: same inputs → same total.
- Technical weight ≤ 10% of the total.
- Weights are validated (exactly the 8 components, non-negative) and versioned.

## Decision engine (`domain/decision.py`)
Actions: BUY, BUY SMALL, ADD, HOLD, WATCH, WAIT, REDUCE, SELL, DATA INSUFFICIENT.

1. Hard vetoes: STALE_PRICE, MISSING_CORE_DATA (core: price, price history, fundamentals; or completeness
   < 0.6), SEVERE_DATA_CONFLICT (conflict on a core field), THESIS_INVALIDATED, UNACCEPTABLE_LIQUIDITY
   (< $20M ADV), EXTREME_EVENT_RISK.
   - stale/missing/conflicting → DATA INSUFFICIENT; thesis invalidated → WAIT (SELL if held);
     liquidity → WAIT (HOLD if held); extreme event risk → BUY capped at BUY SMALL, no ADD.
2. Bands with **hysteresis**: BUY enter ≥ 80 / exit < 76; BUY SMALL enter ≥ 72 / exit < 68; WATCH band
   ≥ 55. Held: SELL < 45, REDUCE < 55, ADD when BUY-grade and price inside the add zone, else HOLD.
   BUY / BUY SMALL also require price ≤ max buy (else WAIT).
3. **Material-change gate**: if the action would change but `what_changed` found no material change,
   the previous action is kept (`suppressed_change=true`). Material: new earnings, new guidance, revision
   Δ ≥ 2pp, new major issue, regime shift, 10Y ±25bp, price entering/leaving the buy zone, stop breach,
   thesis invalidation, R/R Δ ≥ 0.5, score Δ ≥ 5. Vetoes always override.
4. Portfolio cap (deterministic) may cap BUY → BUY SMALL / WATCH.
5. Confidence = 45 + 35 × completeness² + 20 × distance-from-nearest-threshold (≤ 30 for DATA INSUFFICIENT).
6. `apply_downgrade`: the committee/risk review may only move along `ALLOWED_DOWNGRADES`
   (BUY→BUY SMALL/WAIT/WATCH, BUY SMALL→WAIT/WATCH, ADD→HOLD/WAIT, HOLD→REDUCE, REDUCE→SELL).

## Entry engine (`domain/entry.py`)
Stop = nearest support (swing low, SMA20/50/200, anchored VWAP) within 3 ATR minus 0.5 ATR, else price −
2 ATR. Target 1 = first resistance ≥ 1 ATR above price (incl. 52W high), else +3 ATR; Target 2 = next
resistance or +5 ATR. **Max buy = (T1 + min_rr·stop)/(1 + min_rr)** with min_rr = 2.0. Ideal entry =
support + 0.25 ATR (≤ max buy). Add zone = support … +0.5 ATR. Thesis invalidation is evaluated
separately from the price stop.
