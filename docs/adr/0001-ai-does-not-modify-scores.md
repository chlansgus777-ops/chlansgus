# ADR-0001: The AI committee never modifies data or the deterministic score

Status: Accepted (2026-09-25)

## Context
PanWatch lets the LLM (TradingAgents' PM) *be* the decision and parses the action from prose. LLMs can
hallucinate numbers and are sensitive to prompt injection.

## Decision
The committee receives an evidence pack and returns strict-schema reports. It cannot write to the
database, cannot mutate frozen analysis objects, may only adjust confidence within ±10 and may only
*downgrade* the deterministic action through `apply_downgrade`. Numbers not present in the evidence are
removed from its output.

## Consequences
Recommendations remain reproducible and explainable without the LLM; LLM outages degrade to
"AI COMMITTEE UNAVAILABLE". Tests in `tests/ai_safety/` guard this.
