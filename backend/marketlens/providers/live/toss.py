"""Toss Securities Open API (토스증권 오픈API) — READ-ONLY client for the owner's own account.

Written against the published OpenAPI 3.1 spec, version 1.2.17 (``https://openapi.tossinvest.com/openapi-docs/latest/
openapi.json``; the endpoints and fields used here are pinned in tests/fixtures/toss/spec_subset.json and checked by
tests/unit/test_toss_client.py). Only GET endpoints are called — this module has no code path that places, changes or
cancels an order.

What the spec says and this client does about it:
- ``POST /oauth2/token`` (client credentials, form body). One valid access token per client: issuing a new one
  revokes the previous one AT ONCE — so the token is cached until 5 minutes before ``expires_in`` and issued under a
  lock (two threads issuing together would revoke each other's token). A 401 re-issues once; a second 401 means another
  program is using the same key (each issue revokes the other's token) or the key was revoked.
- The client's allowed-IP list: a call from any other IP is refused with 403 (``WTS 설정 > Open API > 허용 IP 관리``).
- Rate limits per client × group, a token bucket (``X-RateLimit-*``, ``Retry-After``); 429 waits ``Retry-After``
  (at most twice, at most 5 s) and then gives up with RATE_LIMITED — never a retry storm.
- Success ``{"result": …}``, failure ``{"error": {"code", "message", "requestId"}}``; ``message`` may be empty, so the
  Korean text shown is ours, by ``code``/status.
- Every amount is a decimal STRING; unknown enum values must be tolerated (a market other than KR/US is kept as
  ``OTHER`` and reported, never dropped silently).
- ``X-Tossinvest-Account`` = ``accountSeq`` from ``GET /api/v1/accounts`` (only BROKERAGE accounts are returned).

Secrets: the client id/secret and every issued token are registered with the log redactor; the account number is
masked (last 4 digits) before it leaves this module.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import httpx

from marketlens.infrastructure.logging import add_secrets, redact_text
from marketlens.providers.live.nasdaq_symbols import canonical

BASE_URL = "https://openapi.tossinvest.com"
SPEC_VERSION = "1.2.17"
TOKEN_MARGIN_S = 300.0  # re-issue this long before the token expires
MAX_429_WAIT_S = 5.0
MIN_GAP_S = 0.12  # our own pacing per rate-limit group (the server's bucket allows ~10/s; we never need that)

GROUP = {"/oauth2/token": "AUTH", "/api/v1/accounts": "ACCOUNT", "/api/v1/holdings": "ASSET", "/api/v1/orders": "ORDER_HISTORY",
         "/api/v1/buying-power": "ORDER_INFO", "/api/v1/exchange-rate": "MARKET_INFO", "/api/v1/prices": "MARKET_DATA"}
PRICES_MAX = 200  # symbols per /api/v1/prices call (the spec)


class TossError(Exception):
    """A failure the screen can explain. ``kind``: NOT_CONFIGURED, BAD_KEY, IP_NOT_ALLOWED, TOKEN_REVOKED, NO_ACCOUNT,
    RATE_LIMITED, UNAVAILABLE, BAD_DATA. ``text`` is Korean and says what to do; never contains a secret."""

    def __init__(self, kind: str, text: str, status: int | None = None, code: str | None = None, request_id: str | None = None) -> None:
        super().__init__(f"{kind}: {text}")
        self.kind, self.text, self.status, self.code, self.request_id = kind, text, status, code, request_id

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "status": self.status, "code": self.code, "request_id": self.request_id}


TEXT = {
    "NOT_CONFIGURED": "토스증권 키가 없습니다. 토스증권 PC 웹(WTS) 설정 > Open API에서 발급한 client_id와 client_secret을 입력하세요.",
    "BAD_KEY": "토스증권이 키를 거부했습니다(client_id 또는 client_secret이 틀렸거나, 키가 비활성·폐기됨). WTS 설정 > Open API에서 키를 확인하고 다시 입력하세요.",
    "IP_NOT_ALLOWED": "이 PC의 인터넷 주소(IP)가 토스증권 허용 목록에 없습니다. WTS 설정 > Open API > 허용 IP 관리에 이 PC의 공인 IP를 등록하세요(공유기·통신사 사정으로 IP가 바뀌면 다시 등록).",
    "TOKEN_REVOKED": "토스증권이 방금 받은 접속 토큰을 거부했습니다. 같은 키를 다른 프로그램(다른 PC, 자동매매 도구, AI 연동 등)에서도 쓰면 서로의 토큰을 무효로 만듭니다 — MarketLens 전용 키를 따로 발급하세요.",
    "NO_ACCOUNT": "조회할 수 있는 토스증권 종합매매 계좌가 없습니다(자녀·연금 계좌는 오픈API 대상이 아님).",
    "RATE_LIMITED": "토스증권 요청 한도에 걸렸습니다. 잠시 뒤 자동으로 다시 시도합니다.",
    "UNAVAILABLE": "토스증권 서버에 연결하지 못했습니다(인터넷 연결 또는 토스증권 점검). 마지막으로 받은 보유 현황을 그대로 표시하고 자동으로 다시 시도합니다.",
    "BAD_DATA": "토스증권 응답을 해석하지 못했습니다(형식이 명세와 다름). 마지막으로 받은 보유 현황을 그대로 표시합니다.",
}


def _err(kind: str, status: int | None = None, code: str | None = None, request_id: str | None = None, extra: str = "") -> TossError:
    return TossError(kind, TEXT[kind] + (f" ({extra})" if extra else ""), status, code, request_id)


# ------------------------------------------------------------------ parsing (decimal strings, enums, dates)

def dec(v: Any, what: str, required: bool = True) -> Decimal | None:
    """A spec ``decimal`` string → Decimal. ``None`` only where the spec allows null; anything else is BAD_DATA."""
    if v is None:
        if required:
            raise _err("BAD_DATA", extra=f"{what} 없음")
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise _err("BAD_DATA", extra=f"{what} 형식")
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation:
        raise _err("BAD_DATA", extra=f"{what} 숫자 아님") from None
    if not d.is_finite():
        raise _err("BAD_DATA", extra=f"{what} 숫자 아님")
    return d


def _ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise _err("BAD_DATA", extra="시각 형식") from None
    if t.tzinfo is None:
        raise _err("BAD_DATA", extra="시각에 시간대 없음")
    return t


def _missing(what: str) -> datetime:
    raise _err("BAD_DATA", extra=f"{what} 없음")


def mask_account(no: str) -> str:
    digits = "".join(c for c in str(no) if c.isdigit())
    return f"····{digits[-4:]}" if len(digits) >= 4 else "····"


def symbol_of(raw: str, market: str) -> str:
    """US tickers in MarketLens's one spelling (BRK.B); KR 6-character codes as given."""
    return canonical(raw) if market == "US" else str(raw).strip().upper()


