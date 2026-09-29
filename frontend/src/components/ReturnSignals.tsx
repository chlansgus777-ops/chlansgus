import type { Component } from "../types";
import { Card } from "./ui";

/** The four return signals of PREREGISTRATION §13 (52-week high, relative strength, F-score, earnings drift): what each
 * says about this stock, and a grade. Weight 0 until the 2016+ validation adopts them — the card says so. */
const NAMES: Record<string, string> = { "signal.high52": "52주 신고가 근접도", "signal.rs_rank": "상대강도 순위", "signal.fscore": "F-스코어(재무 건전성)", "signal.ear": "실적 발표 후 추세" };
const MISSING: Record<string, string> = { "signal.high52": "가격 이력 1년 미만", "signal.rs_rank": "비교할 스캔 종목 분포 없음", "signal.fscore": "재무 자료 부족", "signal.ear": "최근 60거래일 안 실적 발표 없음" };

export function grade(sub: number | null): string | null {
  if (sub === null) return null;
  return sub >= 0.8 ? "A" : sub >= 0.65 ? "B" : sub >= 0.45 ? "C" : sub >= 0.3 ? "D" : "F";
}

export function ReturnSignalsCard({ c }: { c: Component | undefined }) {
  if (!c) return null;
  const g = grade(c.available ? c.subscore : null);
  const rows = Object.keys(NAMES).map((key) => ({ key, reason: c.reasons.find((r) => r.refs.includes(key)) ?? null }));
  const adopted = c.weight > 0;
  return (
    <Card title="수익 신호" sub testId="return-signals"
      explain={adopted ? "과거 검증을 통과해 점수에 반영하는 신호입니다." : "연구로 효과가 알려진 4가지 신호입니다. 7년치 과거 검증에서 효과가 확인되기 전까지 점수에는 넣지 않고 참고로만 보여 줍니다."}
      right={g ? <span className={`grade grade-${g}`} title="4개 신호 평균(A~F)">{g}</span> : <span className="caption">계산 불가</span>}>
      <div className="sig-list">
        {rows.map(({ key, reason }) => (
          <div key={key} className={`sig ${reason ? (reason.sign > 0 ? "up" : reason.sign < 0 ? "down" : "flat") : "none"}`}>
            <span className="dot" aria-hidden />
            <span className="n">{NAMES[key]}</span>
            <span className="t">{reason ? reason.text : <span className="caption">{MISSING[key]} — 계산 안 함</span>}</span>
          </div>
        ))}
      </div>
      {!adopted && <div className="caption" style={{ marginTop: 8 }}>검증 중 · 점수 반영 0점</div>}
    </Card>
  );
}
