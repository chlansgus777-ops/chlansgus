# ADR-0003: Store a point-in-time input snapshot with every recommendation

Status: Accepted

## Context
Re-fetching data later gives restated fundamentals and revised macro prints; a past decision could not
be reproduced, and backtests would leak future information.

## Decision
The pure pipeline `run_analysis(AnalysisInputs, ModelConfig)` is deterministic. `AnalysisInputs` (quote,
bars, quarters with filed dates, estimates, macro, issues, graph slice, events, portfolio review,
previous digest, thesis conditions, provider conflicts…) is JSON-encoded with the recommendation,
together with the exact TOML config texts, weights and a canonical SHA-256 fingerprint. Replay decodes
and recomputes; it never calls providers.

## Consequences
~10–40 KB per recommendation; exact replay (tested); point-in-time filters (`filed_date`, `as_of`) are
applied inside the pipeline so injected future data has no effect (tested).
