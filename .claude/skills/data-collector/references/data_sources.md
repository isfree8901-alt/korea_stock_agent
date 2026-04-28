# 데이터 소스 참조

## pykrx 배치 API

```python
from pykrx import stock

# 전 종목 OHLCV (한 번의 호출로 전체 수집 — 개별 호출 금지)
df = stock.get_market_ohlcv_by_ticker("20250415", market="ALL")
# 컬럼: 시가, 고가, 저가, 종가, 거래량, 거래대금, 등락률

# 전 종목 시가총액
df_cap = stock.get_market_cap_by_ticker("20250415", market="ALL")
# 컬럼: 시가총액, 거래량, 거래대금, 상장주식수

# 섹터 분류 (KOSPI / KOSDAQ 별도 호출 후 합산)
df_sector = stock.get_market_sector_classifications("20250415", market="KOSPI")
# 컬럼: 업종명 (버전마다 상이할 수 있음)

# 종목명 조회
name = stock.get_market_ticker_name("005930")

# 공휴일 처리: 조회 결과가 빈 DataFrame이면 전일로 하루씩 소급
```

## DART OpenAPI

- 기본 URL: `https://opendart.fss.or.kr/api`
- 인증: `crtfc_key` 파라미터 (API 키)
- API 키 발급: https://opendart.fss.or.kr 회원가입 후 신청

### 주요 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /corpCode.xml` | 전체 기업 corp_code 목록 (zip 파일) |
| `GET /fnlttSinglAcntAll.json` | 단일 기업 전체 재무제표 |

### fnlttSinglAcntAll.json 파라미터

| 파라미터 | 값 | 설명 |
|---------|---|------|
| crtfc_key | API 키 | 인증 |
| corp_code | 8자리 | DART 기업 고유 코드 |
| bsns_year | YYYY | 사업연도 |
| reprt_code | 11011~11014 | 보고서 종류 |
| fs_div | CFS / OFS | 연결/별도 재무제표 |

### reprt_code 값

| 코드 | 설명 |
|-----|------|
| 11011 | 사업보고서 (연간) |
| 11012 | 반기보고서 |
| 11013 | 1분기보고서 |
| 11014 | 3분기보고서 |

### account_nm → 재무 항목 매핑

| account_nm | 항목 |
|-----------|------|
| 매출액 / 영업수익 | revenue |
| 당기순이익 | net_income |
| 자본총계 | total_equity |
| 부채총계 | total_debt |
| 자산총계 | total_assets |

### 속도 제한
- 공식 제한 없음. 실용적 권장: 초당 20건 이하 (0.05초 sleep)

---

## 뉴스 RSS 피드

| 소스 | URL | 비고 |
|-----|-----|------|
| 한국경제 | https://rss.hankyung.com/economy.xml | 안정적 |
| 매일경제 | https://rss.mk.co.kr/rss/30000001.xml | 안정적 |
| 연합인포맥스 | https://news.einfomax.co.kr/rss/allArticle.xml | 금융 특화 |
| 인베스팅닷컴 | https://kr.investing.com/rss/news.rss | 글로벌 포함 |

### BeautifulSoup 본문 추출 선택자

```python
ARTICLE_SELECTORS = [
    "div.article-body",   # 한국경제
    "div#articleBody",    # 매일경제
    "div.news_body",      # 연합뉴스
    "div.article_txt",    # 기타
    "article",            # 범용
]
```
