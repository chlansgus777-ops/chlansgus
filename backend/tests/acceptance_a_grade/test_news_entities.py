"""A-grade acceptance: news entity discovery without tags, negation/denial handling, no common-word matches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from marketlens.application.issue_engine import build_issues, load_entity_aliases, polarity_detail
from marketlens.providers.contracts import NewsItem

NOW = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)
NAMES = {"NVDA": "NVIDIA CORP", "TGT": "Target Corp", "AAPL": "Apple Inc.", "JPM": "JPMORGAN CHASE & CO"}


def item(i: int, title: str, summary: str = "", tickers: tuple[str, ...] = ()) -> NewsItem:
    return NewsItem(f"n{i}", NOW - timedelta(hours=i), title, summary, f"https://www.reuters.com/{i}", "Reuters", "WIRE", tickers)


def effects(res, ticker):
    return [e for iss in res.issues for e in iss.primary_effects if e.node_id == ticker]


def test_untagged_market_article_still_reaches_the_company():
    """Audit P1: market-wide 'Nvidia raises earnings guidance' (no ticker tag) produced 0 issues for NVDA."""
    res = build_issues([item(1, "Nvidia raises earnings guidance", "The chipmaker lifted its outlook.")], NOW, NAMES)
    eff = effects(res, "NVDA")
    assert eff and eff[0].direction > 0


def test_curated_aliases_identify_the_company():
    res = build_issues([item(2, "Jensen Huang says Blackwell GPU demand beats expectations", "")], NOW, NAMES, load_entity_aliases())
    assert effects(res, "NVDA")


def test_denial_is_not_a_negative_story():
    """Audit P1: 'Nvidia denies export restrictions will hurt earnings' was classified as negative."""
    pol, hedged = polarity_detail("Nvidia denies export restrictions will hurt earnings")
    assert pol == 0 and hedged
    plain = build_issues([item(3, "US widens chip export restrictions on Nvidia", "")], NOW, NAMES)
    denied = build_issues([item(4, "Nvidia denies export restrictions will hurt earnings", "")], NOW, NAMES)
    e_plain, e_denied = effects(plain, "NVDA"), effects(denied, "NVDA")
    assert e_plain and e_denied and abs(e_denied[0].direction) < abs(e_plain[0].direction) * 0.5
    assert denied.issues[0].confidence < plain.issues[0].confidence


def test_ordinary_words_are_not_company_names():
    res = build_issues([item(5, "Analyst raises price target on chipmakers after record quarter", "")], NOW, NAMES)
    assert not effects(res, "TGT") and not effects(res, "AAPL")


def test_priced_in_without_options_is_lite_and_never_falsely_precise():
    from marketlens.domain.priced_in import PricedInInputs, estimate_priced_in

    import dataclasses

    fields = {f.name for f in dataclasses.fields(PricedInInputs)}
    base = {k: None for k in fields}
    base.update(event_direction=1, run_up_days=10)
    few = estimate_priced_in(PricedInInputs(**{**base, "abnormal_volume_ratio": 2.0, "news_repetition": 8}))
    assert few.model == "LITE" and few.confidence_level == "LOW" and few.band is None  # "반영 정도 추정 제한"
    more = estimate_priced_in(PricedInInputs(**{**base, "abnormal_volume_ratio": 2.0, "news_repetition": 8, "days_since_first_report": 5,
                                                "pre_event_return": 0.05, "daily_volatility": 0.02, "pre_event_benchmark_return": 0.0}))
    assert more.model == "LITE" and more.confidence_level == "MEDIUM" and more.band in ("낮음", "중간", "높음")
