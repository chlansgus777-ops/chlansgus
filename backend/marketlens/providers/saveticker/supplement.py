"""Observed calendar/detail/report contracts. Source text stays separate from MarketLens analysis."""
from __future__ import annotations

import re
import math
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from marketlens.domain.earnings import _surprise
from .parser import SaveTickerParseError, parse_date

KST = timezone(timedelta(hours=9))


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}: self.hidden += 1
        elif tag in {"p", "br", "li", "tr", "div", "h1", "h2", "h3"}: self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "li", "tr", "div"}: self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def text(value, limit=12_000) -> str:
    if not isinstance(value, str) or len(value) > 64_000:
        raise SaveTickerParseError("invalid source text")
    parser = _PlainText()
    parser.feed(value)
    return "\n".join(x.strip() for x in "".join(parser.parts).splitlines() if x.strip())[:limit]


def blocks(value) -> str:
    if value is None: return ""
    if isinstance(value, str): return text(value)
    if not isinstance(value, list) or len(value) > 200:
        raise SaveTickerParseError("invalid content blocks")
    pieces = []
    for block in value:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise SaveTickerParseError("invalid content block")
        if block["type"] in {"text", "paragraph", "html", "heading", "list"}:
            pieces.append(text(block.get("content", "")))
    return "\n".join(pieces)[:12_000]


def required(row: dict, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise SaveTickerParseError(f"invalid {field}")
    return value.strip()


def ident(row):
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value) or len(str(value)) > 200:
        raise SaveTickerParseError("invalid id")
    return str(value)


def items(payload, name, limit):
    if not isinstance(payload, dict) or not isinstance(payload.get(name), list) or len(payload[name]) > limit:
        raise SaveTickerParseError(f"invalid {name} response")
    if not all(isinstance(row, dict) for row in payload[name]):
        raise SaveTickerParseError(f"invalid {name} row")
    return payload[name]


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2000: return None
    u = urlsplit(value)
    return value if u.scheme == "https" and u.hostname and not u.username and not u.password else None


def earnings(body: str) -> dict | None:
    """Only the observed Korean quarter/results section; guidance/ranges never count as actuals."""
    current = None
    result = {}
    for line in body.splitlines():
        match = re.fullmatch(r"([1-4]분기) 실적", line.strip())
        if match:
            current = match[1]
            result = {"period_label": current, "fiscal_year": None, "basis": "unspecified", "currency": "USD"}
            continue
        if not current: continue
        if line.strip() in {"사업 부문별 실적", "1분기 전망", "2분기 전망", "3분기 전망", "4분기 전망"}: break
        match = re.fullmatch(r"-\s*(조정 )?EPS:\s*(-?[\d,.]+)\s*달러(?:\s*\(([^)]+)\))?", line)
        if match:
            estimate = re.search(r"예상:\s*(-?[\d,.]+)\s*달러(?:\s|,|$)", match[3] or "")
            a = float(match[2].replace(",", ""))
            e = float(estimate[1].replace(",", "")) if estimate else None
            if not math.isfinite(a) or e is not None and not math.isfinite(e): raise SaveTickerParseError("invalid EPS number")
            result["eps"] = {"actual": a, "estimate": e, "surprise_fraction": _surprise(a, e), "basis": "adjusted" if match[1] else "unspecified", "source_text": line}
        match = re.fullmatch(r"-\s*(조정 )?매출:\s*([\d,]+)억(?:\s*([\d,]+)만)?\s*달러\s*\(([^)]+)\)", line)
        if match:
            estimate = re.search(r"예상:\s*([\d,]+)억(?:\s*([\d,]+)만)?\s*달러(?:\s|,|$)", match[4])
            amount = lambda a, b: int(a.replace(",", "")) * 100_000_000 + int((b or "0").replace(",", "")) * 10_000
            a = amount(match[2], match[3])
            e = amount(estimate[1], estimate[2]) if estimate else None
            result["revenue"] = {"actual": a, "estimate": e, "surprise_fraction": _surprise(a, e), "basis": "adjusted" if match[1] else "unspecified", "source_text": line}
    if result and all(result[k]["basis"] == "adjusted" for k in ("eps", "revenue") if k in result): result["basis"] = "adjusted"
    return result if "eps" in result or "revenue" in result else None


def parse_calendar(payload, at: datetime) -> list[dict]:
    rows = []
    for r in items(payload, "events", 200):
        title = required(r, "title")
        if type(r.get("event_date_only")) is not bool:
            raise SaveTickerParseError("date precision missing")
        date_only = r["event_date_only"]
        raw = required(r, "event_date")
        stamp = parse_date(raw, KST)
        stance = re.search(r"\((매파|비둘기|중립)/투표권\s*([OX])\)", title)
        rows.append({"event_id": "saveticker:" + ident(r), "title": title, "scheduled_at": None if date_only else stamp.isoformat(),
                     "event_date": raw[:10] if date_only else stamp.astimezone(KST).date().isoformat(), "date_only": date_only,
                     "event_type": "fed_speech" if stance else "macro" if re.search(r"CPI|PPI|PCE|GDP|고용|PMI|실업|FOMC|금리|재고|소매", title, re.I) else "unknown",
                     "stance": {"매파": "hawkish", "비둘기": "dovish", "중립": "neutral"}[stance[1]] if stance else None,
                     "voting_member": stance[2] == "O" if stance else None, "source_text": blocks(r.get("content")),
                     "source": "saveticker", "original_time": raw, "collected_at": at.isoformat()})
    return rows


