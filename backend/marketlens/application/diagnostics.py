"""이 PC의 실제 속도 (owner 2026-10-07: "더 적게, 실제 환경에서 확실하게, 가볍게"): what this running app measured on
the owner's own machine — each screen request's time, each background job's time, the process's CPU and memory — kept
in memory only, never written anywhere, and checked against the targets the owner agreed to.

Recorded: the route's template ("/api/stocks/{ticker}"), never its values, query or body; a background job's key
family ("strategies:momentum", "analysis"), never account figures. Nothing here leaves the PC unless the owner copies it.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# the targets (owner 2026-10-07): a screen answer within 0.3 s mostly, none past the screen's 15 s limit, an idle
# process under 2 % CPU and 500 MB
TARGET_P95_S = 0.3
TARGET_MAX_S = 15.0
TARGET_IDLE_CPU = 0.02
TARGET_RSS_MB = 500.0
SLOW_S = 1.0
KEEP = 400  # recent durations kept per route / job (enough for a p95, small in memory)
MAX_KEYS = 120  # distinct routes / jobs kept (a ticker-keyed job is grouped by its family)


def rss_mb() -> float | None:
    """The process's resident memory now, from the OS (no extra package)."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            proc = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.psapi.GetProcessMemoryInfo(proc, ctypes.byref(c), c.cb):  # type: ignore[attr-defined]
                return c.WorkingSetSize / 1e6
            return None
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # macOS: bytes (peak, the closest it offers)
        return peak / 1e6
    except Exception:  # noqa: BLE001 - memory is shown as unknown, never guessed
        return None


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def job_family(key: str) -> str:
    """'analysis:AAPL' → 'analysis'; 'strategies:momentum' stays (no ticker in it)."""
    head, _, tail = key.partition(":")
    return key if tail and not any(ch.isupper() for ch in tail) else head


