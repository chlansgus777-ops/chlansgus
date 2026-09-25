"""Shared heavier fixtures (mock market analysis)."""

from __future__ import annotations

from functools import lru_cache

from marketlens.application.data_access import DataAccess
from marketlens.application.graph_seed import GraphSeed
from marketlens.application.registry import build_mock_registry
from marketlens.application.scanner import Scanner
from marketlens.application.theses import ThesisBook
from marketlens.config import load_model_config
from tests.conftest import NOW


@lru_cache(maxsize=1)
def mock_scanner() -> Scanner:
    cfg = load_model_config()
    reg = build_mock_registry(now=NOW, universe_size=200)
    return Scanner(DataAccess(reg, cfg.cache_ttl), cfg, GraphSeed(), ThesisBook())


@lru_cache(maxsize=8)
def analysis(ticker: str):  # type: ignore[no-untyped-def]
    return mock_scanner().analyze_single(ticker, NOW)