def parse_details(payload, at: datetime) -> list[dict]:
    rows = []
    for r in items(payload, "details", 8):
        key, title = ident(r), required(r, "title")
        published = parse_date(required(r, "created_at"), KST)
        if published > at: raise SaveTickerParseError("future detail")
        content = blocks(r.get("content"))
        translations = r.get("translations") or {}
        if not isinstance(translations, dict): raise SaveTickerParseError("invalid translations")
        translated = translations.get("translated", {})
        if not isinstance(translated, dict): raise SaveTickerParseError("invalid translated content")
        locale = translations.get("source_locale")
        if locale is not None and (not isinstance(locale, str) or len(locale) > 20): raise SaveTickerParseError("invalid language")
        korean = translated.get("ko_KR") or translated.get("ko") or translated.get("ko-KR") or translated.get(translations.get("source_locale")) or {}
        summary = blocks(korean.get("summary")) if isinstance(korean, dict) else ""
        # Published original text is evidence, not an AI summary. Never invent a summary when absent.
        tickers = r.get("tickers") or []
        if not isinstance(tickers, list): raise SaveTickerParseError("invalid related tickers")
        companies = [{"ticker": required(t, "symbol"), "name": t.get("name") if isinstance(t.get("name"), str) else None} for t in tickers if isinstance(t, dict)]
        if len(companies) > 50: raise SaveTickerParseError("too many related companies")
        votes = r.get("vote_stats") or {}
        if not isinstance(votes, dict): raise SaveTickerParseError("invalid vote counts")
        counts = votes.get("vote_counts", {})
        if not isinstance(counts, dict): raise SaveTickerParseError("invalid vote counts")
        community = None
        positive, negative = counts.get("positive"), counts.get("negative")
        if type(positive) is int and type(negative) is int and min(positive, negative) >= 0:
            total = positive + negative
            community = {"positive_votes": positive, "negative_votes": negative, "total_votes": total,
                         "positive_percent": positive / total * 100 if total else None, "source": "saveticker"}
        extra = r.get("extra") or {}
        if not isinstance(extra, dict): raise SaveTickerParseError("invalid source metadata")
        original_time = extra.get("source_created_at")
        if original_time is not None:
            original_stamp = parse_date(original_time, KST)
            if original_stamp > at: raise SaveTickerParseError("future original publication")
        rows.append({"news_id": "saveticker:" + key, "title": title, "published_at": published.isoformat(),
                     "provider_summary": summary or None, "source_text": content, "related_companies": companies,
                     "source_url": safe_url(extra.get("source_url")) if isinstance(extra, dict) else None,
                     "original_published_at": original_time,
                     "language": translations.get("source_locale") if isinstance(translations, dict) else None,
                     "community_sentiment": community, "earnings": earnings(content), "source": "saveticker", "collected_at": at.isoformat()})
    return rows


def parse_reports(payload, at: datetime) -> list[dict]:
    rows = []
    for r in items(payload, "reports", 20):
        title = required(r, "title")
        stamp = parse_date(required(r, "created_at"), KST)
        if stamp > at: raise SaveTickerParseError("future report")
        kind = "pre_market" if "장전" in title or "장 전" in title else "close" if "마감" in title else "weekly" if "주간" in title or "다음 주" in title else "unknown"
        body = blocks(r.get("content"))
        rows.append({"report_id": "saveticker:" + ident(r), "title": title, "published_at": stamp.isoformat(), "report_type": kind,
                     "source_text": body, "has_readable_text": bool(body), "provider_url": "https://saveticker.com/report/" + ident(r),
                     "source": "saveticker", "collected_at": at.isoformat()})
    return rows


def parse_options(payload, at: datetime) -> list[dict]:
    """Aggregate cards only. No option contracts, implied volatility, Greeks, or current-price authority."""
    rows = []
    names = ("maxPain", "volume", "putCallRatioVolume", "putCallRatioOpenInterest", "referencePrice", "netGammaExposure", "gammaPer1Pct", "callWall", "putWall", "gammaFlip")
    for r in items(payload, "options", 10):  # the extension reads at most 10 names MarketLens asked for
        symbol = required(r, "symbol")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", symbol): raise SaveTickerParseError("invalid option symbol")
        if any(type(r.get(k)) is not bool for k in ("optionable", "snapshotIsPriorDay", "batchIsPriorDay")):
            raise SaveTickerParseError("option date flags missing")
        metrics = {}
        for k in names:
            value = r.get(k)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise SaveTickerParseError("invalid option metric")
            metrics[k] = value
        dates = {}
        for k in ("snapshotDate", "batchDate", "nearestExpiry"):
            raw = r.get(k)
            if raw is not None:
                from datetime import date
                if not isinstance(raw, str): raise SaveTickerParseError("invalid option date")
                try: date.fromisoformat(raw)
                except ValueError as exc: raise SaveTickerParseError("invalid option date") from exc
            dates[k] = raw
        rows.append({"option_id": "saveticker:" + symbol, "title": symbol + " 옵션 집계", "related_companies": [{"ticker": symbol, "name": None}],
                     "metrics": metrics, "dates": dates, "snapshot_prior_day": r["snapshotIsPriorDay"], "batch_prior_day": r["batchIsPriorDay"],
                     "optionable": r["optionable"], "source": "saveticker", "collected_at": at.isoformat(), "data_kind": "aggregate_only"})
    return rows


PARSERS = {"calendar": parse_calendar, "details": parse_details, "reports": parse_reports, "options": parse_options}