class Diagnostics:
    def __init__(self, clock=time.monotonic, cpu=time.process_time, wall=lambda: datetime.now(timezone.utc)) -> None:
        self._clock, self._cpu, self._wall = clock, cpu, wall
        self._lock = threading.Lock()
        self._start = clock()
        self._cpu0 = cpu()
        self._routes: dict[str, deque[float]] = {}
        self._route_counts: dict[str, list[int]] = {}  # [count, slow, over 15 s, errors]
        self._jobs: dict[str, deque[float]] = {}
        self._job_counts: dict[str, list[int]] = {}  # [count, failed]
        self._slow: deque[dict[str, Any]] = deque(maxlen=30)
        self._cpu_samples: deque[tuple[float, float, bool]] = deque(maxlen=120)  # (t, cpu, busy) every ~30 s
        self._busy = 0  # requests or jobs running now

    # ------------------------------------------------------------------ recording
    def request_started(self) -> None:
        with self._lock:
            self._busy += 1

    def request(self, route: str, seconds: float, status: int) -> None:
        with self._lock:
            self._busy = max(0, self._busy - 1)
            if route not in self._routes and len(self._routes) >= MAX_KEYS:
                route = "(기타)"
            self._routes.setdefault(route, deque(maxlen=KEEP)).append(seconds)
            c = self._route_counts.setdefault(route, [0, 0, 0, 0])
            c[0] += 1
            c[1] += seconds >= SLOW_S
            c[2] += seconds >= TARGET_MAX_S
            c[3] += status >= 500
            if seconds >= SLOW_S:
                self._slow.appendleft({"kind": "화면 요청", "name": route, "seconds": round(seconds, 2), "at": self._wall().isoformat()})

    def job(self, key: str, seconds: float, ok: bool) -> None:
        fam = job_family(key)
        with self._lock:
            if fam not in self._jobs and len(self._jobs) >= MAX_KEYS:
                fam = "(기타)"
            self._jobs.setdefault(fam, deque(maxlen=KEEP)).append(seconds)
            c = self._job_counts.setdefault(fam, [0, 0])
            c[0] += 1
            c[1] += not ok
            if seconds >= 5.0:
                self._slow.appendleft({"kind": "백그라운드 계산", "name": fam, "seconds": round(seconds, 2), "at": self._wall().isoformat()})

    def start_sampler(self, every: float = 30.0) -> None:
        """A daemon thread that samples the CPU every ``every`` seconds (one cheap call; it sleeps otherwise)."""
        if getattr(self, "_sampler", None) is not None:
            return
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(every):
                self.sample_cpu()

        self._stop = stop
        self._sampler = threading.Thread(target=loop, daemon=True, name="diagnostics-cpu")
        self._sampler.start()

    def stop(self) -> None:
        if getattr(self, "_stop", None) is not None:
            self._stop.set()

    def sample_cpu(self) -> None:
        """Called by a light timer: the process CPU since the last sample, marked busy when a request was running."""
        with self._lock:
            self._cpu_samples.append((self._clock(), self._cpu(), self._busy > 0))

    # ------------------------------------------------------------------ report
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now, cpu_now = self._clock(), self._cpu()
            up = max(1e-9, now - self._start)
            samples = list(self._cpu_samples) + [(now, cpu_now, self._busy > 0)]
            routes = {k: (list(v), list(self._route_counts[k])) for k, v in self._routes.items()}
            jobs = {k: (list(v), list(self._job_counts[k])) for k, v in self._jobs.items()}
            slow = list(self._slow)
        # CPU over the last ~10 minutes, as a share of one core; "idle" = intervals in which no screen request ran
        recent = [s for s in samples if now - s[0] <= 600]
        cpu_recent = idle = None
        if len(recent) >= 2:
            cpu_recent = (recent[-1][1] - recent[0][1]) / max(1e-9, recent[-1][0] - recent[0][0])
            quiet = [(b[1] - a[1], b[0] - a[0]) for a, b in zip(recent, recent[1:]) if not a[2] and not b[2]]
            idle = sum(c for c, _ in quiet) / sum(t for _, t in quiet) if quiet and sum(t for _, t in quiet) > 0 else None
        all_d = [d for v, _ in routes.values() for d in v]
        p95 = _pct(all_d, 0.95)
        worst = max(all_d) if all_d else None
        over = sum(c[2] for _, c in routes.values())
        mem = rss_mb()
        route_rows = sorted(({"route": k, "count": c[0], "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "max": max(v) if v else None,
                              "slow": c[1], "over_limit": c[2], "errors": c[3]} for k, (v, c) in routes.items()),
                            key=lambda r: -(r["p95"] or 0))
        job_rows = sorted(({"job": k, "count": c[0], "failed": c[1], "avg": sum(v) / len(v) if v else None, "max": max(v) if v else None,
                            "last": v[-1] if v else None} for k, (v, c) in jobs.items()), key=lambda r: -(r["max"] or 0))

        def verdict(value: float | None, target: float, lower_is_better: bool = True) -> str:
            if value is None:
                return "UNKNOWN"
            return "OK" if (value <= target if lower_is_better else value >= target) else "OVER"

        return {
            "at": self._wall().isoformat(), "uptime_s": round(up, 1), "platform": sys.platform,
            "targets": [
                {"id": "p95", "label": "화면 응답 (95%가 이 안에)", "target": TARGET_P95_S, "value": p95, "unit": "s", "status": verdict(p95, TARGET_P95_S)},
                {"id": "over_limit", "label": "15초 넘은 응답 (연결 끊김으로 보임)", "target": 0, "value": over, "unit": "회",
                 "status": "OK" if over == 0 else "OVER"},
                {"id": "idle_cpu", "label": "가만히 둘 때 CPU (코어 1개 기준)", "target": TARGET_IDLE_CPU, "value": idle, "unit": "share", "status": verdict(idle, TARGET_IDLE_CPU)},
                {"id": "memory", "label": "메모리", "target": TARGET_RSS_MB, "value": mem, "unit": "MB", "status": verdict(mem, TARGET_RSS_MB)},
            ],
            "requests": {"count": sum(c[0] for _, c in routes.values()), "p95": p95, "max": worst, "routes": route_rows[:25]},
            "jobs": job_rows[:25], "slow": slow, "cpu_recent": cpu_recent,
            "note": "이 PC에서 앱을 켠 뒤 잰 값입니다. 메모리에만 있고 앱을 끄면 사라집니다. 경로 이름만 기록하고 종목·금액·계좌 정보는 기록하지 않습니다.",
        }
