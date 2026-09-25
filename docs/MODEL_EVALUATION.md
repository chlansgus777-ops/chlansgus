# Model Evaluation

## Recommendation outcomes
For each recommendation, forward returns at **1D / 5D / 20D / 60D** (close-to-close) plus SPY return and
excess return. The base is the close of the **first session that closes after the recommendation time**,
so no pre-recommendation move leaks in. An outcome is written only once `h` trading sessions have
closed after the base (`domain/evaluation.is_mature`), and never overwritten.

## Factor outcome storage
At decision time: Fundamental, Valuation, Earnings/Revision, Catalyst, Macro, Technical, Risk, Entry
sub-scores, issue score and total (`factor_snapshots`).

## IC / IR
- IC: Spearman rank correlation between a factor and forward return (5D / 20D / 60D), requires ≥ 30
  mature samples else N/A.
- IR: mean(IC)/std(IC) over per-date cross-sectional ICs (≥ 5 names per date, ≥ 5 dates) else N/A.

## Look-ahead & survivorship
- Trading-day maturity (not calendar days — PanWatch's calendar-day cutoff was a leak risk).
- Point-in-time fundamentals (`filed_date`), bars filtered to `as_of`, quotes from the future flagged
  CONFLICTING.
- Universe entries keep `listed_at` / `delisted_at`; delisted names stay in history and are excluded
  from the active universe only.
- Tests: `tests/backtest/`, `tests/golden/test_future_*`.

## Dashboard (Model Performance)
Periods 30D / 90D / 1Y / All: recommendation hit rate and average return per horizon, excess return,
paper equity curve and metrics, score-bucket and confidence-bucket performance, sector and regime
performance, factor IC and IR, calibration history.

## Replay
`GET /api/recommendations/{id}/replay` or `python -m marketlens replay <id>` recomputes a recommendation
from its stored snapshot and stored config texts and reports whether score, action and input
fingerprint match.
