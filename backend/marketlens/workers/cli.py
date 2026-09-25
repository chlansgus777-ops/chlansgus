"""Command-line entry point: ``python -m marketlens <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from typing import Any

from marketlens.config import load_settings


def _service(settings: Any):  # type: ignore[no-untyped-def]
    from marketlens.application.services import MarketLensService
    from marketlens.infrastructure.db.session import make_engine, make_session_factory, migrate
    from marketlens.infrastructure.logging import configure_logging

    configure_logging(settings.log_level, settings.secrets(), settings.data_dir / "logs")
    migrate(settings.database_url)
    return MarketLensService(settings, make_session_factory(make_engine(settings.database_url)))


def _serve(settings: Any, host: str, port: int) -> int:
    import uvicorn

    from marketlens.api.app import create_app
    from marketlens.workers.runtime import AlreadyRunning, InstanceLock, PortInUse, choose_port

    if host not in ("127.0.0.1", "localhost"):
        print("보안: MarketLens API는 로컬(127.0.0.1)에서만 실행할 수 있습니다.", file=sys.stderr)
        return 2
    lock = InstanceLock(settings.data_dir / f"marketlens-{settings.mode.value.lower()}.lock")
    try:
        lock.acquire()
    except AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return 4
    try:
        try:
            port = choose_port(host, port)
        except PortInUse as e:
            print(str(e), file=sys.stderr)
            return 3
        print(f"MARKETLENS_PORT={port}", flush=True)  # read by the desktop shell
        uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
        return 0
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="marketlens", description="MarketLens — US equity decision support (no order execution)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the API + UI server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765, help="0 = choose a free port automatically")
    sc = sub.add_parser("scan", help="run a whole-market scan")
    sc.add_argument("--no-committee", action="store_true")
    an = sub.add_parser("analyze", help="analyze one ticker")
    an.add_argument("ticker")
    an.add_argument("--committee", action="store_true")
    sub.add_parser("evaluate", help="update outcomes and paper trades")
    sub.add_parser("calibrate", help="run one calibration cycle (shadow / promotion gates)")
    rp = sub.add_parser("replay", help="replay a stored recommendation from its snapshot")
    rp.add_argument("rec_id", type=int)
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("sync", help="LIVE only: refresh the local point-in-time store from free sources")
    sim = sub.add_parser("simulate", help="MOCK only: run weekly scans over past weeks to populate evaluation data")
    sim.add_argument("--weeks", type=int, default=12)
    args = p.parse_args(argv)
    settings = load_settings()

    if args.cmd == "serve":
        return _serve(settings, args.host, args.port)
    if args.cmd == "migrate":
        from marketlens.infrastructure.db.session import migrate

        migrate(settings.database_url)
        print("migrated", settings.database_url)
        return 0
    s = _service(settings)
    if args.cmd == "sync":
        print(json.dumps(s.sync_market(), default=str)[:4000])
    elif args.cmd == "scan":
        print(json.dumps(s.run_scan(run_committee=not args.no_committee).__dict__, default=str))
    elif args.cmd == "analyze":
        r, c, rid = s.analyze(args.ticker, run_committee=args.committee)
        print(json.dumps({"id": rid, "ticker": r.ticker, "mode": r.mode.value, "price": r.price, "session": r.session, "score": r.scorecard.total,
                          "action": c.final_action if c else r.decision.action.value, "vetoes": [v.value for v in r.decision.vetoes],
                          "breakdown": {x.name: f"{x.points}/{x.weight:g}" for x in r.scorecard.components}}, indent=2))
    elif args.cmd == "evaluate":
        from marketlens.application.evaluation_service import EvaluationService

        ev = EvaluationService(s)
        print(json.dumps({"outcomes": ev.update_outcomes(), "paper": ev.update_paper()}))
    elif args.cmd == "calibrate":
        from marketlens.application.evaluation_service import EvaluationService

        print(json.dumps(EvaluationService(s).calibrate(), default=str)[:4000])
    elif args.cmd == "replay":
        o = s.replay_recommendation(args.rec_id)
        print(json.dumps({"matches": o.matches, "fingerprint_matches": o.fingerprint_matches, "score": [o.original_score, o.replay_score], "action": [o.original_action, o.replay_action]}))
    elif args.cmd == "simulate":
        if s.mode.value != "MOCK" or s.registry.world is None:
            print("simulate is only available in MOCK mode", file=sys.stderr)
            return 2
        from marketlens.application.evaluation_service import EvaluationService

        world = s.registry.world
        end = s.now()
        clock = {"t": end}
        s._now = lambda: clock["t"]  # noqa: SLF001 - simulation drives the service clock
        for w in range(args.weeks, 0, -1):
            clock["t"] = end - timedelta(weeks=w)
            world.set_now(clock["t"])
            s.data.cache = type(s.data.cache)()
            print("scan", clock["t"].date(), s.run_scan(run_committee=False).candidates)
        clock["t"] = end
        world.set_now(end)
        s.data.cache = type(s.data.cache)()
        ev = EvaluationService(s)
        print(json.dumps({"outcomes": ev.update_outcomes(), "paper": ev.update_paper()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
