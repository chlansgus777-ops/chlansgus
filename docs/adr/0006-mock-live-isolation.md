# ADR-0006: MOCK and LIVE data are never mixed

Status: Accepted

## Decision
The whole process runs in one mode (`MARKETLENS_MODE`). Provider chains reject mixed modes; LIVE gaps
use `UnavailableProvider` which raises (→ MISSING), never a mock fallback; the mock LLM is refused in
LIVE mode; all mock data is tagged and named "(MOCK)"; the UI shows a MOCK DATA banner; MOCK and LIVE
use different database files by default.
