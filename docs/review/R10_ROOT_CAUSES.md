# 라운드 10 — 결함 부류별 근본 규칙과 적용 경로 grep

각 항목: 근본 규칙 한 문장, 규칙을 담당하는 공통 함수, 그 규칙을 쓰는 모든 경로의 grep 결과(`backend/marketlens`, 테스트 제외). `python scripts/review/root_cause_greps.py`로 다시 만들 수 있습니다.

## F02 (P0)

**규칙:** 보유 중인 종목은 종가가 화면에 보인 손절가 아래로 마감하면, 직전 판단이 무엇이든(HOLD·REDUCE·WAIT·없음 포함) 매도 판정을 낸다.

**공통 함수:** pipeline.watched_stop(감시 손절가) + decision.decide()의 prior_stop_breached

```
$ grep -rnE 'watched_stop|prior_stop_breached|stop_breached' backend/marketlens --include=*.py
backend/marketlens/application/pipeline.py:218:def watched_stop(prev: AnalysisDigest | None, held: bool) -> float | None:
backend/marketlens/application/pipeline.py:623:        stop_breached=entry.stop_breached if entry else None,
backend/marketlens/application/pipeline.py:643:    watch_stop = watched_stop(prev, inp.held)
backend/marketlens/application/pipeline.py:647:    prior_stop_breached = bool(watch_stop is not None and prev is not None and last_close_bar is not None
backend/marketlens/application/pipeline.py:649:    intraday_stop_breach = bool(watch_stop is not None and price is not None and price <= watch_stop and not prior_stop_breached)
backend/marketlens/application/pipeline.py:664:        prior_stop_breached=prior_stop_breached,
backend/marketlens/domain/decision.py:54:    prior_stop_breached: bool = False  # a completed session CLOSED at/below the stop of the previous bullish recommendation
backend/marketlens/domain/decision.py:148:        if held and ctx.prior_stop_breached:  # a price fact, not a data gap
backend/marketlens/domain/decision.py:203:    if ctx.prior_stop_breached and (raw in BULLISH_ACTIONS | {Action.HOLD} or (ctx.held and raw != Action.SELL)):
backend/marketlens/domain/decision.py:220:    if not vetoes and not ctx.prior_stop_breached and not keeps_invalid_buy and prev is not None and action != prev \
backend/marketlens/domain/what_changed.py:28:    stop_breached: bool | None
backend/marketlens/domain/what_changed.py:92:    if cur.stop_breached and not prev.stop_breached:
backend/marketlens/domain/entry.py:69:    def stop_breached(self) -> bool:
```

## H1 (P1) · H4 (P2) · F12 · F13

**규칙:** 주당 값(가격 수준·손절가·수량·평단·EPS·추정치)은 기록 당시 반영된 분할과 오늘 일봉에 반영된 분할의 차이만큼, 한 함수(share_multiplier)로만 환산한다.

**공통 함수:** corporate_actions.share_multiplier (split_factor·analysis_basis는 이것을 부르는 얇은 포장)

