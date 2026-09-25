"""Pure domain layer.

Rules (enforced by tests/architecture):
- no imports of providers, infrastructure, api, workers, frontend
- no network / database / LLM access
- only stdlib (+ math); all inputs are explicit, immutable facts
"""
