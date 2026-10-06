"""The strategies' market-wide bars are read once and shared (owner 2026-10-06: "프로그램이 왜 이렇게 느려진 거 같지",
"분석 서버 연결 끊김"): one read of ~420 days × every stored name takes 8-15 s and held the API past the screen's limit
when the signals, the momentum book and the paper records each read it again every 10 minutes."""

import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace

from marketlens.application.market_store import GROUPED_DAYS_KEY
from marketlens.application.services import MarketLensService, _window
from marketlens.domain.market import Bar


class FakeStore:
    def __init__(self) -> None:
        self.reads: list[tuple[date, date]] = []
        self.settings = {GROUPED_DAYS_KEY: "2026-10-02", "instrument_kinds_day": "2026-10-05"}
        d0 = date(2025, 1, 1)
        self.bars = {t: [Bar(d0 + timedelta(i), 1, 1, 1, 1 + i, 1) for i in range(700)] for t in ("A", "B")}

    def get_setting(self, k):
        return self.settings.get(k)

    def last_bars_all(self, start, end):
        self.reads.append((start, end))
        return {t: [b for b in bs if start <= b.day <= end] for t, bs in self.bars.items()}


def _svc(store):
    return SimpleNamespace(store=store, _bars_lock=threading.Lock(), _bars_cache=None, BARS_MAX_AGE=MarketLensService.BARS_MAX_AGE)


def read(svc, start, end):
    return MarketLensService._strategy_bars(svc, start, end)


def test_the_strategies_share_one_read_and_each_gets_exactly_its_window():
    st = FakeStore()
    svc = _svc(st)
    end = date(2026, 10, 6)
    wide = read(svc, end - timedelta(days=600), end)
    narrow = read(svc, end - timedelta(days=420), end)
    again = read(svc, end - timedelta(days=420), end)
    assert len(st.reads) == 1
    assert narrow == again == st.last_bars_all(end - timedelta(days=420), end)  # the same bars a direct read gives
    assert wide["A"][0].day == date(2025, 1, 1) + timedelta(0) or wide["A"][0].day >= end - timedelta(days=600)


def test_a_new_market_wide_download_or_an_older_window_reads_again():
    st = FakeStore()
    svc = _svc(st)
    end = date(2026, 10, 6)
    read(svc, end - timedelta(days=420), end)
    read(svc, end - timedelta(days=500), end)  # wider than what was read
    assert len(st.reads) == 2 and st.reads[-1][0] == end - timedelta(days=500)
    st.settings[GROUPED_DAYS_KEY] = "2026-10-02,2026-10-05"  # the sync stored a new market day
    read(svc, end - timedelta(days=420), end)
    assert len(st.reads) == 3
    svc._bars_cache["at"] = time.monotonic() - MarketLensService.BARS_MAX_AGE - 1  # e.g. a held name's Toss bars since
    read(svc, end - timedelta(days=420), end)
    assert len(st.reads) == 4


def test_concurrent_strategies_wait_for_the_one_read_instead_of_reading_twice():
    st = FakeStore()
    slow = st.last_bars_all

    def last_bars_all(start, end):
        time.sleep(0.2)
        return slow(start, end)

    st.last_bars_all = last_bars_all
    svc = _svc(st)
    end = date(2026, 10, 6)
    ts = [threading.Thread(target=read, args=(svc, end - timedelta(days=420), end)) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(st.reads) == 1


def test_window_cuts_each_name_to_its_days():
    d0 = date(2026, 1, 1)
    bars = {"A": [Bar(d0 + timedelta(i), 1, 1, 1, 1, 1) for i in range(10)], "B": [Bar(d0, 1, 1, 1, 1, 1)]}
    w = _window(bars, d0 + timedelta(3), d0 + timedelta(5))
    assert [b.day.day for b in w["A"]] == [4, 5, 6] and "B" not in w
