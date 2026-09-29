"""Persistence helpers (the only place that writes to the database)."""

from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session, defer

from marketlens.domain.market_calendar import UTC
from marketlens.infrastructure.db.models import (
    AppSettingRow,
    CalibrationRunRow,
    CommitteeRow,
    FactorSnapshotRow,
    HoldingRow,
    IssueRow,
    LLMCacheRow,
    LLMCallRow,
    ModelVersionRow,
    OutcomeRow,
    PaperPositionRow,
    ProviderHealthRow,
    RecommendationRow,
    ScanRunRow,
    SecurityRow,
    TransactionRow,
    WatchlistRow,
)


def now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------- scans & recommendations
def latest_scan(s: Session, mode: str | None = None) -> ScanRunRow | None:
    q = select(ScanRunRow)
    if mode is not None:
        q = q.where(ScanRunRow.mode == mode)
    return s.scalars(q.order_by(desc(ScanRunRow.id)).limit(1)).first()


def newer_single_analyses(s: Session, tickers: list[str], after: datetime, mode: str) -> dict[str, RecommendationRow]:
    """For each ticker, its newest analysis outside any scan made after ``after`` (the current version only)."""
    if not tickers:
        return {}
    q = (select(RecommendationRow).options(*heavy_deferred())
         .where(RecommendationRow.ticker.in_(sorted(set(tickers))), RecommendationRow.scan_run_id.is_(None), RecommendationRow.mode == mode,
                RecommendationRow.as_of > after, RecommendationRow.id.not_in(_superseded_ids()))
         .order_by(desc(RecommendationRow.as_of), desc(RecommendationRow.id)))
    out: dict[str, RecommendationRow] = {}
    for r in s.scalars(q):
        out.setdefault(r.ticker, r)
    return out


def scan_before(s: Session, scan_id: int, mode: str | None = None) -> ScanRunRow | None:
    """The newest scan older than ``scan_id`` (what the screens keep showing while ``scan_id`` is still running)."""
    q = select(ScanRunRow).where(ScanRunRow.id < scan_id)
    if mode is not None:
        q = q.where(ScanRunRow.mode == mode)
    return s.scalars(q.order_by(desc(ScanRunRow.id)).limit(1)).first()


def _superseded_ids() -> Any:
    return select(RecommendationRow.supersedes_id).where(RecommendationRow.supersedes_id.is_not(None))


def heavy_deferred() -> list[Any]:
    """Load options for list screens: the stored inputs and model snapshot (about 120 KB of JSON per row) are read only
    if a row needs them, while its session is open (owner report 2026-09-28: every list parsed megabytes of it)."""
    return [defer(RecommendationRow.inputs), defer(RecommendationRow.model_config_snapshot)]


def recommendations_for_scan(s: Session, scan_id: int) -> list[RecommendationRow]:
    """The current version of each recommendation of a scan (superseded versions stay in history). The heavy inputs
    load on demand — use the rows inside the session."""
    return list(s.scalars(select(RecommendationRow).options(*heavy_deferred()).where(RecommendationRow.scan_run_id == scan_id, RecommendationRow.id.not_in(_superseded_ids())).order_by(RecommendationRow.rank)))


def current_version(s: Session, rec_id: int) -> RecommendationRow | None:
    row = s.get(RecommendationRow, rec_id)
    while row is not None:
        nxt = s.scalars(select(RecommendationRow).where(RecommendationRow.supersedes_id == row.id).order_by(desc(RecommendationRow.id)).limit(1)).first()
        if nxt is None:
            return row
        row = nxt
    return None


def original_version(s: Session, rec_id: int) -> RecommendationRow | None:
    row = s.get(RecommendationRow, rec_id)
    while row is not None and row.supersedes_id is not None:
        row = s.get(RecommendationRow, row.supersedes_id)
    return row


def latest_recommendation(s: Session, ticker: str, before: datetime | None = None, mode: str | None = None, inclusive: bool = False, exclude_id: int | None = None) -> RecommendationRow | None:
    """Latest recommendation before ``before`` (point-in-time upper bound; ``inclusive`` also admits the
    same timestamp) in ``mode``."""
    q = select(RecommendationRow).where(RecommendationRow.ticker == ticker)
    if before is not None:
        q = q.where(RecommendationRow.as_of <= before if inclusive else RecommendationRow.as_of < before)
    if mode is not None:
        q = q.where(RecommendationRow.mode == mode)
    if exclude_id is not None:
        q = q.where(RecommendationRow.id != exclude_id)
    return s.scalars(q.order_by(desc(RecommendationRow.as_of), desc(RecommendationRow.id)).limit(1)).first()


