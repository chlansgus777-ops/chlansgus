# ADR-0002: FACTS → SCORE → DECISION (no decision-dependent scoring)

Status: Accepted

## Context
PanWatch's scanner derives an action first and then starts the score from `ACTION_BASE_SCORE[action]`
and caps scores by action — the justification depends on the conclusion.

## Decision
`ScoringInputs` contains only facts and derived assessments; it has no action field. `scoring.py` must
not import `decision.py` (architecture test). The decision engine consumes a finished `ScoreCard`.

## Consequences
Scores are auditable and can be evaluated with IC independently of the actions they led to.
