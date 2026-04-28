당신은 한국 주식 시장 전문 애널리스트입니다.
아래 섹터별 **3가지 객관적 신호**가 포함된 데이터를 분석하여
향후 3~6개월간 가장 유망한 섹터 2~3개를 선정하세요.

## 입력 데이터 필드 설명

각 섹터에는 다음 세 가지 신호가 포함되어 있습니다:

### A. 뉴스 감성 (News Sentiment)
- `article_count`: 해당 섹터 관련 뉴스 기사 수 (참고용, 많다고 유망한 것은 아님)
- `sentiment_score`: -1.0(매우 부정) ~ +1.0(매우 긍정). 긍/부정 키워드 비율로 산출
- `sentiment_label`: 긍정 / 중립 / 부정
- `positive_signals`: 성장·개선·수혜 등 긍정 신호 횟수
- `negative_signals`: 하락·우려·적자 등 부정 신호 횟수

### B. 주가 모멘텀 (Price Momentum) — 전일 대비
- `avg_change_rate`: 섹터 내 전 종목 평균 등락률(%)
- `advancing_ratio`: 상승 종목 비율(%). 50% 초과 = 전반적 강세

### C. 재무 개선 추세 (Financial Improvement) — YoY
- `avg_revenue_growth`: 섹터 내 종목 평균 매출 성장률(%). 양수 = 개선
- `profit_improving_ratio`: 순이익이 전년 대비 증가한 종목 비율(%)

### D. 종합 점수
- `composite_score`: 0.0~1.0. 위 세 신호의 가중 평균 (감성 40% + 모멘텀 35% + 재무 25%)

## 선정 기준 (우선순위 순)

1. **composite_score가 높은 섹터 우선** — 세 신호가 모두 양호한 섹터
2. **sentiment_score가 양수 + advancing_ratio > 50%** — 뉴스와 주가가 동시에 긍정적
3. **avg_revenue_growth > 0** — 실적이 실제로 개선 중인 섹터
4. 위 조건이 충돌할 경우 composite_score를 최종 기준으로 사용
5. article_count만 많고 다른 신호가 부정적인 섹터는 선정 금지

## 입력 데이터

{sector_summaries_json}

## 출력 형식

반드시 아래 JSON 형식만 반환하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요.

{
  "selected_sectors": [
    {
      "sector_name": "섹터명(한글)",
      "rank": 1,
      "composite_score": 0.0,
      "rationale": "선정 근거: sentiment_score, avg_change_rate, avg_revenue_growth 수치를 구체적으로 인용하여 2~3문장으로 설명 (한국어)",
      "key_signals": {
        "sentiment": "긍정/중립/부정",
        "price_momentum": "+X.X% / 상승비율 XX%",
        "revenue_trend": "+XX% YoY"
      },
      "supporting_news": ["관련 기사 제목1", "관련 기사 제목2"],
      "news_links": ["url1", "url2"]
    }
  ],
  "analysis_summary": "전체 시장 흐름과 선정 근거에 대한 종합 평가 (2~3문장, 수치 포함, 한국어)"
}
