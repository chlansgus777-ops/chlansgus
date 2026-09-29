"""Runtime settings (environment / .env) and versioned model configuration (TOML).

Secrets are read from the environment (or the OS keyring when installed) and are never logged.
"""

from __future__ import annotations

import hashlib
import logging
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
        # not the install folder (%LOCALAPPDATA%\MarketLens): an uninstall must never touch the user's data
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MarketLensData"
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
    "TOSS_CLIENT_ID",  # 토스증권 오픈API (read-only portfolio sync); the id is kept like a secret too
    "TOSS_CLIENT_SECRET",
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


def env_file_path() -> Path:
    return Path(os.environ.get("MARKETLENS_ENV_FILE", (default_data_dir() / ".env") if FROZEN else REPO_ROOT / ".env"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional
        return
    env_path = env_file_path()
    if env_path.exists():
        load_dotenv(env_path, override=False, interpolate=False)  # a saved value is used as written: no ${…} expansion


# what the setup screen may write (nothing else: no path or code setting is reachable from the UI; the only URL is a
# LOCAL model server — loopback only, so the screen can never point the app at another host)
SETUP_KEYS = SECRET_ENV_KEYS + ("SEC_USER_AGENT", "MARKETLENS_MODE", "LLM_PROVIDER", "MARKETLENS_SCHEDULER", "OPENAI_BASE_URL", "FAST_MODEL", "DEEP_MODEL", "LLM_BUDGET_USD", "AI_COMMITTEE_ON_SCHEDULE", "LLM_PRICE_INPUT_PER_M", "LLM_PRICE_OUTPUT_PER_M")
_SETUP_VALUE = {"MARKETLENS_MODE": ("MOCK", "LIVE"), "LLM_PROVIDER": ("none", "anthropic", "openai", "openai_compatible"), "MARKETLENS_SCHEDULER": ("0", "1"), "AI_COMMITTEE_ON_SCHEDULE": ("0", "1")}
_LOCAL_URL = r"http://(?:127\.0\.0\.1|localhost)(?::\d{1,5})?(?:/[A-Za-z0-9._/-]*)?"
_MODEL_NAME = r"[A-Za-z0-9._:/-]{1,100}"


def validate_setup(values: dict[str, str]) -> dict[str, str]:
    """Clean and check first-run settings. Raises ValueError with a message the user can act on."""
    import re as _re

    out: dict[str, str] = {}
    for k, v in values.items():
        if k not in SETUP_KEYS:
            raise ValueError(f"{k}: 설정 화면에서 바꿀 수 없는 항목입니다")
        v = (v or "").strip()
        if not v:
            continue  # empty = leave unchanged
        if any(c in v for c in "\r\n\x00") or len(v) > 300:
            raise ValueError(f"{k}: 줄바꿈이 없는 300자 이하 값이어야 합니다")
        if k in _SETUP_VALUE:
            if v not in _SETUP_VALUE[k]:
                raise ValueError(f"{k}: {', '.join(_SETUP_VALUE[k])} 중 하나여야 합니다")
        elif k == "OPENAI_BASE_URL":
            if not _re.fullmatch(_LOCAL_URL, v):
                raise ValueError("OPENAI_BASE_URL: 이 컴퓨터의 로컬 모델 서버 주소만 가능합니다 (예: http://127.0.0.1:11434/v1)")
        elif k == "LLM_BUDGET_USD":
            if not _re.fullmatch(r"\d{1,4}(?:\.\d{1,2})?", v) or not 0 < float(v) <= 1000:
                raise ValueError("LLM_BUDGET_USD: 0보다 크고 1000 이하인 달러 금액이어야 합니다 (예: 8)")
        elif k in ("LLM_PRICE_INPUT_PER_M", "LLM_PRICE_OUTPUT_PER_M"):
            if not _re.fullmatch(r"\d{1,3}(?:\.\d{1,4})?", v) or not 0 < float(v) <= 500:
                raise ValueError(f"{k}: 100만 토큰당 달러 단가여야 합니다 (예: 0.10)")
        elif k in ("FAST_MODEL", "DEEP_MODEL"):
            if not _re.fullmatch(_MODEL_NAME, v):
                raise ValueError(f"{k}: 영문·숫자·._:/- 로 된 모델 이름이어야 합니다 (예: qwen2.5:7b)")
        elif k in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"):
            if not _re.fullmatch(r"[\x21-\x7e]{8,200}", v):
                raise ValueError(f"{k}: 토스증권에서 발급한 값을 공백 없이 그대로 붙여 넣으세요 (영문·숫자·기호 8자 이상)")
        elif k == "SEC_USER_AGENT":
            if not _re.fullmatch(r"[^@\s]+(?: [^@\s]+)* [^@\s]+@[^@\s]+\.[^@\s]+", v):
                raise ValueError("SEC_USER_AGENT: '이름 이메일' 형식이어야 합니다 (예: Hong Gildong hong@example.com). SEC가 요구합니다")
        elif _re.search(r"\s", v):
            raise ValueError(f"{k}: API 키에는 공백이 들어갈 수 없습니다")
        out[k] = v
    return out


@lru_cache(maxsize=1)
def keychain_backend() -> str | None:
    """The OS keychain ``keyring`` would use (e.g. "WinVaultKeyring" — Windows Credential Manager), or None when there
    is none and secrets go to the private .env file."""
    try:
        import keyring  # type: ignore[import-not-found]

        kr = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - not installed / no backend
        return None
    name = type(kr).__name__
    return None if name in ("FailKeyring", "NullKeyring") or getattr(kr, "priority", 1) <= 0 else name


def _keychain_set(name: str, value: str) -> bool:
    """Store a secret in the OS keychain (Windows Credential Manager via ``keyring``); False when no keychain is
    available, so the caller falls back to the private .env file."""
    try:
        import keyring  # type: ignore[import-not-found]

        keyring.set_password("marketlens", name, value)
        return True
    except Exception:  # noqa: BLE001 - keyring missing or no usable backend
        return False


def _dotenv_quote(v: str) -> str:
    """Inside double quotes python-dotenv decodes backslash escapes: escape them so the value read back is the
    value written ("ab\\tc" stays a backslash and a t)."""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def save_setup(values: dict[str, str], env_path: Path | None = None) -> dict[str, str]:
    """Store first-run settings without editing files by hand. Secrets go to the OS keychain when ``keyring`` is
    installed (Windows Credential Manager), else to the private ``.env`` of this installation; other settings go to
    that ``.env``. Returns {name: where} — never the values."""
    clean = validate_setup(values)
    where: dict[str, str] = {}
    to_file: dict[str, str] = {}
    for k, v in clean.items():
        if k in SECRET_ENV_KEYS and _keychain_set(k, v):
            where[k] = "keychain"
            continue
        to_file[k] = v
        where[k] = ".env"
    path = env_path or env_file_path()
    if to_file or path.exists():
        # every saved name loses its old .env line — also one saved to the keychain: .env is loaded into the
        # environment at startup and would win over the keychain (8th evaluation I5)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip().removeprefix("export ").strip() not in clean]
        if to_file:
            kept += [f'{k}="{_dotenv_quote(v)}"' for k, v in to_file.items()]
        if kept == lines:
            return where
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        tmp.replace(path)
    return where


def forget_setup(names: tuple[str, ...], env_path: Path | None = None) -> None:
    """Remove stored settings (keychain entry and .env line) and drop them from this process's environment —
    the Toss key on "연결 해제" must not come back at the next start."""
    for k in names:
        os.environ.pop(k, None)
        try:
            import keyring  # type: ignore[import-not-found]

            keyring.delete_password("marketlens", k)
        except Exception as e:  # noqa: BLE001 - not in the keychain, or no keychain: nothing to remove there
            logging.getLogger("marketlens.config").debug("keychain entry %s not removed: %s", k, type(e).__name__)
    path = env_path or env_file_path()
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip().removeprefix("export ").strip() not in names]
        if kept != lines:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            tmp.replace(path)


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
    toss_client_id: str | None = field(repr=False, default=None)
    toss_client_secret: str | None = field(repr=False, default=None)
    openai_base_url: str = "https://api.openai.com/v1"
    llm_provider: str = "none"  # anthropic | openai | openai_compatible | mock | none
    fast_model: str = "claude-haiku-4-5"
    deep_model: str = "claude-opus-5"
    enable_paper_trading: bool = True
    enable_ai_committee: bool = True
    mock_universe_size: int = 600
    log_level: str = "INFO"
    scheduler: bool = False
    scan_interval_minutes: int = 30
    data_dir: Path = field(default_factory=default_data_dir)
    llm_budget_usd: float = 0.0  # paid LLM spending cap (estimated, USD); 0 = no paid provider is used without one
    llm_price_in: float = 0.0  # USD per 1M input tokens, entered by the user for a model the app has no price for
    llm_price_out: float = 0.0
    ai_committee_on_schedule: bool = False  # the automatic hourly scan runs WITHOUT the AI committee unless this is on
    live_quotes: bool = True  # the app-wide quote stream (application/live_quotes.py)
    quote_stream_max_symbols: int = 50  # the stream's concurrent symbol limit (Finnhub free: 50 — verify on the account)

    def secrets(self) -> list[str]:
        return [s for s in (self.finnhub_api_key, self.fred_api_key, self.polygon_api_key, self.alphavantage_api_key, self.finra_api_key, self.finra_api_secret, self.anthropic_api_key, self.openai_api_key, self.toss_client_id, self.toss_client_secret) if s]


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(v: str | None, default: float) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


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
        toss_client_id=_secret("TOSS_CLIENT_ID"),
        toss_client_secret=_secret("TOSS_CLIENT_SECRET"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        llm_provider=os.environ.get("LLM_PROVIDER", "mock" if mode == DataMode.MOCK else "none").lower(),
        fast_model=os.environ.get("FAST_MODEL", "claude-haiku-4-5"),
        deep_model=os.environ.get("DEEP_MODEL", "claude-opus-5"),
        enable_paper_trading=_bool(os.environ.get("ENABLE_PAPER_TRADING"), True),
        enable_ai_committee=_bool(os.environ.get("ENABLE_AI_COMMITTEE"), True),
        mock_universe_size=int(os.environ.get("MOCK_UNIVERSE_SIZE", "600")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        scheduler=_bool(os.environ.get("MARKETLENS_SCHEDULER"), True),  # on unless turned off: the owner never has to press scan
        scan_interval_minutes=int(os.environ.get("SCAN_INTERVAL_MINUTES", "30")),
        data_dir=data_dir,
        llm_budget_usd=_float(os.environ.get("LLM_BUDGET_USD"), 0.0),
        llm_price_in=_float(os.environ.get("LLM_PRICE_INPUT_PER_M"), 0.0),
        llm_price_out=_float(os.environ.get("LLM_PRICE_OUTPUT_PER_M"), 0.0),
        ai_committee_on_schedule=_bool(os.environ.get("AI_COMMITTEE_ON_SCHEDULE"), False),
        live_quotes=_bool(os.environ.get("MARKETLENS_LIVE_QUOTES"), True),
        quote_stream_max_symbols=max(1, int(os.environ.get("QUOTE_STREAM_MAX_SYMBOLS", "50"))),
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
    committee_full_top_n: int = 5  # Tier 2: full committee (14 calls); ranks up to ai_committee_top_n get LIGHT (5 calls)
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
            committee_full_top_n=int(sc.get("committee_full_top_n", 5)),
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
