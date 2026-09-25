"""Local point-in-time market data store (SQLite/PostgreSQL).

Providers write into the store; the scanner reads from it first. This removes per-ticker API calls from
the whole-market stages and keeps history (universe membership, bars, fundamental vintages) available
for point-in-time analysis and replay.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketlens.application.codec import decode, encode
from marketlens.domain.enums import Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import UTC
from marketlens.infrastructure.db.models import FundamentalVintageRow, PriceBarRow, SecurityRow


def _now() -> datetime:
    return datetime.now(tz=UTC)


class MarketStore:
    def __init__(self, sf: sessionmaker[Session], mode: str) -> None:
        self.sf = sf
        self.mode = mode

    # ------------------------------------------------------------------ universe
    def sync_universe(self, current: Iterable[Security], today: date) -> dict[str, int]:
        """Upsert today's listings; names that disappeared are marked delisted (kept in history)."""
        seen: set[str] = set()
        added = updated = delisted = 0
        with self.sf() as s:
            existing = {r.ticker: r for r in s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode))}
            for sec in current:
                seen.add(sec.ticker)
                row = existing.get(sec.ticker)
                if row is None:
                    s.add(SecurityRow(ticker=sec.ticker, company_name=sec.company_name, exchange=sec.exchange.value, sector=sec.sector,
                                      industry=sec.industry, market_cap=sec.market_cap, is_etf=sec.is_etf, is_adr=sec.is_adr,
                                      country_of_incorporation=sec.country_of_incorporation, currency=sec.currency, active=True,
                                      listed_at=sec.listed_at, delisted_at=None, mode=self.mode, updated_at=_now(), first_seen=today))
                    added += 1
                else:
                    row.company_name, row.exchange = sec.company_name, sec.exchange.value
                    if sec.sector != "Unknown":
                        row.sector, row.industry = sec.sector, sec.industry
                    if sec.market_cap is not None:
                        row.market_cap = sec.market_cap
                    if not row.active:
                        row.active, row.delisted_at = True, None  # relisted
                    row.updated_at = _now()
                    updated += 1
            for t, row in existing.items():
                if t not in seen and row.active:
                    row.active, row.delisted_at = False, today
                    delisted += 1
            s.commit()
        return {"added": added, "updated": updated, "delisted": delisted}

    def securities(self, as_of: date | None = None) -> list[Security]:
        """Universe as of a date: listed (first seen) on or before it and not yet delisted."""
        with self.sf() as s:
            rows = list(s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode)))
        out = []
        for r in rows:
            if as_of is not None:
                if r.first_seen is not None and r.first_seen > as_of:
                    continue
                if r.delisted_at is not None and r.delisted_at <= as_of:
                    continue
            elif not r.active:
                continue
            out.append(Security(ticker=r.ticker, company_name=r.company_name, exchange=Exchange(r.exchange), sector=r.sector,
                                industry=r.industry, market_cap=r.market_cap, is_etf=r.is_etf, is_adr=r.is_adr,
                                country_of_incorporation=r.country_of_incorporation, currency=r.currency, active=r.active,
                                listed_at=r.listed_at or r.first_seen, delisted_at=r.delisted_at))
        return out

    def set_profile(self, ticker: str, profile: Mapping[str, Any]) -> None:
        with self.sf() as s:
            row = s.get(SecurityRow, ticker)
            if row is None:
                return
            row.sic = profile.get("sic")
            row.sector, row.industry = profile.get("sector", row.sector), profile.get("industry", row.industry)
            row.is_adr = bool(profile.get("foreign_issuer", row.is_adr))
            row.country_of_incorporation = str(profile.get("country", row.country_of_incorporation))[:8]
            row.profile_updated_at = _now()
            s.commit()

    def profile_age(self, ticker: str) -> timedelta | None:
        with self.sf() as s:
            row = s.get(SecurityRow, ticker)
            if row is None or row.profile_updated_at is None:
                return None
            return _now() - row.profile_updated_at

    def set_shares(self, shares: Mapping[str, tuple[float, date]]) -> int:
        n = 0
        with self.sf() as s:
            for t, (val, d) in shares.items():
                row = s.get(SecurityRow, t)
                if row is not None:
                    row.shares_outstanding, row.shares_as_of = val, d
                    n += 1
            s.commit()
        return n

    def refresh_market_caps(self) -> int:
        """market cap = cover-page shares outstanding × last stored close."""
        n = 0
        with self.sf() as s:
            last = dict(s.execute(select(PriceBarRow.ticker, func.max(PriceBarRow.day)).group_by(PriceBarRow.ticker)).all())
            for row in s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode, SecurityRow.shares_outstanding.is_not(None))):
                d = last.get(row.ticker)
                if d is None:
                    continue
                bar = s.scalars(select(PriceBarRow).where(PriceBarRow.ticker == row.ticker, PriceBarRow.day == d).limit(1)).first()
                if bar is not None and row.shares_outstanding:
                    row.market_cap = bar.close * row.shares_outstanding
                    n += 1
            s.commit()
        return n

    # ------------------------------------------------------------------ bars
    def save_bars(self, ticker: str, bars: Iterable[Bar], source: str) -> int:
        n = 0
        with self.sf() as s:
            have = {d for (d,) in s.execute(select(PriceBarRow.day).where(PriceBarRow.ticker == ticker, PriceBarRow.source == source))}
            for b in bars:
                if b.day not in have:
                    s.add(PriceBarRow(ticker=ticker, day=b.day, source=source, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, retrieved_at=_now()))
                    n += 1
            s.commit()
        return n

    def save_grouped(self, day: date, bars: Mapping[str, Bar], source: str) -> int:
        with self.sf() as s:
            have = {t for (t,) in s.execute(select(PriceBarRow.ticker).where(PriceBarRow.day == day, PriceBarRow.source == source))}
            rows = [PriceBarRow(ticker=t, day=day, source=source, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, retrieved_at=_now())
                    for t, b in bars.items() if t not in have]
            s.add_all(rows)
            s.commit()
            return len(rows)

    def stored_days(self, source: str) -> set[date]:
        with self.sf() as s:
            return {d for (d,) in s.execute(select(PriceBarRow.day).where(PriceBarRow.source == source).distinct())}

    def bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        with self.sf() as s:
            rows = s.scalars(select(PriceBarRow).where(PriceBarRow.ticker == ticker, PriceBarRow.day >= start, PriceBarRow.day <= end).order_by(PriceBarRow.day))
            by_day: dict[date, Bar] = {}
            for r in rows:
                by_day.setdefault(r.day, Bar(r.day, r.open, r.high, r.low, r.close, r.volume))
        return [by_day[d] for d in sorted(by_day)]

    def last_bars_all(self, start: date, end: date) -> dict[str, list[Bar]]:
        """All stored bars in a window, grouped by ticker (one query for the whole market)."""
        out: dict[str, dict[date, Bar]] = {}
        with self.sf() as s:
            for r in s.scalars(select(PriceBarRow).where(PriceBarRow.day >= start, PriceBarRow.day <= end)):
                out.setdefault(r.ticker, {}).setdefault(r.day, Bar(r.day, r.open, r.high, r.low, r.close, r.volume))
        return {t: [d[k] for k in sorted(d)] for t, d in out.items()}

    # ------------------------------------------------------------------ fundamentals (vintages)
    def save_quarters(self, ticker: str, quarters: Iterable[QuarterlyFinancials]) -> None:
        with self.sf() as s:
            for q in quarters:
                key = (ticker, q.period_end, q.filed_date, q.source)
                if s.get(FundamentalVintageRow, key) is None:
                    s.add(FundamentalVintageRow(ticker=ticker, period_end=q.period_end, filed_date=q.filed_date, source=q.source, payload=encode(q), retrieved_at=_now()))
            s.commit()

    def quarters(self, ticker: str, max_age: timedelta) -> list[QuarterlyFinancials] | None:
        """Stored quarters if retrieved within ``max_age``; None means "fetch from the provider"."""
        with self.sf() as s:
            rows = list(s.scalars(select(FundamentalVintageRow).where(FundamentalVintageRow.ticker == ticker)))
        if not rows or _now() - max(r.retrieved_at for r in rows) > max_age:
            return None
        latest: dict[date, QuarterlyFinancials] = {}
        for r in sorted(rows, key=lambda r: r.filed_date):
            latest.setdefault(r.period_end, decode(QuarterlyFinancials, r.payload))  # earliest vintage = PIT value
        return [latest[d] for d in sorted(latest)]
