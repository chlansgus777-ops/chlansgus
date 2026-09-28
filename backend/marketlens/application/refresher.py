"""Stale-while-revalidate for slow reads (external providers, market-wide recomputation).

A screen read never waits for a provider: it gets the last good result with its real age and a ``refreshing`` flag,
while one background job per key brings a new one (owner report 2026-09-28: the home screen waited 30 s+ for FRED and
the calendar, one after the other, on every visit). Identical concurrent requests share that one job, and at most
``workers`` jobs run at once, so a burst of screens cannot fan out into a burst of provider calls. A failed refresh
keeps the last good value (marked with the error) and is retried after ``retry_after`` seconds, not on every request.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    value: Any  # None until the first computation finished (or when it only ever failed)
    computed_at: datetime | None  # wall-clock time the value was computed — shown as-is, never "now"
    age_s: float | None
    refreshing: bool  # a background job for this key is running
    error: str | None = None  # the last attempt failed (the value, if any, is the previous good one)

    @property
    def ready(self) -> bool:
        return self.computed_at is not None


@dataclass
class _Entry:
    value: Any = None
    at: float | None = None  # monotonic time of the last success
    wall: datetime | None = None
    failed_at: float | None = None
    error: str | None = None


class Refresher:
    def __init__(self, workers: int = 3, clock: Callable[[], float] = time.monotonic, wall: Callable[[], datetime] | None = None) -> None:
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="refresh")
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._inflight: dict[str, Future[None]] = {}
        self._generation: dict[str, int] = {}
        self.runs = 0  # background computations started (tests / diagnostics)
        self._closed = False

    def get(self, key: str, fn: Callable[[], Any], max_age: float, wait: float = 0.0, retry_after: float = 60.0) -> Snapshot:
        """The value for ``key``; starts one background ``fn()`` when it is missing or older than ``max_age``.
        ``wait`` > 0: the request that STARTS the job for a key without any value waits that long for it (a first
        visit); every other request while it runs answers at once with "loading" — never a queue of waiting screens."""
        started = None
        with self._lock:
            e = self._entries.setdefault(key, _Entry())
            now = self._clock()
            fresh = e.at is not None and now - e.at <= max_age
            backing_off = e.failed_at is not None and now - e.failed_at < retry_after and (e.at is None or e.failed_at > e.at)
            if not fresh and key not in self._inflight and not backing_off and not self._closed:
                started = self._submit(key, fn)
        if started is not None and e.at is None and wait > 0:
            self._await(started, wait)
        return self.peek(key)

    def wait(self, key: str, timeout: float) -> Snapshot:
        """Wait at most ``timeout`` seconds for the running job of ``key`` (if any)."""
        with self._lock:
            fut = self._inflight.get(key)
        if fut is not None and timeout > 0:
            self._await(fut, timeout)
        return self.peek(key)

    @staticmethod
    def _await(fut: Future[None], timeout: float) -> None:
        """Wait for a job; a timeout just means "still loading" (``_run`` records its own failures)."""
        try:
            fut.result(timeout=timeout)
        except (FutureTimeout, CancelledError):
            return

    def peek(self, key: str) -> Snapshot:
        with self._lock:
            e = self._entries.get(key) or _Entry()
            fut = self._inflight.get(key)
            return Snapshot(e.value, e.wall, None if e.at is None else max(0.0, self._clock() - e.at), fut is not None and not fut.done(),
                            e.error if (e.failed_at is not None and (e.at is None or e.failed_at > e.at)) else None)

    def invalidate(self, prefix: str = "") -> None:
        """Forget values whose key starts with ``prefix`` (after a scan, sync or holdings change). A job already
        running keeps going but its result is dropped: it may have read the data from before the change."""
        with self._lock:
            for k in [k for k in self._entries if k.startswith(prefix)]:
                del self._entries[k]
                self._generation[k] = self._generation.get(k, 0) + 1
                self._inflight.pop(k, None)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _submit(self, key: str, fn: Callable[[], Any]) -> Future[None]:
        gen = self._generation.get(key, 0)
        self.runs += 1
        fut = self._pool.submit(self._run, key, fn, gen)
        self._inflight[key] = fut
        return fut

    def _run(self, key: str, fn: Callable[[], Any], gen: int) -> None:
        try:
            value, err = fn(), None
        except Exception as ex:  # noqa: BLE001 - a failed refresh keeps the last good value
            log.warning("background refresh %s failed: %s", key, type(ex).__name__)
            value, err = None, f"{type(ex).__name__}: {ex}"[:300]
        with self._lock:
            if self._generation.get(key, 0) == gen:
                e = self._entries.setdefault(key, _Entry())
                if err is None:
                    e.value, e.at, e.wall, e.failed_at, e.error = value, self._clock(), self._wall(), None, None
                else:
                    e.failed_at, e.error = self._clock(), err
                self._inflight.pop(key, None)
