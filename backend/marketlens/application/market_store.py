"""Local point-in-time market data store (SQLite/PostgreSQL).

Providers write into the store; the scanner reads from it first. This removes per-ticker API calls from
the whole-market stages and keeps history (universe membership, bars, fundamental vintages) available
for point-in-time analysis and replay.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketlens.application.codec import decode, encode
from marketlens.domain.enums import Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import NY, UTC
from marketlens.domain.corporate_actions import SplitEvent, split_factor
from marketlens.domain.estimates import EstimateObservation
from marketlens.domain.guidance import GuidanceItem
from marketlens.infrastructure.db.models import (AppSettingRow, CorporateActionRow, EstimateSnapshotRow, FundamentalVintageRow, GuidanceRow, IngestionManifestRow,
                                               PriceBarRow, SecurityRow, TickerHistoryRow)


def _now() -> datetime:
    return datetime.now(tz=UTC)


RELIST_GAP_DAYS = 7  # absent from the SEC file for up to a week and back: a data gap, not a delisting


class MarketStore:
    def __init__(self, sf: sessionmaker[Session], mode: str) -> None:
        self.sf = sf
        self.mode = mode

    # ------------------------------------------------------------------ universe
    def sync_universe(self, current: Iterable[Security], today: date) -> dict[str, int]:
        """Upsert today's listings; names that disappeared are marked delisted (kept in history).

        Security master (the SEC CIK is the identity, the ticker only a label). Two passes, so the result does
        not depend on the order of the listing file:
        1. REUSE: a known ticker now carries a different CIK → the old company's stored rows (bars,
           fundamentals, splits, estimates, guidance) move to an archive key ``TICKER~CIK`` together with its
           security row and its rename links; the ticker row starts over for the new company.
        2. For every listed name without its own row (or whose row was just reset): if its CIK belonged to a
           row that is no longer listed (an old ticker, or an archive created in pass 1), the two are linked —
           RENAME when the old name was still listed until now (a rename is not a delisting), RELIST when the
           old name had been delisted earlier (its delisting stays in history). Otherwise NEW.
        An unknown CIK (rows stored before the security master existed) is adopted, not judged."""
        cur = list(current)
        listed_now = {c.ticker for c in cur}
        seen: set[str] = set()
        n = {"added": 0, "updated": 0, "delisted": 0, "renamed": 0, "relisted": 0, "reused": 0, "unresolved": 0}
        with self.sf() as s:
            existing = {r.ticker: r for r in s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode))}
            reset: set[str] = set()
            # pass 1 — a ticker that changed hands, or a ticker that comes back after its listing interval ended
            for sec in cur:
                row = existing.get(sec.ticker)
                if row is None:
                    continue
                if row.cik is not None and sec.cik is not None and row.cik != sec.cik:
                    arch = self._archive_reused(s, row, sec, today, existing, "REUSE")
                    n["reused"] += 1
                elif not row.active and "~" not in row.ticker and (
                        row.successor is not None  # renamed away earlier, now back (AAA → BBB → AAA)
                        or (row.delisted_at is not None and self._really_delisted(s, row.ticker, row.delisted_at, today))):
                    # the same company, a new listing interval: the old interval (with its delisting or rename)
                    # is kept as an archive row
                    arch = self._archive_reused(s, row, sec, today, existing, "RETURN")
                else:
                    continue
                existing[arch.ticker] = arch
                reset.add(sec.ticker)
            # pass 2 — links, new names, updates
            pending_by_cik: dict[int, list[str]] = {}
            for sec in cur:
                if sec.cik is not None and (sec.ticker not in existing or sec.ticker in reset):
                    pending_by_cik.setdefault(sec.cik, []).append(sec.ticker)
            used: set[str] = set()
            unresolved_ciks: set[int] = set()  # a new ticker of this CIK could not be paired with the name(s) that vanished
            for sec in cur:
                seen.add(sec.ticker)
                row = existing.get(sec.ticker)
                if row is None or sec.ticker in reset:
                    src, ambiguous = self._link_source(existing, sec, listed_now, pending_by_cik.get(sec.cik or -1, []), used, today)
                    if src is not None:
                        used.add(src.ticker)
                    elif ambiguous and sec.cik is not None:
                        unresolved_ciks.add(sec.cik)
                    if row is None:
                        row = SecurityRow(ticker=sec.ticker, company_name=sec.company_name, exchange=sec.exchange.value, sector=sec.sector,
                                          industry=sec.industry, market_cap=sec.market_cap, is_etf=sec.is_etf, is_adr=sec.is_adr,
                                          country_of_incorporation=sec.country_of_incorporation, currency=sec.currency, active=True,
                                          listed_at=sec.listed_at, delisted_at=None, mode=self.mode, updated_at=_now(), first_seen=today, cik=sec.cik)
                        s.add(row)
                        existing[sec.ticker] = row
                    if src is not None:
                        relist = src.delisted_at is not None and src.delisted_at < today
                        row.predecessor = src.ticker
                        # first_seen stays today: before this day the universe lists the OLD name (one row per company per day)
                        row.listed_at = today if relist else (src.listed_at or src.first_seen)
                        row.sector, row.industry, row.sic, row.profile_updated_at = src.sector, src.industry, src.sic, src.profile_updated_at
                        row.is_adr, row.country_of_incorporation = src.is_adr, src.country_of_incorporation
                        row.shares_outstanding, row.shares_as_of = src.shares_outstanding, src.shares_as_of
                        if not relist:
                            row.market_cap = src.market_cap
                        src.successor, src.renamed_on, src.active = sec.ticker, today, False
                        if not relist:
                            src.delisted_at = None  # a rename is not a delisting
                        ev = "RELIST" if relist else "RENAME"
                        s.add(TickerHistoryRow(mode=self.mode, event=ev, ticker=sec.ticker, cik=sec.cik, other_ticker=src.ticker, effective=today, observed_at=_now()))
                        n["relisted" if relist else "renamed"] += 1
                    elif sec.ticker not in reset:
                        s.add(TickerHistoryRow(mode=self.mode, event="NEW", ticker=sec.ticker, cik=sec.cik, effective=today, observed_at=_now()))
                        n["added"] += 1
                    continue
                row.company_name, row.exchange = sec.company_name, sec.exchange.value
                if row.cik is None:
                    row.cik = sec.cik
                if sec.sector != "Unknown":
                    row.sector, row.industry = sec.sector, sec.industry
                if sec.market_cap is not None:
                    row.market_cap = sec.market_cap
                if not row.active:
                    # back after an absence that was a data gap (kept trading, or only a few days): the recorded
                    # "delisting" was wrong and is withdrawn — audited in the ticker history
                    s.add(TickerHistoryRow(mode=self.mode, event="GAP", ticker=sec.ticker, cik=sec.cik, effective=row.delisted_at or today, observed_at=_now()))
                    row.active, row.delisted_at = True, None
                    row.successor = row.renamed_on = None
                row.updated_at = _now()
                n["updated"] += 1
            for t, row in existing.items():
                if t not in seen and row.active and "~" not in t:
                    if row.cik is not None and row.cik in unresolved_ciks:
                        # the company is still listed under a new ticker we could not pair with this one: the name ended
                        # here, but this is NOT a delisting (outcomes stay pending instead of taking a final last price)
                        row.active, row.renamed_on = False, today
                        s.add(TickerHistoryRow(mode=self.mode, event="UNRESOLVED", ticker=t, cik=row.cik, effective=today, observed_at=_now()))
                        n["unresolved"] += 1
                        continue
                    row.active, row.delisted_at = False, today
                    n["delisted"] += 1
            s.commit()
        return n

    @staticmethod
    def _really_delisted(s: Session, ticker: str, absent_since: date, today: date) -> bool:
        """Was the name really off the market, or only missing from the SEC file? Decided by evidence, not by
        the time between two syncs (a desktop app may not sync for weeks):
        - it kept trading: grouped-daily bars exist for (almost) every session of the absence → a data gap;
        - no bars during the absence and more than ``RELIST_GAP_DAYS`` passed → a real delisting (relist);
        - otherwise (a short absence, no evidence either way) → a data gap."""
        from marketlens.domain.market_calendar import is_trading_day

        sessions, d = [], absent_since
        while d < today:
            if is_trading_day(d):
                sessions.append(d)
            d += timedelta(days=1)
        if sessions:
            have = {r for (r,) in s.execute(select(PriceBarRow.day).where(PriceBarRow.ticker == ticker, PriceBarRow.day >= sessions[0], PriceBarRow.day <= sessions[-1]))}
            missing = sum(1 for x in sessions if x not in have)
            if have and missing <= max(1, len(sessions) // 10):
                return False  # it traded through the "absence"
        return (today - absent_since).days > RELIST_GAP_DAYS

    @staticmethod
    def _link_source(existing: Mapping[str, SecurityRow], sec: Security, listed_now: set[str], new_of_cik: list[str], used: set[str],
                     today: date) -> tuple[SecurityRow | None, bool]:
        """(row this listing continues, ambiguous). Candidates are unlisted rows of the same CIK. Names that were
        listed until now (still active, or archived today) come first; a class retired long ago is only a candidate
        when nothing was listed until now (a relist). With several candidates or several new tickers of one CIK
        (share classes renamed together) the pair must be decidable from the class suffix (XA→YA, BRK-B→…-B);
        otherwise nothing is linked (``ambiguous``) — a wrong link would hand one class the other's history."""
        if sec.cik is None:
            return None, False
        cands = [r for r in existing.values()
                 if r.cik == sec.cik and r.ticker != sec.ticker and r.ticker not in listed_now and r.successor is None and r.ticker not in used]
        recent = [r for r in cands if r.active or r.delisted_at == today]
        cands = recent or cands
        if not cands:
            return None, False
        if len(cands) == 1 and len(new_of_cik) <= 1:
            return cands[0], False

        def cls(t: str) -> str:
            t = t.split("~", 1)[0]
            return re.split(r"[-./]", t)[-1] if re.search(r"[-./]", t) else t[-1:]

        mine = [r for r in cands if cls(r.ticker) == cls(sec.ticker)]
        rivals = [t for t in new_of_cik if t != sec.ticker and cls(t) == cls(sec.ticker)]
        if len(mine) == 1 and not rivals and len({cls(r.ticker) for r in cands}) == len(cands):
            return mine[0], False
        return None, True  # undecidable pairing: left unlinked rather than guessed

    def _archive_reused(self, s: Session, row: SecurityRow, sec: Security, today: date, existing: Mapping[str, SecurityRow], event: str) -> SecurityRow:
        """The ticker now names another company: move the old company's rows to an archive key (``TICKER~CIK``,
        ``TICKER~CIK.2`` … when the ticker changed hands before) and keep its rename links pointing at it."""
        from sqlalchemy import delete, update

        base = f"{row.ticker}~{row.cik}"
        arch, k = base, 1
        while arch in existing or s.get(SecurityRow, arch) is not None:
            k += 1
            arch = f"{base}.{k}"
        for model in (PriceBarRow, FundamentalVintageRow, CorporateActionRow, EstimateSnapshotRow, GuidanceRow):
            s.execute(update(model).where(model.ticker == row.ticker).values(ticker=arch).execution_options(synchronize_session=False))
        s.execute(delete(IngestionManifestRow).where(IngestionManifestRow.ticker == row.ticker, IngestionManifestRow.mode == self.mode))
        # the old company: delisted when its name was still in use until now; a renamed-away or earlier delisted
        # company keeps its own record
        delisted = row.delisted_at if row.delisted_at is not None else (None if row.successor else today)
        a = SecurityRow(ticker=arch, company_name=row.company_name, exchange=row.exchange, sector=row.sector, industry=row.industry, market_cap=None,
                        is_etf=row.is_etf, is_adr=row.is_adr, country_of_incorporation=row.country_of_incorporation, currency=row.currency, active=False,
                        listed_at=row.listed_at, delisted_at=delisted, mode=self.mode, updated_at=_now(), first_seen=row.first_seen,
                        sic=row.sic, cik=row.cik, shares_outstanding=row.shares_outstanding, shares_as_of=row.shares_as_of,
                        predecessor=row.predecessor, successor=row.successor, renamed_on=row.renamed_on, profile_updated_at=row.profile_updated_at)
        s.add(a)
        for other in existing.values():  # rename links now point at the archive
            if other is row:
                continue
            if other.predecessor == row.ticker:
                other.predecessor = arch
            if other.successor == row.ticker:
                other.successor = arch
        s.add(TickerHistoryRow(mode=self.mode, event=event, ticker=row.ticker, cik=sec.cik, other_cik=row.cik, archived_as=arch, effective=today, observed_at=_now()))
        row.company_name, row.cik, row.first_seen, row.listed_at, row.delisted_at, row.active = sec.company_name, sec.cik, today, sec.listed_at, None, True
        row.sector, row.industry, row.sic, row.profile_updated_at = "Unknown", "Unknown", None, None
        row.shares_outstanding = row.shares_as_of = row.market_cap = None
        row.predecessor = row.successor = row.renamed_on = None
        s.flush()
        return a

    def audit_history(self) -> list[dict[str, Any]]:
        """Read-only check of the stored security history for records an earlier version may have written wrong
        (evaluation 5, R7). Nothing is changed; each finding says what to review.

        - NO_CIK: rows stored before the security master (identity cannot be checked).
        - TRADED_AFTER_DELISTING: a delisted name whose bars continue after the delisting date (likely a data gap
          recorded as a delisting).
        - DELISTING_LOOKS_LIKE_RENAME: a delisted name whose CIK appears under another, unlinked ticker within
          7 days (likely a rename recorded as a delisting).
        - UNRESOLVED: a name that ended while its company continued under a ticker that could not be paired."""
        out: list[dict[str, Any]] = []
        with self.sf() as s:
            rows = list(s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode)))
            unresolved = {t for (t,) in s.execute(select(TickerHistoryRow.ticker).where(TickerHistoryRow.mode == self.mode, TickerHistoryRow.event == "UNRESOLVED"))}
            for r in rows:
                if r.cik is None and "~" not in r.ticker:
                    out.append({"kind": "NO_CIK", "ticker": r.ticker, "detail": "CIK 없음 — 종목 마스터 이전 저장분, 티커 재사용 여부를 확인할 수 없음"})
                if r.delisted_at is not None:
                    after = s.execute(select(func.count(), func.max(PriceBarRow.day)).where(PriceBarRow.ticker == r.ticker, PriceBarRow.day > r.delisted_at)).one()
                    if after[0]:
                        out.append({"kind": "TRADED_AFTER_DELISTING", "ticker": r.ticker,
                                    "detail": f"상장폐지일 {r.delisted_at.isoformat()} 이후 일봉 {after[0]}개(마지막 {after[1].isoformat()}) — 데이터 누락을 상장폐지로 기록했을 가능성"})
                    if r.cik is not None and r.successor is None:
                        near = [o for o in rows if o is not r and o.cik == r.cik and o.predecessor is None and o.first_seen is not None
                                and 0 <= (o.first_seen - r.delisted_at).days <= RELIST_GAP_DAYS and "~" not in o.ticker]
                        for o in near:
                            out.append({"kind": "DELISTING_LOOKS_LIKE_RENAME", "ticker": r.ticker,
                                        "detail": f"같은 CIK {r.cik}가 {o.first_seen.isoformat()}에 {o.ticker}로 나타남(연결 없음) — 개명을 상장폐지로 기록했을 가능성"})
                if r.ticker in unresolved:
                    out.append({"kind": "UNRESOLVED", "ticker": r.ticker, "detail": "같은 회사의 새 티커와 짝을 정하지 못함 — 성과는 확정하지 않고 보류 중, 사람이 확인 필요"})
        return out

    def is_active(self, ticker: str) -> bool | None:
        """True / False for a known security, None when the ticker is not in the security master."""
        with self.sf() as s:
            row = s.get(SecurityRow, ticker)
            return None if row is None or row.mode != self.mode else bool(row.active)

    def resolve(self, ticker: str, on: date, session: Session | None = None) -> str:
        """The storage key that held ``ticker``'s data on day ``on``: a ticker reused later by another company
        resolves to the archive of the company that used it then. Pass the caller's open ``session`` (a nested
        session would reset a shared in-memory SQLite connection)."""
        if session is not None:
            return self._resolve(session, ticker, on)
        with self.sf() as s:
            return self._resolve(s, ticker, on)

    def _resolve(self, s: Session, ticker: str, on: date) -> str:
        ev = s.scalars(select(TickerHistoryRow).where(TickerHistoryRow.mode == self.mode, TickerHistoryRow.ticker == ticker, TickerHistoryRow.archived_as.is_not(None),
                                                     TickerHistoryRow.effective > on).order_by(TickerHistoryRow.effective).limit(1)).first()
        return ev.archived_as if ev is not None and ev.archived_as else ticker

    def aliases(self, ticker: str) -> list[tuple[str, date | None, date | None]]:
        """(storage ticker, from, until) covering one company across renames: its own rows, a predecessor's
        rows before the rename and a successor's rows from the rename on."""
        out: list[tuple[str, date | None, date | None]] = [(ticker, None, None)]
        with self.sf() as s:
            rows = {r.ticker: r for r in s.scalars(select(SecurityRow).where(SecurityRow.mode == self.mode, (SecurityRow.predecessor.is_not(None)) | (SecurityRow.successor.is_not(None))))}
        seen = {ticker}
        cur = rows.get(ticker)
        while cur is not None and cur.predecessor and cur.predecessor not in seen:  # backwards
            prev = rows.get(cur.predecessor)
            if prev is not None and prev.delisted_at is not None:
                break  # RELIST after a delisting: the price history does not run through the gap
            until = prev.renamed_on if prev is not None else None
            out.append((cur.predecessor, None, until))
            seen.add(cur.predecessor)
            cur = prev
        cur = rows.get(ticker)
        while cur is not None and cur.successor and cur.successor not in seen and cur.delisted_at is None:  # forwards
            out.append((cur.successor, cur.renamed_on, None))
            seen.add(cur.successor)
            cur = rows.get(cur.successor)
        if len(out) > 1:  # own rows only within the own ticker's lifetime
            own = rows.get(ticker)
            first = next((a for a in out[1:] if a[2] is not None), None)
            own_from = first[2] if first else None
            own_until = own.renamed_on if own is not None and own.successor else None
            out[0] = (ticker, own_from, own_until)
        return out

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
                if r.renamed_on is not None and r.renamed_on <= as_of:
                    continue  # listed under its successor ticker from then on
            elif not r.active:
                continue
            out.append(Security(ticker=r.ticker, company_name=r.company_name, exchange=Exchange(r.exchange), sector=r.sector,
                                industry=r.industry, market_cap=r.market_cap, is_etf=r.is_etf, is_adr=r.is_adr,
                                country_of_incorporation=r.country_of_incorporation, currency=r.currency, active=r.active,
                                listed_at=r.listed_at or r.first_seen, delisted_at=r.delisted_at, cik=r.cik))
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
        """Daily bars of one company, across ticker renames (security master)."""
        by_day: dict[date, Bar] = {}
        aliases = self.aliases(ticker)  # own session first: never nest sessions (shared SQLite connection)
        with self.sf() as s:
            for alias, frm, until in aliases:
                q = select(PriceBarRow).where(PriceBarRow.ticker == alias, PriceBarRow.day >= max(start, frm or start), PriceBarRow.day <= end)
                if until is not None:
                    q = q.where(PriceBarRow.day < until)
                for r in s.scalars(q.order_by(PriceBarRow.day)):
                    by_day.setdefault(r.day, Bar(r.day, r.open, r.high, r.low, r.close, r.volume))
        return [by_day[d] for d in sorted(by_day)]

    def last_bars_all(self, start: date, end: date) -> dict[str, list[Bar]]:
        """All stored bars in a window, grouped by ticker (one query for the whole market)."""
        out: dict[str, dict[date, Bar]] = {}
        with self.sf() as s:
            for r in s.scalars(select(PriceBarRow).where(PriceBarRow.day >= start, PriceBarRow.day <= end)):
                out.setdefault(r.ticker, {}).setdefault(r.day, Bar(r.day, r.open, r.high, r.low, r.close, r.volume))
            renamed = [t for (t,) in s.execute(select(SecurityRow.ticker).where(SecurityRow.mode == self.mode, SecurityRow.predecessor.is_not(None)))]
        for t in renamed:  # a renamed company keeps its price history (security master)
            merged = dict(out.get(t, {}))
            for alias, frm, until in self.aliases(t)[1:]:
                for d, b in out.get(alias, {}).items():
                    if (frm is None or d >= frm) and (until is None or d < until):
                        merged.setdefault(d, b)
            if merged:
                out[t] = merged
        return {t: [d[k] for k in sorted(d)] for t, d in out.items()}

    # ------------------------------------------------------------------ coverage (readiness)
    def coverage_stats(self, today: date, min_market_cap: float) -> dict[str, Any]:
        """Aggregate counts for the scanner-readiness gate (SQL aggregates, no row loading)."""
        with self.sf() as s:
            active = list(s.execute(select(SecurityRow.ticker, SecurityRow.market_cap, SecurityRow.sector, SecurityRow.exchange, SecurityRow.is_adr, SecurityRow.country_of_incorporation)
                                    .where(SecurityRow.mode == self.mode, SecurityRow.active.is_(True))).all())
            counts = dict(s.execute(select(PriceBarRow.ticker, func.count(func.distinct(PriceBarRow.day)))
                                    .where(PriceBarRow.day >= today - timedelta(days=400), PriceBarRow.day <= today).group_by(PriceBarRow.ticker)).all())
            fund = {t for (t,) in s.execute(select(FundamentalVintageRow.ticker).distinct())}
            days = s.execute(select(func.count(func.distinct(PriceBarRow.day))).where(PriceBarRow.day >= today - timedelta(days=400))).scalar() or 0
            est_first = s.execute(select(func.min(EstimateSnapshotRow.observed_on)).where(EstimateSnapshotRow.provider == "finnhub")).scalar()
            man = dict(s.execute(select(IngestionManifestRow.ticker, IngestionManifestRow.status)
                                 .where(IngestionManifestRow.mode == self.mode, IngestionManifestRow.dataset == "fundamentals")).all())
        listed = [a for a in active if a[3] != "OTC"]
        big = [a for a in listed if a[1] is not None and a[1] >= min_market_cap]
        return {
            "listed": len(listed),
            "with_market_cap": sum(1 for a in listed if a[1] is not None),
            "large": len(big),
            "bars_60": sum(1 for a in listed if counts.get(a[0], 0) >= 60),
            "bars_200": sum(1 for a in listed if counts.get(a[0], 0) >= 200),
            "bars_240": sum(1 for a in listed if counts.get(a[0], 0) >= 240),
            "large_with_sector": sum(1 for a in big if a[2] not in (None, "", "Unknown")),
            "large_with_fundamentals": sum(1 for a in big if a[0] in fund),
            # only CONFIRMED foreign issuers (SEC profile: 20-F/40-F filer) without quarterly us-gaap facts are left out
            # of the coverage denominator; a domestic filer the parser cannot read is missing coverage
            "large_fund_not_supported": sum(1 for a in big if a[0] not in fund and man.get(a[0]) == "NOT_SUPPORTED" and (a[4] or (a[5] or "US") != "US")),
            "large_fund_failed": sum(1 for a in big if a[0] not in fund and man.get(a[0]) in ("FAILED", "RATE_LIMITED")),
            "large_fund_parse_gap": sum(1 for a in big if a[0] not in fund and (man.get(a[0]) == "PARSE_GAP" or (man.get(a[0]) == "NOT_SUPPORTED" and not (a[4] or (a[5] or "US") != "US")))),
            "market_days": int(days),
            "estimate_history_days": (today - est_first).days if est_first else 0,
        }

    def tickers_with_fundamentals(self) -> set[str]:
        with self.sf() as s:
            return {t for (t,) in s.execute(select(FundamentalVintageRow.ticker).distinct())}

    # ------------------------------------------------------------------ ingestion manifest
    RETRY_NOT_SUPPORTED = timedelta(days=30)  # e.g. a 20-F filer has no quarterly us-gaap facts
    RETRY_PARSE_GAP = timedelta(days=7)  # tags the parser does not read: retried after a parser update / new filing
    RETRY_MAX = timedelta(hours=24)

    def ingestion(self, dataset: str, ticker: str) -> IngestionManifestRow | None:
        with self.sf() as s:
            row = s.get(IngestionManifestRow, (self.mode, dataset, ticker))
            if row is not None:
                s.expunge(row)
            return row

    def ingestion_all(self, dataset: str) -> dict[str, IngestionManifestRow]:
        with self.sf() as s:
            rows = list(s.scalars(select(IngestionManifestRow).where(IngestionManifestRow.mode == self.mode, IngestionManifestRow.dataset == dataset)))
            for r in rows:
                s.expunge(r)
        return {r.ticker: r for r in rows}

    def record_ingestion(self, dataset: str, ticker: str, now: datetime, status: str, error: str | None = None, rows: int = 0) -> None:
        """OK resets the failure count. A failure schedules the next attempt: NOT_SUPPORTED after 30 days,
        otherwise 1h, 2h, 4h … capped at 24h (never a tight retry loop against a free provider)."""
        with self.sf() as s:
            row = s.get(IngestionManifestRow, (self.mode, dataset, ticker))
            if row is None:
                row = IngestionManifestRow(mode=self.mode, dataset=dataset, ticker=ticker, status=status, attempts=0, last_attempt_at=now, rows=0)
                s.add(row)
            row.status, row.last_attempt_at = status, now
            if status == "OK":
                row.attempts, row.last_success_at, row.next_attempt_at, row.error, row.rows = 0, now, None, None, rows
            else:
                row.attempts = (row.attempts or 0) + 1
                row.error = (error or status)[:300]
                wait = (self.RETRY_NOT_SUPPORTED if status == "NOT_SUPPORTED" else self.RETRY_PARSE_GAP if status == "PARSE_GAP"
                        else min(self.RETRY_MAX, timedelta(hours=2 ** min(row.attempts - 1, 5))))
                row.next_attempt_at = now + wait
            s.commit()

    def ingestion_stats(self, dataset: str) -> dict[str, int]:
        with self.sf() as s:
            return {st: n for st, n in s.execute(select(IngestionManifestRow.status, func.count()).where(IngestionManifestRow.mode == self.mode, IngestionManifestRow.dataset == dataset)
                                                 .group_by(IngestionManifestRow.status)).all()}

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
        out: list[SplitEvent] = []
        aliases = self.aliases(ticker)
        with self.sf() as s:
            for alias, frm, until in aliases:
                for r in s.scalars(select(CorporateActionRow).where(CorporateActionRow.ticker == alias)):
                    if (frm is None or r.execution_date >= frm) and (until is None or r.execution_date < until):
                        out.append(SplitEvent(ticker, r.execution_date, r.split_from, r.split_to, r.source))
        return sorted(out, key=lambda e: (e.execution_date, e.source))

    def adjust_bars_for_splits(self, as_of: date) -> int:
        """Rescale stored bars that were retrieved *before* a split became effective (vendor-adjusted bars
        fetched after the split are already on the new basis). Each split is applied exactly once and only
        once it has actually executed (``execution_date <= as_of``, the New York calendar date): an
        announced future split must not change today's prices. A split reported by two sources for the
        same ticker and day is applied once. The split applies to the whole company: bars stored under an
        earlier ticker (before a rename) are rescaled too, so the rename day shows no fake price jump."""
        with self.sf() as s:
            pending = [(r.ticker, r.execution_date, r.source) for r in s.scalars(
                select(CorporateActionRow).where(CorporateActionRow.bars_adjusted_at.is_(None), CorporateActionRow.execution_date <= as_of)
                .order_by(CorporateActionRow.execution_date))]
        spans = {t: self.aliases(t) for t in {p[0] for p in pending}}  # read first: never nest sessions
        n = 0
        with self.sf() as s:
            for key in pending:
                ev = s.get(CorporateActionRow, key)
                if ev is None or ev.bars_adjusted_at is not None:
                    continue
                if ev.split_from > 0 and ev.split_to > 0:
                    twin = s.scalars(select(CorporateActionRow.source).where(CorporateActionRow.ticker == ev.ticker, CorporateActionRow.execution_date == ev.execution_date,
                                                                            CorporateActionRow.bars_adjusted_at.is_not(None)).limit(1)).first()
                    if twin is None:
                        ratio = ev.split_to / ev.split_from
                        # the split takes effect at the New York start of its execution day
                        effective = datetime.combine(ev.execution_date, datetime.min.time(), tzinfo=NY).astimezone(UTC)
                        for alias, frm, until in spans.get(ev.ticker, [(ev.ticker, None, None)]):
                            q = select(PriceBarRow).where(PriceBarRow.ticker == alias, PriceBarRow.day < ev.execution_date, PriceBarRow.retrieved_at < effective)
                            if frm is not None:
                                q = q.where(PriceBarRow.day >= frm)
                            if until is not None:
                                q = q.where(PriceBarRow.day < until)
                            for b in s.scalars(q):
                                b.open, b.high, b.low, b.close, b.volume = b.open / ratio, b.high / ratio, b.low / ratio, b.close / ratio, b.volume * ratio
                                n += 1
                ev.bars_adjusted_at = _now()
                s.flush()
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
        downloaded again. First-reported values are never overwritten.

        A field the stored vintage did not have (e.g. a concept a newer parser reads, a derived Q4 that
        became computable) is *added* with its own first-publication date in ``field_filed``, so it stays
        invisible before that date; an existing value is never replaced."""
        with self.sf() as s:
            for q in quarters:
                key = (ticker, q.period_end, q.filed_date, q.source)
                row = s.get(FundamentalVintageRow, key)
                if row is None:
                    s.add(FundamentalVintageRow(ticker=ticker, period_end=q.period_end, filed_date=q.filed_date, source=q.source, payload=encode(q), retrieved_at=_now()))
                    continue
                old = decode(QuarterlyFinancials, row.payload)
                upd: dict[str, Any] = {}
                filed = dict(old.field_filed)
                for k in VALUE_FIELDS:
                    if getattr(old, k) is None and (v := getattr(q, k)) is not None:
                        upd[k] = v
                        filed[k] = q.field_filed.get(k, q.filed_date)
                extras = {**dict(q.extras), **dict(old.extras)}  # new keys added, stored ones kept
                merged = _merge_revisions(old.revisions, q.revisions)
                if upd or extras != dict(old.extras) or merged != dict(old.revisions):
                    row.payload = encode(replace(old, **upd, field_filed=filed, extras=extras, revisions=merged))
                row.retrieved_at = _now()
            s.commit()

    def quarters(self, ticker: str, max_age: timedelta | None) -> list[QuarterlyFinancials] | None:
        """Stored quarters if retrieved within ``max_age`` (None = whatever is stored, any age; the data is
        point in time, only the check for newer filings is due); None means "fetch from the provider"."""
        with self.sf() as s:
            rows = list(s.scalars(select(FundamentalVintageRow).where(FundamentalVintageRow.ticker == ticker)))
        if not rows or (max_age is not None and _now() - max(r.retrieved_at for r in rows) > max_age):
            return None
        latest: dict[date, QuarterlyFinancials] = {}
        for r in sorted(rows, key=lambda r: r.filed_date):
            q = decode(QuarterlyFinancials, r.payload)
            if r.period_end not in latest:
                latest[r.period_end] = q  # earliest vintage = first reported
            else:  # a later first-filing row for the same period: its values are revisions of the first
                base = latest[r.period_end]
                extra = {k: ((r.filed_date, v),) for k in VALUE_FIELDS
                         if (v := getattr(q, k)) is not None and getattr(base, k) is not None and abs(v - getattr(base, k)) > 1e-9 * max(1.0, abs(getattr(base, k)))}
                fill = {k: getattr(q, k) for k in VALUE_FIELDS if getattr(base, k) is None and getattr(q, k) is not None}
                filed = {**dict(base.field_filed), **{k: q.field_filed.get(k, r.filed_date) for k in fill}}
                latest[r.period_end] = replace(base, **fill, field_filed=filed, revisions=_merge_revisions(_merge_revisions(base.revisions, q.revisions), extra))
        return [latest[d] for d in sorted(latest)]


VALUE_FIELDS = ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "operating_cash_flow", "capex", "sbc", "depreciation_amortization",
                "cash", "total_debt", "total_equity", "shares_diluted", "inventory", "shares_outstanding")


def _merge_revisions(a: Mapping[str, Iterable[Iterable[Any]]], b: Mapping[str, Iterable[Iterable[Any]]]) -> dict[str, tuple[tuple[date, float], ...]]:
    out: dict[str, set[tuple[date, float]]] = {}
    for src in (a, b):
        for k, obs in src.items():
            for fd, v in obs:
                out.setdefault(k, set()).add((fd if isinstance(fd, date) else date.fromisoformat(str(fd)), float(v)))
    return {k: tuple(sorted(v)) for k, v in out.items()}