@dataclass(frozen=True)
class TossAccount:
    seq: int
    masked: str
    kind: str


@dataclass(frozen=True)
class TossHolding:
    symbol: str
    name: str
    market: str  # KR / US / OTHER
    currency: str  # KRW / USD / OTHER
    quantity: Decimal
    last_price: Decimal
    avg_price: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    pnl: Decimal
    pnl_rate: Decimal
    daily_pnl: Decimal
    daily_rate: Decimal
    commission: Decimal
    tax: Decimal | None


@dataclass(frozen=True)
class TossFill:
    order_id: str
    symbol: str
    side: str  # BUY / SELL
    status: str
    quantity: Decimal  # filled quantity (> 0)
    avg_price: Decimal | None
    amount: Decimal | None
    commission: Decimal | None
    tax: Decimal | None
    currency: str
    ordered_at: datetime
    filled_at: datetime | None
    settlement: date | None


@dataclass
class _Group:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last: float = -1e9


def _market(v: Any) -> str:
    return v if v in ("KR", "US") else "OTHER"


def _currency(v: Any) -> str:
    return v if v in ("KRW", "USD") else "OTHER"


def parse_holdings(result: Any) -> list[TossHolding]:
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise _err("BAD_DATA", extra="보유 목록 없음")
    out = []
    for it in result["items"]:
        if not isinstance(it, dict) or not it.get("symbol"):
            raise _err("BAD_DATA", extra="보유 항목 형식")
        mk = _market(it.get("marketCountry"))
        mv, pl, dp, cost = (it.get(k) or {} for k in ("marketValue", "profitLoss", "dailyProfitLoss", "cost"))
        out.append(TossHolding(
            symbol=symbol_of(it["symbol"], mk), name=str(it.get("name") or it["symbol"])[:80], market=mk, currency=_currency(it.get("currency")),
            quantity=dec(it.get("quantity"), "수량"), last_price=dec(it.get("lastPrice"), "현재가"), avg_price=dec(it.get("averagePurchasePrice"), "매수 평균가"),
            purchase_amount=dec(mv.get("purchaseAmount"), "매입금액"), market_value=dec(mv.get("amount"), "평가금액"),
            pnl=dec(pl.get("amount"), "손익"), pnl_rate=dec(pl.get("rate"), "손익률"), daily_pnl=dec(dp.get("amount"), "일간 손익"),
            daily_rate=dec(dp.get("rate"), "일간 손익률"), commission=dec(cost.get("commission"), "수수료"), tax=dec(cost.get("tax"), "세금", required=False),
        ))
    return out


