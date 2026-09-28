"""Backtest-only tables (own MetaData: never part of the operating schema or its migrations)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

import httpx
from sqlalchemy import Boolean, Column, Date, Float, Integer, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.engine import Engine

BT_META = MetaData()

# every Polygon reference record of a stock ticker (active and delisted): the raw material of the ticker → CIK map
bt_tickers = Table(
    "bt_tickers", BT_META,
    Column("ticker", String(16), primary_key=True),
    Column("seq", Integer, primary_key=True),  # several records per ticker (reuse, renames)
    Column("active", Boolean, nullable=False),
    Column("cik", Integer, nullable=True),
    Column("type", String(16), nullable=True),
    Column("name", String(256), nullable=True),
    Column("exchange", String(16), nullable=True),
    Column("delisted", Date, nullable=True),
)

# ticker → CIK on a day: valid_from <= day < valid_to (None = open)
bt_ticker_map = Table(
    "bt_ticker_map", BT_META,
    Column("ticker", String(16), primary_key=True),
    Column("valid_from", Date, primary_key=True),
    Column("valid_to", Date, nullable=True),
    Column("cik", Integer, nullable=True),
    Column("type", String(16), nullable=True),
    Column("name", String(256), nullable=True),
    Column("exchange", String(16), nullable=True),
)

bt_dividends = Table(
    "bt_dividends", BT_META,
    Column("ticker", String(16), primary_key=True),
    Column("ex_date", Date, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("cash_amount", Float, nullable=False),
    Column("currency", String(8), nullable=True),
    Column("dividend_type", String(8), nullable=True),
    Column("declaration_date", Date, nullable=True),
    Column("pay_date", Date, nullable=True),
)

# current SEC profile per company (sector, industry): no publication time — one of the two allowed exceptions
bt_profiles = Table(
    "bt_profiles", BT_META,
    Column("cik", Integer, primary_key=True),
    Column("sector", String(64), nullable=True),
    Column("industry", String(128), nullable=True),
    Column("sic", Integer, nullable=True),
    Column("payload", Text, nullable=True),
)

# recorded provider HTTP answers, replayed offline (FRED/ALFRED vintages requested exactly as the app requests them)
bt_http = Table(
    "bt_http", BT_META,
    Column("key", String(64), primary_key=True),
    Column("url", Text, nullable=False),
    Column("status", Integer, nullable=False),
    Column("body", Text, nullable=False),
)

# market series that are never revised (VIX, rates, FX), full history by observation day — for regimes and cash yield
bt_series = Table(
    "bt_series", BT_META,
    Column("series_id", String(32), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("value", Float, nullable=False),
)

# listing intervals the collector could not resolve safely (docs/freedata: collector defence rules): their bars stay in
# price_bars for the audit, and the backtest leaves them out
bt_unresolved = Table(
    "bt_unresolved", BT_META,
    Column("ticker", String(16), primary_key=True),
    Column("valid_from", Date, primary_key=True),  # the interval of bt_ticker_map
    Column("reason", String(32), primary_key=True),
    Column("detail", Text, nullable=True),
)

bt_meta = Table(
    "bt_meta", BT_META,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)

SECRET_PARAMS = {"api_key", "apikey", "apiKey", "token"}


def request_key(url: str) -> tuple[str, str]:
    """(key, url without secrets) of a GET request: path + sorted query parameters, credentials removed."""
    u = urlsplit(url)
    q = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in SECRET_PARAMS)
    clean = f"{u.netloc}{u.path}?" + "&".join(f"{k}={v}" for k, v in q)
    return hashlib.sha256(clean.encode()).hexdigest(), clean


def bt_engine(path: str) -> Engine:
    eng = create_engine(f"sqlite:///{path}")
    from marketlens.infrastructure.db.models import Base

    Base.metadata.create_all(eng)
    BT_META.create_all(eng)
    return eng


def get_meta(eng: Engine, key: str) -> str | None:
    with eng.connect() as c:
        return c.execute(select(bt_meta.c.value).where(bt_meta.c.key == key)).scalar()


def get_meta_date(eng: Engine, key: str) -> date | None:
    """A date stored by ``set_meta`` — written as a plain ISO string (a str is stored as is), older runs may hold a
    JSON-quoted one. Both read back as the same date (run 36396716134 crashed on json.loads("2016-01-04"))."""
    v = get_meta(eng, key)
    if not v:
        return None
    v = v.strip()
    if v.startswith('"'):
        v = json.loads(v)
    return date.fromisoformat(v) if v else None


def set_meta(eng: Engine, key: str, value: Any) -> None:
    v = value if isinstance(value, str) else json.dumps(value)
    with eng.begin() as c:
        c.execute(bt_meta.delete().where(bt_meta.c.key == key))
        c.execute(bt_meta.insert().values(key=key, value=v))


class RecordingTransport(httpx.BaseTransport):
    """Forwards to the network and stores every answer under its credential-free request key."""

    def __init__(self, eng: Engine, inner: httpx.BaseTransport | None = None) -> None:
        self._real = inner or httpx.HTTPTransport(retries=2)  # ``inner``: a fixture transport in tests
        self._eng = eng

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._real.handle_request(request)
        resp.read()
        key, clean = request_key(str(request.url))
        with self._eng.begin() as c:
            c.execute(bt_http.delete().where(bt_http.c.key == key))
            c.execute(bt_http.insert().values(key=key, url=clean, status=resp.status_code, body=resp.content.decode("utf-8", "replace")))
        return httpx.Response(resp.status_code, content=resp.content, headers={"content-type": "application/json"}, request=request)


class NetworkBlocked(httpx.TransportError):
    """A backtest asked for something outside the collected data (leak check 4: no network). An httpx transport
    error, so the app's HttpClient turns it into ProviderUnavailable — the input is MISSING, never fetched."""


class ReplayTransport(httpx.BaseTransport):
    """Serves recorded answers; anything not recorded fails — the backtest never reaches the network."""

    def __init__(self, eng: Engine) -> None:
        self._eng = eng
        self.served = 0
        self.misses: list[str] = []
        self.urls: list[str] = []  # credential-free URLs served (time audit: the vintages asked for)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key, clean = request_key(str(request.url))
        with self._eng.connect() as c:
            row = c.execute(select(bt_http.c.status, bt_http.c.body).where(bt_http.c.key == key)).first()
        if row is None:
            self.misses.append(clean)
            raise NetworkBlocked(f"not in the collected data: {clean[:200]}", request=request)
        self.served += 1
        self.urls.append(clean)
        return httpx.Response(row[0], content=row[1].encode(), headers={"content-type": "application/json"}, request=request)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows_as_dicts(rows: Any) -> list[Mapping[str, Any]]:
    return [dict(r._mapping) for r in rows]
