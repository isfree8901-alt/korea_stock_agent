# Skill: financial-analyzer

## 목적
거래량 TOP20 종목과 포트폴리오 종목에 대해 6개 재무지표를 계산하고
섹터 평균 및 Z-score를 산출하여 LLM 정성 평가용 컨텍스트를 구성한다.

## 스크립트 목록

| 스크립트 | 역할 | 입력 | 출력 |
|---------|------|-----|------|
| `scripts/top20_volume.py` | 거래량 TOP20 종목 추출 | `step1_market_data.json` | `top20_tickers.json` |
| `scripts/calc_indicators.py` | 6개 지표 계산 | `top20_tickers.json`, `step1_market_data.json`, `step1_financial_data.json` | `step5_indicators.json` |
| `scripts/calc_sector_avg.py` | 섹터별 평균/표준편차/Z-score 계산 | `step5_indicators.json` | `step5_indicators_with_context.json` |

## 계산 지표 목록
| 지표 | 공식 |
|------|------|
| PER | 주가 / EPS |
| ROE | 당기순이익 / 자기자본 × 100 |
| PBR | 주가 / BPS (주당순자산) |
| EPS 성장률 | (현재 EPS - 전기 EPS) / \|전기 EPS\| × 100 |
| 매출 성장률 | (현재 매출 - 전기 매출) / \|전기 매출\| × 100 |
| 부채비율 | 부채총계 / 자기자본 × 100 |

## 실패 처리
- 특정 종목 재무 데이터 없음 → `disclosure_warning: true` + 지표 null + 계속 진행
- 지표 이상값 (PER < 0, ROE > 200%) → null 처리 (pipeline_warn.log 기록)