def parse_fills(orders: Any) -> list[TossFill]:
    """Closed orders with at least one execution (a partly filled order that was then cancelled counts for what filled)."""
    if not isinstance(orders, list):
        raise _err("BAD_DATA", extra="주문 목록 형식")
    out = []
    for o in orders:
        ex = o.get("execution") if isinstance(o, dict) else None
        if not isinstance(ex, dict):
            raise _err("BAD_DATA", extra="체결 결과 없음")
        q = dec(ex.get("filledQuantity"), "체결 수량")
        if q is None or q <= 0:
            continue
        side = o.get("side")
        if side not in ("BUY", "SELL"):
            continue  # an unknown side is not guessed
        cur = _currency(o.get("currency"))
        settle = ex.get("settlementDate")
        out.append(TossFill(
            order_id=str(o.get("orderId") or ""), symbol=symbol_of(o.get("symbol") or "", "US" if cur == "USD" else "KR"), side=side, status=str(o.get("status") or ""),
            quantity=q, avg_price=dec(ex.get("averageFilledPrice"), "평균 체결가", required=False), amount=dec(ex.get("filledAmount"), "체결 금액", required=False),
            commission=dec(ex.get("commission"), "수수료", required=False), tax=dec(ex.get("tax"), "세금", required=False), currency=cur,
            ordered_at=_ts(o.get("orderedAt")) or _missing("주문 시각"), filled_at=_ts(ex.get("filledAt")),
            settlement=date.fromisoformat(settle) if isinstance(settle, str) and len(settle) == 10 else None,
        ))
    return out


