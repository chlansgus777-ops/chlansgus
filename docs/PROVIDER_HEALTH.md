# Provider Health, Failover and Data Quality

## Chains (`providers/router.py`)
Each data kind has an ordered provider chain. The router:
- skips providers that are not configured (no key) — they report DOWN,
- wraps every call in a **circuit breaker** (CLOSED → OPEN after 5 consecutive failures → HALF_OPEN
  after 60 s → CLOSED on a successful probe), so a failing provider is not hammered,
- **retries** with exponential backoff + jitter (0.5 s base, ×2, max 8 s, 3 attempts) and honours
  `Retry-After` on HTTP 429,
- respects client-side **token-bucket** rate limits per provider,
- **fails over** to the next provider on availability errors (`NotSupported` also falls through without
  counting as a failure),
- optionally **cross-checks** a secondary provider (quotes: 2% tolerance) and returns the disagreement as
  a conflict → the field becomes CONFLICTING; a conflict on a core field is a SEVERE_DATA_CONFLICT veto.
  The router never silently picks one side.
- refuses to build a chain mixing MOCK and LIVE providers.

## Health states (`infrastructure/health.py`)
HEALTHY, DEGRADED (error rate > 10% or ≥ 50% with prior success), RATE_LIMITED, STALE (no fresh data
for 26 h), DOWN (not configured, breaker open, or failing without any success).
Metrics: requests, last_success, last_failure, last_error, average latency, error rate (last 50 calls),
freshness, rate-limit state, breaker state. Transitions emit PROVIDER_FAILED / PROVIDER_RECOVERED; a
snapshot is persisted after each scan (`provider_health`). The System Health page also shows the LLM
provider status and token/cost usage.

## Data quality
FRESH, DELAYED, STALE, CONFLICTING, MISSING per field (`domain/facts.py`). Price freshness
(`domain/market.assess_price_quality`): open market → realtime ≤ 2 min FRESH, ≤ 20 min DELAYED, else
STALE; closed market → only a quote at/after the last session close is valid; future timestamps are
CONFLICTING. A previous day's close is never shown as the current price.

Source traceability: each evidence item stores value, source, source timestamp and quality; each
recommendation stores the provider per field (`source_map`) and the reasons for missing data.

## Cache TTLs (`[cache_ttl_seconds]`)
price 15 s, news 5 min, fundamentals 24 h, macro 1 h, analyst 6 h, options 15 min, universe 24 h.
