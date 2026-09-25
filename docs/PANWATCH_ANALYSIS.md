# PanWatch Source Analysis (Phase 0)

Analysed: `PanWatch-main` (uploaded zip, ~76k Python LOC, 373 `.py` files, 115 test files).
License: **MIT** (Copyright (c) 2026 sunxiao0721). Although MIT would permit reuse with attribution,
MarketLens **copies no PanWatch code**. Only design ideas were studied; every line of MarketLens was
written fresh for a different (US-only, fundamentals-first) purpose. PanWatch is not a dependency.

Format per feature: **A** how it is implemented · **B** strengths · **C** weaknesses ·
**D** value to MarketLens · **E** what must not be reused as-is · **F** MarketLens redesign.

---

## 1. Multi-Agent analysis / Analysts / Bull-Bear / Risk / Portfolio Manager
Files: `src/modules/automation/tradingagents/{agent,decision,toolkit_adapter,operations}.py`

- **A** Wraps the external *TradingAgents* LangGraph framework (4 analysts: market/social/news/fundamentals →
  bull/bear debate (`debate_rounds`) → trader → risk judge → PM `final_trade_decision`). `decision.py` then
  **parses the PM's free-text markdown** with regexes to recover a 5-tier rating (buy/overweight/hold/…),
  with a "fuzzy scan" fallback of the body text. Unparseable output becomes `REVIEW`.
- **B** Role separation, explicit debate, independent risk step, PM integration, cost estimate per run,
  idempotent triggers, progress recovery tests.
- **C** (1) The final action is extracted from prose (`final_trade_decision`) — brittle; the code comments
  admit the upstream `decision` "distorts" the body. (2) Agents fetch their own data through a toolkit
  (they can see/interpret raw data and invent numbers; nothing checks numeric claims). (3) The LLM output
  *is* the decision — there is no deterministic score/veto underneath it. (4) Heavy external dependency.
- **D** High: the committee *shape* (specialists → debate → risk → portfolio) is exactly what we want.
- **E** Free-text parsing of decisions; agents owning data access; LLM as decision maker; LangGraph dependency.
- **F** MarketLens `application/committee`: agents receive a **pre-built evidence pack** (IDs + values),
  answer in **strict Pydantic schemas** (`extra="forbid"`), a **numeric-claim guard** rejects numbers not in
  the pack, and the committee may only (a) adjust confidence within ±10, (b) *downgrade* the deterministic
  action. Debate is fixed at 2 rounds, each argument cites evidence IDs.

## 2. Factor scoring
Files: `src/modules/strategy/strategy_engine.py` (`_compute_factor_breakdown`, `_normalize_action_view`),
`entry_candidates.py` (`_derive_market_scan_decision`, `_score_market_scan_candidate`, `ACTION_BASE_SCORE`)

- **A** A scan derives an `action` from technical points (MA alignment, MACD cross, volume ratio, % change,
  pullback to support). The candidate score then **starts from `ACTION_BASE_SCORE[action]`**. In the factor
  breakdown, `regime_multiplier` depends on `action in ("buy","add")`, and the final score is **capped by
  the action** (`if action in ("hold","watch"): final_score = min(final_score, 65)`).
- **B** Breakdown dict is persisted; factor weights are externalised (`FactorWeight`) and audited.
- **C** **Decision → Score circularity**: the action is an input to the score that is supposed to justify
  the action. Momentum/technical-dominant candidate discovery (MACD golden cross, +1.5..8.5% daily move,
  turnover). Many magic numbers inline.
- **D** Breakdown persistence and externalised weights: yes. Everything else: no.
- **E** Action-dependent scores, technical-first discovery, inline constants.
- **F** `domain/scoring` computes 8 components from facts only; the decision module imports scoring, never
  the reverse (enforced by `tests/architecture`). Technicals feed timing/entry only (10 pts) and every
  threshold lives in versioned `config/scoring_model.toml`.

## 3. IC / IR calculation
File: `src/modules/strategy/factor_eval.py`

