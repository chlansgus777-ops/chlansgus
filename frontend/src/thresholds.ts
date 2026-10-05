/** 매수 판단의 점수 기준 — config/scoring_model.toml [decision] 과 같은 값 (backend/tests/unit/test_frontend_thresholds.py 가
 * 둘이 같은지 확인합니다). decision-3.6.0: 매수 72·소량 66 (소유자 요청 — 매수 종목이 너무 많음). */
export const BUY_SCORE = 72;
export const SMALL_SCORE = 66;
