import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { num, pct, price, stamp } from "../format";
import { RISK_KO, SESSION_KO, ko } from "../i18n";
import type { OppRow } from "../types";
import { Action, Quality, StatusBadge, Vetoes } from "./ui";

type Key = keyof OppRow;

const COLS: [Key, string, string][] = [
  ["rank", "순위", ""], ["ticker", "종목", ""], ["company", "회사명", ""], ["sector", "섹터", ""],
  ["price", "현재가(USD)", "분석 시점의 가격과 품질(최신/지연/오래됨)"], ["session", "세션", ""],
  ["score", "점수", "0~100 결정론적 점수(AI가 바꿀 수 없음)"], ["confidence", "신뢰도", "분류의 견고성(보정된 성공확률 아님)"],
  ["action", "추천", "추천 행동. 만료된 매수 신호는 취소선으로 표시"], ["current_status", "현재 유효성", "추천 이후 거래일 경과 여부로 지금 다시 판정"],
  ["ideal_entry", "이상적 진입가", ""], ["max_buy", "최대 매수가", "이 가격을 넘으면 손익비 2 미만"], ["target", "1차 목표가", ""],
  ["downside", "손절까지", "손절가까지의 하락률"], ["rr", "손익비", "(목표가−현재가)÷(현재가−손절가)"], ["catalyst", "다음 촉매", ""],
  ["risk", "이벤트 위험", ""], ["data_quality", "데이터", "전체 데이터 품질"],
];

export function OppTable({ rows, compact = false }: { rows: OppRow[]; compact?: boolean }) {
  const [sort, setSort] = useState<Key>("rank");
  const [asc, setAsc] = useState(true);
  const sorted = useMemo(() => {
    const r = [...rows];
    r.sort((a, b) => {
      const x = a[sort];
      const y = b[sort];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * (asc ? 1 : -1);
    });
    return r;
  }, [rows, sort, asc]);
  const compactKeys: Key[] = ["rank", "ticker", "company", "price", "score", "confidence", "action", "current_status", "max_buy", "rr", "risk"];
  const cols = compact ? COLS.filter(([k]) => compactKeys.includes(k)) : COLS;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {cols.map(([k, l, help]) => (
              <th key={k} title={help} onClick={() => { if (sort === k) setAsc(!asc); else { setSort(k); setAsc(true); } }}>
                {l}{sort === k ? (asc ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className={r.actionable_now === false ? "row-expired" : ""}>
              {cols.map(([k]) => (
                <td key={k}>{cell(r, k)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cell(r: OppRow, k: Key) {
  switch (k) {
    case "ticker":
      return <Link to={`/stocks/${r.ticker}`}><b>{r.ticker}</b></Link>;
    case "company":
      return <span title={r.company}>{r.company.length > 26 ? `${r.company.slice(0, 26)}…` : r.company}</span>;
    case "sector":
      return r.sector_known === false ? <span className="warn" title="업종 분류 정보가 없어 일반 모델로 평가(신뢰도 하향)">분류 불명확</span> : r.sector;
    case "price":
      return <span title={`출처 ${r.price_source ?? "N/A"} · ${stamp(r.price_timestamp)}`}>{price(r.price)} <Quality q={r.price_quality} /></span>;
    case "session":
      return ko(SESSION_KO, r.session, "N/A");
    case "score":
      return <b>{num(r.score, 1)}</b>;
    case "confidence":
      return `${num(r.confidence, 0)}%`;
    case "action":
      return <><Action a={r.action} status={r.current_status} quality={r.data_quality} /> <Vetoes v={r.vetoes} /></>;
    case "current_status":
      return <StatusBadge s={r.current_status} reason={r.current_status_reason} />;
    case "ideal_entry": case "max_buy": case "target":
      return price(r[k]);
    case "downside":
      return <span className="neg">{pct(r.downside)}</span>;
    case "rr":
      return num(r.rr, 2);
    case "catalyst":
      return r.catalyst ? <span title={r.catalyst_date ?? ""}>{r.catalyst.length > 24 ? `${r.catalyst.slice(0, 24)}…` : r.catalyst}</span> : "—";
    case "risk":
      return <span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "pos"}>{ko(RISK_KO, r.risk, "N/A")}</span>;
    case "data_quality":
      return <Quality q={r.data_quality} />;
    default: {
      const v = r[k];
      return v === null || v === undefined ? "N/A" : String(v);
    }
  }
}
