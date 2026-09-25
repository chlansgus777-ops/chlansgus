# Calibration (`domain/calibration.py`, `EvaluationService.calibrate`)

Weights never change in one day, and never without evidence.

1. **Samples**: factor snapshots (component sub-scores at decision time) joined with realised outcomes.
   Only outcomes whose horizon has fully elapsed in **trading days** are used (default horizon 20D).
2. **Minimum sample**: `MIN_CALIBRATION_SAMPLES = 100` mature samples, else status
   `INSUFFICIENT_SAMPLES` and nothing changes.
3. **Proposal**: per-factor Spearman IC; factors above the mean IC are nudged up, below it down; each
   change is capped at `MAX_WEIGHT_CHANGE = 5%` of that weight (relative) and the adjustment is
   zero-sum (the larger side is scaled down, so caps still hold).
4. **Shadow**: the proposal is stored as a SHADOW model version with a start date. Production keeps
   running on the old weights.
5. **Promotion** (next calibration run): compare production vs shadow composites **out-of-sample** —
   only recommendations made after the shadow start — and promote only if
   - ≥ `min_shadow_samples` (100) mature OOS samples,
   - IC improvement ≥ `min_ic_improvement` (0.01),
   - top-quintile hit rate not worse,
   - top-quintile downside (drawdown proxy) not worse.
   Promotion marks the old PRODUCTION model RETIRED and emits `MODEL_PROMOTED`; the new weights are used
   from the next scan and recorded as a new `scoring_model_version`.
6. **Segments**: sector and regime segments are evaluated; a segment with fewer than
   `min_segment_samples` (60) falls back to the global model (reported per segment).

Every run is stored in `calibration_runs` (status, candidate, IC table, comparison). Manual changes to
`config/scoring_model.toml` must bump `scoring_model_version`.
