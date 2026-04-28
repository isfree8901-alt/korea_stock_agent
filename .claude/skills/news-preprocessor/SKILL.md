# Skill: news-preprocessor

## 목적
수집된 원문 뉴스를 정제하여 로컬 LLM이 소화할 수 있는 섹터별 요약 텍스트로 변환한다.

## 스크립트 목록

| 스크립트 | 역할 | 입력 | 출력 |
|---------|------|-----|------|
| `scripts/deduplicate.py` | 제목 유사도(≥0.85) 기준 중복 기사 제거 | `step1_news_raw.json` | `step1_news_raw.json` (덮어쓰기) |
| `scripts/extract_sentences.py` | TF 기반 핵심 문장 추출 (기사당 4문장) | `step1_news_raw.json` | `step1_news_raw.json` (key_sentences 필드 추가) |
| `scripts/build_llm_input.py` | 섹터 키워드 태깅 + 요약 텍스트 조합 (최대 500자) | `step1_news_raw.json`, `keyword_dict.json` | `step2_news_preprocessed.json` |

## 성공 기준
- 전처리 완료 기사 수 ≥ 수집 기사의 80%
- 각 섹터 summary 길이 ≤ 500자
- `step2_news_preprocessed.json`에 최소 1개 이상 섹터 키 존재

## 참조
- `references/keyword_dict.json`: 섹터별 키워드 사전 (12개 섹터)