def recommendation_history(s: Session, ticker: str, limit: int = 20, mode: str | None = None, until: datetime | None = None) -> list[RecommendationRow]:
    q = select(RecommendationRow).where(RecommendationRow.ticker == ticker)
    if mode is not None:
        q = q.where(RecommendationRow.mode == mode)
    if until is not None:
        q = q.where(RecommendationRow.as_of <= until)
    return list(s.scalars(q.order_by(desc(RecommendationRow.as_of), desc(RecommendationRow.id)).limit(limit)))


def delisted_on(s: Session, ticker: str, mode: str) -> date | None:
    """Delisting date. A rename is not a delisting (the renamed row keeps ``delisted_at`` empty); a company
    that was delisted and later relisted under another ticker keeps its delisting."""
    row = s.get(SecurityRow, ticker)
    if row is None or row.mode != mode:
        return None
    if row.delisted_at is not None:
        from sqlalchemy import select

        from marketlens.infrastructure.db.models import PriceBarRow

        # strictly after: a bar ON the delisting day is its last trade, backfilled later (8th evaluation I4)
        traded_after = s.scalars(select(PriceBarRow.day).where(PriceBarRow.ticker == ticker, PriceBarRow.day > row.delisted_at).limit(1)).first()
        if traded_after is not None:
            return None  # it kept trading after it left the listing: not (yet) a delisting — never close at a "last price"
    return row.delisted_at


def get_recommendation(s: Session, rec_id: int) -> RecommendationRow | None:
    return s.get(RecommendationRow, rec_id)


def all_recommendations(s: Session, mode: str | None = None, until: datetime | None = None, originals_only: bool = False) -> list[RecommendationRow]:
    q = select(RecommendationRow)
    if originals_only:
        q = q.where(RecommendationRow.supersedes_id.is_(None))
    if mode is not None:
        q = q.where(RecommendationRow.mode == mode)
    if until is not None:
        q = q.where(RecommendationRow.as_of <= until)
    return list(s.scalars(q.order_by(RecommendationRow.as_of, RecommendationRow.id)))


def recommendation_keys(s: Session, mode: str | None = None, until: datetime | None = None) -> list[Any]:
    """id, ticker, as_of, final_action, confidence of every recommendation — the evaluation screens' view, without the
    stored analysis, inputs and model snapshot (about 190 KB of JSON per row: 2,400 rows took 6 s to load)."""
    q = select(RecommendationRow.id, RecommendationRow.ticker, RecommendationRow.as_of, RecommendationRow.final_action, RecommendationRow.confidence)
    if mode is not None:
        q = q.where(RecommendationRow.mode == mode)
    if until is not None:
        q = q.where(RecommendationRow.as_of <= until)
    return list(s.execute(q.order_by(RecommendationRow.as_of, RecommendationRow.id)))


def committee_for(s: Session, rec_id: int) -> CommitteeRow | None:
    return s.scalars(select(CommitteeRow).where(CommitteeRow.recommendation_id == rec_id).order_by(desc(CommitteeRow.id)).limit(1)).first()


def latest_committee_for_ticker(s: Session, ticker: str) -> tuple[RecommendationRow, CommitteeRow] | None:
    rows = s.execute(
        select(RecommendationRow, CommitteeRow).join(CommitteeRow, CommitteeRow.recommendation_id == RecommendationRow.id)
        .where(RecommendationRow.ticker == ticker).order_by(desc(CommitteeRow.id)).limit(1)
    ).first()
    return (rows[0], rows[1]) if rows else None


# ---------------------------------------------------------------- LLM
class DbLLMCache:
    """LLM response cache bound to the caller's session (thread-safe via a lock; committed with the caller)."""

    def __init__(self, session: Session) -> None:
        self.s = session
        self._lock = threading.Lock()

    def get(self, fingerprint: str) -> str | None:
        with self._lock:
            row = self.s.get(LLMCacheRow, fingerprint)
            return row.response if row else None

    def put(self, fingerprint: str, model: str, response: str) -> None:
        with self._lock:
            if self.s.get(LLMCacheRow, fingerprint) is None:
                self.s.add(LLMCacheRow(fingerprint=fingerprint, model=model, response=response, created_at=now()))


def record_llm_call(s: Session, rec: Any) -> None:
    s.add(LLMCallRow(provider=rec.provider, model=rec.model, tier=rec.tier, purpose=rec.purpose, input_tokens=rec.input_tokens, output_tokens=rec.output_tokens, latency_ms=rec.latency_ms, estimated_cost_usd=rec.estimated_cost_usd, cached=rec.cached, error=rec.error, created_at=rec.created_at))


