# Skill: data-collector

## 목적
KRX 시장 데이터(pykrx), DART 재무 데이터, 뉴스(RSS 크롤링)를 수집하여 output/ 에 저장한다.

## 스크립트 목록

| 스크립트 | 역할 | 필수 입력 | 출력 |
|---------|------|---------|------|
| `scripts/fetch_krx.py` | pykrx 배치 API로 전 종목 OHLCV/시총/섹터 수집 | 없음 | `output/step1_market_data.json` |
| `scripts/fetch_dart.py` | DART OpenAPI 분기 재무제표 수집 | `step1_market_data.json`, `DART_API_KEY` | `output/step1_financial_data.json` |
| `scripts/fetch_news.py` | RSS 피드 파싱 + 본문 수집 | 없음 | `output/step1_news_raw.json` |

## 환경 요구사항
- `DART_API_KEY` 환경변수 필수
- 인터넷 연결 필수
- `pykrx`, `requests`, `beautifulsoup4`, `feedparser` 설치 필요

## 에러 코드
- exit 1: pykrx 수집 실패 또는 종목 수 ≤ 2000 (CRITICAL — 파이프라인 중단)
- exit 0 + pipeline_warn.log: DART 부분 실패 또는 뉴스 소스 실패

## 참조
- `references/data_sources.md`: 엔드포인트, API 파라미터, CSS 선택자 목록