```
$ grep -rnE 'share_multiplier\(|split_factor\(|adjust_shares\(|split_factor_since|split_adjusted' backend/marketlens --include=*.py
backend/marketlens/api/routes.py:90:        "sector_model": r.sector_model, "price": lv["price"] if lv else r.price, "split_factor_since": lv["split_factor"] if lv else 1.0, "session": r.session, "price_timestamp": r.price_timestamp.isoformat() if r.price_timestamp else None,
backend/marketlens/application/estimate_book.py:54:        f = share_multiplier(splits, ShareBasis(h.observed_on, unknown_on_execution_day=True), as_of)
backend/marketlens/application/sync.py:46:    bars_split_adjusted: int = 0
backend/marketlens/application/sync.py:126:        rep.bars_split_adjusted = self.store.adjust_bars_for_splits(ny_today)
backend/marketlens/application/services.py:237:            f = share_multiplier(self.data.security_splits(sid, s), ShareBasis(entered[r.ticker]), today) or 1.0
backend/marketlens/application/services.py:239:            hs.append(Holding(label, r.quantity * f, r.cost_basis / f, sector, themes, rates, split_adjusted=f))
backend/marketlens/application/services.py:335:        f = share_multiplier(self.data.splits(row.ticker), analysis_basis(to_ny(row.as_of).date(), known), to_ny(self.now()).date()) or 1.0
backend/marketlens/application/evaluation_service.py:125:            f = share_multiplier(self.svc.data.splits(key or pos.ticker), analysis_basis(to_ny(pos.recommended_at).date(), applied), basis_date) or 1.0
backend/marketlens/application/market_store.py:478:                    f = split_factor(splits, row.shares_as_of, d) if row.shares_as_of else 1.0
backend/marketlens/application/pipeline.py:239:    f = share_multiplier(splits, analysis_basis(to_ny(prev.as_of).date(), prev.splits_applied), to_ny(as_of).date()) or 1.0
backend/marketlens/domain/portfolio.py:20:    split_adjusted: float = 1.0  # share multiplier of splits executed after the holding was entered (quantity ×, cost ÷)
backend/marketlens/domain/portfolio.py:231:    split_adjusted: float = 1.0  # the entered quantity/cost were put on today's share basis by this multiplier
backend/marketlens/domain/portfolio.py:283:                                     (px / h.cost_basis - 1) if px is not None and h.cost_basis > 0 else None, None, h.sector, h.split_adjusted,
backend/marketlens/domain/corporate_actions.py:47:def share_multiplier(splits: Sequence[SplitEvent], basis: ShareBasis, through: date) -> float | None:
backend/marketlens/domain/corporate_actions.py:66:def split_factor(splits: Sequence[SplitEvent], after: date, through: date) -> float:
backend/marketlens/domain/corporate_actions.py:68:    return share_multiplier(splits, ShareBasis(after), through) or 1.0
backend/marketlens/domain/corporate_actions.py:114:            f = split_factor(splits, filed, basis_date)
backend/marketlens/domain/corporate_actions.py:122:def adjust_shares(value: float, as_of: date, splits: Sequence[SplitEvent], basis_date: date) -> float:
backend/marketlens/domain/corporate_actions.py:124:    return value * split_factor(splits, as_of, basis_date)
```

## H4 구조 해결 (기능 a)

**규칙:** 거래 기록이 있는 회사의 보유는 거래 기록과 저장된 분할에서 한 함수(domain.ledger.positions)로만 계산하고, 보유를 쓰는 모든 곳은 Service.portfolio 하나를 거친다.

**공통 함수:** domain.ledger.positions / Service.portfolio

```
$ grep -rnE '\.portfolio\(|repo\.holdings\(|positions\(' backend/marketlens --include=*.py
backend/marketlens/infrastructure/db/repository.py:191:def open_paper_positions(s: Session) -> list[PaperPositionRow]:
backend/marketlens/infrastructure/db/repository.py:195:def all_paper_positions(s: Session) -> list[PaperPositionRow]:
backend/marketlens/api/routes.py:179:    pf = s.portfolio(ss)
backend/marketlens/api/routes.py:285:        pf = s.portfolio(ss)
backend/marketlens/api/routes.py:335:        pf = s.portfolio(ss)
backend/marketlens/application/services.py:218:        rows = repo.holdings(s)
backend/marketlens/application/services.py:265:                pos, err = positions(trades, g["splits"], today), None
backend/marketlens/application/services.py:406:        open_same = [p for p in repo.open_paper_positions(s) if p.ticker == r.ticker]
backend/marketlens/application/services.py:479:            pf = self.portfolio(s)
backend/marketlens/application/services.py:563:            r, inp = sc.analyze_single(ticker, ctx.as_of, self.portfolio(s), ctx)
backend/marketlens/application/evaluation_service.py:137:            positions = [p for p in repo.all_paper_positions(s) if p.recommended_at <= as_of]
backend/marketlens/application/evaluation_service.py:245:            positions = [p for p in repo.all_paper_positions(s) if p.recommended_at <= now]
backend/marketlens/domain/ledger.py:172:def positions(trades: Sequence[Trade], splits: Sequence[SplitEvent], as_of: date) -> Position:
```

## H2 (P2) · H3 (P3)

**규칙:** 가이던스는 한 절 안에 전망 신호·지표·기간·범위가 모두 있을 때만 EXTRACTED로 추출하며, 규칙은 말뭉치 점수로만 바꾼다.

**공통 함수:** domain.guidance.extract (절 단위 _clauses → _parse), 채점 backend/tests/corpus/scoring.py

