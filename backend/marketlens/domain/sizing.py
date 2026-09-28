"""One size limit for every buy (independent review 2026-09-28, F01/F05).

A buy's size is decided in several places — the action itself (BUY = full, BUY SMALL / ADD = half), the hard
vetoes (extreme event risk → small), an unknown sector (→ small), the portfolio review (cash, sector, theme,
correlation → FULL/HALF/SMALL/WATCH) and the AI portfolio manager (may only lower it). The review found the dollar
amount read only one of them, so a HALF or SMALL limit came back as a full-size purchase on the screen. Every
consumer (the decision, the stored recommendation, the buy amount, the paper position) now takes the tightest of
all of them through this module."""

from __future__ import annotations

from typing import Any

SIZE_ORDER = ("WATCH", "SMALL", "HALF", "FULL")  # tightest first
ACTION_BASE_SIZE = {"BUY": "FULL", "BUY SMALL": "HALF", "ADD": "HALF"}
SIZE_FRACTION_OF_FULL = {"FULL": 1.0, "HALF": 0.5, "SMALL": 0.25, "WATCH": 0.0}


def tightest(*caps: str | None) -> str | None:
    """The smallest of the given size limits; None (and anything that is not a size class) means no limit."""
    known = [c for c in caps if c in SIZE_ORDER]
    return min(known, key=SIZE_ORDER.index) if known else None


def effective_size(action: str, *caps: str | None) -> str | None:
    """The size a bullish ``action`` may actually take under every limit; None when the action buys nothing."""
    base = ACTION_BASE_SIZE.get(action)
    return tightest(base, *caps) if base else None


def recommendation_size_cap(row: Any) -> str | None:
    """The limit a stored recommendation carries: its decision's ``size_limit`` and the stored ``size_class`` (portfolio
    review and AI portfolio manager), whichever is smaller — a row stored before 2026-09-28 may hold it in only one."""
    result = getattr(row, "result", None)
    decision = (result.get("decision") or {}) if isinstance(result, dict) else {}
    return tightest(decision.get("size_limit"), getattr(row, "size_class", None))
