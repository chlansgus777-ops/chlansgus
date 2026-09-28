# MarketLens 독립 검토 증거

대상 ZIP: chlansgus-claude-marketlens-investment-system-iyki44.zip
SHA-256: C27E97A83193B480596862BDF2A5BAFCDB63362920622054E8E5DAC5EAA227CF

원본 프로그램의 동작을 바꾸지 않고 경계 사례를 실행한 자료입니다. 12개 반례 테스트는 정상 동작을 assertion으로 표현했기 때문에 원본에서는 실패하는 것이 이번 검토의 관찰 결과입니다. 고유 검토 항목은 10개이며, F08은 별도 계측으로 확인했습니다.

## 백엔드 반례 실행

프로젝트의 backend[dev]를 설치한 Python 환경이 필요합니다. 먼저 압축 해제한 **프로젝트 최상위 폴더**를 지정하세요. 테스트 파일이 프로젝트 내부에 있을 필요는 없습니다.

```powershell
$env:MARKETLENS_REVIEW_ROOT = 'C:\path\to\chlansgus-claude-marketlens-investment-system-iyki44'
python -m pytest .\test_review_checks.py -q --tb=short
python .\review_perf.py
```

모의 공급자와 임시 메모리 DB를 사용합니다. 실제 시장 공급자나 AI에 접속하지 않습니다. 키 노출 반례도 가짜 키와 httpx.MockTransport로 실행합니다.

| 테스트 | 보고서 |
|---|---|
| test_half_cap_reaches_amount | F01 |
| test_small_cap_reaches_already_small_buy | F01 |
| test_cold_start_preserves_sector_limit | F02 |
| test_expired_recommendation_has_no_current_buy_quantity | F04 |
| test_committee_watch_blocks_every_buy의 두 입력 | F05 |
| test_new_analysis_refreshes_shared_news_context | F06 |
| test_sync_refused_while_scan_lock_held | F07 |
| review_perf.py | F08 |
| test_provider_error_does_not_echo_short_quoted_api_key | F09 |
| test_disjoint_price_days_report_missing_valuation | F10 |

F06은 새 context가 이미 생성되었다는 조건을 가짜 scanner로 제어해 대입·재사용 경로를 검사합니다. F07은 실행 중인 스캔과 같은 잠금 상태를 만들고 실제 다운로드 대신 짧은 테스트 작업을 사용해 동시 시작이 허용되는지를 검사합니다. 이 두 반례가 실제 공급자 변경이나 데이터 손상까지 시뮬레이션하는 것은 아닙니다.

## 프런트엔드 반례 실행

프로젝트 복사본에서 `npm ci` 후, `frontend/review` 폴더를 만들고 `counterexamples.test.tsx`를 그 안에 넣습니다. frontend 폴더에서 다음을 실행합니다.

```text
npx vitest run review/counterexamples.test.tsx
```

화면 테스트는 네트워크를 흉내 낸 응답과 기존 stock_mock.json으로 실제 React 컴포넌트를 jsdom에 렌더링합니다. Windows WebView 실기 테스트는 아닙니다. 첫 테스트는 F03, 둘째는 F04입니다.

## 기존 검사 결과

백엔드: 수집된 1,771개 중 1,770개 통과, 심볼릭 링크 권한으로 1개 skip. 이와 별도로 Playwright 미설치로 E2E 모듈이 수집되지 않았습니다. 실행은 `python -m pytest backend/tests -q --disable-warnings --tb=short`에 해당합니다. pyproject.toml의 addopts=-q와 겹쳐 최종 개수 요약이 로그에 생략되어, 진행 표시 및 nodeids 1,771개와 별도 collect-only 기록으로 확인했습니다. 프로세스 종료 코드는 0입니다.

프런트엔드: 별도 반례 추가 전 기존 테스트 18개 파일·93개 통과. `npm run build`는 tsc 검사와 Vite 빌드 통과. Node 24.19.0. 제품 소스는 바꾸지 않았습니다.

backend-environment.txt는 이번 검사 환경의 패키지 버전입니다. 백엔드 lock 파일 전체를 그대로 재현한 환경은 아닙니다. 실제 LIVE 호출, 설치 프로그램 실행, 전체 시장 장기 부하·수익률 검증 결과는 포함하지 않습니다.