class TossClient:
    """Read-only. Thread-safe: the token is issued under a lock; each rate-limit group is paced on its own."""

    def __init__(self, client_id: str | None, client_secret: str | None, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, timeout: float = 12.0) -> None:
        self._id, self._secret = (client_id or "").strip(), (client_secret or "").strip()
        self.configured = bool(self._id and self._secret)
        add_secrets(self._id, self._secret)
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport, headers={"Accept": "application/json"})
        self._clock, self._sleep = clock, sleep
        self._token: str | None = None
        self._token_until = 0.0
        self._token_lock = threading.Lock()
        self._groups: dict[str, _Group] = {}
        self.issued = 0  # tokens issued by this client (tests / diagnostics)
        self.calls = 0

    def close(self) -> None:
        self._http.close()

    # ---------------------------------------------------------------- token
    def _token_now(self, stale: str | None = None) -> str:
        """The cached token, else a new one. ``stale``: the token a 401 just refused — replaced only if it is still
        the cached one (another thread may have replaced it already; issuing again would revoke that new one)."""
        if not self.configured:
            raise _err("NOT_CONFIGURED")
        with self._token_lock:
            if self._token and self._token != stale and self._clock() < self._token_until:
                return self._token
            r = self._send("POST", "/oauth2/token", data={"grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret})
            if r.status_code == 200:
                try:
                    body = r.json()
                    tok, ttl = str(body["access_token"]), float(body["expires_in"])
                except (ValueError, KeyError, TypeError):
                    raise _err("BAD_DATA", 200, extra="토큰 응답") from None
                if not tok or ttl <= 0:
                    raise _err("BAD_DATA", 200, extra="토큰 응답")
                add_secrets(tok)
                self._token, self._token_until = tok, self._clock() + max(ttl - TOKEN_MARGIN_S, ttl / 2)
                self.issued += 1
                return tok
            self._token = None
            try:
                code = r.json().get("error")
            except (ValueError, AttributeError):
                code = None  # no OAuth error body: decided by the status alone
            if r.status_code == 403:
                raise _err("IP_NOT_ALLOWED", 403, code)
            if r.status_code in (400, 401) and code in ("invalid_client", "unauthorized_client", "access_denied", "invalid_grant", None):
                raise _err("BAD_KEY", r.status_code, code)
            raise self._status_error(r, code)

    # ---------------------------------------------------------------- transport
    def _pace(self, path: str) -> None:
        g = self._groups.setdefault(GROUP.get(path, "OTHER"), _Group())
        with g.lock:
            wait = g.last + MIN_GAP_S - self._clock()
            if wait > 0:
                self._sleep(wait)
            g.last = self._clock()

    def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        for attempt in range(3):
            self._pace(path)
            self.calls += 1
            try:
                r = self._http.request(method, path, **kw)
            except httpx.HTTPError as e:
                if attempt == 0:
                    self._sleep(1.0)
                    continue
                raise _err("UNAVAILABLE", extra=type(e).__name__) from None
            if r.status_code == 429 and attempt < 2:
                ra = r.headers.get("Retry-After", "")
                wait = float(ra) if ra.isdigit() else 1.0
                if wait > MAX_429_WAIT_S:
                    return r
                self._sleep(wait)
                continue
            if r.status_code >= 500 and attempt == 0:
                self._sleep(1.0)
                continue
            return r
        return r

    def _status_error(self, r: httpx.Response, code: str | None = None, rid: str | None = None) -> TossError:
        if r.status_code == 429:
            return _err("RATE_LIMITED", 429, code, rid)
        if r.status_code >= 500:
            return _err("UNAVAILABLE", r.status_code, code, rid)
        if r.status_code == 403:
            return _err("IP_NOT_ALLOWED", 403, code, rid)
        return _err("BAD_DATA", r.status_code, code, rid, extra=redact_text(f"HTTP {r.status_code} {code or ''}").strip())

    def _get(self, path: str, params: dict[str, Any] | None = None, account: int | None = None) -> Any:
        tok = self._token_now()
        for again in (False, True):
            headers = {"Authorization": f"Bearer {tok}"}
            if account is not None:
                headers["X-Tossinvest-Account"] = str(account)
            r = self._send("GET", path, params=params, headers=headers)
            if r.status_code == 401 and not again:
                tok = self._token_now(stale=tok)  # expired early or revoked by a newer token: one fresh token
                continue
            break
        try:
            body: Any = r.json()
        except ValueError:
            body = None  # not JSON: judged by the status below (a 200 without a result is BAD_DATA)
        err = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
        if r.status_code == 200:
            if not isinstance(body, dict) or "result" not in body:
                raise _err("BAD_DATA", 200, extra="result 없음")
            return body["result"]
        if r.status_code == 401:
            with self._token_lock:
                self._token = None
            raise _err("TOKEN_REVOKED", 401, err.get("code"), err.get("requestId"))
        raise self._status_error(r, err.get("code"), err.get("requestId"))

    # ---------------------------------------------------------------- endpoints (GET only)
    def accounts(self) -> list[TossAccount]:
        res = self._get("/api/v1/accounts")
        if not isinstance(res, list):
            raise _err("BAD_DATA", extra="계좌 목록 형식")
        out = []
        for a in res:
            try:
                out.append(TossAccount(int(a["accountSeq"]), mask_account(a.get("accountNo", "")), str(a.get("accountType") or "")))
            except (KeyError, TypeError, ValueError):
                raise _err("BAD_DATA", extra="계좌 항목 형식") from None
        return out

    def brokerage_account(self) -> TossAccount:
        accts = [a for a in self.accounts() if a.kind == "BROKERAGE"]
        if not accts:
            raise _err("NO_ACCOUNT")
        return accts[0]

    def holdings(self, account: int) -> list[TossHolding]:
        return parse_holdings(self._get("/api/v1/holdings", account=account))

    def buying_power(self, account: int, currency: str) -> Decimal:
        res = self._get("/api/v1/buying-power", {"currency": currency}, account=account)
        if not isinstance(res, dict):
            raise _err("BAD_DATA", extra="매수 가능 금액 형식")
        return dec(res.get("cashBuyingPower"), "매수 가능 금액")  # type: ignore[return-value]

    def usd_krw(self) -> tuple[Decimal, Decimal, datetime | None]:
        """(buy rate, mid rate, valid from) for 1 USD in KRW — a reference rate (the spec: the rate of an order may differ)."""
        res = self._get("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"})
        if not isinstance(res, dict):
            raise _err("BAD_DATA", extra="환율 형식")
        return dec(res.get("rate"), "환율"), dec(res.get("midRate"), "매매기준율"), _ts(res.get("validFrom"))  # type: ignore[return-value]

    def prices(self, symbols: list[str]) -> list[tuple[str, Decimal, datetime | None]]:
        """Current prices (symbol, last price, data time) for up to ``PRICES_MAX`` symbols per call — the market data
        group, no account header. A symbol Toss does not know is left out of the answer (never guessed)."""
        out: list[tuple[str, Decimal, datetime | None]] = []
        for i in range(0, len(symbols), PRICES_MAX):
            res = self._get("/api/v1/prices", {"symbols": ",".join(symbols[i:i + PRICES_MAX])})
            if not isinstance(res, list):
                raise _err("BAD_DATA", extra="현재가 형식")
            for r in res:
                if not isinstance(r, dict) or not r.get("symbol"):
                    raise _err("BAD_DATA", extra="현재가 항목 형식")
                p = dec(r.get("lastPrice"), "현재가")
                if p is None or p <= 0:
                    continue
                out.append((symbol_of(r["symbol"], "US" if r.get("currency") == "USD" else "KR"), p, _ts(r.get("timestamp"))))
        return out

    def fills(self, account: int, since: date, until: date, max_pages: int = 30) -> tuple[list[TossFill], bool]:
        """Executions of closed orders ordered from ``since`` to ``until`` (KST days, inclusive), newest pages first.
        Returns (fills, complete) — complete=False when ``max_pages`` pages of 100 did not reach the end."""
        out: list[TossFill] = []
        cursor: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"status": "CLOSED", "from": since.isoformat(), "to": until.isoformat(), "limit": 100}
            if cursor:
                params["cursor"] = cursor
            res = self._get("/api/v1/orders", params, account=account)
            if not isinstance(res, dict):
                raise _err("BAD_DATA", extra="주문 목록 형식")
            out += parse_fills(res.get("orders"))
            cursor = res.get("nextCursor")
            if not res.get("hasNext") or not cursor:
                return out, True
        return out, False