def llm_usage(s: Session, since: datetime | None = None) -> dict[str, Any]:
    """LLM calls, tokens and estimated cost (all time, or since ``since`` — e.g. one scan)."""
    w = [LLMCallRow.created_at >= since] if since is not None else []
    r = s.execute(select(func.count(LLMCallRow.id), func.sum(LLMCallRow.input_tokens), func.sum(LLMCallRow.output_tokens), func.sum(LLMCallRow.estimated_cost_usd), func.avg(LLMCallRow.latency_ms)).where(*w)).one()
    cached = s.scalar(select(func.count(LLMCallRow.id)).where(LLMCallRow.cached.is_(True), *w)) or 0
    errors = s.scalar(select(func.count(LLMCallRow.id)).where(LLMCallRow.error.is_not(None), *w)) or 0
    unknown = s.scalar(select(func.count(LLMCallRow.id)).where(LLMCallRow.estimated_cost_usd.is_(None), LLMCallRow.cached.is_(False), LLMCallRow.error.is_(None), *w)) or 0
    return {"calls": r[0] or 0, "input_tokens": r[1] or 0, "output_tokens": r[2] or 0,
            "estimated_cost_usd": round(r[3] or 0.0, 4), "unknown_cost_calls": unknown,
            "cost_complete": unknown == 0, "avg_latency_ms": round(r[4] or 0.0, 1), "cached_calls": cached, "errors": errors}


# ---------------------------------------------------------------- outcomes / factors
def outcomes_for(s: Session, rec_id: int) -> list[OutcomeRow]:
    return list(s.scalars(select(OutcomeRow).where(OutcomeRow.recommendation_id == rec_id)))


def factor_samples(s: Session, mode: str | None = None) -> list[tuple[FactorSnapshotRow, dict[int, float]]]:
    q = select(FactorSnapshotRow)
    if mode is not None:
        q = q.join(RecommendationRow, RecommendationRow.id == FactorSnapshotRow.recommendation_id).where(RecommendationRow.mode == mode)
    snaps = list(s.scalars(q))
    outs: dict[int, dict[int, float]] = {}
    for o in s.scalars(select(OutcomeRow)):
        outs.setdefault(o.recommendation_id, {})[o.horizon] = o.forward_return
    return [(f, outs.get(f.recommendation_id, {})) for f in snaps]


# ---------------------------------------------------------------- paper
def open_paper_positions(s: Session) -> list[PaperPositionRow]:
    return list(s.scalars(select(PaperPositionRow).where(PaperPositionRow.status.in_(("PENDING", "OPEN")))))


def all_paper_positions(s: Session) -> list[PaperPositionRow]:
    return list(s.scalars(select(PaperPositionRow).order_by(PaperPositionRow.recommended_at)))


# ---------------------------------------------------------------- portfolio & watchlist
def holdings(s: Session) -> list[HoldingRow]:
    return list(s.scalars(select(HoldingRow).order_by(HoldingRow.ticker)))


def upsert_holding(s: Session, ticker: str, quantity: float, cost_basis: float) -> None:
    row = s.get(HoldingRow, ticker)
    if quantity <= 0:
        if row:
            s.delete(row)
        return
    if row is None:
        s.add(HoldingRow(ticker=ticker, quantity=quantity, cost_basis=cost_basis, updated_at=now()))
    else:
        row.quantity, row.cost_basis, row.updated_at = quantity, cost_basis, now()


def transactions(s: Session) -> list[TransactionRow]:
    return list(s.scalars(select(TransactionRow).order_by(TransactionRow.day, TransactionRow.id)))


def add_transaction(s: Session, **fields: object) -> TransactionRow:
    row = TransactionRow(created_at=now(), **fields)
    s.add(row)
    s.flush()
    return row


def delete_transaction(s: Session, tid: int) -> bool:
    row = s.get(TransactionRow, tid)
    if row is None:
        return False
    s.delete(row)
    return True


def get_setting(s: Session, key: str, default: str | None = None) -> str | None:
    row = s.get(AppSettingRow, key)
    return row.value if row else default


def delete_setting(s: Session, key: str) -> None:
    row = s.get(AppSettingRow, key)
    if row is not None:
        s.delete(row)


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(AppSettingRow, key)
    if row is None:
        s.add(AppSettingRow(key=key, value=value))
    else:
        row.value = value


def watchlist(s: Session) -> list[WatchlistRow]:
    return list(s.scalars(select(WatchlistRow).order_by(WatchlistRow.ticker)))


def add_watch(s: Session, ticker: str, note: str = "") -> None:
    if s.get(WatchlistRow, ticker) is None:
        s.add(WatchlistRow(ticker=ticker, note=note, added_at=now()))


def remove_watch(s: Session, ticker: str) -> None:
    s.execute(delete(WatchlistRow).where(WatchlistRow.ticker == ticker))


