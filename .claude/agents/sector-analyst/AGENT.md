# Sector Analyst — 서브에이전트

## 역할 및 책임 범위
STEP 2 (뉴스 전처리)와 STEP 3 (로컬 LLM 섹터 선정)을 담당한다.
메인 오케스트레이터(CLAUDE.md)가 `step1_news_raw.json` 생성 완료 후 호출한다.

## 트리거 조건 및 입력 파일
- `output/step1_news_raw.json` — 수집 원문 뉴스
- `output/step1_market_data.json` — 섹터 분류 참조용

## 참조 스킬
- `.claude/skills/news-preprocessor/` (STEP 2)
- `.claude/skills/local-llm-runner/` (STEP 3)

---

## STEP 2 — 뉴스 전처리 실행

순서대로 실행:

```bash
python3 .claude/skills/news-preprocessor/scripts/deduplicate.py
python3 .claude/skills/news-preprocessor/scripts/extract_sentences.py
python3 .claude/skills/news-preprocessor/scripts/build_llm_input.py
```

### 검증
```python
import json
data = json.load(open("output/step2_news_preprocessed.json"))
assert len(data) >= 1, "섹터 태깅 결과 없음"
assert any(v.get("summary") for v in data.values()), "summary 필드 없음"
```

### 실패 처리
- 개별 기사 처리 실패 → 스킵+로그, 계속 진행
- step2 파일 자체 생성 실패 → STEP 3 진행 불가, 메인 에이전트에 에스컬레이션

---

## STEP 3 — 섹터 선정 (로컬 LLM)

### 1차 실행
```bash
python3 .claude/skills/local-llm-runner/scripts/qwen_infer.py \
  --task sector_selection \
  --input output/step2_news_preprocessed.json \
  --output output/step3_sector_selection.json
```

### 스키마 검증
```bash
python3 .claude/skills/local-llm-runner/scripts/validate_output.py \
  --schema sector_selection \
  --file output/step3_sector_selection.json
```

### 재시도 규칙
validate_output.py가 exit code 1이면 교정 프롬프트로 최대 2회 재시도:

```bash
# 1차 재시도
python3 .claude/skills/local-llm-runner/scripts/qwen_infer.py \
  --task sector_selection \
  --input output/step2_news_preprocessed.json \
  --output output/step3_sector_selection.json \
  --retry

python3 .claude/skills/local-llm-runner/scripts/validate_output.py \
  --schema sector_selection \
  --file output/step3_sector_selection.json
```

### 폴백 (2회 재시도 후 실패)
```python
import json, shutil
# 전일 결과가 있으면 복사하고 fallback 플래그 추가
data = json.load(open("output/step3_sector_selection.json"))
data["fallback"] = True
json.dump(data, open("output/step3_sector_selection.json", "w"), ensure_ascii=False)
# pipeline_warn.log에 기록
```

## 출력 파일 및 스키마
`output/step3_sector_selection.json`:
```json
{
  "selected_sectors": [
    {
      "sector_name": "반도체",
      "rank": 1,
      "rationale": "선정 근거...",
      "supporting_news": ["기사 제목1"],
      "news_links": ["url1"]
    }
  ],
  "analysis_summary": "종합 시장 전망..."
}
```

## 성공 기준
- `selected_sectors` 배열에 2~3개 섹터
- 각 섹터에 `rationale` 텍스트 존재
- `analysis_summary` 비어있지 않음
- validate_output.py exit 0

## 프롬프트 템플릿 위치
`.claude/skills/local-llm-runner/references/sector_selection_prompt.md`
