"""Runtime settings (environment / .env) and versioned model configuration (TOML).

Secrets are read from the environment (or the OS keyring when installed) and are never logged.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from marketlens.domain.calibration import CalibrationConfig
from marketlens.domain.catalysts import EventRiskConfig
from marketlens.domain.decision import DecisionThresholds
from marketlens.domain.entry import EntryConfig
from marketlens.domain.enums import DataMode
from marketlens.domain.exposure_graph import HopDecay
from marketlens.domain.issues import ImpactConfig
from marketlens.domain.market import FreshnessPolicy
from marketlens.domain.paper import PaperConfig
from marketlens.domain.portfolio import PortfolioLimits
from marketlens.domain.scoring import ScoringModel
from marketlens.domain.sector_models import MetricRule, SectorModel

FROZEN = bool(getattr(sys, "frozen", False))
# In a PyInstaller build resources are unpacked under sys._MEIPASS; in development the repo root is used.
REPO_ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("MARKETLENS_CONFIG_DIR", REPO_ROOT / "config"))
ALEMBIC_DIR = REPO_ROOT / "alembic" if FROZEN else REPO_ROOT / "backend" / "alembic"


def default_data_dir() -> Path:
    if FROZEN and os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MarketLens"
    return REPO_ROOT / "data" if not FROZEN else Path.home() / ".marketlens"

SECRET_ENV_KEYS = (
    "FINNHUB_API_KEY",
    "FRED_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "POLYGON_API_KEY",
    "ALPHAVANTAGE_API_KEY",
    "FINRA_API_KEY",
    "FINRA_API_SECRET",
)

SCHEMA_VERSION = "schema-2"
AGENT_PROMPT_VERSION = "prompts-2.1.0"  # 2.1.0: evidence carries ACTUAL/FORECAST basis


@lru_cache(maxsize=1)
def code_version() -> str:
    """Git commit of the running code (``+dirty`` when uncommitted changes exist), or the commit baked
    into a release build via MARKETLENS_BUILD_COMMIT / build_commit.txt; "unknown" otherwise."""
    env = os.environ.get("MARKETLENS_BUILD_COMMIT")
    if env:
        return env.strip()[:64]
    baked = REPO_ROOT / "build_commit.txt"
    if baked.exists():
        return baked.read_text(encoding="utf-8").strip()[:64] or "unknown"
    import subprocess

    try:
        head = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3)
        if head.returncode != 0:
            return "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3)
        return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional
        return
    env_path = Path(os.environ.get("MARKETLENS_ENV_FILE", (default_data_dir() / ".env") if FROZEN else REPO_ROOT / ".env"))
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _secret(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    try:  # optional OS keychain (Windows Credential Manager via keyring)
        import keyring  # type: ignore[import-not-found]

        return keyring.get_password("marketlens", name)
    except Exception:  # keyring missing or backend unavailable → treat as not configured
        return None


@dataclass(frozen=True)
class Settings:
    mode: DataMode
    database_url: str
    sec_user_agent: str | None
    finnhub_api_key: str | None = field(repr=False, default=None)
    fred_api_key: str | None = field(repr=False, default=None)
    polygon_api_key: str | None = field(repr=False, default=None)
    alphavantage_api_key: str | None = field(repr=False, default=None)
    finra_api_key: str | None = field(repr=False, default=None)
    finra_api_secret: str | None = field(repr=False, default=None)
    finnhub_realtime: bool = False
    anthropic_api_key: str | None = field(repr=False, default=None)
    openai_api_key: str | None = field(repr=False, default=None)
    openai_base_url: str = "https://api.openai.com/v1"
    llm_provider: str = "none"  # anthropic | openai | openai_compatible | mock | none
    fast_model: str = "claude-haiku-4-5"
    deep_model: str = "claude-opus-5"
    enable_paper_trading: bool = True
    enable_ai_committee: bool = True
    mock_universe_size: int = 600
    log_level: str = "INFO"
    scheduler: bool = False
    scan_interval_minutes: int = 60
    data_dir: Path = field(default_factory=default_data_dir)

    def secrets(self) -> list[str]:
        return [s for s in (self.finnhub_api_key, self.fred_api_key, self.polygon_api_key, self.alphavantage_api_key, self.finra_api_key, self.finra_api_secret, self.anthropic_api_key, self.openai_api_key) if s]


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    _load_dotenv()
    mode = DataMode(os.environ.get("MARKETLENS_MODE", "MOCK").upper())
    data_dir = Path(os.environ.get("MARKETLENS_DATA_DIR", default_data_dir()))
    default_db = f"sqlite:///{(data_dir / ('marketlens_mock.db' if mode == DataMode.MOCK else 'marketlens.db')).as_posix()}"
    return Settings(
        mode=mode,
        database_url=os.environ.get("MARKETLENS_DATABASE_URL", default_db),
        sec_user_agent=os.environ.get("SEC_USER_AGENT"),
        finnhub_api_key=_secret("FINNHUB_API_KEY"),
        fred_api_key=_secret("FRED_API_KEY"),
        polygon_api_key=_secret("POLYGON_API_KEY"),
        alphavantage_api_key=_secret("ALPHAVANTAGE_API_KEY"),
        finra_api_key=_secret("FINRA_API_KEY"),
        finra_api_secret=_secret("FINRA_API_SECRET"),
        finnhub_realtime=_bool(os.environ.get("FINNHUB_REALTIME"), False),
        anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
        openai_api_key=_secret("OPENAI_API_KEY"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        llm_provider=os.environ.get("LLM_PROVIDER", "mock" if mode == DataMode.MOCK else "none").lower(),
        fast_model=os.environ.get("FAST_MODEL", "claude-haiku-4-5"),
        deep_model=os.environ.get("DEEP_MODEL", "claude-opus-5"),
        enable_paper_trading=_bool(os.environ.get("ENABLE_PAPER_TRADING"), True),
        enable_ai_committee=_bool(os.environ.get("ENABLE_AI_COMMITTEE"), True),
        mock_universe_size=int(os.environ.get("MOCK_UNIVERSE_SIZE", "600")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        scheduler=_bool(os.environ.get("MARKETLENS_SCHEDULER"), False),
        scan_interval_minutes=int(os.environ.get("SCAN_INTERVAL_MINUTES", "60")),
        data_dir=data_dir,
    )


@dataclass(frozen=True)
class ScannerConfig:
    min_price: float
    min_market_cap: float
    min_avg_dollar_volume: float
    include_etfs: bool
    stage2_keep: int
    stage3_keep: int
    stage4_keep: int
    final_candidates: int
    ai_committee_top_n: int
    stage2_weights: dict[str, float]
    estimate_top_n: int = 20  # final candidates that get the (daily-limited) Alpha Vantage consensus
    estimate_daily_budget: int = 22  # stay under the free 25/day, leaving room for manual stock pages
    estimate_ttl_days: int = 3  # an Alpha Vantage snapshot younger than this is not re-requested


@dataclass(frozen=True)
class ModelConfig:
    scoring_model: ScoringModel
    decision_model_version: str
    config_version: str
    config_hash: str
    decision: DecisionThresholds
    committee_max_confidence_adjustment: float
    entry: EntryConfig
    freshness: FreshnessPolicy
    scanner: ScannerConfig
    impact: ImpactConfig
    major_issue_importance: float
    event_risk: EventRiskConfig
    portfolio: PortfolioLimits
    paper: PaperConfig
    calibration: CalibrationConfig
    min_ic_samples: int
    min_period_samples: int
    min_ic_periods: int
    cache_ttl: dict[str, timedelta]
    sector_models: tuple[SectorModel, ...]
    sector_models_version: str
    raw: dict[str, Any]


def _rules(items: list[dict[str, Any]]) -> tuple[MetricRule, ...]:
    return tuple(MetricRule(i["metric"], i["label"], float(i["weight"]), float(i["bad"]), float(i["good"]), bool(i.get("critical", False))) for i in items)


def load_sector_models(path: Path) -> tuple[tuple[SectorModel, ...], str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    models = tuple(
        SectorModel(
            model_id=m["id"],
            name=m["name"],
            sectors=tuple(m.get("sectors", [])),
            industry_keywords=tuple(m.get("industry_keywords", [])),
            fundamental_rules=_rules(m["fundamental"]),
            valuation_rules=_rules(m["valuation"]),
            primary_multiple=m["primary_multiple"],
            rationale=m["rationale"],
            min_coverage=float(m.get("min_coverage", 0.5)),
            ticker_overrides=tuple(m.get("ticker_overrides", [])),
        )
        for m in data["model"]
    )
    return models, data["version"]


def load_model_config(config_dir: Path | None = None, weights_override: dict[str, float] | None = None, scoring_version_override: str | None = None) -> ModelConfig:
    cdir = config_dir or CONFIG_DIR
    scoring_path = cdir / "scoring_model.toml"
    sector_path = cdir / "sector_models.toml"
    raw_bytes = scoring_path.read_bytes() + sector_path.read_bytes()
    d = tomllib.loads(scoring_path.read_text(encoding="utf-8"))
    weights = {k: float(v) for k, v in d["scoring"]["weights"].items()}
    if weights_override is not None:
        weights = dict(weights_override)
    sm = ScoringModel(
        version=scoring_version_override or d["scoring_model_version"],
        weights=weights,
        missing_component_subscore=float(d["scoring"]["missing_component_subscore"]),
    )
    dec = d["decision"]
    committee_adj = float(dec.pop("committee_max_confidence_adjustment"))
    sectors, sver = load_sector_models(sector_path)
    sc = d["scanner"]
    iss = d["issues"]
    fr = d["freshness"]
    return ModelConfig(
        scoring_model=sm,
        decision_model_version=d["decision_model_version"],
        config_version=d["config_version"],
        config_hash=hashlib.sha256(raw_bytes).hexdigest()[:12],
        decision=DecisionThresholds(**{k: float(v) for k, v in dec.items()}),
        committee_max_confidence_adjustment=committee_adj,
        entry=EntryConfig(**{k: float(v) for k, v in d["entry"].items()}),
        freshness=FreshnessPolicy(
            realtime_max_age=timedelta(seconds=fr["realtime_max_age_seconds"]),
            delayed_max_age=timedelta(seconds=fr["delayed_max_age_seconds"]),
        ),
        scanner=ScannerConfig(
            min_price=float(sc["min_price"]),
            min_market_cap=float(sc["min_market_cap"]),
            min_avg_dollar_volume=float(sc["min_avg_dollar_volume"]),
            include_etfs=bool(sc["include_etfs"]),
            stage2_keep=int(sc["stage2_keep"]),
            stage3_keep=int(sc["stage3_keep"]),
            stage4_keep=int(sc["stage4_keep"]),
            final_candidates=int(sc["final_candidates"]),
            ai_committee_top_n=int(os.environ.get("AI_COMMITTEE_TOP_N", sc["ai_committee_top_n"])),
            stage2_weights={k: float(v) for k, v in sc["stage2_weights"].items()},
            estimate_top_n=int(sc.get("estimate_top_n", 20)),
            estimate_daily_budget=int(sc.get("estimate_daily_budget", 22)),
            estimate_ttl_days=int(sc.get("estimate_ttl_days", 3)),
        ),
        impact=ImpactConfig(
            max_hops=int(iss["max_hops"]),
            decay=HopDecay(float(iss["decay_direct"]), float(iss["decay_one_hop"]), float(iss["decay_two_hop"])),
        ),
        major_issue_importance=float(iss["major_importance"]),
        event_risk=EventRiskConfig(**d["event_risk"]),
        portfolio=PortfolioLimits(**{k: float(v) for k, v in d["portfolio"].items()}),
        paper=PaperConfig(**d["paper"]),
        calibration=CalibrationConfig(**d["calibration"]),
        min_ic_samples=int(d["evaluation"]["min_ic_samples"]),
        min_period_samples=int(d["evaluation"]["min_period_samples"]),
        min_ic_periods=int(d["evaluation"]["min_ic_periods"]),
        cache_ttl={k: timedelta(seconds=int(v)) for k, v in d["cache_ttl_seconds"].items()},
        sector_models=sectors,
        sector_models_version=sver,
        raw=d,
    )


@lru_cache(maxsize=1)
def default_model_config() -> ModelConfig:
    return load_model_config()
