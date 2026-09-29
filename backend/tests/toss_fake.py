"""A fake Toss Securities Open API server (httpx transport) that behaves as the published spec says — used instead of
the real service, which needs the owner's key and an allowed IP. Every request is checked against the pinned spec
subset (method, path, required parameters and headers) and every response it sends is validated against the spec's
response schema, so a fixture that drifts from the spec fails the test instead of passing a wrong client.

Behaviour copied from the spec text: one valid token per client (a new issue revokes the old one at once); the
allowed-IP list (403 on the token endpoint and every API); 401 with WWW-Authenticate for a bad/revoked token; 429 with
Retry-After / X-RateLimit-*; ``status=CLOSED`` pages of ``limit`` (max 100) with ``nextCursor``/``hasNext``; the
``{"result": …}`` / ``{"error": {requestId, code, message}}`` envelopes; decimal strings.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

SPEC = json.loads((Path(__file__).parent / "fixtures" / "toss" / "spec_subset.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ a small JSON-schema check (the subset the spec uses)
def check(schema: dict[str, Any], v: Any, where: str = "$") -> list[str]:
    errs: list[str] = []
    for sub in schema.get("allOf", []):
        errs += check(sub, v, where)
    if "oneOf" in schema or "anyOf" in schema:
        subs = schema.get("oneOf") or schema.get("anyOf")
        if all(check(s, v, where) for s in subs):
            errs.append(f"{where}: matches none of oneOf/anyOf")
    t = schema.get("type")
    types = t if isinstance(t, list) else ([t] if t else [])
    ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool), "number": isinstance(v, (int, float)) and not isinstance(v, bool),
          "boolean": isinstance(v, bool), "object": isinstance(v, dict), "array": isinstance(v, list), "null": v is None}
    if types and not any(ok.get(x, False) for x in types):
        return errs + [f"{where}: {type(v).__name__} is not {types}"]
    if "enum" in schema and v is not None and v not in schema["enum"]:
        errs.append(f"{where}: {v!r} not in {schema['enum']}")
    if schema.get("format") == "decimal" and isinstance(v, str):
        try:
            float(v)
        except ValueError:
            errs.append(f"{where}: {v!r} is not a decimal string")
    if isinstance(v, dict):
        for r in schema.get("required", []):
            if r not in v:
                errs.append(f"{where}.{r}: required")
        for k, sub in schema.get("properties", {}).items():
            if k in v:
                errs += check(sub, v[k], f"{where}.{k}")
    if isinstance(v, list) and "items" in schema:
        for i, x in enumerate(v):
            errs += check(schema["items"], x, f"{where}[{i}]")
    return errs


def response_schema(path: str, method: str, status: int) -> dict[str, Any] | None:
    r = SPEC["paths"][path][method]["responses"].get(str(status))
    return (r or {}).get("content", {}).get("application/json", {}).get("schema")


# ------------------------------------------------------------------ sample data (shapes from the spec's examples)
def holding(symbol: str, qty: str, avg: str, last: str, market: str = "US", name: str | None = None) -> dict[str, Any]:
    q, a, p = float(qty), float(avg), float(last)
    cur = "USD" if market == "US" else "KRW"
    buy, val = q * a, q * p
    fmt = (lambda x: f"{x:.2f}") if cur == "USD" else (lambda x: f"{x:.0f}")
    return {"symbol": symbol, "name": name or symbol, "marketCountry": market, "currency": cur, "quantity": qty, "lastPrice": last, "averagePurchasePrice": avg,
            "marketValue": {"purchaseAmount": fmt(buy), "amount": fmt(val), "amountAfterCost": fmt(val * 0.998)},
            "profitLoss": {"amount": fmt(val - buy), "amountAfterCost": fmt(val * 0.998 - buy), "rate": f"{(val - buy) / buy:.4f}", "rateAfterCost": f"{(val * 0.998 - buy) / buy:.4f}"},
            "dailyProfitLoss": {"amount": fmt(val * 0.01), "rate": "0.0100"}, "cost": {"commission": fmt(buy * 0.001), "tax": None if cur == "USD" else fmt(val * 0.0018)}}


def order(n: int, symbol: str, side: str, qty: str, price: str, day: str, cur: str = "USD", filled: str | None = None, status: str = "FILLED") -> dict[str, Any]:
    f = filled if filled is not None else qty
    return {"orderId": f"ord{n:04d}", "symbol": symbol, "side": side, "orderType": "LIMIT", "timeInForce": "DAY", "status": status, "price": price, "quantity": qty,
            "orderAmount": None, "currency": cur, "orderedAt": f"{day}T23:30:00.000+09:00", "canceledAt": None,
            "execution": {"filledQuantity": f, "averageFilledPrice": price if float(f) > 0 else None, "filledAmount": f"{float(f) * float(price):.2f}" if float(f) > 0 else None,
                          "commission": "0.10" if float(f) > 0 else None, "tax": "0" if float(f) > 0 else None,
                          "filledAt": f"{day}T23:31:00.000+09:00" if float(f) > 0 else None, "settlementDate": None}}


class FakeToss:
    def __init__(self, client_id: str = "c_01TESTCLIENT0000", secret: str = "s3cr3t-value-for-tests", expires_in: int = 86400) -> None:
        self.client_id, self.secret, self.expires_in = client_id, secret, expires_in
        self.ip_allowed = True
        self.token: str | None = None
        self._n = itertools.count(1)
        self.accounts = [{"accountNo": "12345678901", "accountSeq": 7, "accountType": "BROKERAGE"}]
        self.items: list[dict[str, Any]] = [holding("NVDA", "10", "100.00", "120.50"), holding("BRK.B", "2.5", "400", "410", name="Berkshire Hathaway B"),
                                            holding("005930", "30", "65000", "72000", market="KR", name="삼성전자")]
        self.cash = {"USD": "1234.56", "KRW": "500000"}
        self.orders: list[dict[str, Any]] = [order(i, "NVDA", "BUY", "1", "100.00", "2026-09-%02d" % (1 + i % 20)) for i in range(1, 131)]
        self.rate = {"rate": "1385.50", "midRate": "1380.00"}
        self.prices: dict[str, tuple[str, str | None]] = {}  # symbol -> (lastPrice, timestamp) for /api/v1/prices
        self.faults: list[httpx.Response | Exception] = []  # served first, in order (429, 500, a network error…)
        self.requests: list[httpx.Request] = []
        self.errors: list[str] = []  # spec violations seen (requests or our own responses)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---------------------------------------------------------------- helpers
    def _resp(self, path: str, method: str, status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
        sch = response_schema(path, method, status)
        if sch is not None:
            self.errors += [f"response {method.upper()} {path} {status}: {e}" for e in check(sch, body)]
        rid = f"01REQ{next(self._n):08d}"
        return httpx.Response(status, json=body, headers={"X-Request-Id": rid, **(headers or {})})

    def _error(self, path: str, method: str, status: int, code: str, message: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
        return self._resp(path, method, status, {"error": {"requestId": "01REQERR", "code": code, "message": message}}, headers)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.faults:
            f = self.faults.pop(0)
            if isinstance(f, Exception):
                raise f
            return f
        path, method = req.url.path, req.method.lower()
        op = SPEC["paths"].get(path, {}).get(method)
        if op is None:
            self.errors.append(f"request to an endpoint MarketLens must not call: {req.method} {path}")
            return httpx.Response(404, json={"error": {"requestId": "x", "code": "not-found", "message": ""}})
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        for prm in op.get("parameters", []):
            if prm.get("required"):
                present = (prm["name"] in q) if prm["in"] == "query" else (req.headers.get(prm["name"]) is not None) if prm["in"] == "header" else True
                if not present:
                    self.errors.append(f"request {req.method} {path}: required {prm['in']} {prm['name']} missing")
            sch = prm.get("schema", {})
            if prm["in"] == "query" and prm["name"] in q and "enum" in sch and q[prm["name"]] not in sch["enum"]:
                self.errors.append(f"request {req.method} {path}: {prm['name']}={q[prm['name']]} not in {sch['enum']}")
        if not self.ip_allowed:
            if path == "/oauth2/token":
                return self._resp(path, method, 403, {"error": "access_denied", "error_description": "IP not allowed"})
            return self._error(path, method, 403, "ip-not-allowed")
        if path == "/oauth2/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if req.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
                self.errors.append("token request is not form-encoded")
            if form.get("grant_type") != "client_credentials":
                return self._resp(path, method, 400, {"error": "unsupported_grant_type"})
            if form.get("client_id") != self.client_id or form.get("client_secret") != self.secret:
                return self._resp(path, method, 401, {"error": "invalid_client"})
            self.token = f"eyJfake.{next(self._n)}.sig"  # the new token revokes the previous one at once
            return self._resp(path, method, 200, {"access_token": self.token, "token_type": "Bearer", "expires_in": self.expires_in})
        auth = req.headers.get("authorization", "")
        if not self.token or auth != f"Bearer {self.token}":
            return self._error(path, method, 401, "unauthorized", headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        acct = req.headers.get("x-tossinvest-account")
        if path == "/api/v1/prices":
            syms = [x for x in q.get("symbols", "").split(",") if x]
            if len(syms) > 200:
                self.errors.append(f"/api/v1/prices with {len(syms)} symbols (spec: at most 200)")
            rows = [{"symbol": x, "timestamp": self.prices[x][1], "lastPrice": self.prices[x][0], "currency": "USD"} for x in syms if x in self.prices]
            return self._resp(path, method, 200, {"result": rows})
        if path == "/api/v1/accounts":
            return self._resp(path, method, 200, {"result": self.accounts})
        if acct is not None and int(acct) not in {a["accountSeq"] for a in self.accounts}:
            return self._error(path, method, 404 if path == "/api/v1/buying-power" else 400, "account-not-found")
        if path == "/api/v1/holdings":
            return self._resp(path, method, 200, {"result": self._summary(self.items)})
        if path == "/api/v1/buying-power":
            cur = q.get("currency", "USD")
            return self._resp(path, method, 200, {"result": {"currency": cur, "cashBuyingPower": self.cash[cur]}})
        if path == "/api/v1/exchange-rate":
            return self._resp(path, method, 200, {"result": {"baseCurrency": "USD", "quoteCurrency": "KRW", **self.rate, "basisPoint": "40", "rateChangeType": "UP",
                                                             "validFrom": "2026-09-29T09:30:00+09:00", "validUntil": "2026-09-29T09:31:00+09:00"}})
        if path == "/api/v1/orders":
            if q.get("status") != "CLOSED":
                return self._resp(path, method, 200, {"result": {"orders": [], "nextCursor": None, "hasNext": False}})
            limit = min(int(q.get("limit", 20)), 100)
            rows = [o for o in self.orders if (not q.get("from") or o["orderedAt"][:10] >= q["from"]) and (not q.get("to") or o["orderedAt"][:10] <= q["to"])]
            rows.sort(key=lambda o: o["orderedAt"], reverse=True)
            start = int(q.get("cursor", "0"))
            page = rows[start:start + limit]
            more = start + limit < len(rows)
            return self._resp(path, method, 200, {"result": {"orders": page, "nextCursor": str(start + limit) if more else None, "hasNext": more}})
        return self._error(path, method, 404, "not-found")

    @staticmethod
    def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        def tot(get: Any, cur: str) -> str | None:
            xs = [float(get(i)) for i in items if i["currency"] == cur]
            if cur == "USD":
                return f"{sum(xs):.2f}" if xs else None
            return f"{sum(xs):.0f}"

        def pair(get: Any) -> dict[str, Any]:
            return {"krw": tot(get, "KRW"), "usd": tot(get, "USD")}
        return {"totalPurchaseAmount": pair(lambda i: i["marketValue"]["purchaseAmount"]),
                "marketValue": {"amount": pair(lambda i: i["marketValue"]["amount"]), "amountAfterCost": pair(lambda i: i["marketValue"]["amountAfterCost"])},
                "profitLoss": {"amount": pair(lambda i: i["profitLoss"]["amount"]), "amountAfterCost": pair(lambda i: i["profitLoss"]["amountAfterCost"]), "rate": "0.1000", "rateAfterCost": "0.0900"},
                "dailyProfitLoss": {"amount": pair(lambda i: i["dailyProfitLoss"]["amount"]), "rate": "0.0100"}, "items": items}