- **A** Pure-Python Spearman (average ranks) between factor snapshot values and forward returns; IR as
  mean/std of per-snapshot-date IC series; excludes samples whose horizon hasn't elapsed.
- **B** Point-in-time intent, small dependency surface, per-date IC for IR, min-sample guards.
- **C** Horizon cutoff uses **calendar days** (`today - timedelta(days=horizon)`) while horizons are trading
  days → a 5-trading-day outcome can be admitted after 5 calendar days (look-ahead leakage risk around
  weekends/holidays). Broad `except Exception` returns `{}` silently.
- **D** High (the method is standard and correct in spirit).
- **E** Calendar-day maturity; swallowing errors.
- **F** `domain/evaluation/ic.py` with an explicit NYSE trading calendar: an outcome is *mature* only when
  `h` trading sessions have closed after the recommendation session. IR returns `None` (N/A) below
  `MIN_IC_PERIODS`. Errors propagate.

## 4. Calibration
File: `src/modules/strategy/factor_calibration.py`

- **A** Converts IC/IR into a target weight `1 + beta·term`, EMA-blends into `FactorWeight`, clamps to
  [0.5, 1.5], writes `FactorWeightHistory`. Pinning and per-factor auto-calibration switch. Applies directly
  to production.
- **B** Audit trail, pinning, EMA smoothing, penalty-factor sign handling, raw-factor IC (not weighted).
- **C** No shadow period, no out-of-sample comparison, no drawdown gate; min sample 20; production weights
  change in one step.
- **D** High for audit/pinning ideas.
- **E** Direct-to-production updates.
- **F** `domain/calibration`: proposal → **SHADOW** (both models score the same live candidates) →
  promotion only if samples ≥ `MIN_CALIBRATION_SAMPLES` (100), composite IC improves by
  `MIN_IC_IMPROVEMENT`, hit-rate/downside not worse; per-cycle change ≤ `MAX_WEIGHT_CHANGE` (5% relative).
  Segment calibration falls back to global when a segment is under-sampled.

## 5. Paper trading
Files: `src/modules/paper_trading/paper_trading_engine.py`, `strategy/backtest/cost_model.py`

- **A** Opens positions from `StrategySignalRun` rows at the **current quote** during trading time, sizes
  by rank score, cost model with bps slippage + fees, stop/target fallbacks (`entry*0.92`, `entry*1.15`).
- **B** Cost model separation, per-market cash allocation, notification hooks.
- **C** Hard-coded fallback stop/targets detached from the analysis; entry at "current" quote rather than a
  provably post-recommendation price; no MAE/MFE; exit reasons limited.
- **D** Medium-high (cost model idea, per-position audit).
- **E** Magic stop/target fallbacks; sizing by rank score.
- **F** `domain/paper`: entry = **first tradable bar strictly after the recommendation timestamp**, fill at
  its open + slippage + half-spread; stop/targets come from the Entry Engine (no plan → no paper trade);
  exits: stop (gap-aware), T1 (half), T2, time, thesis invalidation, downgrade. Metrics incl. MAE/MFE,
  expectancy, profit factor, SPY excess.

## 6. Recommendation / prediction outcome evaluation
File: `src/modules/research/prediction_outcome.py`, `entry_candidates.evaluate_entry_candidate_outcomes`

- **A** Picks close on/before target day from klines; counts trading days from the last visible bar.
- **B** Uses last bar visible at suggestion time as the anchor (good PIT instinct).
- **C** Mixed calendar/trading-day logic across modules; broad excepts.
- **F** One `OutcomeTracker` with horizons 1/5/20/60 trading days; stores factor scores at recommendation
  time; values are written only once mature and never overwritten.

## 7. Provider health / failover / circuit breaker
Files: `packages/marketdata` (vendor engine, `_FAIL_UNTIL` negative cache), `platform/ai/ai_failover.py`,
`administration/api/datasources.py`

- **A** Negative-cache cooldown dict (`_FAIL_UNTIL`, 60s) per vendor/model; error classification
  (param / switch / fatal) for LLMs; datasource admin page with a "test" endpoint.
