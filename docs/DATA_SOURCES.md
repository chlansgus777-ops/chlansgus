# Data Sources

Principle: every number comes from a provider, an official source, or a deterministic calculation.
Missing data is `null` / MISSING / N/A — never imagined, never silently replaced by mock data.

## Provider contracts (`providers/contracts.py`)
UniverseProvider, PriceProvider, FundamentalProvider, AnalystProvider, NewsProvider, MacroProvider,
OptionsProvider, ShortInterestProvider, InsiderProvider, InstitutionalProvider, CalendarProvider.
All return canonical domain types; failures raise `ProviderUnavailable`, `RateLimited`,
`ProviderDataError` or `NotSupported`.

## LIVE implementations (free, official sources only)

Status words: **IMPLEMENTED** (code path + fixture/contract tests), **LIVE VERIFIED** (only after
`marketlens live-verify` succeeded against the real API — all 13 categories passed together in GitHub Actions
run 36253710550 on 2026-09-26 with free keys; the smoke test checks a few tickers per category, not every company), **PARTIAL**, **ACCUMULATING** (own history growing),
**UNAVAILABLE**. No scraping, no unofficial endpoints, no multiple free accounts, no rate-limit evasion.

| Data | Provider (free) | Key | Status | Notes |
|---|---|---|---|---|
| Universe | SEC `company_tickers_exchange.json` | `SEC_USER_AGENT` | IMPLEMENTED | NASDAQ/NYSE/NYSE American; delisting inferred from disappearance (kept in history). No ETF flag / share-class master yet. |
| Sector/industry | SEC submissions (SIC) | `SEC_USER_AGENT` | PARTIAL | SIC → sector/industry mapping; foreign-issuer flag from 20-F/6-K forms. |
| Market cap | SEC dei shares × stored close, split-normalised | `SEC_USER_AGENT` | PARTIAL | No ADR ratio → ADR market cap not reliable. |
| Quotes | Finnhub `/quote` | `FINNHUB_API_KEY` | IMPLEMENTED | Marked DELAYED (no real-time guarantee on free plan). No bid/ask, no dedicated extended-hours feed. |
| Daily bars | Polygon grouped daily (bulk) | `POLYGON_API_KEY` | IMPLEMENTED | ~5 req/min free: one request per day for the whole market; days without rows tracked; sync reports SYNC_COMPLETE / SYNC_PARTIAL. |
| Splits | Polygon `/v3/reference/splits` (bulk) | `POLYGON_API_KEY` | IMPLEMENTED | Append-only `corporate_actions`; stored pre-split bars rescaled once; EPS/shares normalised to the price basis. |
| Fundamentals (US GAAP) | SEC XBRL companyfacts | `SEC_USER_AGENT` | IMPLEMENTED | Concept fallbacks chosen per period. Q4 = FY − (Q1+Q2+Q3); Q4 diluted EPS = net income / Q4 shares when tagged, else FY EPS − (Q1+Q2+Q3 EPS) (approximation: EPS is not additive when the share count moved). First-reported values kept; later different values stored as revisions; analysis uses LATEST_KNOWN_AS_OF (restatement visible only after it was filed). Cash-flow restatements: first reported only. |
| Fundamentals (20-F / IFRS) | SEC XBRL `ifrs-full` | `SEC_USER_AGENT` | PARTIAL | ANNUAL_ONLY, reporting currency; QUARTERLY_DATA_UNAVAILABLE; per-share valuation not computed without a verified ADR ratio. |
| Bank KPIs | SEC XBRL us-gaap | `SEC_USER_AGENT` | PARTIAL | Loans, deposits, provisions, net charge-offs, goodwill/intangibles → loan/deposit growth, provision/loans, charge-off rate, ROTCE, ROE, P/TBV. CET1 only if tagged; NIM not computed (FFIEC not connected). |
| Consensus (next report) | Finnhub earnings calendar | `FINNHUB_API_KEY` | IMPLEMENTED + ACCUMULATING | One snapshot per day for every upcoming report (append-only `estimate_snapshots`) → MarketLens's own 7/30/60/90-day revisions; before enough history: "ACCUMULATING n/90". |
| Consensus FY1/FY2, revisions | Alpha Vantage `EARNINGS_ESTIMATES` | `ALPHAVANTAGE_API_KEY` | IMPLEMENTED (response shape seen live: per-period rows) | ~25 requests/day: final candidates only, cached 3 days, daily budget; provider-reported 7/30/60/90-day consensus, analyst count, high/low. The real answer lists every fiscal period with horizon "fiscal year"/"fiscal quarter"; current/next are decided by the period-end date. Field contract checked on every used row; an answer with no usable row is an error, never an empty success. |
| Provider disagreement | Finnhub vs Alpha Vantage | — | IMPLEMENTED | Same quarter only; CONSISTENT / DATA_CONFLICT / SEVERE_DATA_CONFLICT; never averaged. |
| Guidance | SEC 8-K Item 2.02, Exhibit 99 | `SEC_USER_AGENT` | PARTIAL | Deterministic extraction with the source sentence and URL; GUIDANCE_UNCLEAR / NO_GUIDANCE; compared only with a consensus snapshot taken before the release. No LLM extraction. |
| Earnings history | Finnhub `/stock/earnings` (actual vs consensus EPS, last 4 quarters free) + SEC 8-K Item 2.02 acceptance time | `FINNHUB_API_KEY`, `SEC_USER_AGENT` | IMPLEMENTED | Each fiscal period is dated by its first 8-K earnings release within 75 days (the real announcement), never by the period end; a period without a release is dropped. The free earnings calendar returned 0 rows for a symbol even for the last 35 days (run 36252886492); it remains only as a fallback. Revenue surprise: not in this free source (MISSING). |
| News | Finnhub market + company news | `FINNHUB_API_KEY` | IMPLEMENTED | Entity discovery without tags (names + `config/entity_aliases.toml`), negation/denial discount. |
| Macro | FRED / ALFRED | `FRED_API_KEY` | IMPLEMENTED | Vintage requests; SOX, breadth, Fed futures not available. |
| Short interest | FINRA consolidated short interest | optional `FINRA_API_KEY/SECRET` (OAuth) | **LIVE VERIFIED** (public access, 2026-09-26, GitHub Actions run 36249539188) | NVDA rows returned by the real API. The first live run found a contract error (a sort needs the partition key `settlementDate` in an EQUAL filter → 400); fixed by a date-range filter and local ordering. OAuth client credentials → Bearer when keys are set. |
| Insider | SEC Form 4 | `SEC_USER_AGENT` | PARTIAL | Recent filings only. |
| Options (IV, expected move) | — | — | UNAVAILABLE | No free official source. Priced-In is "Lite" (price/volume/news/revisions). |
| Institutional (13F) | — | — | UNAVAILABLE | |

