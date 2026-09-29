"""The weekly point-in-time backtest loop on the app's own code (docs/backtest/PREREGISTRATION.md §엔진).

For every analysis time t (the last trading day of each week, 20:00 New York):

1. the backtest store answers as of t (``BacktestStore.set_time``), a fresh ``DataAccess`` (no cache across weeks) reads
   through it and the offline registry (FRED replay, everything else blocked);
2. the operating ``Scanner`` builds the context and runs stage 1 (eligibility) over the whole point-in-time universe;
3. every eligible name is analysed with ``run_analysis`` twice, as the scanner does for its final names: a first pass
   collects the sector-model peer multiples, the second uses them (here for ALL eligible names, not only the funnel's
   top 60 — the IC is measured over the whole eligible cross-section);
4. the previous week's analysis of the same security (hysteresis, carried stop) and whether the simulated account holds
   it are passed in, exactly as the app does with its stored recommendations and its paper account;
5. every input passes the time audit (``leaks.audit_inputs``) before it is analysed — a violation fails the run.

The backtest configuration differs from the operating one ONLY in (see ``backtest_config``): the weights of the two
components that cannot be verified in the past (earnings_revision, catalyst) are 0, so the total is the renormalised
verifiable total; the completeness veto counts only the fields that can exist in the past; the model-coverage veto
is not applied to valuation (its forward / history / rate inputs cannot exist in the past). No AI committee.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Sequence

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, insert

from marketlens.backtest.leaks import audit_inputs, audit_vintages
from marketlens.backtest.store import BacktestStore
from marketlens.config import ModelConfig, load_model_config
from marketlens.domain.corporate_actions import ShareBasis, share_multiplier
from marketlens.domain.enums import BULLISH_ACTIONS, Action, ExitReason
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import to_ny
from marketlens.domain.paper import AccountItem, PaperConfig, PaperSignal, simulate_account
from marketlens.domain.what_changed import AnalysisDigest

UNVERIFIABLE_COMPONENTS = ("earnings_revision", "catalyst")
# the ten data-quality fields of the pipeline; five can never exist in the past (analyst, earnings, news, options,
# short interest). The operating veto needs 6/10 = 60 %; the same 60 % of the five that can exist is 3 → 3/10 = 0.3.
QUALITY_FIELDS = 10
VERIFIABLE_QUALITY_FIELDS = 5
BACKTEST_MIN_COMPLETENESS = round(0.6 * VERIFIABLE_QUALITY_FIELDS / QUALITY_FIELDS, 4)
FAR_FUTURE = date(2100, 1, 1)


def backtest_config(base: ModelConfig | None = None) -> ModelConfig:
    """The operating configuration with the documented backtest differences (never written back anywhere)."""
    base = base or load_model_config()
    w = dict(base.scoring_model.weights)
    for k in UNVERIFIABLE_COMPONENTS:
        w[k] = 0.0
    cfg = load_model_config(weights_override=w, scoring_version_override=base.scoring_model.version + "+backtest-verifiable")
    if abs(base.decision.min_completeness - 0.6) > 1e-9:
        raise RuntimeError("operating min_completeness changed: re-derive BACKTEST_MIN_COMPLETENESS before running")
    raw = dict(cfg.raw)
    # valuation's forward multiples, PEG, rate spread (forward earnings yield) and own-multiple history cannot be
    # verified in the past; its model-coverage veto would then fire for want of unverifiable inputs → not applied
    raw["backtest"] = {"coverage_veto_components": ["fundamental"]}
    return replace(cfg, decision=replace(cfg.decision, min_completeness=BACKTEST_MIN_COMPLETENESS), raw=raw)


RESULTS_META = MetaData()
bt_rows = Table(
    "bt_rows", RESULTS_META,
    Column("t", String(32), primary_key=True),
    Column("key", String(64), primary_key=True),
    Column("label", String(16), nullable=False),
    Column("cik", Integer, nullable=True),
    Column("sector", String(64), nullable=True),
    Column("eligible", Integer, nullable=False),
    Column("excluded", Text, nullable=True),
    Column("payload", Text, nullable=True),  # JSON: factors, total, action, levels, adv, beta, market cap
)
bt_manifest = Table("bt_manifest", RESULTS_META, Column("key", String(64), primary_key=True), Column("value", Text, nullable=False))


@dataclass
class Signal:
    t: datetime
    key: str
    label: str
    action: str
    score: float
    confidence: float
    stop: float
    target1: float
    target2: float
    max_buy: float | None
    adv20: float | None
    regime: str
    sector: str


@dataclass
class EngineState:
    previous: dict[str, tuple[AnalysisDigest, Action]] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    later: dict[str, list[tuple[datetime, str, bool]]] = field(default_factory=dict)  # key -> (t, action, thesis_invalidated)


# ------------------------------------------------------------------------------------------------ costs
COMMISSION_ONE_WAY = 0.0010


def half_spread(adv20: float | None) -> float:
    """Half-spread by 20-day average dollar volume (PREREGISTRATION §비용)."""
    if adv20 is not None and adv20 >= 100e6:
        return 0.0002
    if adv20 is not None and adv20 >= 20e6:
        return 0.0005
    return 0.0015


def one_way_cost(adv20: float | None, k: float = 1.0) -> float:
    return k * (COMMISSION_ONE_WAY + half_spread(adv20))


# ------------------------------------------------------------------------------------------------ account
def exit_events(later: Sequence[tuple[datetime, str, bool]], after: datetime) -> tuple[tuple[datetime, ExitReason], ...]:
    """evaluation_service.exit_events_for on the backtest's own later recommendations."""
    for ts, act, bad in sorted(x for x in later if x[0] > after):
        if bad:
            return ((ts, ExitReason.THESIS_INVALIDATION),)
        if Action(act) not in BULLISH_ACTIONS and act != Action.HOLD.value:
            return ((ts, ExitReason.RECOMMENDATION_DOWNGRADE),)
    return ()


