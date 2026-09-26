import { useState } from "react";
import { OppTable } from "../components/OppTable";
import { Card, Empty, Err, Loading } from "../components/ui";
import { NotReady, ReadinessBanner } from "../components/Readiness";
import { useApi } from "../components/useApi";
import { stamp } from "../format";
import { ACTION_INFO } from "../i18n";
import type { Opportunities as Opp } from "../types";

export default function Opportunities() {
  const o = useApi<Opp>("/opportunities");
  const [filter, setFilter] = useState("");
  const [action, setAction] = useState("ALL");
  const [onlyCurrent, setOnlyCurrent] = useState(false);
  if (o.state === "loading") return <Loading what="매수 후보" />;
  if (!o.data) return <Err error={o.error} retry={o.reload} />;
  const rows = o.data.rows.filter((r) => (action === "ALL" || r.action === action)
    && (!onlyCurrent || r.current_status === "CURRENT")
    && (filter === "" || `${r.ticker} ${r.company} ${r.sector}`.toLowerCase().includes(filter.toLowerCase())));
  return (
    <div className="grid">
      <div className="page-head"><div><h1>기회 찾기</h1><div className="t-sub">미국 전체 상장 종목을 단계별로 걸러 남은 후보입니다. 표 머리글에 마우스를 올리면 각 항목 설명이 나옵니다. 종목을 누르면 자세한 판단과 매수 계획을 볼 수 있습니다.</div></div></div>
      <ReadinessBanner r={o.data.readiness} />
      {o.data.readiness?.scanner_status === "SCANNER_NOT_READY" && !o.data.rows.length && <NotReady r={o.data.readiness} />}
      <Card right={<span className="muted">{o.data.scan ? `스캔 #${o.data.scan.id} · ${stamp(o.data.scan.as_of)} · ${o.data.scan.scoring_model_version}` : "스캔 없음"}</span>}>
        <div className="row" style={{ marginBottom: 10 }}>
          <input placeholder="종목 / 회사명 / 섹터 검색" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="추천 필터">
            <option value="ALL">전체 추천</option>
            {Object.entries(ACTION_INFO).map(([a, i]) => <option key={a} value={a}>{i.label}({a})</option>)}
          </select>
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={onlyCurrent} onChange={(e) => setOnlyCurrent(e.target.checked)} /> 현재 유효한 추천만</label>
          <span className="muted">{rows.length.toLocaleString("ko-KR")}개 후보</span>
        </div>
        {rows.length ? <OppTable rows={rows} /> : o.data.readiness?.scanner_status === "SCANNER_NOT_READY"
          ? <Empty hint="위의 준비 상태가 100%에 가까워지면 다시 스캔하세요.">데이터 준비 중이라 후보를 계산하지 못했습니다(‘살 종목이 없음’이 아님).</Empty>
          : <Empty>조건에 맞는 후보가 없습니다.</Empty>}
      </Card>
    </div>
  );
}
