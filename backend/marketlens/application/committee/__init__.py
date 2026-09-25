"""AI Investment Committee — a verification layer on top of the deterministic analysis.

Hard rules (tested): agents see only an evidence pack, answer in strict schemas, cannot create numbers
that are not in the evidence, cannot change market data or the deterministic score, and can only
*downgrade* the deterministic action (never override a hard veto).
"""
