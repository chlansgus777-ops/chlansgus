# ADR-0007: Calibration through shadow models and promotion gates

Status: Accepted

## Context
PanWatch applies EMA-blended IC-driven weights directly to production after 20 samples.

## Decision
Minimum 100 mature samples; ≤5% relative change per weight per cycle; zero-sum; the proposal runs as a
SHADOW version and is promoted only after out-of-sample IC improvement with hit rate and downside not
worse; segment models fall back to global when under-sampled.
