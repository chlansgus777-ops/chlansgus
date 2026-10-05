import { StrategyBoard } from "../components/StrategyViews";

/** 전략: the strategy-centred view (owner 2026-10-06) — the rules, their verification, today's signals and the
 * forward paper record. The composite score lives on the stock pages as explanation, not here. */
export default function Strategies() {
  return (
    <div className="grid">
      <div className="page-head enter"><div><h1>전략</h1>
        <div className="t-sub">규칙이 명확한 매매 전략의 신호와 검증 상태입니다. 종가로 신호를 확정하고 다음 거래일 시가 체결을 가정합니다. 실제 주문은 넣지 않습니다.</div></div></div>
      <StrategyBoard />
    </div>
  );
}
