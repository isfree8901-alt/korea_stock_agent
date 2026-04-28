# Financial Analyst — 서브에이전트

## 역할 및 책임 범위
STEP 5 (재무지표 계산 + 로컬 LLM 정성 평가)를 담당한다.
메인 오케스트레이터(CLAUDE.md)가 `step4_portfolio_signals.json` 생성 완료 후 호출한다.

## 트리거 조건 및 입력 파일
- `output/step4_portfolio_signals.json` — 포트폴리오 시그널 (분석 대상 종목)
- `output/step1_market_data.json` — 시가총액/섹터 정보
- `output/step1_financial_data.json` — DART 재무 원천 데이터

## 참조 스킬
- `.claude/skills/financial-analyzer/` (지표 계산)
- `.claude/skills/local-llm-runner/` (정성 평가)

---

## STEP 5 — 스크립트 처리 단계

순서대로 실행:

```bash
python3 .claude/skills/financial-analyzer/scripts/top20_volume.py
python3 .claude/skills/financial-analyzer/scripts/calc_indicators.py
python3 .claude/skills/financial-analyzer/scripts/calc_sector_avg.py
```

### 중간 산출물
- `output/top20_tickers.json` — 거래량 TOP20 종목 리스트
- `output/step5_indicators.json` — 6개 재무지표
- `output/step5_indicators_with_context.json` — 섹터 평균 + Z-score 포함 (LLM 입력용)

### 실패 처리
- 특정 종목 재무 데이터 누락 → `disclosure_warning: true` + pipeline_warn.log + 계속 진행

---

## STEP 5 — LLM 정성 평가 단계

### 1차 실행
```bash
python3 .claude/skills/local-llm-runner/scripts/qwen_infer.py \
  --task financial_eval \
  --input output/step5_indicators_with_context.json \
  --output output/step5_financial_analysis.json
```

### 스키마 검증
```bash
python3 .claude/skills/local-llm-runner/scripts/validate_output.py \
  --schema financial_eval \
  --file output/step5_financial_analysis.json
```

### 재시도 규칙
validate_output.py가 exit code 1이면 최대 2회 재시도:

```bash
python3 .claude/skills/local-llm-runner/scripts/qwen_infer.py \
  --task financial_eval \
  --input output/step5_indicators_with_context.json \
  --output output/step5_financial_analysis.json \
  --retry
```

### 폴백 (2회 재시도 후 실패)
LLM 평가 없이 지표 데이터만으로 step5_financial_analysis.json 생성:
```python
import json
indicators = json.load(open("output/step5_indicators_with_context.json"))
# evaluation 필드를 빈 문자열로 채워서 저장
for ticker in indicators:
    indicators[ticker]["evaluation"] = "LLM 평가 실패 — 지표 데이터만 제공"
json.dump(indicators, open("output/step5_financial_analysis.json", "w"), ensure_ascii=False, indent=2)
```

## 출력 파일 및 스키마
`output/step5_financial_analysis.json`:
```json
{
  "005930": {
    "name": "삼성전자",
    "sector": "반도체",
    "per": 14.2,
    "roe": 12.5,
    "pbr": 1.8,
    "eps_growth": 5.3,
    "revenue_growth": 8.1,
    "debt_ratio": 35.2,
    "sector_avg": {"per": 18.0, "roe": 10.0, "pbr": 2.1, "debt_ratio": 40.0},
    "z_scores": {"per": -0.8, "roe": 0.6, "pbr": -0.4, "debt_ratio": -0.3},
    "evaluation": "섹터 평균 대비 PER이 낮아 저평가 구간...",
    "disclosure_warning": false
  }
}
```

## 성공 기준
- TOP20 종목 전체 + 포트폴리오 종목에 대해 6개 지표 계산 완료
- 각 종목에 `evaluation` 텍스트 존재
- validate_output.py exit 0

## 프롬프트 템플릿 위치
`.claude/skills/local-llm-runner/references/financial_eval_prompt.md`