Run `MARKETLENS_MODE=LIVE marketlens live-verify` once keys and network access exist. It checks every
category for NVDA, AAPL, MSFT, JPM, XOM, AMZN, TSM (value + provider + timestamp + source) and only then
removes the "not live verified" marker in the System Status matrix.

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


## Live verification log

`marketlens live-verify` runs against the real providers from GitHub Actions (`.github/workflows/live-verify.yml`,
manual). Results so far:

| Date | Run | Result |
|---|---|---|
| 2026-09-26 | 36248705111 | FINRA 400 (sort without partition key — contract error found); SEC 403 (User-Agent without contact e-mail); others: no key |
| 2026-09-26 | 36249539188 | **FINRA short interest VERIFIED** after the fix; SEC 403 (needs `SEC_USER_AGENT` = "name e-mail"); Finnhub, Polygon, FRED, Alpha Vantage: BLOCKED_BY_CREDENTIAL (no key) |
| 2026-09-26 | 36249699094 | FINRA VERIFIED again, with the evidence printed: NVDA 294,225,803 and AAPL 128,753,092 shares short, settlement 2026-09-15. Other categories unchanged |
| 2026-09-26 | 36251446786 | With `SEC_USER_AGENT`: SEC accepted. bank (JPM extras), insider (NVDA net −967,599,313.47) and FINRA VERIFIED. Real-data bug found: the total-debt vintages crashed (`max() arg is an empty sequence`) when a component was first filed after the period's first filing → fixed in 6c6abc1 (only components known on the date count; a parser crash is now a per-company ProviderDataError). Guidance: 0 values extracted |
| 2026-09-26 | 36251788168 | With `FINNHUB_API_KEY`: price (NVDA 225.07, AAPL 341.07, MSFT 516.17), news (NVDA 56 items), ifrs (TSM revenue 2,894,307,700,000), bank, short interest, insider VERIFIED. Real-data bugs found, all fixed in the next commit with regression tests (`tests/regression/test_real_data_run2.py`): (1) NVDA latest quarter revenue None — one XBRL concept was chosen for all periods; now chosen per period; (2) TTM EPS None for NVDA/AAPL/MSFT — 10-Ks tag no Q4 diluted share count, so Q4 EPS is now FY EPS − (Q1+Q2+Q3) (approximation, net income / Q4 shares preferred when tagged); (3) earnings: Finnhub returned an empty list and the check crashed (IndexError) — an empty history is now reported as missing data with a 35-day-window probe; (4) guidance: the Exhibit 99.1 of NVIDIA is named `q2fy26pr.htm` — now found by its TYPE on the filing index page. Polygon, FRED, Alpha Vantage: BLOCKED_BY_CREDENTIAL (no key) |
| 2026-09-26 | 36252886492 | All keys configured. **11 of 13 VERIFIED**: price, bars (Polygon grouped daily, NVDA 225.07 on 2026-09-25), fundamentals (NVDA revenue 96,221,000,000), eps_ttm (NVDA 7.91, AAPL 8.71, MSFT 17.95), ifrs, bank, news, macro (FRED: fed funds 3.88, 2Y 4.87, 10Y 5.18), guidance (NVDA `q2fy27pr.htm`, 2 values), short interest, insider. FAILED: earnings (Finnhub calendar 0 rows for NVDA, 800-day and 35-day windows) → replaced by `/stock/earnings` + SEC 8-K release times; estimates (Alpha Vantage answered but no row was stored — rows silently skipped) → now an error naming the horizons seen |
| 2026-09-26 | 36253463681 | **12 of 13 VERIFIED** — earnings now VERIFIED through `/stock/earnings` + SEC 8-K release times (NVDA EPS 1.87 released 2026-08-26, AAPL 1.91 released 2026-07-30). estimates: the real Alpha Vantage answer has one row per fiscal period (41 rows for NVDA, past and future) with horizon "fiscal year" / "fiscal quarter" only — not the documented "current/next fiscal year". Fixed: such rows are labelled current / next / +k by their period end; periods ended more than 120 days ago are dropped |
| 2026-09-26 | 36253710550 | **All 13 categories VERIFIED (exit code 0)** on commit 9fdab63: price, bars, fundamentals, eps_ttm, ifrs, bank, earnings, news, macro, estimates (Alpha Vantage NVDA forward EPS 13.46, observed 2026-09-26), guidance, short interest, insider. This proves each free provider answers and is parsed on the sampled tickers; it does not prove every company parses (the ingestion manifest records per-company failures) |

To verify the rest: add the repository secrets `SEC_USER_AGENT` ("Your Name your@email"), `FINNHUB_API_KEY`,
`POLYGON_API_KEY`, `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY` (all free tiers) and run the workflow.
