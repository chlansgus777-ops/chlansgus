"""News → structured issues (deterministic rule-based structuring).

Steps: canonicalise → deduplicate syndicated copies → cluster stories → classify category → polarity →
entity relevance → primary effects → rank.

Design rules
- Market news (fetched with ``tickers=None``) only creates macro/theme effects plus effects on companies
  that are explicitly *named* in the headline/summary.
- Company news (fetched per candidate ticker) attaches an effect to a ticker only when the article is
  relevant to that ticker by name/ticker mention (a provider tag alone is not enough: tags are often
  attached to round-up articles that barely mention the company).
- Syndicated copies (same story re-published by aggregators) never raise importance; only independent
  sources do.
- Only title + summary are classified; article bodies are untrusted and never interpreted as instructions.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

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

SOURCE_QUALITY = {"OFFICIAL": 0.95, "WIRE": 0.8, "COMMERCIAL": 0.6, "OTHER": 0.4}
STATUS_BY_SOURCE = {"OFFICIAL": ConfirmedStatus.CONFIRMED, "WIRE": ConfirmedStatus.REPORTED, "COMMERCIAL": ConfirmedStatus.REPORTED, "OTHER": ConfirmedStatus.RUMOR}
BASE_IMPORTANCE = {
    IssueCategory.EXPORT_CONTROL: 0.8, IssueCategory.GUIDANCE: 0.8, IssueCategory.EARNINGS: 0.75, IssueCategory.MA: 0.75,
    IssueCategory.RATES: 0.7, IssueCategory.INFLATION: 0.7, IssueCategory.AI: 0.7, IssueCategory.TARIFF: 0.7,
    IssueCategory.ANTITRUST: 0.6, IssueCategory.OIL: 0.65, IssueCategory.GEOPOLITICS: 0.6,
}
MACRO_CATEGORIES = (IssueCategory.RATES, IssueCategory.INFLATION, IssueCategory.OIL)
STOPWORDS = frozenset({"mock", "the", "and", "for", "with", "from", "into", "over", "after", "amid", "its", "his", "her", "their", "says", "said", "report", "reports", "update", "news", "inc", "corp", "co", "ltd", "plc", "company"})
CLUSTER_WINDOW = timedelta(hours=48)
STORY_SIMILARITY = 0.55  # same story (independent coverage)
SYNDICATION_SIMILARITY = 0.85  # near-identical copy of the same article
MIN_RELEVANCE = 0.5
_NAME_SUFFIXES = re.compile(r"\b(incorporated|inc|corp|corporation|co|company|ltd|limited|plc|holdings?|group|sa|nv|ag|adr|class [a-z])\b\.?", re.I)


def _stem(w: str) -> str:
    for suf in ("ing", "ers", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(title: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in STOPWORDS)


def _trigrams(title: str) -> frozenset[str]:
    s = re.sub(r"[^a-z0-9 ]+", "", title.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return frozenset(s[i : i + 3] for i in range(max(0, len(s) - 2)))


def title_similarity(a: str, b: str) -> float:
    """Robust headline similarity: max of token Jaccard, token containment and character-trigram Dice.

    Containment catches re-headlined copies ("Reuters: X" vs "X — shares rise"); trigram Dice catches
    inflections and punctuation differences that break plain word Jaccard."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    jac = inter / len(ta | tb)
    cont = inter / min(len(ta), len(tb)) if min(len(ta), len(tb)) >= 3 else 0.0
    ga, gb = _trigrams(a), _trigrams(b)
    dice = 2 * len(ga & gb) / (len(ga) + len(gb)) if ga and gb else 0.0
    return max(jac, 0.9 * cont, dice)


def canonical_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}".lower()


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


_GENERIC_FIRST_WORDS = frozenset({
    "american", "general", "united", "first", "national", "international", "global", "advanced", "the", "new", "bank",
    "digital", "applied", "royal", "energy", "health", "capital", "financial", "southern", "northern", "western", "eastern",
    # geography: "Taiwan", "China" … alone would match geopolitical news about the country, not the company
    "taiwan", "china", "chinese", "japan", "korea", "america", "canadian", "canada", "british", "euro", "europe", "india", "texas", "pacific", "atlantic",
})


