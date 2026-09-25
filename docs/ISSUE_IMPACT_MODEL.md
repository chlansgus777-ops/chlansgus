# Issue Impact Model

Headline sentiment is never an investment signal. News becomes an impact only through a causal
mechanism, company exposure, a time-horizon profile and a priced-in adjustment.

## 1. News → Issue (`application/issue_engine.py`)
normalise → deduplicate/cluster (title-token Jaccard ≥ 0.6) → classify category (keyword taxonomy over
title + summary; bodies are untrusted and never interpreted) → polarity → primary effects → rank.
Categories: Earnings, Guidance, AI, Regulation, Export Control, Tariff, Geopolitics, M&A, Product,
Competition, Supply Chain, Rates, Inflation, Oil, Legal, Antitrust, Financing, Dilution, Buyback,
Management, Cybersecurity.

Issue fields: issue_id, title, category, summary, event_time, publish_time, sources, source_quality
(OFFICIAL 0.95 > WIRE 0.8 > COMMERCIAL 0.65 > OTHER 0.4), confirmed_status (CONFIRMED / REPORTED / RUMOR),
affected sectors/companies, importance, surprise_factor, market_awareness (cluster size), confidence.

Primary effects are category specific, e.g. an AI-capex increase at a hyperscaler is modelled as an
effect on the spender with `origin_impact=False` (it only transmits to suppliers); oil news is an effect
on `COMMODITY:OIL`; rates/inflation news on `MACRO:RATES`.

## 2. Exposure graph (`domain/exposure_graph.py`, `config/exposure_graph.toml`)
Nodes: Company, Sector, Industry, Country, Commodity, Macro Factor, Technology Theme.
Edges: SUPPLIER_OF, CUSTOMER_OF, COMPETITOR_OF, PARTNER_OF, DEPENDS_ON, PROVIDES_TO, EXPOSED_TO_REGION,
EXPOSED_TO_RATE, EXPOSED_TO_COMMODITY, EXPOSED_TO_AI, EXPOSED_TO_CLOUD — each with weight, confidence,
source, last_updated.

Transmission is **direction-aware** (scoring-1.1.0): for `A SUPPLIER_OF B`, an effect on B (customer
demand) reaches A with ×1.0, an effect on A reaches B with only ×0.2; competitors ×−0.5; partners ×0.8;
macro/theme exposures flow only from the factor to the company.

Propagation keeps the strongest path to each company up to **2 hops** with decay
Direct 1.0 / 1-hop 0.65 / 2-hop 0.35 (config `[issues]`). Example (tested):
`MSFT AI capex ↑ → NVDA (1-hop) → TSM (2-hop)`, `→ VRT`, `→ ANET`, none of which is mentioned in the
headline.

## 3. Horizon impact (`domain/issues.py`)
For each company and horizon — Immediate (0–1d), Short (1–5d), Swing (2–6w), Fundamental (1–4q):
```
impact = direction × path_multiplier × importance × (0.5 + 0.5·surprise) × status_weight × horizon_profile[category][h]
         × (1 − priced_in/100 × damping[h])        # damping: 0.9 / 0.8 / 0.5 / 0.1
score  = clip(impact × 100, −100, +100)
confidence = issue.confidence × path.confidence × source_quality
```
Every impact stores direction, impact_score, confidence and the **causal chain**, e.g.
`addressable market in restricted region ↓ → revenue opportunity ↓ → EPS revision risk ↑ → valuation
pressure → transmitted via TSM [SUPPLIER_OF] NVDA (1-hop)`.

## 4. Priced-in (`domain/priced_in.py`)
Estimated 0–100 from: abnormal pre-event run-up (z-score vs volatility and benchmark), abnormal volume,
gap vs options expected move, IV rank, revisions already moved in the event direction, news repetition,
time since first report. Confidence = share of the 7 inputs available. Always labelled **estimate**.

## 5. Use in scoring and decisions
- The Swing-horizon net impact feeds the Catalyst component.
- Thesis conditions can be tied to issue categories (e.g. NVDA "Export regulation materially worse" if
  the Fundamental-horizon impact ≤ −60) → THESIS_INVALIDATED veto.
- New major issues are material changes (What Changed).

## 6. Scenarios
Bull / Base / Bear with trigger, mechanism, price range and invalidation; probability stays N/A until
calibrated.
