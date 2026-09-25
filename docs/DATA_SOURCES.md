# Data Sources

Principle: every number comes from a provider, an official source, or a deterministic calculation.
Missing data is `null` / MISSING / N/A — never imagined, never silently replaced by mock data.

## Provider contracts (`providers/contracts.py`)
UniverseProvider, PriceProvider, FundamentalProvider, AnalystProvider, NewsProvider, MacroProvider,
OptionsProvider, ShortInterestProvider, InsiderProvider, InstitutionalProvider, CalendarProvider.
All return canonical domain types; failures raise `ProviderUnavailable`, `RateLimited`,
`ProviderDataError` or `NotSupported`.

## LIVE implementations

| Kind | Provider | Key / requirement | What is implemented | Notes |
|---|---|---|---|---|
| universe | SEC EDGAR `company_tickers_exchange.json` | `SEC_USER_AGENT` (contact e-mail) | NASDAQ / NYSE / NYSE American listings, CIK map | Sector, industry and market cap are **not** in this file → Unknown/None (a sector/market-cap source is a remaining gap; stage 1 excludes unknown market caps). |
| fundamentals | SEC EDGAR XBRL companyfacts | `SEC_USER_AGENT` | Revenue, GP, OI, NI, diluted EPS, OCF, CapEx, SBC, D&A, cash, debt, equity, diluted shares, inventory | Point-in-time: first filing wins (restatements do not overwrite), `filed_date` kept; YTD cash-flow → quarterly; Q4 = FY − Q1..Q3. IFRS/20-F filers → NotSupported (ADR data needs another FundamentalProvider). |
| price (quotes) | Finnhub `/quote` | `FINNHUB_API_KEY` | Real-time/last price with timestamp; session derived from timestamp | Cross-checked against a secondary price source when configured; disagreement > 2% → CONFLICTING. |
| price (bars) | Polygon aggregates / grouped daily | `POLYGON_API_KEY` | Per-ticker daily bars; grouped-daily for whole-market pulls | Free tier is rate-limited (~5 req/min); a bar-sync job using grouped daily is recommended for large universes. |
| news | Finnhub company-news | `FINNHUB_API_KEY` | Headlines/summaries per ticker | Bodies are untrusted text. |
| calendar | Finnhub earnings calendar | `FINNHUB_API_KEY` | Earnings dates | Macro release dates (FOMC/CPI/…) are not yet sourced live. |
| analyst | Finnhub `/stock/earnings` | `FINNHUB_API_KEY` | EPS actual vs estimate history | **Estimate revisions (7/30/90D), revenue consensus, guidance and valuation history require a licensed estimates feed — not configured → MISSING.** |
| macro | FRED | `FRED_API_KEY` | Fed funds, 2Y/10Y/30Y, CPI/Core CPI YoY, PCE/Core PCE YoY, payrolls change, unemployment, GDP, broad USD index, WTI, Brent, VIX, HY OAS, S&P 500, NASDAQ Composite | "." values are skipped, not interpolated; staleness per series frequency. Gold, NDX, RUT, SOX and breadth are not available from FRED → MISSING in LIVE. |
| options | — | licensed provider needed | — | `UnavailableProvider` → MISSING |
| short interest / insider / institutional | — | licensed provider / SEC Form 4 & 13F parsers (planned) | — | `UnavailableProvider` → MISSING |

LLM providers: Anthropic (official SDK, structured outputs), OpenAI-compatible HTTP (OpenAI, Gemini's
OpenAI endpoint, local servers such as Ollama/LM Studio). See AI_COMMITTEE.md.

## MOCK implementations
`providers/mock/` generates a deterministic synthetic market (default 600 securities, including
well-known tickers so the pipeline can be exercised). Every company name ends with "(MOCK)", every
quote/fact is tagged `DataMode.MOCK` and source `mock`, and the UI shows a red **MOCK DATA** banner.
Price paths are anchored to a fixed date so worlds created at different clocks share history — this
lets `marketlens simulate` replay past weeks without look-ahead.

## Curated reference data (not market data)
- `config/exposure_graph.toml` — company/supplier/customer/competitor edges and macro sensitivities,
  with weight, confidence, source and last-updated date.
- `config/theses.toml` — thesis-invalidation conditions.
- `config/sector_models.toml` — sector model definitions.

## Licensing
No scraping. Only APIs/feeds the user is licensed for. Respect provider terms and rate limits
(token-bucket limiters per provider). Without a key/licence the feature is MISSING.
