"""Invariant: on any portfolio, no buy suggestion — the portfolio review's size cap turned into dollars and shares by
position_plan — takes one name, one sector or one theme above its limit, or spends more cash than there is.
400 random portfolios (fixed seed), every buy action, with and without an existing position in the name."""

from __future__ import annotations

import random

import pytest

from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, PortfolioLimits, position_plan, review_candidate

SECTORS = ("Technology", "Health Care", "Financials", "Energy", "Industrials")
THEMES = ("AI", "Rates", "China")


def _portfolio(rng: random.Random) -> tuple[Portfolio, dict[str, float]]:
    holdings = []
    prices: dict[str, float] = {}
    for i in range(rng.randint(0, 12)):
        t = f"H{i}"
        px = rng.uniform(5, 500)
        holdings.append(Holding(t, rng.randint(1, 400), px * rng.uniform(0.5, 1.5), rng.choice(SECTORS), tuple(rng.sample(THEMES, rng.randint(0, 2)))))
        prices[t] = px
    return Portfolio(tuple(holdings), rng.uniform(0, 150_000)), prices


CASES = list(range(400))


@pytest.mark.parametrize("seed", CASES)
def test_no_buy_suggestion_breaks_a_limit(seed):
    rng = random.Random(seed)
    lim = PortfolioLimits()
    pf, prices = _portfolio(rng)
    held = rng.random() < 0.3 and pf.holdings
    cand_ticker = pf.holdings[0].ticker if held else "NEW"
    sector = pf.holdings[0].sector if held else rng.choice(SECTORS)
    themes = pf.holdings[0].themes if held else tuple(rng.sample(THEMES, rng.randint(0, 2)))
    review = review_candidate(pf, prices, CandidateProfile(cand_ticker, sector, themes, 0.0), {}, lim)
    nav = pf.cash + sum(h.quantity * prices[h.ticker] for h in pf.holdings)
    price = prices.get(cand_ticker, rng.uniform(5, 500))
    current = sum(h.quantity * prices[h.ticker] for h in pf.holdings if h.ticker == cand_ticker)
    for action in (("ADD",) if held else ("BUY", "BUY SMALL")):
        p = position_plan(action, review.size_cap.value, nav, price, price * 0.9, current, lim)
        if p is None or p.shares == 0:
            continue
        amt = p.amount
        eps = 1e-9 * max(1.0, nav)
        sector_now = sum(h.quantity * prices[h.ticker] for h in pf.holdings if h.sector == sector)
        assert (current + amt) <= lim.max_single_name * nav + eps, ("single", seed, action, current, amt, nav)
        assert (sector_now + amt) <= lim.max_sector * nav + eps, ("sector", seed, action, sector_now, amt, nav)
        for th in themes:
            theme_now = sum(h.quantity * prices[h.ticker] for h in pf.holdings if th in h.themes)
            assert (theme_now + amt) <= lim.max_theme * nav + eps, ("theme", th, seed, action, theme_now, amt, nav)
        assert amt <= pf.cash + eps, ("cash", seed, action, amt, pf.cash)