```
$ grep -rnE 'guidance\.extract\(|from marketlens\.domain\.guidance import|def extract\(' backend/marketlens --include=*.py
backend/marketlens/application/live_verify.py:200:        from marketlens.domain.guidance import extract, html_to_text
backend/marketlens/application/data_access.py:287:        from marketlens.domain.guidance import extract, html_to_text
backend/marketlens/application/market_store.py:25:from marketlens.domain.guidance import GuidanceItem
backend/marketlens/domain/guidance.py:485:def extract(text: str) -> list[GuidanceItem]:
```

## H5 (P3)

**규칙:** 상태를 바꾸는 작업(동기화·스캔·거래 기록 변경)은 잠금 하나로 직렬화하고, 모든 종료 경로(예외 포함)에서 잠금을 푼다.

**공통 함수:** Service._lock / _sync_lock / _sync_run / _ledger_lock

```
$ grep -rnE '_sync_run|_sync_lock|_ledger_lock|self\._lock\b' backend/marketlens --include=*.py
backend/marketlens/infrastructure/resilience.py:40:        self._lock = threading.Lock()
backend/marketlens/infrastructure/resilience.py:44:        with self._lock:
backend/marketlens/infrastructure/resilience.py:52:        with self._lock:
backend/marketlens/infrastructure/resilience.py:62:        with self._lock:
backend/marketlens/infrastructure/resilience.py:66:        with self._lock:
backend/marketlens/infrastructure/resilience.py:71:        with self._lock:
backend/marketlens/infrastructure/resilience.py:145:        self._lock = threading.Lock()
backend/marketlens/infrastructure/resilience.py:149:            with self._lock:
backend/marketlens/infrastructure/db/repository.py:145:        self._lock = threading.Lock()
backend/marketlens/infrastructure/db/repository.py:148:        with self._lock:
backend/marketlens/infrastructure/db/repository.py:153:        with self._lock:
backend/marketlens/infrastructure/health.py:82:        self._lock = threading.Lock()
backend/marketlens/infrastructure/health.py:86:        with self._lock:
backend/marketlens/infrastructure/health.py:92:        with self._lock:
backend/marketlens/infrastructure/health.py:115:        with self._lock:
backend/marketlens/application/data_access.py:40:        self._lock = threading.Lock()
backend/marketlens/application/data_access.py:43:        with self._lock:
backend/marketlens/application/data_access.py:50:        with self._lock:
backend/marketlens/application/services.py:158:        self._lock = threading.Lock()
backend/marketlens/application/services.py:159:        self._sync_lock = threading.Lock()  # the background preparation job (taken by start_sync, freed by the job)
backend/marketlens/application/services.py:160:        self._ledger_lock = threading.Lock()  # one trade-record change at a time (check and write together)
backend/marketlens/application/services.py:161:        self._sync_run = threading.Lock()  # one sync_market() at a time, whoever calls it (9th evaluation H5)
backend/marketlens/application/services.py:275:        with self._ledger_lock, self.sf() as s:
backend/marketlens/application/services.py:290:        with self._ledger_lock, self.sf() as s:
backend/marketlens/application/services.py:466:        if self._sync_lock.locked():
backend/marketlens/application/services.py:468:        if not self._lock.acquire(blocking=False):
backend/marketlens/application/services.py:473:            self._lock.release()
backend/marketlens/application/services.py:552:        if state.get("status") == "RUNNING" and not self._lock.locked():
backend/marketlens/application/services.py:625:        with self._sync_run:
backend/marketlens/application/services.py:639:        if not self._sync_lock.acquire(blocking=False):
backend/marketlens/application/services.py:646:            self._sync_lock.release()  # the job never started: the lock must not stay taken
backend/marketlens/application/services.py:656:        """Runs holding ``_sync_lock`` (taken by :meth:`start_sync`)."""
backend/marketlens/application/services.py:684:                self._sync_lock.release()  # always: the button must work again without restarting the app
backend/marketlens/application/services.py:692:        if job and job.get("status") == "RUNNING" and not self._sync_lock.locked():
```

## F06 (P2)

**규칙:** 티커 재사용 아카이브는 재사용 효력일 전의 기록만 옛 회사로 옮긴다.

**공통 함수:** MarketStore._archive_reused