# ---------------------------------------------------------------- issues & health
def upsert_issues(s: Session, issues: Iterable[tuple[str, str, float, dict[str, Any]]]) -> None:
    t = now()
    for iid, cat, imp, payload in issues:
        row = s.get(IssueRow, iid)
        if row is None:
            s.add(IssueRow(issue_id=iid, category=cat, importance=imp, payload=payload, first_seen=t, last_seen=t))
        else:
            row.payload, row.importance, row.last_seen = payload, imp, t


def recent_issues(s: Session, limit: int = 100) -> list[IssueRow]:
    return list(s.scalars(select(IssueRow).order_by(desc(IssueRow.importance), desc(IssueRow.last_seen)).limit(limit)))


def snapshot_health(s: Session, items: list[dict[str, Any]]) -> None:
    t = now()
    for it in items:
        s.add(ProviderHealthRow(name=str(it["name"]), status=str(it["status"]), payload=it, recorded_at=t))


# ---------------------------------------------------------------- model versions & calibration
def production_model(s: Session) -> ModelVersionRow | None:
    return s.scalars(select(ModelVersionRow).where(ModelVersionRow.status == "PRODUCTION").order_by(desc(ModelVersionRow.created_at)).limit(1)).first()


def shadow_model(s: Session) -> ModelVersionRow | None:
    return s.scalars(select(ModelVersionRow).where(ModelVersionRow.status == "SHADOW").order_by(desc(ModelVersionRow.created_at)).limit(1)).first()


def calibration_runs(s: Session, limit: int = 20) -> list[CalibrationRunRow]:
    return list(s.scalars(select(CalibrationRunRow).order_by(desc(CalibrationRunRow.id)).limit(limit)))


def add_calibration_run(s: Session, status: str, candidate: str | None, payload: dict[str, Any]) -> None:
    s.add(CalibrationRunRow(status=status, candidate_version=candidate, payload=payload, created_at=now()))


def model_versions(s: Session) -> list[ModelVersionRow]:
    return list(s.scalars(select(ModelVersionRow).order_by(desc(ModelVersionRow.created_at))))


def recs_between(s: Session, start: date, end: date) -> list[RecommendationRow]:
    return list(s.scalars(select(RecommendationRow).where(RecommendationRow.as_of >= datetime.combine(start, datetime.min.time(), tzinfo=UTC), RecommendationRow.as_of <= datetime.combine(end, datetime.max.time(), tzinfo=UTC))))


# ---------------------------------------------------------------- universe / history (point-in-time storage)
def upsert_securities(s: Session, securities: Iterable[Any], mode: str) -> int:
    """Keep every security ever seen. Delisted names stay in history (active=False), never deleted."""
    from marketlens.infrastructure.db.models import SecurityRow

    n = 0
    t = now()
    for sec in securities:
        row = s.get(SecurityRow, sec.ticker)
        vals = dict(company_name=sec.company_name, exchange=sec.exchange.value, sector=sec.sector, industry=sec.industry,
                    market_cap=sec.market_cap, is_etf=sec.is_etf, is_adr=sec.is_adr, country_of_incorporation=sec.country_of_incorporation,
                    currency=sec.currency, active=sec.active and sec.delisted_at is None, listed_at=sec.listed_at, delisted_at=sec.delisted_at,
                    mode=mode, updated_at=t)
        if row is None:
            s.add(SecurityRow(ticker=sec.ticker, **vals))
        else:
            for k, v in vals.items():
                setattr(row, k, v)
        n += 1
    return n


def store_fundamental_vintages(s: Session, ticker: str, quarters: Iterable[Any]) -> None:
    """Each (period_end, filed_date, source) is stored once; later restatements add rows, never overwrite."""
    import dataclasses
    import json

    from marketlens.infrastructure.db.models import FundamentalVintageRow

    t = now()
    for q in quarters:
        key = (ticker, q.period_end, q.filed_date, q.source)
        if s.get(FundamentalVintageRow, key) is None:
            payload = json.loads(json.dumps(dataclasses.asdict(q), default=str))  # dates → ISO strings (codec-compatible)
            s.add(FundamentalVintageRow(ticker=ticker, period_end=q.period_end, filed_date=q.filed_date, source=q.source, payload=payload, retrieved_at=t))


def store_bars(s: Session, ticker: str, bars: Iterable[Any], source: str) -> None:
    from marketlens.infrastructure.db.models import PriceBarRow

    existing = {d for (d,) in s.execute(select(PriceBarRow.day).where(PriceBarRow.ticker == ticker, PriceBarRow.source == source))}
    t = now()
    for b in bars:
        if b.day not in existing:
            s.add(PriceBarRow(ticker=ticker, day=b.day, source=source, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, retrieved_at=t))