- **B** Error classification is smart (don't fail over on prompt errors); cooldown avoids hammering.
- **C** Module-global mutable state; no HALF_OPEN probe semantics; failover silently picks whichever vendor
  answered — **no conflict detection** between vendors; health is not a persisted time series.
- **F** `infrastructure/resilience.py`: proper CLOSED/OPEN/HALF_OPEN breaker with injectable clock,
  exponential backoff + jitter, rate-limit awareness; `providers/router.py` failover that can
  cross-check a secondary and returns `CONFLICTING` instead of silently choosing; health states
  HEALTHY/DEGRADED/RATE_LIMITED/STALE/DOWN persisted per provider.

## 8. Architecture boundary tests
File: `tests/test_architecture_boundaries.py`

- **A** AST-parses imports: `platform` must not import `modules`; modules must not import other modules'
  `models/repository/api`.
- **B** Cheap, effective guard against erosion.
- **F** Adopted and extended: domain ↛ providers/infrastructure/api/frontend/httpx/sqlalchemy;
  scoring ↛ LLM/decision; decision ↛ network; committee ↛ database/repositories; plus *runtime* AI-safety
  tests (agent output cannot mutate frozen market data or scores).

## 9. Audit trail / logging / observability
Files: `platform/observability/{log_context,log_handler,otel}.py`, `research/analysis_history.py`

- **A** ContextVar trace ids, DB log handler, optional OTEL export; analysis history stored as markdown.
- **B** Trace ids; persisted history.
- **C** History stores rendered text rather than the full input snapshot → cannot replay a past decision.
- **F** Every recommendation stores the full **input snapshot JSON** + versions (scoring, decision, prompt,
  config, provider, schema); `application/replay.py` recomputes from the snapshot only and asserts
  equality. JSON structured logs with an `event` enum (SCAN_STARTED … MODEL_PROMOTED) and secret redaction.

## 10. Backtesting / model performance tracking
Files: `strategy/backtest/{engine,metrics,cost_model}.py`, `test_strategy_outcome_efficiency.py`

- **B** Separate cost model and metrics module. **C** Uses current-universe data (survivorship), no data
  vintages. **F** Universe snapshots with `listed_at/delisted_at` and fundamentals keyed by `filed_date`
  (vintage) so `as_of` queries reproduce what was knowable.

## 11. Structured output
File: `research/signals/structured_output.py`
- **A** Tries to find a JSON block between HTML-comment tags or code fences in free text; alias table.
- **C** Tolerant parsing hides model drift. **F** Strict schema validation; invalid output → the agent
  report is marked `INVALID_OUTPUT` and excluded from consensus (never "best-effort" parsed).

## 12. Other observations
- 188 occurrences of `except Exception:` with continue/None fallbacks in `src/` — silent fallback culture.
- Several 2000+ line modules (`strategy_engine.py`, `entry_candidates.py`, `migrations.py`) — god modules.
- Market focus is A-share/HK; US support is secondary; no SEC/FRED integration, no sector models, no
  analyst revision engine, no exposure graph.

## Summary table

| Capability | Adopt idea | Reject | MarketLens component |
|---|---|---|---|
| Multi-agent committee | ✅ roles, debate, risk, PM | free-text decision parsing, LLM decides | `application/committee` |
| Bull/Bear | ✅ | unbounded rounds | fixed 2 rounds, evidence-cited |
| Risk manager | ✅ independent review | upgrade ability | downgrade-only |
| Portfolio manager | ✅ | — | deterministic exposure + advisory size ≤ cap |
| Factor scoring | weights externalised | action→score circularity, momentum scan | `domain/scoring` |
| IC/IR | ✅ Spearman, per-date IR | calendar-day maturity | trading-calendar maturity |
| Calibration | ✅ audit, pin | direct-to-prod | shadow + promotion gates |
| Paper trading | ✅ cost model | current-quote entry, magic stops | next-bar entry, plan-driven exits |
| Provider failover | ✅ error classes, cooldown | silent vendor choice | breaker + conflict detection |
| Boundary tests | ✅ | — | extended + runtime AI-safety tests |