```
$ grep -rnE '_archive_reused|archived_as' backend/marketlens --include=*.py
backend/marketlens/infrastructure/db/models.py:74:    names a different company; the old company's stored rows were archived under ``archived_as``)."""
backend/marketlens/infrastructure/db/models.py:84:    archived_as: Mapped[str | None] = mapped_column(String(32), nullable=True)
backend/marketlens/application/market_store.py:70:                    arch = self._archive_reused(s, row, sec, today, existing, "REUSE")
backend/marketlens/application/market_store.py:82:                    arch = self._archive_reused(s, row, sec, today, existing, "RETURN")
backend/marketlens/application/market_store.py:223:    def _archive_reused(self, s: Session, row: SecurityRow, sec: Security, today: date, existing: Mapping[str, SecurityRow], event: str) -> SecurityRow:
backend/marketlens/application/market_store.py:258:        s.add(TickerHistoryRow(mode=self.mode, event=event, ticker=row.ticker, cik=sec.cik, other_cik=row.cik, archived_as=arch, effective=today, observed_at=_now()))
backend/marketlens/application/market_store.py:314:        ev = s.scalars(select(TickerHistoryRow).where(TickerHistoryRow.mode == self.mode, TickerHistoryRow.ticker == ticker, TickerHistoryRow.archived_as.is_not(None),
backend/marketlens/application/market_store.py:316:        return ev.archived_as if ev is not None and ev.archived_as else ticker
backend/marketlens/application/market_store.py:334:                                                          TickerHistoryRow.archived_as.is_not(None))):
backend/marketlens/application/market_store.py:335:            archives.setdefault(ev.ticker, []).append((ev.effective, str(ev.archived_as)))
```

## F07 (P3)

**규칙:** 저장소가 요청 구간의 시작을 덮지 못하면 그 응답을 완전한 이력으로 취급하지 않는다.

**공통 함수:** DataAccess.bars(fill_gaps) + bars_backfill_complete

```
$ grep -rnE 'fill_gaps|bars_backfill_complete|def bars\(' backend/marketlens --include=*.py
backend/marketlens/providers/mock/world.py:195:    def bars(self, ticker: str) -> list[tuple[date, float, float, float, float, float]]:
backend/marketlens/application/live_verify.py:118:    def bars() -> list[dict[str, Any]]:
backend/marketlens/application/sync.py:105:                self.store.set_setting("bars_backfill_complete", (today - timedelta(days=backfill_days)).isoformat())
backend/marketlens/application/data_access.py:109:    def bars(self, t: str, start: date, end: date, fill_gaps: bool = True) -> Fetched:
backend/marketlens/application/data_access.py:110:        """``fill_gaps``: when the stored history misses the requested START and the market sync has not finished its
backend/marketlens/application/data_access.py:121:                front_ok = (stored[0].day - start).days <= 5 or bool(self.store.get_setting("bars_backfill_complete"))
backend/marketlens/application/data_access.py:122:                if front_ok or not fill_gaps:
backend/marketlens/application/market_store.py:509:    def bars(self, ticker: str, start: date, end: date) -> list[Bar]:
backend/marketlens/application/scanner.py:200:        bars = take("bars", self.data.bars(t, d - timedelta(days=HISTORY_CALENDAR_DAYS), d, fill_gaps=False)) or []
```

## F08 (P3)

**규칙:** 매수 크기는 종목·업종·테마·현금 한도의 남은 금액 중 가장 작은 값을 넘지 않는다(축소 뒤에도 다시 검사).

**공통 함수:** domain.portfolio.review_candidate(fitting) → position_plan

```
$ grep -rnE 'fitting|position_plan\(|review_candidate\(' backend/marketlens --include=*.py
backend/marketlens/api/routes.py:162:            "position_plan": _position_plan(s, ss, row, summary),
backend/marketlens/api/routes.py:174:def _position_plan(s: MarketLensService, ss: Any, row: Any, summary: dict[str, Any]) -> dict[str, Any]:
backend/marketlens/api/routes.py:187:    p = position_plan(row.final_action, size_cap, nav, price, summary.get("stop"), current, s.model_config().portfolio)  # stop on today's share basis
backend/marketlens/application/scanner.py:253:            review = review_candidate(portfolio, prices, CandidateProfile(t, sec.sector, themes, exp.rates, _returns_by_date(bars)), hold_rets, self.cfg.portfolio, valuation_day=val_day)
backend/marketlens/domain/portfolio.py:109:def review_candidate(
backend/marketlens/domain/portfolio.py:170:    def fitting(room: float) -> SizeClass:
backend/marketlens/domain/portfolio.py:180:        limit(fitting(limits.max_sector - sector_w.get(cand.sector, 0.0)), f"편입 시 섹터 {cand.sector} 비중 {sector_after:.0%} (한도 {limits.max_sector:.0%})")
backend/marketlens/domain/portfolio.py:183:            limit(fitting(limits.max_theme - theme_w.get(t, 0.0)), f"편입 시 테마 {t} 노출 {w:.0%} (한도 {limits.max_theme:.0%})")
backend/marketlens/domain/portfolio.py:340:def position_plan(action: str, size_cap: str | None, nav: float | None, price: float | None, stop: float | None,
```

