"""News → structured issues (deterministic rule-based structuring).

Steps: normalise → deduplicate/cluster → classify category → polarity → primary effects → rank.
Only title + summary are classified; article bodies are untrusted and never interpreted as instructions.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from marketlens.application.safety import detect_injection
from marketlens.domain.enums import ConfirmedStatus, IssueCategory
from marketlens.domain.issues import CAUSAL_TEMPLATES, Issue, IssueEffect
from marketlens.providers.contracts import NewsItem

CATEGORY_RULES: list[tuple[IssueCategory, tuple[str, ...]]] = [
    (IssueCategory.EXPORT_CONTROL, ("export restriction", "export control", "export licens", "entity list")),
    (IssueCategory.TARIFF, ("tariff",)),
    (IssueCategory.ANTITRUST, ("antitrust", "ftc ", "monopoly")),
    (IssueCategory.MA, ("acquire", "acquisition", "merger", "takeover")),
    (IssueCategory.GUIDANCE, ("guidance", "outlook", "forecast")),
    (IssueCategory.EARNINGS, ("earnings", "quarterly results", "eps")),
    (IssueCategory.AI, ("ai ", "artificial intelligence", "data-center", "data center", "gpu", "accelerator")),
    (IssueCategory.INFLATION, ("cpi", "inflation", "pce ")),
    (IssueCategory.RATES, ("treasury yield", "yields", "rate hike", "rate cut", "fomc", "federal reserve")),
    (IssueCategory.OIL, ("oil", "crude", "opec")),
    (IssueCategory.CYBERSECURITY, ("breach", "cyberattack", "ransomware", "hack")),
    (IssueCategory.LEGAL, ("lawsuit", "sued", "verdict", "settlement")),
    (IssueCategory.DILUTION, ("share offering", "dilution", "at-the-market")),
    (IssueCategory.BUYBACK, ("buyback", "repurchase")),
    (IssueCategory.MANAGEMENT, ("ceo", "cfo", "resigns", "steps down")),
    (IssueCategory.SUPPLY_CHAIN, ("shortage", "supply chain", "outage")),
    (IssueCategory.GEOPOLITICS, ("sanction", "war", "conflict", "taiwan strait")),
    (IssueCategory.PRODUCT, ("launch", "unveil", "release")),
    (IssueCategory.COMPETITION, ("competitor", "market share")),
    (IssueCategory.FINANCING, ("notes offering", "credit facility", "refinanc")),
    (IssueCategory.REGULATION, ("regulat", "rule", "probe", "investigation")),
]

POSITIVE = ("raise", "raises", "beat", "beats", "surge", "jump", "jumps", "climb", "climbs", "expand", "expands", "approval", "record", "upgrade", "lifts", "boost")
NEGATIVE = ("cut", "cuts", "restriction", "restrictions", "ban", "miss", "misses", "fall", "falls", "plunge", "probe", "downgrade", "delay", "lawsuit", "halt")

SOURCE_QUALITY = {"OFFICIAL": 0.95, "WIRE": 0.8, "COMMERCIAL": 0.65, "OTHER": 0.4}
STATUS_BY_SOURCE = {"OFFICIAL": ConfirmedStatus.CONFIRMED, "WIRE": ConfirmedStatus.REPORTED, "COMMERCIAL": ConfirmedStatus.REPORTED, "OTHER": ConfirmedStatus.RUMOR}
BASE_IMPORTANCE = {
    IssueCategory.EXPORT_CONTROL: 0.8, IssueCategory.GUIDANCE: 0.8, IssueCategory.EARNINGS: 0.75, IssueCategory.MA: 0.75,
    IssueCategory.RATES: 0.7, IssueCategory.INFLATION: 0.7, IssueCategory.AI: 0.7, IssueCategory.TARIFF: 0.7,
    IssueCategory.ANTITRUST: 0.6, IssueCategory.OIL: 0.65, IssueCategory.GEOPOLITICS: 0.6,
}


def _norm(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in ("mock", "the", "and", "for", "with")}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def classify(text: str) -> tuple[IssueCategory | None, float]:
    t = f" {text.lower()} "
    ai_terms = dict(CATEGORY_RULES)[IssueCategory.AI]
    if ("capex" in t or "capital expenditure" in t) and any(k in t for k in ai_terms):
        return IssueCategory.AI, 0.9
    for cat, kws in CATEGORY_RULES:
        hits = sum(1 for k in kws if k in t)
        if hits:
            return cat, min(1.0, 0.6 + 0.2 * hits)
    return None, 0.0


def polarity(text: str) -> int:
    words = re.findall(r"[a-z\-]+", text.lower())
    p = sum(1 for w in words if w in POSITIVE)
    n = sum(1 for w in words if w in NEGATIVE)
    return 1 if p > n else -1 if n > p else 0


@dataclass(frozen=True)
class IssueBuildResult:
    issues: list[Issue]
    injection_flags: dict[str, list[str]]  # news_id -> patterns
    unclassified: int


def cluster(items: Sequence[NewsItem], threshold: float = 0.6) -> list[list[NewsItem]]:
    clusters: list[tuple[set[str], list[NewsItem]]] = []
    for it in sorted(items, key=lambda x: x.published_at):
        toks = _norm(it.title)
        for ctoks, members in clusters:
            if _jaccard(toks, ctoks) >= threshold:
                members.append(it)
                ctoks |= toks
                break
        else:
            clusters.append((set(toks), [it]))
    return [m for _, m in clusters]


def _effects(cat: IssueCategory, pol: int, text: str, tickers: tuple[str, ...]) -> tuple[IssueEffect, ...]:
    chain = CAUSAL_TEMPLATES.get(cat, ("event", "estimate impact", "re-rating"))
    t = text.lower()
    if cat == IssueCategory.AI and "capex" in t:
        # a customer's AI capex increase transmits to its suppliers; the spender itself is only the origin
        d = 1.0 if pol >= 0 else -1.0
        return tuple(IssueEffect(tk, d, ("customer AI capex ↑" if d > 0 else "customer AI capex ↓",) + chain, origin_impact=False) for tk in tickers) or (IssueEffect("THEME:AI", d, chain),)
    if cat == IssueCategory.EXPORT_CONTROL:
        d = -0.8 if pol <= 0 else 0.5
        return tuple(IssueEffect(tk, d, chain) for tk in tickers) + (IssueEffect("COUNTRY:CN", 0.0, chain, origin_impact=False),)
    if cat == IssueCategory.OIL:
        d = 1.0 if pol >= 0 else -1.0
        return (IssueEffect("COMMODITY:OIL", d, ("oil price ↑" if d > 0 else "oil price ↓",) + chain[1:], origin_impact=False),)
    if cat in (IssueCategory.RATES, IssueCategory.INFLATION):
        d = 1.0 if pol >= 0 else -1.0  # "yields climb" → rates up
        scale = 1.0 if cat == IssueCategory.RATES else 0.7
        return (IssueEffect("MACRO:RATES", d * scale, ("rates ↑" if d > 0 else "rates ↓",) + chain[1:], origin_impact=False),)
    if not tickers or pol == 0:
        return ()
    return tuple(IssueEffect(tk, 0.6 * pol, chain) for tk in tickers)


def build_issues(items: Iterable[NewsItem], now: datetime) -> IssueBuildResult:
    items = list(items)
    flags: dict[str, list[str]] = {}
    for it in items:
        f = detect_injection(f"{it.title}\n{it.summary}\n{it.body}")
        if f:
            flags[it.news_id] = f
    issues: list[Issue] = []
    unclassified = 0
    for group in cluster(items):
        lead = max(group, key=lambda x: SOURCE_QUALITY.get(x.source_type, 0.4))
        text = f"{lead.title}. {lead.summary}"
        cat, cconf = classify(text)
        if cat is None:
            unclassified += 1
            continue
        pol = polarity(text)
        tickers = tuple(sorted({t for g in group for t in g.tickers}))
        effects = _effects(cat, pol, text, tickers)
        if not effects:
            unclassified += 1
            continue
        sq = max(SOURCE_QUALITY.get(g.source_type, 0.4) for g in group)
        importance = min(1.0, BASE_IMPORTANCE.get(cat, 0.5) + 0.05 * (len(group) - 1))
        digest = hashlib.sha1(" ".join(sorted(_norm(lead.title))).encode()).hexdigest()[:8]
        iid = f"ISSUE_{cat.name}_{digest}"
        issues.append(
            Issue(
                issue_id=iid,
                title=lead.title,
                category=cat,
                summary=lead.summary[:500],
                event_time=min(g.published_at for g in group),
                publish_time=min(g.published_at for g in group),
                sources=tuple(sorted({g.source for g in group})),
                source_quality=sq,
                confirmed_status=STATUS_BY_SOURCE.get(lead.source_type, ConfirmedStatus.RUMOR),
                affected_sectors=(),
                primary_effects=effects,
                importance=round(importance, 3),
                surprise_factor=0.5,
                market_awareness=round(min(1.0, len(group) / 5), 3),
                confidence=round(cconf * sq, 3),
                evidence_id=iid,
            )
        )
    issues.sort(key=lambda i: -i.importance)
    return IssueBuildResult(issues, flags, unclassified)
