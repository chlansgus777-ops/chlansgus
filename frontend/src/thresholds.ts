/** 매수 판단의 점수 기준 — config/scoring_model.toml [decision] 과 같은 값 (backend/tests/unit/test_frontend_thresholds.py 가
 * 둘이 같은지 확인합니다). decision-3.4.0: 7년 자료의 점수 분포 상위 약 0.5 %·4 %. */
export const BUY_SCORE = 68;
export const SMALL_SCORE = 62;