## F09 (P3)

**규칙:** 가이던스는 같은 회계분기의 컨센서스와만 비교한다.

**공통 함수:** estimate_book._named_quarter / _fiscal_quarter

```
$ grep -rnE '_named_quarter|_fiscal_quarter|attach_guidance' backend/marketlens --include=*.py
backend/marketlens/application/estimate_book.py:146:def _named_quarter(label: str | None) -> tuple[int, int | None] | None:
backend/marketlens/application/estimate_book.py:159:def _fiscal_quarter(period: str | None) -> tuple[int, int] | None:
backend/marketlens/application/estimate_book.py:167:def attach_guidance(reports: Sequence[EarningsReport], rows: Sequence[object], history: Sequence[EstimateObservation], as_of: date) -> list[EarningsReport]:
backend/marketlens/application/estimate_book.py:203:        cq = _fiscal_quarter(nxt.period)
backend/marketlens/application/estimate_book.py:205:            gq = _named_quarter(getattr(g_item, "period_label", None)) if g_item is not None else None
backend/marketlens/application/scanner.py:214:            from marketlens.application.estimate_book import attach_guidance
backend/marketlens/application/scanner.py:217:            earnings = attach_guidance(earnings, self.data.store.guidance(t, ctx.as_of), self.data.store.estimate_history(t, day), day)
```

## F10 (P2) · 라운드 10 리뷰 P1(주식 종류 병합)

**규칙:** '같은 종목인가'는 security_id(티커, 날짜) 하나로만 판단한다 — 저장 키의 이름 변경·재상장 계보. 재사용은 끊고, 같은 CIK의 다른 주식 종류는 다른 종목(추천 이력·직전 판단·모의투자·거래 기록·수동 보유).

**공통 함수:** MarketStore.security_id / security_ids → DataAccess.security_of / securities_of → company_recommendations / ledger_securities

```
$ grep -rnE 'security_id\(|security_ids\(|security_of\(|securities_of\(|company_recommendations\(|identity_on\(|ledger_securities\(|company_id\(' backend/marketlens --include=*.py
backend/marketlens/api/routes.py:157:        history = [{"id": h.id, "as_of": h.as_of.isoformat(), "score": h.score, "action": h.final_action} for h in s.company_recommendations(ss, t, limit=30)]
backend/marketlens/application/data_access.py:151:    def company_recommendations(self, s: Any, ticker: str, mode: str, before: datetime | None, on: date, limit: int = 500,
backend/marketlens/application/data_access.py:161:        sid = self.security_of(ticker, on, s)
backend/marketlens/application/data_access.py:169:        ids = self.securities_of([(r.ticker, to_ny(r.as_of).date()) for r in rows], s)
backend/marketlens/application/data_access.py:173:    def security_of(self, ticker: str, on: date, s: Any) -> str:
backend/marketlens/application/data_access.py:174:        return self.securities_of([(ticker, on)], s)[(ticker, on)]
backend/marketlens/application/data_access.py:176:    def securities_of(self, pairs: list[tuple[str, date]], s: Any) -> dict[tuple[str, date], str]:
backend/marketlens/application/data_access.py:179:            return self.store.security_ids(pairs, s)
backend/marketlens/application/data_access.py:182:    def ledger_securities(self, s: Any, rows: list[Any]) -> list[dict[str, Any]]:
backend/marketlens/application/data_access.py:186:        ids = self.securities_of([(r.ticker, r.day) for r in rows], s)
backend/marketlens/application/data_access.py:208:    def identity_on(self, t: str, d: date, session: Any = None) -> str:
backend/marketlens/application/services.py:220:        sids = self.data.securities_of([(r.ticker, entered[r.ticker]) for r in rows], s)
backend/marketlens/application/services.py:262:        for g in self.data.ledger_securities(s, repo.transactions(s)):
backend/marketlens/application/services.py:277:            sid = self.data.security_of(ticker, day, s)
backend/marketlens/application/services.py:278:            group = next((g for g in self.data.ledger_securities(s, repo.transactions(s)) if g["security"] == sid), None)
backend/marketlens/application/services.py:292:            group = next((g for g in self.data.ledger_securities(s, repo.transactions(s)) if any(r.id == tid for r in g["rows"])), None)
backend/marketlens/application/services.py:299:    def company_recommendations(self, s: Session, ticker: str, before: datetime | None = None, on: date | None = None, limit: int = 500,
backend/marketlens/application/services.py:302:        return self.data.company_recommendations(s, ticker, self.mode.value, before, on or to_ny(before or self.now()).date(), limit, inclusive, exclude_id)
backend/marketlens/application/services.py:306:        rows = self.company_recommendations(s, ticker, before, limit=1, inclusive=inclusive, exclude_id=exclude_id)
backend/marketlens/application/evaluation_service.py:98:                key = self.svc.data.identity_on(rec.ticker, base_day, s)  # the company recommended, even if the ticker was reused later
backend/marketlens/application/evaluation_service.py:138:            keys = {p.id: self.svc.data.identity_on(p.ticker, to_ny(p.recommended_at).date(), s) for p in positions}  # data location
backend/marketlens/application/evaluation_service.py:139:            sids = self.svc.data.securities_of([(p.ticker, to_ny(p.recommended_at).date()) for p in positions], s)
backend/marketlens/application/evaluation_service.py:153:                later = self.svc.data.company_recommendations(s, pos.ticker, self.svc.mode.value, as_of, to_ny(pos.recommended_at).date())
backend/marketlens/application/market_store.py:318:    def security_id(self, ticker: str, on: date, session: Session | None = None) -> str:
backend/marketlens/application/market_store.py:324:            return self.security_ids([(ticker, on)], session)[(ticker, on)]
backend/marketlens/application/market_store.py:326:            return self.security_ids([(ticker, on)], s)[(ticker, on)]
backend/marketlens/application/market_store.py:328:    def security_ids(self, pairs: Iterable[tuple[str, date]], s: Session) -> dict[tuple[str, date], str]:
```

