# Skill: portfolio-builder

## 목적
선정된 섹터에서 시가총액 상위 10종목을 필터링하고 전일 대비 BUY/SELL/HOLD 시그널을 생성한다.

## 스크립트 목록

| 스크립트 | 역할 | 입력 | 출력 |
|---------|------|-----|------|
| `scripts/filter_top10.py` | 섹터별 market_cap 상위 10종목 추출 | `step3_sector_selection.json`, `step1_market_data.json` | `step4_top10_filtered.json` |
| `scripts/diff_portfolio.py` | 전일 포트폴리오 비교 → 시그널 초안 생성 | `step4_top10_filtered.json`, `portfolio_prev.json` | `step4_portfolio_diff.json` |
| `scripts/generate_signals.py` | disclosure_warning 부착 + portfolio_prev 갱신 | `step4_portfolio_diff.json`, `step1_financial_data.json` | `step4_portfolio_signals.json`, `portfolio_prev.json` |

## 시그널 정의
- **BUY**: 금일 포트폴리오 신규 편입 종목
- **SELL**: 전일 포트폴리오에서 이탈한 종목
- **HOLD**: 전일과 동일하게 유지되는 종목
- 첫 실행 (`portfolio_prev.json`이 비어있음): 전체 BUY 처리

## 실패 처리
- 특정 섹터 종목 수 < 10: 가용 종목으로 대체 + pipeline_warn.log
- portfolio_prev.json 없음: 첫 실행으로 간주하여 전체 BUY
