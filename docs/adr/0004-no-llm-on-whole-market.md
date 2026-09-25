# ADR-0004: No LLM calls on the whole market

Status: Accepted

## Decision
The scanner runs cheap → expensive: eligibility (all) → cheap quant ranks (~400) → sector-model deep
filter (~150) → issue/event analysis (~60) → final ranking (~40). The committee runs only on the top
`AI_COMMITTEE_TOP_N` (default 20). An integration test bounds LLM calls to top_n × calls-per-committee
regardless of universe size. LLM responses are cached by snapshot fingerprint.

## Consequences
Predictable cost and latency; the deterministic scan is useful on its own.
