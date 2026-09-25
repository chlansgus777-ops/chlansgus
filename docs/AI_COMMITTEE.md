# AI Investment Committee (`application/committee/`)

The committee is a **verification layer** on top of the deterministic analysis. If no LLM is configured
or it fails, the UI shows **AI COMMITTEE UNAVAILABLE** and every deterministic feature keeps working.

## Flow
1. **Seven analysts** (parallel, FAST tier): Fundamental, Earnings, Valuation, Macro, Technical,
   News & Issue, Risk Analyst. Each receives only its **evidence pack** — the evidence categories relevant
   to its role (e.g. Valuation: valuation + analyst; Technical: technical + entry + price). Never the raw
   database. External news text reaches only the News analyst, wrapped in
   `<untrusted_external_data>` with injection flags.
2. **Bull / Bear debate** (DEEP tier), fixed **2 rounds**: round 1 cases, round 2 rebuttals with the
   opponent's previous argument in context. Every point must cite ≥ 1 valid evidence ID and give an
   interpretation — same evidence/different interpretation or different evidence.
3. **Decision Synthesizer** (DEEP): committee agreement, strongest bull/bear argument, unresolved
   uncertainty, advisory stance, confidence adjustment (schema-bounded to ±10).
4. **Risk Manager** (DEEP): independent review of event/gap/volatility/liquidity/data/thesis/concentration
   risk; may recommend a downgrade.
5. **Portfolio Manager** (FAST): portfolio fit and size class; the deterministic portfolio review already
   computed concentration, sector/theme exposure, correlation and rate sensitivity; the final size is
   `min(advice, deterministic cap)`.
6. **Consensus** (deterministic, `consensus.py`): stance values weighted by domain relevance (macro weighs
   more for banks/REITs/utilities/energy) × agent confidence × data completeness → "Committee Consensus
   %"; weighted stance dispersion → divergence LOW/MEDIUM/HIGH; HIGH divergence lowers confidence by 8.

## Hard guarantees (tests in `tests/ai_safety/`)
- **Strict schemas** (`schemas.py`, Pydantic `extra="forbid"`): a report with any extra field (e.g.
  `current_price`) is rejected (`invalid_outputs`), not "best-effort" parsed.
- **Evidence-only**: unknown evidence IDs are removed and logged.
- **Numeric-claim guard** (`guard.py`): every number in agent text must match a value in that agent's
  evidence pack (tolerance 2% or ±0.05; ×100 for percentages; small integers/look-back windows/years
  allowed). "NVDA current price = $999" or an invented EPS is removed and recorded.
- **Cannot alter data or scores**: analysis objects are frozen dataclasses; the committee returns a
  separate result.
- **Downgrade-only**: `apply_committee` uses `apply_downgrade`; WAIT→BUY or DATA INSUFFICIENT→BUY is
  impossible, so hard vetoes cannot be overridden.
- **Confidence** adjustment is bounded to ±`committee_max_confidence_adjustment` (10).
- **No DB access**: the committee package cannot import the database (architecture test); caching and
  cost recording are injected.
- **Prompt injection**: detected patterns are flagged; text is sanitised and enveloped; tests show that
  an injected article does not change the committee outcome.

## Providers and tiers (`providers/llm/`)
| Provider | How | Notes |
|---|---|---|
| Anthropic | official `anthropic` SDK, `output_config.format` JSON schema (strict, additionalProperties false) | default FAST `claude-haiku-4-5`, DEEP `claude-opus-5`; deep calls use server-side refusal fallbacks (`fallbacks="default"`); refusals / truncation raise and mark the step invalid |
| OpenAI / Gemini / local | OpenAI-compatible `/chat/completions` with `response_format: json_schema` | set `OPENAI_BASE_URL`, `FAST_MODEL`, `DEEP_MODEL` |
| Mock | deterministic, derived from the evidence pack, outputs prefixed "(MOCK AI)" | only allowed in MOCK mode |

Configure with `LLM_PROVIDER`, `FAST_MODEL`, `DEEP_MODEL`, `AI_COMMITTEE_TOP_N` (default 20).

## Cache & cost
Fingerprint = SHA-256 of prompt version, tier, provider, system prompt, user prompt (which embeds the
price/financial/earnings/issue/macro evidence snapshot) and schema → identical snapshots never call the
LLM twice (`llm_cache`). Each call stores model, input/output tokens, latency, estimated cost
(`providers/llm/base.PRICING`), cached flag and error (`llm_calls`); totals are shown on System Health.

## What changed for agents
Agent stances are part of the analysis digest; stance changes between runs appear in What Changed.
