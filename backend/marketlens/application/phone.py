"""The phone (owner 2026-09-30: "모바일용도 가능해? 안드로이드" → "집 와이파이만").

The PC keeps doing everything (data, the live judgements, the 토스증권 account — its keys never leave the PC); a phone on
the same Wi-Fi opens the same screens from the PC, read-only:
- off by default; when switched on, a second listener serves the app on the home network (api/phone_server.py)
- a phone is let in once with a 6-digit code shown on the PC (5 minutes, 5 tries, one use); it then carries a device
  token (a cookie; only its SHA-256 is stored) until it is removed on the PC
- only private-network addresses (192.168.x.x, 10.x.x.x, 172.16–31.x.x) are served; nothing from the internet
- a phone can look, never change: every non-GET request from it is refused (api/app.py)
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from datetime import datetime
from typing import Any, Callable

from marketlens.infrastructure.db import repository as repo

log = logging.getLogger("marketlens.phone")
ENABLED_KEY, DEVICES_KEY = "phone.enabled", "phone.devices"
PORT = 8766  # fixed, so the address typed into the phone stays the same
CODE_TTL_S = 300.0
CODE_TRIES = 5
MAX_DEVICES = 5
LOCKOUT_S = 60.0  # an address that got the code wrong 5 times waits this long
COOKIE = "ml_phone"


class PairError(ValueError):
    pass


def is_private(ip: str) -> bool:
    """A home-network IPv4 address (RFC 1918). Loopback, link-local, public and IPv6 addresses are not."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local and not a.is_unspecified


def lan_addresses() -> list[str]:
    """This PC's home-network addresses (what the phone types in). No packet is sent: connecting a UDP socket only
    picks the outgoing interface."""
    ips: set[str] = set()
    for probe in ("10.255.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                ips.add(s.getsockname()[0])
        except OSError as e:  # no route of that kind (no network): the other probe or the host name may still answer
            log.debug("lan probe %s: %s", probe, type(e).__name__)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError as e:  # the host name does not resolve: the probes' answer stands
        log.debug("lan host name: %s", type(e).__name__)
    return sorted(ip for ip in ips if is_private(ip))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PhoneLink:
    def __init__(self, sf: Callable[[], Any], now: Callable[[], datetime], port: int = PORT) -> None:
        self.sf, self.now, self.port = sf, now, port
        self._lock = threading.Lock()
        self._code: tuple[str, float, int] | None = None  # (code, expires at (monotonic), tries left)
        self._fails: dict[str, tuple[int, float]] = {}  # ip -> (wrong codes, since)
        self.server: Any = None  # api.phone_server.PhoneServer, set by the app
        self._seen: dict[str, float] = {}
        with self.sf() as s:
            self._enabled = repo.get_setting(s, ENABLED_KEY) == "1"
            raw = repo.get_setting(s, DEVICES_KEY)
        self._devices: list[dict[str, Any]] = json.loads(raw) if raw else []

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self, with_code: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "enabled": self._enabled, "port": self.port, "addresses": lan_addresses() if self._enabled else [],
            "listening": bool(self.server and self.server.listening), "error": self.server.error if self.server else None,
            "devices": [{k: d[k] for k in ("id", "name", "created", "last_seen")} for d in self._devices],
        }
        if with_code and self._enabled:
            code = self._live_code()
            out["code"] = code[0] if code else None
            out["code_expires_in"] = round(code[1] - time.monotonic()) if code else None
        return out

    def set_enabled(self, on: bool) -> dict[str, Any]:
        with self._lock:
            self._enabled = on
            if not on:
                self._code = None
            self._save(ENABLED_KEY, "1" if on else "0")
        if self.server is not None:
            (self.server.start if on else self.server.stop)()
        return self.status(with_code=True)

    def new_code(self) -> str:
        if not self._enabled:
            raise PairError("폰 연결이 꺼져 있습니다")
        with self._lock:
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._code = (code, time.monotonic() + CODE_TTL_S, CODE_TRIES)
            return code

    def _live_code(self) -> tuple[str, float] | None:
        c = self._code
        if c is None or time.monotonic() >= c[1] or c[2] <= 0:
            return None
        return c[0], c[1]

    # ------------------------------------------------------------------ pairing and checking
    def pair(self, code: str, name: str, ip: str) -> str:
        """The device token for a phone that typed the current code. Raises PairError with the reason."""
        if not self._enabled:
            raise PairError("PC에서 폰 연결이 꺼져 있습니다")
        now = time.monotonic()
        with self._lock:
            n, since = self._fails.get(ip, (0, now))
            if n >= CODE_TRIES and now - since < LOCKOUT_S:
                raise PairError("코드를 여러 번 틀렸습니다. 1분 뒤 다시 시도하세요")
            live = self._live_code()
            if live is None:
                raise PairError("PC 화면에서 새 연결 코드를 만드세요(코드는 5분 동안만 쓸 수 있습니다)")
            if not secrets.compare_digest(code.strip(), live[0]):
                c = self._code
                assert c is not None
                self._code = (c[0], c[1], c[2] - 1)
                self._fails[ip] = (n + 1 if now - since < LOCKOUT_S else 1, since if now - since < LOCKOUT_S else now)
                raise PairError("코드가 맞지 않습니다")
            self._code = None  # one use
            self._fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            dev = {"id": secrets.token_hex(6), "name": (name or "휴대폰").strip()[:40], "hash": _hash(token),
                   "created": self.now().isoformat(timespec="seconds"), "last_seen": self.now().isoformat(timespec="seconds")}
            self._devices = (self._devices + [dev])[-MAX_DEVICES:]
            self._save(DEVICES_KEY, json.dumps(self._devices))
            return token

    def check(self, token: str | None) -> dict[str, Any] | None:
        """The paired device of this token, or None."""
        if not token or not self._enabled:
            return None
        h = _hash(token)
        dev = next((d for d in self._devices if secrets.compare_digest(d["hash"], h)), None)
        if dev is not None and time.monotonic() - self._seen.get(dev["id"], -1e9) > 60:  # the last-seen time, once a minute
            self._seen[dev["id"]] = time.monotonic()
            with self._lock:
                dev["last_seen"] = self.now().isoformat(timespec="seconds")
                self._save(DEVICES_KEY, json.dumps(self._devices))
        return dev

    def revoke(self, dev_id: str) -> bool:
        with self._lock:
            before = len(self._devices)
            self._devices = [d for d in self._devices if d["id"] != dev_id]
            self._save(DEVICES_KEY, json.dumps(self._devices))
            return len(self._devices) < before

    def _save(self, key: str, value: str) -> None:
        with self.sf() as s:
            repo.set_setting(s, key, value)
            s.commit()