def account_inputs(signals: Sequence[Signal], later: dict[str, list[tuple[datetime, str, bool]]], split_of: Callable[[str], Any],
                   k: float = 1.0) -> list[AccountItem]:
    """Paper signals on the final share basis (levels set at t ÷ every split executed after t), costs at k×."""
    items = []
    for i, s in enumerate(signals):
        f = share_multiplier(split_of(s.key), ShareBasis(to_ny(s.t).date()), FAR_FUTURE) or 1.0
        hs = half_spread(s.adv20) * k
        sig = PaperSignal(s.key, s.t, s.action, s.score, s.confidence, s.stop / f, s.target1 / f, s.target2 / f, "backtest", "backtest", s.regime, s.sector,
                          spread_bps=hs * 2 * 1e4, max_buy=s.max_buy / f if s.max_buy is not None else None)
        items.append(AccountItem(f"{i:07d}", sig, exit_events(later.get(s.key, []), s.t)))
    return items


def paper_config(base: PaperConfig, k: float) -> PaperConfig:
    """The app's paper account with the backtest cost model: commission as slippage, the ADV half-spread per signal."""
    return replace(base, slippage_bps=COMMISSION_ONE_WAY * 1e4 * k, default_half_spread_bps=0.0)


def final_basis_bars(store: BacktestStore, key: str, upto: date) -> list[Bar]:
    ln = store.data.by_key.get(key)
    if ln is None:
        return []
    out = []
    for d, (o, h, lo, c, v) in zip(ln.days, ln.rows):
        if d > upto:
            break
        f = share_multiplier(ln.splits, ShareBasis(d), FAR_FUTURE) or 1.0
        out.append(Bar(d, o / f, h / f, lo / f, c / f, v * f))
    return out


