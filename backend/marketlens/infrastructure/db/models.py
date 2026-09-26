"""SQLAlchemy ORM models. UTC timestamps everywhere. SQLite locally, PostgreSQL-compatible types."""

from __future__ import annotations

from datetime import date, datetime

from datetime import timezone

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Always stores UTC and always returns timezone-aware UTC datetimes (SQLite drops tzinfo otherwise)."""

    impl = SADateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be stored; use timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def DateTime(timezone: bool = True) -> UTCDateTime:  # noqa: N802 - keeps column declarations readable
    return UTCDateTime()


class Base(DeclarativeBase):
    pass


class SecurityRow(Base):
    __tablename__ = "securities"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(256))
    exchange: Mapped[str] = mapped_column(String(32))
    sector: Mapped[str] = mapped_column(String(64))
    industry: Mapped[str] = mapped_column(String(128))
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_etf: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adr: Mapped[bool] = mapped_column(Boolean, default=False)
    country_of_incorporation: Mapped[str] = mapped_column(String(8), default="US")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    mode: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    sic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)


class PriceBarRow(Base):
    __tablename__ = "price_bars"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FundamentalVintageRow(Base):
    """Each (period, filed_date) vintage is kept: restatements never overwrite history."""

    __tablename__ = "fundamentals_quarterly"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    filed_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CorporateActionRow(Base):
    """Stock splits (append-only). ``bars_adjusted_at`` records when stored pre-split bars were rescaled."""

    __tablename__ = "corporate_actions"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    execution_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="SPLIT")
    split_from: Mapped[float] = mapped_column(Float)
    split_to: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bars_adjusted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EstimateSnapshotRow(Base):
    """Append-only consensus snapshots from free providers. Revisions (7/30/60/90 days) are computed from
    this history; days before the first snapshot are UNKNOWN (never back-filled)."""

    __tablename__ = "estimate_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(24))  # e.g. "FY2027", "Q2026-12"
    period_type: Mapped[str] = mapped_column(String(8))  # quarter | annual
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    eps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyst_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eps_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_revisions: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # provider-reported history (e.g. 7/30/60/90 days ago)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_on: Mapped[date] = mapped_column(Date, index=True)  # snapshot day (NY)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ux_estimate_snapshot", EstimateSnapshotRow.ticker, EstimateSnapshotRow.provider, EstimateSnapshotRow.period, EstimateSnapshotRow.observed_on, unique=True)


class GuidanceRow(Base):
    """Management guidance extracted from SEC 8-K earnings releases (exact source sentence kept)."""

    __tablename__ = "guidance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    accession: Mapped[str] = mapped_column(String(32))
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(String(300))
    metric: Mapped[str] = mapped_column(String(24))  # revenue | eps | gross_margin | operating_margin | capex
    period_label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(12))  # USD | fraction
    sentence: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # EXTRACTED | GUIDANCE_UNCLEAR
    confidence: Mapped[str] = mapped_column(String(8))  # LOW | MEDIUM
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ux_guidance", GuidanceRow.ticker, GuidanceRow.accession, GuidanceRow.metric, GuidanceRow.sentence, unique=False)


class ScanRunRow(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    stages: Mapped[list] = mapped_column(JSON)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    regimes: Mapped[list] = mapped_column(JSON, default=list)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    scoring_model_version: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(8))
    session: Mapped[str | None] = mapped_column(String(16), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_quality: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    deterministic_action: Mapped[str] = mapped_column(String(24))
    final_action: Mapped[str] = mapped_column(String(24))
    size_class: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sector: Mapped[str] = mapped_column(String(64))
    sector_model: Mapped[str] = mapped_column(String(32))
    regime: Mapped[str] = mapped_column(String(64))
    data_quality: Mapped[str] = mapped_column(String(16))
    committee_status: Mapped[str] = mapped_column(String(24), default="NOT_RUN")
    result: Mapped[dict] = mapped_column(JSON)  # full AnalysisResult (audit)
    inputs: Mapped[dict] = mapped_column(JSON)  # point-in-time AnalysisInputs (replay)
    model_config_snapshot: Mapped[dict] = mapped_column(JSON)  # TOML texts used for this decision
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    scoring_model_version: Mapped[str] = mapped_column(String(64))
    decision_model_version: Mapped[str] = mapped_column(String(64))
    agent_prompt_version: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    config_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)  # git commit of the running code
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_model_ids: Mapped[str | None] = mapped_column(String(200), nullable=True)  # exact model IDs used by the committee
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # when this version became known
    # append-only versioning: a later committee review creates a NEW row that supersedes this one;
    # an issued recommendation is never edited (paper trading and evaluation use the version as issued)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


Index("ix_rec_ticker_asof", RecommendationRow.ticker, RecommendationRow.as_of)


class CommitteeRow(Base):
    __tablename__ = "committee_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    consensus: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LLMCallRow(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(8))
    purpose: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = unknown price, not free
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LLMCacheRow(Base):
    __tablename__ = "llm_cache"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutcomeRow(Base):
    __tablename__ = "recommendation_outcomes"
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), primary_key=True)
    horizon: Mapped[int] = mapped_column(Integer, primary_key=True)
    forward_return: Mapped[float] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    excess_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    matured_on: Mapped[date] = mapped_column(Date)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="OK", server_default="OK")  # OK | DELISTED_LAST_PRICE


class FactorSnapshotRow(Base):
    __tablename__ = "factor_snapshots"
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    rec_day: Mapped[date] = mapped_column(Date, index=True)
    factors: Mapped[dict] = mapped_column(JSON)
    total_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    action: Mapped[str] = mapped_column(String(24))
    sector: Mapped[str] = mapped_column(String(64))
    regime: Mapped[str] = mapped_column(String(64))
    scoring_model_version: Mapped[str] = mapped_column(String(64))


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    recommended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # PENDING | OPEN | CLOSED | SKIPPED
    entry_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    action: Mapped[str] = mapped_column(String(24))
    regime: Mapped[str] = mapped_column(String(64))
    sector: Mapped[str] = mapped_column(String(64))
    stop: Mapped[float] = mapped_column(Float)
    target1: Mapped[float] = mapped_column(Float)
    target2: Mapped[float] = mapped_column(Float)
    max_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    thesis: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(64))
    exits: Mapped[list] = mapped_column(JSON, default=list)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueRow(Base):
    __tablename__ = "issues"
    issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    importance: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderHealthRow(Base):
    __tablename__ = "provider_health"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    weights: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))  # PRODUCTION | SHADOW | RETIRED | REJECTED
    parent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    shadow_started: Mapped[date | None] = mapped_column(Date, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalibrationRunRow(Base):
    __tablename__ = "calibration_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    candidate_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HoldingRow(Base):
    __tablename__ = "portfolio_holdings"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    quantity: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WatchlistRow(Base):
    __tablename__ = "watchlist"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSettingRow(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
