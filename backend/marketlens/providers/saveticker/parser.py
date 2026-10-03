"""The public news-list contract only; detail, summary and financial figures are not inferred."""
from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from typing import Any

from marketlens.providers.contracts import NewsItem, NewsMetadata, ProviderDataError


class SaveTickerParseError(ProviderDataError):
    pass


def parse_date(value: Any, server_timezone: tzinfo | None = None) -> datetime:
    if not isinstance(value, str):
        raise SaveTickerParseError("created_at must be an ISO datetime")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as ex:
        raise SaveTickerParseError("invalid created_at") from ex
    if stamp.tzinfo is None:
        if server_timezone is None:
            raise SaveTickerParseError("created_at timezone missing; not assumed to be KST or UTC")
        stamp = stamp.replace(tzinfo=server_timezone)
    return stamp.astimezone(timezone.utc)


def extract_tickers(tags: list[str]) -> tuple[str, ...]:
    # Only explicitly attached dollar tags. No company-name guesses or body extraction.
    return tuple(sorted({tag[1:].upper() for tag in tags if re.fullmatch(r"\$[A-Za-z][A-Za-z0-9.\-]{0,14}", tag)}))


EVENTS = (
    ("dilution", r"증자|희석|전환사채|\bATM\b|convertible|dilution"),
    ("guidance", r"가이던스|guidance"),
    ("earnings", r"실적|어닝|\bEPS\b|earnings"),
    ("ma", r"인수합병|인수|합병|\bM&A\b|merger|acquisition"),
    ("regulation", r"규제|\bFDA\b|regulation"),
    ("buyback", r"자사주 매입|buyback|repurchase"),
    ("dividend", r"배당|dividend"),
    ("analyst", r"목표주가|투자의견|analyst|price target"),
    ("management", r"\bCEO\b|\bCFO\b|최고경영자|최고재무책임자"),
    ("options_news", r"옵션|options|미결제약정"),
    ("fed", r"연준|연은|\bFed\b|\bFOMC\b"),
    ("macro", r"\bCPI\b|\bPPI\b|\bPCE\b|\bGDP\b|고용|금리|국채|소매판매|\bPMI\b"),
    ("contract", r"계약|contract"),
    ("product", r"제품|신제품|product"),
    ("geopolitics", r"지정학|전쟁|관세|geopolitic|tariff"),
)


def classify_title(title: str) -> tuple[str, str]:
    event = next((name for name, pattern in EVENTS if re.search(pattern, title, re.I)), "company_news")
    # Only explicit earnings/guidance statements. Rumours, negation, and mixed statements remain unknown.
    hint = "unknown"
    if event in {"earnings", "guidance"} and not re.search(r"카더라|루머|부인|아니|않|rumou?r|den(?:y|ies)|not\b", title, re.I):
        up = bool(re.search(r"가이던스 상향|실적 예상 상회|guidance (?:raised|increased)|earnings beat", title, re.I))
        down = bool(re.search(r"가이던스 하향|실적 예상 하회|guidance (?:cut|lowered)|earnings miss", title, re.I))
        if up != down:
            hint = "bullish" if up else "bearish"
    return event, hint


def parse_news(payload: Any, collected_at: datetime, server_timezone: tzinfo | None = None) -> list[NewsItem]:
    if not isinstance(payload, dict) or not isinstance(payload.get("news_list"), list):
        raise SaveTickerParseError("public news-list shape changed: news_list missing")
    if len(payload["news_list"]) > 100:
        raise SaveTickerParseError("unexpected list size")
    result = []
    for row in payload["news_list"]:
        if not isinstance(row, dict):
            raise SaveTickerParseError("invalid news row")
        ident, title = row.get("id"), row.get("title")
        if isinstance(ident, bool) or not isinstance(ident, (str, int)) or not str(ident) or not isinstance(title, str) or not title.strip():
            raise SaveTickerParseError("news id/title missing")
        tags = row.get("tag_names") or []
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise SaveTickerParseError("tag_names changed")
        published = parse_date(row.get("created_at"), server_timezone)
        if published > collected_at:
            raise SaveTickerParseError("future-dated news")
        source = row.get("source")
        if source is not None and not isinstance(source, str):
            raise SaveTickerParseError("source changed")
        views = row.get("view_count")
        if views is not None and (isinstance(views, bool) or not isinstance(views, int) or views < 0):
            raise SaveTickerParseError("view_count invalid")
        tickers = extract_tickers(tags)
        event, hint = classify_title(title)
        meta = NewsMetadata(provider="saveticker", original_source=source, provider_url="https://saveticker.com/news", collected_at=collected_at,
                            original_published_at=row["created_at"], category=next((tag for tag in tags if not tag.startswith("$")), None),
                            tags=tuple(tags), event_type=event, bullish_bearish_hint=hint, view_count=views)
        # Use the observed list page. No invented per-article deep links or gated detail content.
        result.append(NewsItem("saveticker:"+str(ident), published, title.strip(), "", "", source or "SaveTicker", "OTHER", tickers,
                               metadata=meta, provenance=("saveticker",)))
    return result
