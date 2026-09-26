"""Local point-in-time market data store (SQLite/PostgreSQL).

Providers write into the store; the scanner reads from it first. This removes per-ticker API calls from
the whole-market stages and keeps history (universe membership, bars, fundamental vintages) available
for point-in-time analysis and replay.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketlens.application.codec import decode, encode
from marketlens.domain.enums import Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import UTC
from marketlens.domain.corporate_actions import SplitEvent, split_factor
from marketlens.domain.estimates import EstimateObservation
from marketlens.domain.guidance import GuidanceItem
from marketlens.infrastructure.db.models import AppSettingRow, CorporateActionRow, EstimateSnapshotRow, GuidanceRow, FundamentalVintageRow, PriceBarRow, SecurityRow


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
                    splits = [SplitEvent(e.ticker, e.execution_date, e.split_from, e.split_to, e.source)
                              for e in s.scalars(select(CorporateActionRow).where(CorporateActionRow.ticker == row.ticker))]
                    f = split_factor(splits, row.shares_as_of, d) if row.shares_as_of else 1.0
                    row.market_cap = bar.close * row.shares_outstanding * f
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

    # ------------------------------------------------------------------ checkpoints
    def get_setting(self, key: str) -> str | None:
        with self.sf() as s:
            row = s.get(AppSettingRow, f"{self.mode}:{key}")
            return row.value if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.sf() as s:
            row = s.get(AppSettingRow, f"{self.mode}:{key}")
            if row is None:
                s.add(AppSettingRow(key=f"{self.mode}:{key}", value=value))
            else:
                row.value = value
            s.commit()

    # ------------------------------------------------------------------ corporate actions (splits)
    def save_splits(self, events: Iterable[SplitEvent]) -> list[SplitEvent]:
        """Append new split events; returns the ones not seen before."""
        new: list[SplitEvent] = []
        with self.sf() as s:
            for e in events:
                if s.get(CorporateActionRow, (e.ticker, e.execution_date, e.source)) is None:
                    s.add(CorporateActionRow(ticker=e.ticker, execution_date=e.execution_date, source=e.source, kind="SPLIT",
                                             split_from=e.split_from, split_to=e.split_to, observed_at=_now()))
                    new.append(e)
            s.commit()
        return new

    def splits(self, ticker: str) -> list[SplitEvent]:
        with self.sf() as s:
            rows = list(s.scalars(select(CorporateActionRow).where(CorporateActionRow.ticker == ticker).order_by(CorporateActionRow.execution_date)))
        return [SplitEvent(r.ticker, r.execution_date, r.split_from, r.split_to, r.source) for r in rows]

    def adjust_bars_for_splits(self) -> int:
        """Rescale stored bars that were retrieved *before* a split became effective (vendor-adjusted bars
        fetched after the split are already on the new basis). Each split is applied exactly once."""
        n = 0
        with self.sf() as s:
            for ev in list(s.scalars(select(CorporateActionRow).where(CorporateActionRow.bars_adjusted_at.is_(None)))):
                if ev.split_from <= 0 or ev.split_to <= 0:
                    continue
                ratio = ev.split_to / ev.split_from
                effective = datetime.combine(ev.execution_date, datetime.min.time(), tzinfo=UTC)
                for b in s.scalars(select(PriceBarRow).where(PriceBarRow.ticker == ev.ticker, PriceBarRow.day < ev.execution_date, PriceBarRow.retrieved_at < effective)):
                    b.open, b.high, b.low, b.close, b.volume = b.open / ratio, b.high / ratio, b.low / ratio, b.close / ratio, b.volume * ratio
                    n += 1
                ev.bars_adjusted_at = _now()
            s.commit()
        return n

    # ------------------------------------------------------------------ consensus estimate snapshots (append-only)
    def save_estimates(self, obs: Iterable[EstimateObservation]) -> int:
        """One snapshot per (ticker, provider, period, day); the first one of a day is kept, nothing is
        ever updated or deleted — revisions are computed from this history."""
        n = 0
        with self.sf() as s:
            for o in obs:
                exists = s.scalars(select(EstimateSnapshotRow.id).where(EstimateSnapshotRow.ticker == o.ticker, EstimateSnapshotRow.provider == o.provider,
                                                                      EstimateSnapshotRow.period == o.period, EstimateSnapshotRow.observed_on == o.observed_on).limit(1)).first()
                if exists is not None:
                    continue
                s.add(EstimateSnapshotRow(ticker=o.ticker, provider=o.provider, period=o.period, period_type=o.period_type, period_end=o.period_end,
                                          eps_estimate=o.eps, revenue_estimate=o.revenue, analyst_count=o.analyst_count, eps_high=o.eps_high, eps_low=o.eps_low,
                                          provider_revisions={**dict(o.provider_revisions), "_horizon": o.horizon, "_report_date": o.report_date.isoformat() if o.report_date else None},
                                          provider_timestamp=o.provider_timestamp, observed_on=o.observed_on, observed_at=_now()))
                n += 1
            s.commit()
        return n

    def estimate_history(self, ticker: str, until: date) -> list[EstimateObservation]:
        with self.sf() as s:
            rows = list(s.scalars(select(EstimateSnapshotRow).where(EstimateSnapshotRow.ticker == ticker, EstimateSnapshotRow.observed_on <= until).order_by(EstimateSnapshotRow.observed_on)))
        out = []
        for r in rows:
            extra = dict(r.provider_revisions or {})
            horizon = str(extra.pop("_horizon", "") or "")
            rd = extra.pop("_report_date", None)
            out.append(EstimateObservation(r.ticker, r.provider, r.period, r.period_type, r.period_end, r.observed_on, r.eps_estimate, r.revenue_estimate,
                                           r.analyst_count, r.eps_high, r.eps_low, horizon, date.fromisoformat(rd) if rd else None, extra, r.provider_timestamp))
        return out

    def last_estimate_day(self, ticker: str, provider: str) -> date | None:
        with self.sf() as s:
            return s.scalars(select(func.max(EstimateSnapshotRow.observed_on)).where(EstimateSnapshotRow.ticker == ticker, EstimateSnapshotRow.provider == provider)).first()

    # ------------------------------------------------------------------ SEC guidance (append-only)
    def save_guidance(self, ticker: str, accession: str, filed_at: datetime, url: str, items: Iterable[GuidanceItem]) -> int:
        n = 0
        with self.sf() as s:
            if s.scalars(select(GuidanceRow.id).where(GuidanceRow.ticker == ticker, GuidanceRow.accession == accession).limit(1)).first() is not None:
                return 0  # a filing is extracted once; the extraction is never rewritten
            for it in items:
                s.add(GuidanceRow(ticker=ticker, accession=accession, filed_at=filed_at, source_url=url[:300], metric=it.metric, period_label=it.period_label,
                                  low=it.low, high=it.high, unit=it.unit or "-", sentence=it.sentence, status=it.status, confidence=it.confidence, observed_at=_now()))
                n += 1
            if n == 0:  # remember that the filing was read and had no guidance sentences
                s.add(GuidanceRow(ticker=ticker, accession=accession, filed_at=filed_at, source_url=url[:300], metric="none", period_label=None,
                                  low=None, high=None, unit="-", sentence="(보도자료에서 가이던스 문장을 찾지 못함)", status="GUIDANCE_UNCLEAR", confidence="LOW", observed_at=_now()))
            s.commit()
        return n

    def guidance(self, ticker: str, until: datetime) -> list[GuidanceRow]:
        with self.sf() as s:
            return list(s.scalars(select(GuidanceRow).where(GuidanceRow.ticker == ticker, GuidanceRow.filed_at <= until).order_by(GuidanceRow.filed_at, GuidanceRow.id)))

    # ------------------------------------------------------------------ fundamentals (vintages)
    def save_quarters(self, ticker: str, quarters: Iterable[QuarterlyFinancials]) -> None:
        """One row per (period, first filing). Re-fetching the same period only ever *adds* revisions
        (later filings with different values) and refreshes ``retrieved_at`` so a TTL-valid store is not
        downloaded again. First-reported values are never overwritten."""
        with self.sf() as s:
            for q in quarters:
                key = (ticker, q.period_end, q.filed_date, q.source)
                row = s.get(FundamentalVintageRow, key)
                if row is None:
                    s.add(FundamentalVintageRow(ticker=ticker, period_end=q.period_end, filed_date=q.filed_date, source=q.source, payload=encode(q), retrieved_at=_now()))
                    continue
                old = decode(QuarterlyFinancials, row.payload)
                merged = _merge_revisions(old.revisions, q.revisions)
                if merged != dict(old.revisions):
                    row.payload = encode(replace(old, revisions=merged))
                row.retrieved_at = _now()
            s.commit()

    def quarters(self, ticker: str, max_age: timedelta) -> list[QuarterlyFinancials] | None:
        """Stored quarters if retrieved within ``max_age``; None means "fetch from the provider"."""
        with self.sf() as s:
            rows = list(s.scalars(select(FundamentalVintageRow).where(FundamentalVintageRow.ticker == ticker)))
        if not rows or _now() - max(r.retrieved_at for r in rows) > max_age:
            return None
        latest: dict[date, QuarterlyFinancials] = {}
        for r in sorted(rows, key=lambda r: r.filed_date):
            q = decode(QuarterlyFinancials, r.payload)
            if r.period_end not in latest:
                latest[r.period_end] = q  # earliest vintage = first reported
            else:  # a later first-filing row for the same period: its values are revisions of the first
                base = latest[r.period_end]
                extra = {k: ((r.filed_date, v),) for k in ("revenue", "net_income", "eps_diluted", "shares_diluted", "shares_outstanding")
                         if (v := getattr(q, k)) is not None and getattr(base, k) is not None and abs(v - getattr(base, k)) > 1e-9 * max(1.0, abs(getattr(base, k)))}
                latest[r.period_end] = replace(base, revisions=_merge_revisions(_merge_revisions(base.revisions, q.revisions), extra))
        return [latest[d] for d in sorted(latest)]


def _merge_revisions(a: Mapping[str, Iterable[Iterable[Any]]], b: Mapping[str, Iterable[Iterable[Any]]]) -> dict[str, tuple[tuple[date, float], ...]]:
    out: dict[str, set[tuple[date, float]]] = {}
    for src in (a, b):
        for k, obs in src.items():
            for fd, v in obs:
                out.setdefault(k, set()).add((fd if isinstance(fd, date) else date.fromisoformat(str(fd)), float(v)))
    return {k: tuple(sorted(v)) for k, v in out.items()}
