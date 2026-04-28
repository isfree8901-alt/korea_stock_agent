# Skill: backtester

## 목적
FinanceDataReader로 과거 시계열 데이터를 로드하고
현재 포트폴리오 종목에 대해 동일가중 포트폴리오 시뮬레이션을 실행한다.

## 스크립트 목록

| 스크립트 | 역할 | 입력 | 출력 |
|---------|------|-----|------|
| `scripts/run_backtest.py` | 과거 시계열 로드 + 포트폴리오 시뮬레이션 | `step4_portfolio_signals.json` | `step6_simulation.json` |
| `scripts/calc_metrics.py` | 총수익률/승률/MDD/초과수익 계산 | `step6_simulation.json` | `step6_backtest_result.json` |

## 시뮬레이션 방식 (MVP)
- 동일 가중치 (equal-weight)
- BUY/HOLD 시그널 종목만 포함
- 초기 포트폴리오 가치: 100 기준 정규화
- 벤치마크: KOSPI 지수 (KS11)

## 환경 변수
- `BACKTEST_START_DATE`: 기본 `2022-01-01`
- `HISTORICAL_DATA_DIR`: 기본 `./data/historical` (CSV 캐시)

## 실패 처리
- 개별 종목 과거 데이터 없음 → 해당 종목 제외 + pipeline_warn.log
- 유효 종목 0개 → `step6_backtest_result.json`에 `"error": "데이터 부족"` 기록
- 대시보드는 `error` 필드 존재 시 "데이터 부족" 표시