def _name_keys(company: str | None) -> list[str]:
    """Name variants that identify a company in text: cleaned full name, first two words, and a
    distinctive first word ("NVIDIA CORP" → "NVIDIA"; "Advanced Micro Devices" → "Advanced Micro")."""
    if not company:
        return []
    k = re.sub(r"\([^)]*\)", " ", company)  # "(MOCK)", "(The)", share-class notes
    k = _NAME_SUFFIXES.sub("", k)
    k = re.sub(r"[,.]", " ", k)
    words = [w for w in re.sub(r"\s+", " ", k).strip(" -").split(" ") if w]
    while words and words[-1].lower() in ("&", "and", "-"):
        words.pop()
    if not words:
        return []
    out = [" ".join(words)]
    if len(words) >= 2:
        out.append(" ".join(words[:2]))
    if len(words[0]) >= 4 and words[0].lower() not in _GENERIC_FIRST_WORDS:
        out.append(words[0])
    return [x for x in dict.fromkeys(out) if len(x) >= 3]


def relevance(item: NewsItem, ticker: str, company: str | None = None) -> float:
    """0..1 relevance of an article to one company, from explicit entity mentions.

    title mention 0.6, summary mention 0.3, provider tag 0.2 (−0.1 for round-ups tagging >5 names)."""
    tick = re.compile(rf"(?<![A-Za-z0-9])\$?{re.escape(ticker)}(?![A-Za-z0-9])")
    names = _name_keys(company)
    name_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])", re.I) if names else None

    def mentions(text: str) -> bool:
        return bool(tick.search(text) or (name_re is not None and name_re.search(text)))

    s = 0.0
    if mentions(item.title):
        s += 0.6
    if mentions(item.summary):
        s += 0.3
    if ticker in item.tickers:
        s += 0.2 if len(item.tickers) <= 5 else 0.1
    return round(min(1.0, s), 3)


@dataclass(frozen=True)
class IssueBuildResult:
    issues: list[Issue]
    injection_flags: dict[str, list[str]]  # news_id -> patterns
    unclassified: int
    articles: dict[str, NewsItem] = field(default_factory=dict)  # news_id -> article (for evidence / AI context)
    relevance: dict[str, dict[str, float]] = field(default_factory=dict)  # news_id -> {ticker: score}
    syndicated_dropped: int = 0

    def merged(self, other: "IssueBuildResult") -> "IssueBuildResult":
        by_id = {i.issue_id: i for i in self.issues}
        for i in other.issues:
            by_id.setdefault(i.issue_id, i)
        rel = {k: dict(v) for k, v in self.relevance.items()}
        for k, v in other.relevance.items():
            rel.setdefault(k, {}).update(v)
        issues = sorted(by_id.values(), key=lambda i: (-i.importance, i.issue_id))
        return IssueBuildResult(issues, {**self.injection_flags, **other.injection_flags}, self.unclassified + other.unclassified,
                                {**self.articles, **other.articles}, rel, self.syndicated_dropped + other.syndicated_dropped)


def dedupe(items: Sequence[NewsItem]) -> tuple[list[NewsItem], int]:
    """Drop syndicated copies: identical canonical URL, or a near-identical headline within the window.

    The copy kept is the one from the highest-quality source (then the earliest)."""
    ranked = sorted(items, key=lambda x: (-SOURCE_QUALITY.get(x.source_type, 0.4), x.published_at, x.news_id))
    kept: list[NewsItem] = []
    urls: set[str] = set()
    dropped = 0
    for it in ranked:
        cu = canonical_url(it.url) if it.url else None
        if cu and cu in urls:
            dropped += 1
            continue
        if any(abs(it.published_at - k.published_at) <= CLUSTER_WINDOW and title_similarity(it.title, k.title) >= SYNDICATION_SIMILARITY for k in kept):
            dropped += 1
            continue
        kept.append(it)
        if cu:
            urls.add(cu)
    return sorted(kept, key=lambda x: (x.published_at, x.news_id)), dropped


def cluster(items: Sequence[NewsItem], threshold: float = STORY_SIMILARITY) -> list[list[NewsItem]]:
    """Group independent coverage of the same story (time-bounded, deterministic order)."""
    clusters: list[list[NewsItem]] = []
    for it in sorted(items, key=lambda x: (x.published_at, x.news_id)):
        for members in clusters:
            if abs(it.published_at - members[0].published_at) <= CLUSTER_WINDOW and max(title_similarity(it.title, m.title) for m in members) >= threshold:
                members.append(it)
                break
        else:
            clusters.append([it])
    return clusters


