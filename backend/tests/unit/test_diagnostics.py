"""이 PC의 실제 속도 (owner 2026-10-07): what the running app measured, against the agreed targets, with no account data."""

from marketlens.application.diagnostics import Diagnostics, job_family, rss_mb


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_requests_are_summarised_against_the_targets_and_slow_ones_are_listed():
    d = Diagnostics()
    for _ in range(90):
        d.request_started()
        d.request("GET /api/dashboard", 0.05, 200)
    for s in (0.8, 0.8, 0.8, 0.8, 0.8, 1.2, 2.0, 3.0, 0.8, 16.0):
        d.request_started()
        d.request("GET /api/strategies", s, 200)
    snap = d.snapshot()
    t = {x["id"]: x for x in snap["targets"]}
    assert snap["requests"]["count"] == 100
    assert t["over_limit"]["value"] == 1 and t["over_limit"]["status"] == "OVER"  # the 16 s one: shown as a disconnect
    assert t["p95"]["status"] == "OVER"  # p95 lands in the slow tail (≥ 0.8 s)
    assert snap["requests"]["routes"][0]["route"] == "GET /api/strategies"  # worst route first
    assert [x["seconds"] for x in snap["slow"]] == [16.0, 3.0, 2.0, 1.2]


def test_idle_cpu_counts_only_intervals_without_a_running_request():
    clock, cpu = Clock(), Clock()
    d = Diagnostics(clock=clock, cpu=cpu)
    d.sample_cpu()
    clock.t, cpu.t = 30.0, 0.3  # 1 % of a core while idle
    d.sample_cpu()
    d.request_started()  # a request runs through the next interval: not idle
    clock.t, cpu.t = 60.0, 15.3
    d.sample_cpu()
    t = {x["id"]: x for x in d.snapshot()["targets"]}
    assert abs(t["idle_cpu"]["value"] - 0.01) < 1e-9 and t["idle_cpu"]["status"] == "OK"


def test_jobs_are_grouped_by_family_never_by_ticker():
    assert job_family("analysis:AAPL") == "analysis" and job_family("strategies:momentum") == "strategies:momentum"
    d = Diagnostics()
    d.job("analysis:AAPL", 6.0, True)
    d.job("analysis:MSFT", 1.0, False)
    rows = {r["job"]: r for r in d.snapshot()["jobs"]}
    assert rows["analysis"]["count"] == 2 and rows["analysis"]["failed"] == 1 and rows["analysis"]["max"] == 6.0
    assert "AAPL" not in str(d.snapshot())


def test_memory_is_read_from_the_os_or_shown_unknown():
    m = rss_mb()
    assert m is None or m > 1


def test_the_api_reports_route_templates_not_values():
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from tests.integration.test_service_api import make_service

    svc = make_service()
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        c.get("/api/stocks/AAPL/live")
        snap = c.get("/api/diagnostics").json()
    routes = [r["route"] for r in snap["requests"]["routes"]]
    assert any("{ticker}" in r for r in routes) and not any("AAPL" in r for r in routes)
    assert {t["id"] for t in snap["targets"]} == {"p95", "over_limit", "idle_cpu", "memory"}