## F11 (P3)

**규칙:** 방향을 말하는 단어가 없으면 거시 이슈의 방향은 0(중립)이다.

**공통 함수:** issue_engine._move

```
$ grep -rnE '_move\(|direction =' backend/marketlens --include=*.py
backend/marketlens/application/issue_engine.py:277:def _move(text: str) -> float:
backend/marketlens/application/issue_engine.py:296:        d = _move(t)
backend/marketlens/application/issue_engine.py:301:        d = _move(t)  # "yields climb" → rates up; "leaves rates unchanged" → no effect
backend/marketlens/application/committee/claims.py:246:        direction = -1 if any(w in low for w in _NEG_WORDS) else 1 if any(w in low for w in _POS_WORDS) else 0
backend/marketlens/application/pipeline.py:512:        direction = 1 if raw[0].at(Horizon.SWING).impact_score >= 0 else -1
backend/marketlens/domain/macro.py:295:        direction = "순풍" if c > 0 else "역풍"
backend/marketlens/domain/issues.py:156:                direction = 1 if score > 2 else -1 if score < -2 else 0
```

## F14 (P2)

**규칙:** 저장하거나 상태를 바꾸는 요청은 GET을 포함해 모두 교차 사이트 방어(클라이언트 헤더·Fetch Metadata·Origin) 뒤에서만 실행된다.

**공통 함수:** api/app.py local_guard + routes.stock의 쓰기 조건

```
$ grep -rnE 'CLIENT_HEADER|x-marketlens-client|sec-fetch-site|UNSAFE_METHODS' backend/marketlens --include=*.py
backend/marketlens/api/routes.py:139:    may_write = req.headers.get("x-marketlens-client") is not None
backend/marketlens/api/app.py:39:CLIENT_HEADER = "x-marketlens-client"
backend/marketlens/api/app.py:41:UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
backend/marketlens/api/app.py:110:        site = request.headers.get("sec-fetch-site")
backend/marketlens/api/app.py:114:            if request.method in UNSAFE_METHODS and request.headers.get(CLIENT_HEADER) is None:
backend/marketlens/api/app.py:115:                return JSONResponse({"detail": f"상태 변경 요청에는 {CLIENT_HEADER} 헤더가 필요합니다."}, status_code=403)
backend/marketlens/api/app.py:125:    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["content-type", CLIENT_HEADER, TOKEN_HEADER])
```
