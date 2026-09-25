# Architecture

MarketLens is a local, Windows-first decision-support application for US equities. It **never places
orders**. Pipeline order is fixed:

```
Raw data (providers) → validation / quality → normalisation (canonical domain types) → structured facts
→ fundamentals → earnings & revisions → sector model → valuation → macro & regimes → news → issues
→ exposure graph propagation → issue impact (4 horizons) → priced-in → catalysts / event risk
→ technical & entry plan → deterministic score → decision (vetoes, hysteresis, material change)
→ ranking → AI committee (top N only) → bull/bear → risk review → portfolio review → final action
→ persistence (audit + point-in-time snapshot) → paper trading → outcomes → IC/IR → calibration
```

## Layers (backend/marketlens)

| Layer | Package | Responsibility | May import |
|---|---|---|---|
| Domain | `domain/` | Pure, deterministic calculations on frozen dataclasses (indicators, fundamentals, earnings, valuation, sector models, macro/regimes, exposure graph, issues, priced-in, catalysts, entry, scoring, decision, what-changed, portfolio, paper, evaluation, calibration, scenarios, NYSE calendar). | stdlib only |
| Providers | `providers/` | Contracts (`contracts.py`), MOCK implementations, LIVE implementations (SEC EDGAR, FRED, Finnhub, Polygon), `Unavailable` placeholders, LLM providers, failover router. | domain, infrastructure.resilience/health/logging |
| Infrastructure | `infrastructure/` | SQLAlchemy models/repository/session, circuit breaker, retry, token bucket, provider health, JSON logging + secret redaction. | domain |
| Application | `application/` | Orchestration: data access + caching, scanner, pure `pipeline.run_analysis`, issue engine, evidence, committee, services, evaluation/calibration, replay, codec. | all of the above |
| API | `api/` | FastAPI routes (thin; no business logic). | application |
| Workers | `workers/` | CLI and in-process scheduler. | application |
| Frontend | `frontend/` | React + TypeScript (strict) UI, Tauri desktop shell. | HTTP API only |

Boundaries are enforced by `tests/architecture/test_boundaries.py` (AST import analysis): the domain
cannot import providers/infrastructure/api/frameworks, scoring cannot import the decision engine or LLM
code, the decision/scoring/entry modules cannot import network libraries, the committee cannot import
the database or data providers, providers cannot import the application/API, and there are no bare
`except:` or silent `except …: pass` blocks.

## Key design decisions
- **FACTS → SCORE → DECISION**: `ScoringInputs` has no action field; decisions consume score cards,
  never the reverse (ADR-0002).
- **Pure per-ticker pipeline**: `application/pipeline.run_analysis(AnalysisInputs, ModelConfig)` is
  deterministic and side-effect free. `AnalysisInputs` is the point-in-time snapshot stored with each
  recommendation; replay decodes it and recomputes (ADR-0003).
- **AI is an enhancement layer**: the committee only downgrades actions and adjusts confidence within
  ±10; it never changes prices, facts or scores (ADR-0001).
- **Cheap → expensive scanning**: the LLM runs on at most `AI_COMMITTEE_TOP_N` names (ADR-0004).
- **Sector models as versioned data** (`config/sector_models.toml`, ADR-0005).
- **MOCK and LIVE never mix**: `ProviderChain` raises `ModeMixError`; the mock LLM is refused in LIVE
  mode; LIVE gaps are MISSING (ADR-0006).
- **Calibration via shadow models** with promotion gates (ADR-0007).

## Runtime
`python -m marketlens serve` starts FastAPI (uvicorn) on 127.0.0.1:8765, applies Alembic migrations and
serves the built UI from `frontend/dist`. The Tauri shell starts the same backend as a sidecar
(`marketlens-backend.exe`, built with PyInstaller) and shows the UI in WebView2.

## Data storage
SQLite by default (`data/marketlens_mock.db` or `data/marketlens.db`; `%LOCALAPPDATA%\MarketLens` when
packaged). All timestamps are stored as UTC (`UTCDateTime` type decorator rejects naive datetimes).
PostgreSQL works via `MARKETLENS_DATABASE_URL`. Schema is managed by Alembic (`backend/alembic`).

Tables: securities, price_bars, fundamentals_quarterly (keyed by filed_date vintage), scan_runs,
recommendations (full audit + inputs snapshot + versions), committee_reports, llm_calls, llm_cache,
recommendation_outcomes, factor_snapshots, paper_positions, issues, provider_health, model_versions,
calibration_runs, portfolio_holdings, watchlist, app_settings.

## Versioning
Every recommendation records `scoring_model_version`, `decision_model_version`, `agent_prompt_version`,
`provider_version`, `config_version` (+ SHA of the TOML files) and `schema_version`, plus the exact TOML
texts and weights used (`model_config_snapshot`).

## Observability
JSON logs with event names: SCAN_STARTED, SCAN_FINISHED, PROVIDER_FAILED, PROVIDER_RECOVERED,
PROVIDER_CONFLICT, ISSUE_CREATED, SCORE_CREATED, DECISION_CREATED, COMMITTEE_STARTED,
COMMITTEE_FINISHED, COMMITTEE_UNAVAILABLE, AGENT_OUTPUT_REJECTED, PROMPT_INJECTION_DETECTED, RISK_VETO,
RECOMMENDATION_CHANGED, PAPER_POSITION_OPENED, PAPER_POSITION_CLOSED, CALIBRATION_RUN, MODEL_PROMOTED,
OUTCOMES_UPDATED. Secrets are redacted by a logging filter.

## Timezones
DB: UTC. Market logic: America/New_York via `zoneinfo` (DST-safe, NYSE holidays/early closes computed
by rule). UI: the user's local time plus ET in parentheses.
