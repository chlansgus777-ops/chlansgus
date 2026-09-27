# 라운드 10 — `/security-review` 결과

대상: `19c7e79..e8978ff` (라운드 10 전체, 55개 파일). 추가 사용량을 늘리지 않기 위해 하위 에이전트 없이 한 번에 검토했고, 발견 후보마다 코드를 직접 읽어 확인했습니다.

## 결론

신뢰도 8 이상인 보안 취약점은 **없음**.

## 확인한 공격면

| 변경 | 확인 내용 | 판단 |
|---|---|---|
| `POST /api/transactions`, `DELETE /api/transactions/{tid}` | `api/app.py local_guard`: 상태 변경 메서드는 `x-marketlens-client` 헤더가 없으면 403. 사용자 정의 헤더가 있으면 교차 출처 요청은 사전 요청(preflight)을 거치고, CORS 허용 출처는 localhost·Tauri뿐. Origin 검사와 Sec-Fetch-Site 검사도 앞에 있음. `tests/invariants/test_security.py`가 모든 POST/PUT/DELETE 라우트를 헤더 없이 호출해 403을 확인(새 라우트 포함) | 안전 |
| `GET /api/transactions` | 읽기 전용(데이터베이스 변경 없음, `test_security`의 "헤더 없는 GET은 DB를 바꾸지 않음" 불변 조건 통과). 교차 사이트 읽기는 CORS·Sec-Fetch-Site로 막힘. Host는 TrustedHostMiddleware로 localhost만(DNS rebinding 방어) | 안전 |
| 입력 검증 | `TradeIn`: 종류 Literal, 수치 `ge=0`·상한·`allow_inf_nan=False`, 메모 200자. 티커는 `_ticker` 정규식. 도메인 `validate`가 다시 검사(비율 범위, 미래 날짜). 메모는 화면에 그리지 않음 | 안전 |
| SQL | 새 쿼리(`security_ids`, `security_splits`, `transactions`)는 모두 SQLAlchemy 표현식(`in_`, `==`)으로 매개변수화 | 안전 |
| `GET /api/stocks/{t}` 쓰기 조건 | 분석·저장은 클라이언트 헤더가 있을 때만(`may_write`) | 안전 |
| `.github/workflows/guidance-corpus.yml` | `workflow_dispatch`만(쓰기 권한자만 실행). 입력 `sec_user_agent`는 `env:`로만 전달되고 `run:` 스크립트에 직접 삽입되지 않음(스크립트 주입 없음). 커밋 메시지에는 `github.run_id`만 들어감 | 안전 |
| `scripts/corpus/collect_guidance_sentences.py` | SEC 응답을 정규식 기반 `html_to_text`로 처리(XML 파서·역직렬화·서브프로세스 없음). 출력 경로는 CLI 인자(신뢰 입력) | 안전 |
| 프런트엔드 `Ledger.tsx` | React 텍스트 렌더링만(`dangerouslySetInnerHTML` 없음) | 안전 |

## 보안이 아닌 참고

- 거래 기록 변경은 서비스 잠금(`_ledger_lock`)으로 직렬화되어, 동시에 보낸 두 매도가 함께 검사를 통과하지 못함(`test_two_sales_submitted_together_cannot_both_pass`).
