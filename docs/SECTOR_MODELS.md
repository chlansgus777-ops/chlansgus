# Sector Models (`config/sector_models.toml`, version sector-models-1.0.0)

NVDA and JPM are never scored with the same formula. Model selection (`domain/sector_models.py`):
explicit ticker override → industry keyword → sector → generic. The selection reason is stored and shown
in the UI ("industry 'Semiconductors' matches keyword 'semiconductor' → Semiconductor model …").

Each rule maps a metric linearly from `bad` (0) to `good` (1) — works in both directions (e.g. forward
P/E 60→15). A model is scored only if the available rule weight ≥ `min_coverage` (default 0.5);
otherwise the component is MISSING (conservative).

| Model | Selected by | Fundamental metrics | Valuation metrics |
|---|---|---|---|
| Semiconductor | industry "semiconductor" | revenue & EPS growth, gross margin (+Δ YoY), FCF margin, inventory growth − revenue growth, CapEx/revenue, AI revenue share | forward P/E, PEG, EV/Sales, FCF yield |
| Software | "software", "application", "saas" | revenue growth, Rule of 40, gross margin, FCF margin, SBC/revenue, RPO growth, ARR growth, NRR, dilution | EV/Sales, forward P/E, FCF yield, PEG |
| Internet / Platforms | Communication Services; "internet", "interactive media", "e-commerce" | revenue growth, operating margin (+Δ), FCF margin, EPS growth, SBC | forward P/E, EV/EBITDA, FCF yield, PEG |
| Consumer | Consumer Cyclical/Defensive | revenue growth, gross margin Δ, operating margin, inventory vs revenue, FCF margin, ROIC, net debt/EBITDA | forward P/E, EV/EBITDA, FCF yield |
| Financial / Bank | Financial Services; "bank", "capital markets", "insurance" | ROTCE, CET1, NIM, loan growth, deposit growth, net charge-offs, provisions/loans, capital return yield, ROE | P/TBV, P/B, forward P/E |
| Biotech | "biotechnology" | cash runway (quarters), dilution, revenue growth, late-stage programs, gross margin | EV/Sales, forward P/E |
| Healthcare | Healthcare; "drug manufacturers", "medical" | revenue & EPS growth, operating margin, FCF margin, ROIC, leverage | forward P/E, PEG, FCF yield |
| Energy | Energy; "oil", "gas" | FCF margin, net debt/EBITDA, ROIC, production growth, capital return yield, CapEx/revenue | EV/EBITDA, FCF yield, forward P/E |
| Industrials | Industrials; "aerospace", "electrical equipment", "machinery" | revenue growth, backlog growth, book-to-bill, operating margin Δ, FCF margin, ROIC, leverage | EV/EBITDA, forward P/E, FCF yield |
| REIT | Real Estate; "reit" | FFO growth, AFFO payout, occupancy, debt/EBITDA, interest coverage, NAV discount | P/FFO, dividend yield |
| Utilities | Utilities | rate-base growth, EPS growth, allowed ROE, leverage, ROE | forward P/E, dividend yield, EV/EBITDA |
| Technology Hardware | Technology (fallback within tech) | revenue & EPS growth, gross margin, FCF margin, ROIC | forward P/E, PEG, FCF yield |
| Generic | fallback | growth, operating margin, FCF margin, ROIC, leverage | forward P/E, EV/EBITDA, FCF yield, PEG |

Sector KPIs that are not in standard statements (CET1, NIM, occupancy, FFO, RPO, backlog, …) come from
`FundamentalProvider.get_extras`. In LIVE mode no licensed KPI source is configured, so these metrics are
MISSING and the coverage rule decides whether the model can be scored.

Changing thresholds/weights requires bumping `version` and regenerating the regression baseline.
