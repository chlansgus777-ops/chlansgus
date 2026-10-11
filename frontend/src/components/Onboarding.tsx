import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

/** 첫 사용 안내 (owner spec 2026-10-11 §2-3): three short steps the first time the app opens — what the app does, how to
 * follow the routine, where to see the result. Shown once per device; 용어·도움말 opens it again. */
const KEY = "ml.onboarded";
const OPEN = "ml:onboarding";

const STEPS: { title: string; body: string; link?: [string, string] }[] = [
  { title: "마켓렌즈는 무엇을 하나요?", body: "미국 주식 중 이번 달에 무엇을 얼마나 살지 정리해 드리는 도우미입니다. 주문은 하지 않습니다. 사고파는 것은 쓰시는 증권사 앱에서 직접 하세요." },
  { title: "루틴은 이렇게 따라 하세요", body: "한 달에 한 번, 홈의 ‘이번 달 할 일’ 카드를 보세요. ‘종목 목록 펼치기’를 누르면 어떤 종목을 몇 주 살지 나옵니다. 나머지 날은 그대로 두면 됩니다." },
  { title: "결과는 여기서 확인하세요", body: "홈 맨 위 ‘내 현황’에 총 자산과 수익(산 가격 대비)이 나옵니다. ‘과거 성적표’에는 이 방법이 과거에 어땠는지와 가장 나빴을 때가 함께 나옵니다.", link: ["/evidence", "추천 근거 자세히 보기"] },
];

export function openOnboarding(): void {
  window.dispatchEvent(new Event(OPEN));
}

export function Onboarding() {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(KEY) !== "1"; } catch { return false; } });
  const [i, setI] = useState(0);
  useEffect(() => {
    const show = () => { setI(0); setOpen(true); };
    window.addEventListener(OPEN, show);
    return () => window.removeEventListener(OPEN, show);
  }, []);
  if (!open) return null;
  const done = () => { try { localStorage.setItem(KEY, "1"); } catch { /* private window: shown again next time */ } setOpen(false); };
  const s = STEPS[i]!;
  return (
    <div className="onb-backdrop" role="dialog" aria-modal="true" aria-labelledby="onb-h" data-testid="onboarding">
      <div className="onb">
        <div className="onb-steps" aria-hidden>{STEPS.map((_, k) => <span key={k} className={k === i ? "on" : k < i ? "done" : ""} />)}</div>
        <div className="t-kicker">처음 오셨나요? {i + 1} / {STEPS.length}</div>
        <h2 id="onb-h">{s.title}</h2>
        <p>{s.body}</p>
        {s.link && <Link to={s.link[0]} onClick={done}>{s.link[1]} →</Link>}
        <div className="onb-foot">
          <button type="button" className="ghost" onClick={done} data-testid="onb-skip">건너뛰기</button>
          {i > 0 && <button type="button" onClick={() => setI(i - 1)}>이전</button>}
          {i < STEPS.length - 1
            ? <button type="button" className="primary" onClick={() => setI(i + 1)} data-testid="onb-next">다음</button>
            : <button type="button" className="primary" onClick={done} data-testid="onb-done">시작하기</button>}
        </div>
      </div>
    </div>
  );
}
