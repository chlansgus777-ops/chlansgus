"""Nasdaq Trader symbol directory (free, official, no key): what KIND of security each listed ticker is.

https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt and otherlisted.txt (definitions:
https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs) — current listings only, never a history. The SEC ticker file
has no instrument type, so preferreds, units, warrants, notes and funds were counted as if they were stocks in the
readiness denominators (diagnosis 2026-09-28: 7,676 listed, 6,427 common). A ticker the directory does not know stays
"unknown" and is counted (never excluded on a guess).
"""

from __future__ import annotations

import re
from typing import Any

from marketlens.domain.enums import DataMode
from marketlens.providers.contracts import ProviderDataError
from marketlens.providers.live.http import HttpClient

COMMON_KINDS = frozenset({"common", "unknown"})  # what the scanner can use: common stock, ordinary shares, ADSs

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("warrant", re.compile(r"\bwarrants?\b", re.I)),
    ("right", re.compile(r"\brights?\b", re.I)),
    ("unit", re.compile(r"\bunits?\b", re.I)),
    ("note", re.compile(r"\b(notes?|debentures?|bonds?)\b|\bdue \d{4}\b", re.I)),
    ("preferred", re.compile(r"\bpreferred\b|\bpref\b|\bseries [a-z]\b.*%|%.*\bseries\b|\bperpetual\b", re.I)),
    ("common", re.compile(r"common stock|common shares|ordinary shares|american depositary shares|depositary shares|\bADS\b|class [a-z] (common|ordinary|shares)|shares of beneficial interest", re.I)),
)


def canonical(ticker: str) -> str:
    """One spelling for a class suffix: SEC 'BRK-B', Nasdaq ACT 'BRK.B', CQS 'BRK B' → 'BRK.B'."""
    return re.sub(r"[-/ ]", ".", ticker.strip().upper())


def classify(name: str, etf: bool) -> str:
    if etf:
        return "etf"
    for kind, rx in _RULES:
        if rx.search(name):
            if kind == "common" and re.search(r"\bpreferred\b|%", name, re.I):
                return "preferred"  # "Depositary Shares, each representing 1/1000 of a 6.5% Preferred" is a preferred
            return kind
    return "unknown"


def parse_directory(text: str, symbol_col: str) -> dict[str, str]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or "|" not in lines[0]:
        raise ProviderDataError("nasdaqtrader: unexpected symbol directory format")
    head = lines[0].split("|")
    try:
        i_sym, i_name, i_etf = head.index(symbol_col), head.index("Security Name"), head.index("ETF")
    except ValueError as e:
        raise ProviderDataError(f"nasdaqtrader: missing column ({e})") from e
    i_test = head.index("Test Issue") if "Test Issue" in head else None
    out: dict[str, str] = {}
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        f = ln.split("|")
        if len(f) < len(head) or (i_test is not None and f[i_test] == "Y"):
            continue
        out[canonical(f[i_sym])] = classify(f[i_name], f[i_etf] == "Y")
    if not out:
        raise ProviderDataError("nasdaqtrader: empty symbol directory")
    return out


class NasdaqSymbolDirectory:
    name = "nasdaqtrader"
    mode = DataMode.LIVE
    configured = True

    def __init__(self, transport: Any = None) -> None:
        self._http = HttpClient("https://www.nasdaqtrader.com", transport=transport, timeout=60.0)

    def instrument_kinds(self) -> dict[str, str]:
        """canonical ticker → common | preferred | warrant | unit | right | note | etf | unknown (two small files)."""
        kinds = parse_directory(self._http.get_text("/dynamic/SymDir/nasdaqlisted.txt"), "Symbol")
        kinds.update(parse_directory(self._http.get_text("/dynamic/SymDir/otherlisted.txt"), "ACT Symbol"))
        return kinds
