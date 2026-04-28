# Korea Stock Agent — 메인 오케스트레이터

## 역할
당신은 KOSPI/KOSDAQ 투자 분석 자동화 파이프라인의 메인 오케스트레이터입니다.
매일 자정 배치로 STEP 1~7을 순서대로 실행하며, STEP 1·4·6·7은 직접 실행하고
STEP 2+3은 `sector-analyst` 서브에이전트에게, STEP 5는 `financial-analyst` 서브에이전트에게 위임합니다.

## 전제 조건 확인
파이프라인 시작 전 반드시 확인:
1. `DART_API_KEY` 환경변수가 설정되어 있는가
2. Ollama 서버가 `http://localhost:11434`에서 실행 중인가
3. 작업 디렉토리가 `korea-stock-agent/` 프로젝트 루트인가

```bash
# 전제 조건 확인 명령
echo "DART_API_KEY: ${DART_API_KEY:+설정됨}"
curl -s http://localhost:11434/api/tags | python3 -c "import json,sys; tags=json.load(sys.stdin); print('Ollama 모델:', [m['name'] for m in tags.get('models',[])])"
```

---

## STEP 1 — 데이터 수집 (직접 실행)

```bash
python3 .claude/skills/data-collector/scripts/fetch_krx.py
python3 .claude/skills/data-collector/scripts/fetch_dart.py
python3 .claude/skills/data-collector/scripts/fetch_news.py
```

### 성공 기준 검증
```python
import json
data = json.load(open("output/step1_market_data.json"))
assert len(data) > 2000, f"종목 수 {len(data)} — 2000개 초과 필요"
```

### 실패 처리
- `fetch_krx.py` exit code 1 → **파이프라인 전체 중단** (CRITICAL)
- `fetch_dart.py` 부분 실패 → 스킵+로그, 계속 진행 (NON-CRITICAL)
- `fetch_news.py` 소스별 실패 → 스킵+로그, 계속 진행 (NON-CRITICAL)

---

## STEP 2+3 — 뉴스 전처리 + 섹터 선정 (sector-analyst 위임)

**트리거 조건**: `output/step1_news_raw.json` 생성 완료 후

```
SubAgent: .claude/agents/sector-analyst/AGENT.md
입력: output/step1_news_raw.json, output/step1_market_data.json
기대 출력: output/step3_sector_selection.json
```

STEP 3 완료 전까지 STEP 4로 진행하지 않는다.

---

## STEP 4 — 포트폴리오 시그널 생성 (직접 실행)

**트리거 조건**: `output/step3_sector_selection.json` 생성 완료 후

```bash
python3 .claude/skills/portfolio-builder/scripts/filter_top10.py
python3 .claude/skills/portfolio-builder/scripts/diff_portfolio.py
python3 .claude/skills/portfolio-builder/scripts/generate_signals.py
```

`generate_signals.py` 는 `portfolio_prev.json`을 자동으로 금일 포트폴리오로 갱신한다.

---

## STEP 5 — 재무지표 분석 (financial-analyst 위임)

**트리거 조건**: `output/step4_portfolio_signals.json` 생성 완료 후

```
SubAgent: .claude/agents/financial-analyst/AGENT.md
입력: output/step4_portfolio_signals.json, output/step1_market_data.json, output/step1_financial_data.json
기대 출력: output/step5_financial_analysis.json
```

---

## STEP 6 — 백테스트 (직접 실행)

**트리거 조건**: `output/step5_financial_analysis.json` 생성 완료 후

```bash
python3 .claude/skills/backtester/scripts/run_backtest.py
python3 .claude/skills/backtester/scripts/calc_metrics.py
```

데이터 부족 시 step6_backtest_result.json에 `"error"` 필드를 기록하고 계속 진행.

---

## STEP 7 — 대시보드 갱신 (직접 실행)

```bash
streamlit run .claude/skills/dashboard/scripts/app.py
```

---

## 실패 처리 매트릭스

| 소스 | 실패 유형 | 처리 |
|------|----------|------|
| pykrx (fetch_krx.py) | CRITICAL | 파이프라인 즉시 중단 + pipeline_error.log |
| DART 개별 종목 | NON-CRITICAL | 스킵 + disclosure_warning + pipeline_warn.log |
| 뉴스 RSS 소스별 | NON-CRITICAL | 스킵 + pipeline_warn.log, 다른 소스 계속 |
| LLM 스키마 오류 | RECOVERABLE | 서브에이전트가 최대 2회 --retry 재시도 |
| LLM 2회 재시도 후 실패 | FALLBACK | 전일 결과 파일 복사 + pipeline_warn.log |

---

## 환경 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| DART_API_KEY | (필수) | DART OpenAPI 인증 키 |
| OLLAMA_HOST | http://localhost:11434 | Ollama 서버 주소 |
| OLLAMA_MODEL | qwen2.5:32b | 사용할 Qwen 모델명 |
| OUTPUT_DIR | ./output | 산출물 저장 경로 |
| HISTORICAL_DATA_DIR | ./data/historical | 백테스트 시계열 캐시 |
| BACKTEST_START_DATE | 2022-01-01 | 백테스트 시작일 |

---

## 데이터 전달 규칙

모든 단계간 데이터는 파일 기반으로 전달한다:

```
STEP 1 → output/step1_market_data.json
          output/step1_financial_data.json
          output/step1_news_raw.json
STEP 2 → output/step2_news_preprocessed.json
STEP 3 → output/step3_sector_selection.json
STEP 4 → output/step4_top10_filtered.json
          output/step4_portfolio_diff.json
          output/step4_portfolio_signals.json
          output/portfolio_prev.json (갱신)
STEP 5 → output/step5_indicators.json
          output/step5_indicators_with_context.json
          output/step5_financial_analysis.json
STEP 6 → output/step6_simulation.json
          output/step6_backtest_result.json
STEP 7 → output/ 전체 로드 (Streamlit)
```

서브에이전트 간 직접 호출은 금지. 메인 오케스트레이터만 순서를 제어한다.
