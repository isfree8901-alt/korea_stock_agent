# Skill: local-llm-runner

## 목적
로컬 Ollama 서버에서 실행 중인 Qwen 모델에 추론 요청을 보내고 출력을 검증한다.
섹터 선정(sector_selection)과 재무 평가(financial_eval) 두 가지 태스크를 지원한다.

## 스크립트 목록

| 스크립트 | 역할 |
|---------|------|
| `scripts/qwen_infer.py` | Ollama API 호출, JSON 추출, 파일 저장 |
| `scripts/validate_output.py` | LLM 출력 스키마 검증 (exit 0: 성공, exit 1: 실패) |

## 사용법

```bash
# 섹터 선정
python3 qwen_infer.py --task sector_selection \
  --input output/step2_news_preprocessed.json \
  --output output/step3_sector_selection.json

# 재무 평가
python3 qwen_infer.py --task financial_eval \
  --input output/step5_indicators_with_context.json \
  --output output/step5_financial_analysis.json

# 재시도 (교정 프롬프트 포함)
python3 qwen_infer.py --task sector_selection ... --retry

# 검증
python3 validate_output.py --schema sector_selection --file output/step3_sector_selection.json
```

## 환경 설정
- `OLLAMA_HOST`: 기본 `http://localhost:11434`
- `OLLAMA_MODEL`: 기본 `qwen2.5:32b` (VRAM 24GB+ 필요)
- 타임아웃: 300초

## 재시도 동작
`--retry` 플래그 사용 시 프롬프트에 교정 지시를 추가:
"이전 출력에 스키마 오류가 있었습니다. JSON 스키마를 정확히 따라 JSON만 반환하세요."

## 참조 프롬프트 템플릿
- `references/sector_selection_prompt.md`
- `references/financial_eval_prompt.md`