def _effects(cat: IssueCategory, pol: int, text: str, tickers: tuple[str, ...]) -> tuple[IssueEffect, ...]:
    chain = CAUSAL_TEMPLATES.get(cat, ("사건 발생", "실적 추정치 영향", "재평가"))
    t = text.lower()
    if cat == IssueCategory.AI and "capex" in t:
        # a customer's AI capex increase transmits to its suppliers; the spender itself is only the origin
        d = 1.0 if pol >= 0 else -1.0
        return tuple(IssueEffect(tk, d, ("고객사 AI 설비투자 ↑" if d > 0 else "고객사 AI 설비투자 ↓",) + chain, origin_impact=False) for tk in tickers) or (IssueEffect("THEME:AI", d, chain),)
    if cat == IssueCategory.EXPORT_CONTROL:
        d = -0.8 if pol <= 0 else 0.5
        return tuple(IssueEffect(tk, d, chain) for tk in tickers) + (IssueEffect("COUNTRY:CN", 0.0, chain, origin_impact=False),)
    if cat == IssueCategory.OIL:
        d = 1.0 if pol >= 0 else -1.0
        return (IssueEffect("COMMODITY:OIL", d, ("유가 ↑" if d > 0 else "유가 ↓",) + chain[1:], origin_impact=False),)
    if cat in (IssueCategory.RATES, IssueCategory.INFLATION):
        d = 1.0 if pol >= 0 else -1.0  # "yields climb" → rates up
        scale = 1.0 if cat == IssueCategory.RATES else 0.7
        return (IssueEffect("MACRO:RATES", d * scale, ("금리 ↑" if d > 0 else "금리 ↓",) + chain[1:], origin_impact=False),)
    if not tickers or pol == 0:
        return ()
    return tuple(IssueEffect(tk, 0.6 * pol, chain) for tk in tickers)


def build_issues(items: Iterable[NewsItem], now: datetime, names: Mapping[str, str] | None = None) -> IssueBuildResult:
    """Structure news into issues. ``names`` maps ticker → company name for entity relevance."""
    names = names or {}
    raw = [it for it in items if it.published_at <= now]  # never future-dated articles
    flags: dict[str, list[str]] = {}
    for it in raw:
        f = detect_injection(f"{it.title}\n{it.summary}\n{it.body}")
        if f:
            flags[it.news_id] = f
    unique, dropped = dedupe(raw)
    issues: list[Issue] = []
    unclassified = 0
    rel_map: dict[str, dict[str, float]] = {}
    for group in cluster(unique):
        lead = max(group, key=lambda x: (SOURCE_QUALITY.get(x.source_type, 0.4), -x.published_at.timestamp(), x.news_id))
        text = f"{lead.title}. {lead.summary}"
        cat, cconf = classify(text)
        if cat is None:
            unclassified += 1
            continue
        pol = polarity(text)
        # entity relevance: which tagged companies does the story actually concern?
        candidates = sorted({t for g in group for t in g.tickers})
        relevant: list[str] = []
        for tk in candidates:
            per_article = {g.news_id: relevance(g, tk, names.get(tk)) for g in group}
            for nid, r in per_article.items():
                rel_map.setdefault(nid, {})[tk] = r
            if max(per_article.values()) >= MIN_RELEVANCE:
                relevant.append(tk)
        effects = _effects(cat, pol, text, tuple(relevant))
        if not effects:
            unclassified += 1
            continue
        independent = len({g.source.lower() for g in group})
        sq = max(SOURCE_QUALITY.get(g.source_type, 0.4) for g in group)
        importance = min(1.0, BASE_IMPORTANCE.get(cat, 0.5) + 0.05 * (independent - 1))
        digest = hashlib.sha1(" ".join(sorted(_tokens(lead.title))).encode()).hexdigest()[:8]
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
                market_awareness=round(min(1.0, independent / 5), 3),
                confidence=round(cconf * sq, 3),
                evidence_id=iid,
                news_ids=tuple(sorted(g.news_id for g in group)),
            )
        )
    issues.sort(key=lambda i: (-i.importance, i.issue_id))
    return IssueBuildResult(issues, flags, unclassified, {it.news_id: it for it in unique}, rel_map, dropped)


def news_for_ticker(result: IssueBuildResult | None, ticker: str, issue_ids: Iterable[str], limit: int = 5) -> list[NewsItem]:
    """Articles that may be shown to the AI committee for ``ticker``: only articles belonging to issues
    that actually reached the ticker through a mechanism, plus articles directly relevant to it."""
    if result is None:
        return []
    wanted = set(issue_ids)
    ids: list[str] = []
    for iss in result.issues:
        if iss.issue_id in wanted:
            ids.extend(iss.news_ids)
    for nid, rel in result.relevance.items():
        if rel.get(ticker, 0.0) >= MIN_RELEVANCE and nid not in ids:
            ids.append(nid)
    arts = [result.articles[i] for i in ids if i in result.articles]
    arts.sort(key=lambda a: (-SOURCE_QUALITY.get(a.source_type, 0.4), -a.published_at.timestamp(), a.news_id))
    return arts[:limit]