# ------------------------------------------------------------------------------------------------ the loop
class Engine:
    RECYCLE_WEEKS = 20  # fresh workers this often: a forked worker slowly copies the shared data it touches (copy-on-write)
    LOW_MEMORY_MB = 1500  # … and at once when the machine has less than this left after a week

    def __init__(self, store: BacktestStore, registry: Any, cfg: ModelConfig, results_path: str, replay: Any = None, resume: bool = False) -> None:
        from marketlens.application.graph_seed import GraphSeed
        from marketlens.application.theses import ThesisBook

        self.store = store
        self.reg = registry
        self.cfg = cfg
        self.seed = GraphSeed()
        self.theses = ThesisBook()
        self.state = EngineState()
        self.out = create_engine("sqlite://" if results_path == ":memory:" else f"sqlite:///{results_path}")
        if not resume:  # a resumed run keeps the rows of the weeks it already did (run.py checkpoints)
            RESULTS_META.drop_all(self.out)
        RESULTS_META.create_all(self.out)
        self._key_of: dict[str, str] = {}
        self._bars_cache: dict[str, list[Bar]] = {}
        self.audited = 0
        self.replay = replay  # the FRED replay: every vintage it served at t is audited
        self.pool: Any = None
        self.blocked: dict[str, Any] = {}  # the blocked providers (their attempts are counted in the manifest)
        self.workers = 1
        self.min_parallel = 16  # fewer names than this are analysed here (forking work costs more than it saves)
        self.worker_counts = {"blocked": 0, "replay_served": 0}
        self._week_ctx: tuple[datetime, Any, Any] | None = None  # (t, scanner, context) of the week this process analyses

    def _previous(self, label: str, _ts: datetime) -> tuple[AnalysisDigest | None, Action | None]:
        key = self._key_of.get(label)
        hit = self.state.previous.get(key) if key else None
        return (hit[0], hit[1]) if hit else (None, None)

    def held_at(self, t: datetime) -> set[str]:
        """Keys the simulated account (1× cost, the app's paper rules) holds at t."""
        if not self.state.signals:
            return set()
        day = self.store.session
        keys = {s.key for s in self.state.signals}
        bars = {k: [b for b in self._bars(k) if b.day <= day] for k in keys}
        items = account_inputs(self.state.signals, self.state.later, self._splits)
        acct = simulate_account(items, bars, paper_config(self.cfg.paper, 1.0), day)
        return {r.signal.ticker for _k, r in acct.trades if r.open}

    def _bars(self, key: str) -> list[Bar]:
        if key not in self._bars_cache:
            self._bars_cache[key] = final_basis_bars(self.store, key, FAR_FUTURE)
        return self._bars_cache[key]

    def _splits(self, key: str) -> list[Any]:
        ln = self.store.data.by_key.get(key)
        return ln.splits if ln else []

    def step(self, t: datetime) -> dict[str, Any]:
        from marketlens.application.data_access import DataAccess
        from marketlens.application.scanner import Scanner

        self.store.set_time(t)
        n_urls = len(self.replay.urls) if self.replay is not None else 0
        self._key_of = {label: ln.key for label, ln in self.store.labels().items()}
        data = DataAccess(self.reg, self.cfg.cache_ttl, store=self.store, now_fn=lambda: t)  # a fresh cache every week
        sc = Scanner(data, self.cfg, self.seed, self.theses, previous_lookup=self._previous)
        ctx = sc.build_context(t)
        excluded: dict[str, str] = {}
        eligible = sc.stage1(ctx, excluded)
        sc.rank_universe(ctx, eligible)  # the relative-strength rank of §13, as the scanner does before stage 2
        held = self.held_at(t)
        if self.replay is not None:
            audit_vintages(self.replay.urls[n_urls:], t)
        labels = sorted(eligible)
        prev = {lab: self.state.previous[self._key_of[lab]] for lab in labels if self._key_of[lab] in self.state.previous}
        week = Week(t, ctx.rs_universe, ctx.rs_session, prev, frozenset(self._key_of[lab] for lab in labels if self._key_of[lab] in held))
        self._week_ctx = (t, sc, ctx)  # this process analyses with the context it just built (workers build their own)
        # pass 1 (the scanner's stage 3): sector-model peer multiples from every eligible name
        lite = self._map(week, "lite", [(lab, ()) for lab in labels])
        peers: dict[str, list[tuple[str, float]]] = {}
        for lab, (mid, pv) in zip(labels, lite):
            if pv is not None:
                peers.setdefault(mid, []).append((lab, pv))
        # pass 2: every eligible name with its peers
        jobs = [(lab, tuple(v for other, v in peers.get(mid, []) if other != lab)) for lab, (mid, _pv) in zip(labels, lite)]
        rows = []
        new_prev: dict[str, tuple[AnalysisDigest, Action]] = {}
        for lab, res in zip(labels, self._map(week, "full", jobs)):
            key = self._key_of[lab]
            self.audited += 1
            new_prev[key] = (res["digest"], Action(res["action"]))
            self.state.later.setdefault(key, []).append((t, res["action"], res["thesis_invalidated"]))
            if res["signal"] is not None:
                self.state.signals.append(Signal(t, key, lab, *res["signal"]))
            sec = ctx.securities[lab]
            rows.append({"t": t.isoformat(), "key": key, "label": lab, "cik": sec.cik, "sector": sec.sector, "eligible": 1, "excluded": None,
                         "payload": json.dumps(res["payload"], sort_keys=True)})
        for lab, why in sorted(excluded.items()):
            sec = ctx.securities.get(lab)
            rows.append({"t": t.isoformat(), "key": self._key_of.get(lab, lab), "label": lab, "cik": sec.cik if sec else None,
                         "sector": sec.sector if sec else None, "eligible": 0, "excluded": why, "payload": None})
        # a name outside this week's eligible set gets no new analysis: its last one stays the previous (as in the app)
        self.state.previous.update(new_prev)
        with self.out.begin() as c:
            if rows:
                c.execute(insert(bt_rows), rows)
        return {"t": t.isoformat(), "universe": len(ctx.securities), "eligible": len(eligible), "held": len(held), "macro": ctx.macro is not None}

    # ------------------------------------------------------------------ the per-name analyses (in this process or the workers)
    def _map(self, week: "Week", kind: str, jobs: list[tuple[str, tuple[float, ...]]]) -> list[Any]:
        if self.pool is None or len(jobs) < self.min_parallel:
            return analyse_chunk(self, week, kind, jobs)
        size = max(1, -(-len(jobs) // (self.workers * 4)))
        chunks = [jobs[i:i + size] for i in range(0, len(jobs), size)]
        out: list[Any] = []
        for part, counts in self.pool.imap(_worker_chunk, [(week, kind, c) for c in chunks]):  # imap keeps the order
            out.extend(part)
            self.worker_counts["blocked"] += counts["blocked"]
            self.worker_counts["replay_served"] += counts["replay_served"]
        return out

    def start_workers(self, n: int) -> None:
        """Fork ``n`` workers that share this engine's loaded data (copy-on-write). Results are merged in label order,
        so a run with workers gives the same rows as one without (tests/backtest: test_workers_give_the_same_rows)."""
        import gc
        import multiprocessing as mp

        if n <= 1:
            return
        if "fork" not in mp.get_all_start_methods():  # Windows: the per-name analyses stay in this process (same rows)
            return
        global _W
        _W = self
        gc.collect()
        gc.freeze()  # the loaded bars stay shared: the collector of a worker never touches them
        self.workers = n
        self._n_workers = n
        self.pool = mp.get_context("fork").Pool(n, initializer=_worker_init)

    def stop_workers(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None

    def run(self, times: Sequence[datetime], log: Callable[[str], None] = print, deadline: float | None = None,
            on_week: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
        """The weeks in order. ``deadline`` (time.monotonic()): stop after the week that passes it (run.py saves a
        checkpoint after every week through ``on_week`` and the next runner goes on from there)."""
        out = []
        since = 0  # weeks since the workers were last started
        for t in times:
            if since >= self.RECYCLE_WEEKS and self.pool is not None:
                self._recycle()
                since = 0
            t0 = time.monotonic()
            info = self.step(t)
            since += 1
            info["seconds"] = round(time.monotonic() - t0, 1)
            mem = memory_mb(self.pool)
            log(json.dumps(info | mem))
            if self.pool is not None and mem.get("avail_mb", 1 << 30) < self.LOW_MEMORY_MB:
                self._recycle()  # the machine is running out: fresh workers now (the 2026-09-29 legs swapped to a halt)
                since = 0
                log(json.dumps({"recycled_workers": True, **memory_mb(self.pool)}))
            out.append(info)
            if on_week is not None:
                on_week(info)
            if deadline is not None and time.monotonic() >= deadline:
                break
        return out

    def _recycle(self) -> None:
        n = self._n_workers
        self.stop_workers()
        self.start_workers(n)

    def checkpoint(self) -> dict[str, Any]:
        """What a later process needs to go on exactly where this one stopped (the rows are in the results file)."""
        return {"state": self.state, "audited": self.audited, "worker_counts": dict(self.worker_counts),
                "replay": None if self.replay is None else {"served": self.replay.served, "urls": list(self.replay.urls), "misses": list(self.replay.misses)},
                "blocked": {k: getattr(b, "attempts", 0) for k, b in self.blocked.items()}}

    def restore(self, cp: dict[str, Any]) -> None:
        self.state = cp["state"]
        self.audited = cp["audited"]
        self.worker_counts = dict(cp["worker_counts"])
        if self.replay is not None and cp["replay"] is not None:
            self.replay.served, self.replay.urls[:], self.replay.misses[:] = cp["replay"]["served"], cp["replay"]["urls"], cp["replay"]["misses"]
        for k, n in cp["blocked"].items():
            if k in self.blocked:
                self.blocked[k].attempts = n

    def signals_json(self) -> list[dict[str, Any]]:
        return [{**{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in s.__dict__.items()}} for s in self.state.signals]


@dataclass(frozen=True)
class Week:
    """What a per-name analysis needs from the week besides the store: the §13 rank universe, the previous analyses
    of these names (hysteresis, carried stop) and which of them the simulated account holds."""
    t: datetime
    rs_universe: tuple[float, ...]
    rs_session: date | None
    previous: dict[str, tuple[AnalysisDigest, Action]]
    held: frozenset[str]


def _week_scanner(eng: Engine, week: Week) -> tuple[Any, Any]:
    """The scanner and context of ``week`` in this process (built once per week; the parent's own for 1 process)."""
    from marketlens.application.data_access import DataAccess
    from marketlens.application.scanner import Scanner

    if eng._week_ctx is not None and eng._week_ctx[0] == week.t:
        return eng._week_ctx[1], eng._week_ctx[2]
    if eng.store.t != week.t:
        eng.store.set_time(week.t)
    n_urls = len(eng.replay.urls) if eng.replay is not None else 0
    data = DataAccess(eng.reg, eng.cfg.cache_ttl, store=eng.store, now_fn=lambda: week.t)
    sc = Scanner(data, eng.cfg, eng.seed, eng.theses, previous_lookup=lambda label, _ts: week.previous.get(label, (None, None)))
    ctx = sc.build_context(week.t)
    ctx.rs_universe, ctx.rs_session = week.rs_universe, week.rs_session
    if eng.replay is not None:
        audit_vintages(eng.replay.urls[n_urls:], week.t)
    eng._week_ctx = (week.t, sc, ctx)
    return sc, ctx


def analyse_chunk(eng: Engine, week: Week, kind: str, jobs: list[tuple[str, tuple[float, ...]]]) -> list[Any]:
    """Pass 1 (``lite``: sector model and primary multiple) or pass 2 (``full``: the analysis the app would store) of
    the listed names at ``week.t`` — the same code in the parent and in a worker."""
    from marketlens.application.pipeline import _beta, run_analysis

    sc, ctx = _week_scanner(eng, week)
    n_urls = len(eng.replay.urls) if eng.replay is not None else 0
    out: list[Any] = []
    for lab, pv in jobs:
        sec = ctx.securities[lab]
        if kind == "lite":
            inp = sc.gather_inputs(ctx, sec, None, (), full=False)
            audit_inputs(inp, week.t, eng.store)
            r = run_analysis(inp, eng.cfg, fingerprint=False)
            rv = r.relative_valuation.primary_value if r.relative_valuation else None
            out.append((r.sector_model_id, rv))
            continue
        key = eng.store.labels()[lab].key
        inp = sc.gather_inputs(ctx, sec, None, pv, full=True)
        inp = replace(inp, held=key in week.held)  # the simulated account (the app: the user's portfolio)
        audit_inputs(inp, week.t, eng.store)
        r = run_analysis(inp, eng.cfg, fingerprint=False)
        card, dec, plan = r.scorecard, r.decision, r.entry
        adv = r.technicals.avg_dollar_volume_20d if r.technicals else None
        signal = None
        if dec.action in BULLISH_ACTIONS and plan is not None:
            signal = (dec.action.value, card.total, dec.confidence, plan.stop, plan.target1, plan.target2, plan.max_buy, adv, r.primary_regime, sec.sector)
        hist = eng.store.bars(lab, eng.store.session - timedelta(days=400), eng.store.session)
        payload = {
            "total": card.total, "sell_total": card.sell_side_total, "completeness": card.completeness,
            "components": {c.name: {"sub": c.subscore, "available": c.available, "coverage": c.coverage} for c in card.components},
            "action": dec.action.value, "raw_action": dec.raw_action.value, "vetoes": [v.value for v in dec.vetoes], "held": key in week.held,
            "price": r.price, "stop": plan.stop if plan else None, "target1": plan.target1 if plan else None, "max_buy": plan.max_buy if plan else None,
            "adv20": adv, "market_cap": sec.market_cap, "beta252": _beta(tuple(hist), tuple(ctx.benchmark_bars), 252),
            "sector_model": r.sector_model_id, "dq_completeness": r.data_quality.completeness,
            # the four §13 sub-signals (reference ICs only — never used to change the combination)
            "signals": {s.key: s.sub for s in r.return_signals.signals} if r.return_signals else {},
        }
        out.append({"digest": r.digest, "action": dec.action.value, "thesis_invalidated": bool(r.thesis_invalidated), "signal": signal, "payload": payload})
    if eng.replay is not None:
        audit_vintages(eng.replay.urls[n_urls:], week.t)  # anything the analyses themselves asked FRED
    return out


def memory_mb(pool: Any = None) -> dict[str, int]:
    """Resident memory of this process and its workers, and what the machine has left (Linux /proc; {} elsewhere) —
    in the run's log, so a runner that runs out of memory shows it coming."""
    def rss(pid: int | str) -> int:
        try:
            with open(f"/proc/{pid}/status", encoding="ascii") as f:
                return next((int(x.split()[1]) // 1024 for x in f if x.startswith("VmRSS:")), 0)
        except OSError:
            return 0
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            avail = next((int(x.split()[1]) // 1024 for x in f if x.startswith("MemAvailable:")), 0)
    except OSError:
        return {}
    kids = sum(rss(p.pid) for p in getattr(pool, "_pool", []) or [])
    return {"rss_mb": rss("self"), "workers_rss_mb": kids, "avail_mb": avail}


_W: Engine | None = None  # the engine a forked worker inherited


def _worker_init() -> None:
    assert _W is not None
    _W.store.data.eng.dispose(close=False)  # never share the parent's SQLite connections across processes
    _W.pool = None


def _worker_chunk(arg: tuple[Week, str, list[tuple[str, tuple[float, ...]]]]) -> tuple[list[Any], dict[str, int]]:
    assert _W is not None
    week, kind, jobs = arg
    b0 = sum(getattr(p, "attempts", 0) for p in _blocked_providers(_W))
    s0 = _W.replay.served if _W.replay is not None else 0
    part = analyse_chunk(_W, week, kind, jobs)
    return part, {"blocked": sum(getattr(p, "attempts", 0) for p in _blocked_providers(_W)) - b0,
                  "replay_served": (_W.replay.served if _W.replay is not None else 0) - s0}


def _blocked_providers(eng: Engine) -> list[Any]:
    return list(getattr(eng, "blocked", {}).values())


def config_fingerprint(cfg: ModelConfig) -> str:
    blob = json.dumps({"hash": cfg.config_hash, "weights": dict(cfg.scoring_model.weights), "min_completeness": cfg.decision.min_completeness,
                       "version": cfg.scoring_model.version}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
