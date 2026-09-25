# Paper Trading (`domain/paper.py`, `application/evaluation_service.py`)

No real orders exist anywhere in MarketLens. When `ENABLE_PAPER_TRADING=true`, every recommendation
whose final action is BUY, BUY SMALL or ADD and that has a price plan creates a PENDING paper position.

## Entry — no future prices
- The entry is the **first session whose open is strictly after the recommendation timestamp**
  (pre-market recommendation → same-day open; during/after the session → next session's open).
- Fill = open × (1 + slippage_bps + half-spread). Slippage default 5 bps; half-spread from the stored
  bid/ask at recommendation time, else 2 bps.
- If the open gaps below the stop, the trade is skipped.
- Simulation only sees bars up to `as_of` (the last completed session).

## Position record
ticker, entry time/day, entry price, quantity (fixed notional, default $10,000), score, confidence,
action, market regime, sector, stop, target 1, target 2, thesis, model version.

## Exits
Stop (gap-aware: fills at min(open, stop)), Target 1 (sells 50%, moves stop to breakeven), Target 2,
Time exit (60 trading days), Thesis invalidation and Recommendation downgrade (exit at the next open
after a later recommendation reports the invalidation/downgrade). Sells pay slippage + half-spread.
Stops are checked before targets within a bar (conservative).

## Metrics
Win rate, average/median return, profit factor, expectancy, max drawdown (equity curve with 10% of
equity per trade), average holding days, MAE, MFE, excess return vs SPY over the same holding period.
Also by sector and by regime. Shown on Model Performance.
