# DART OpenAPI 사용 가이드

## API 키 발급
1. https://opendart.fss.or.kr 접속
2. 회원가입 후 로그인
3. "오픈API" → "API 키 신청" 메뉴에서 신청
4. 발급된 키를 `.env` 파일의 `DART_API_KEY=` 에 설정

## 기본 URL
`https://opendart.fss.or.kr/api/`

## 주요 엔드포인트

### 1. 기업 코드 다운로드 (`corpCode.xml`)
```
GET /corpCode.xml?crtfc_key={API_KEY}
응답: ZIP 파일 (내부 CORPCODE.xml)
```
- `corp_code` (8자리): DART 내부 기업 식별자
- `stock_code` (6자리): 증권 종목코드 (일치하지 않을 수 있음)
- 연 1~2회 업데이트, 캐시 권장

### 2. 단일 기업 전체 재무제표 (`fnlttSinglAcntAll.json`)
```
GET /fnlttSinglAcntAll.json
파라미터:
  crtfc_key: API 키
  corp_code:  기업 코드 (8자리)
  bsns_year:  사업연도 (예: 2024)
  reprt_code: 보고서 종류 (아래 표 참조)
  fs_div:     CFS(연결) 또는 OFS(별도)
```

### reprt_code 값표

| 코드 | 보고서 |
|-----|-------|
| 11011 | 사업보고서 (12월 결산 기준 다음해 3월 공시) |
| 11012 | 반기보고서 (1~6월, 8월 공시) |
| 11013 | 1분기보고서 (1~3월, 5월 공시) |
| 11014 | 3분기보고서 (1~9월, 11월 공시) |

### account_nm → 재무 항목 매핑

| account_nm (DART 명칭) | 매핑 키 | 비고 |
|-----------------------|--------|------|
| 매출액 | revenue | 일부 기업은 영업수익 |
| 영업수익 | revenue | 금융/보험업 |
| 당기순이익 | net_income | |
| 당기순손익 | net_income | 표기 다를 때 |
| 자본총계 | total_equity | 연결 기준 |
| 부채총계 | total_debt | |
| 자산총계 | total_assets | |
| 주당순이익 | eps_raw | 직접 제공 시 사용 |

## 응답 상태 코드

| status | 의미 |
|--------|------|
| 000 | 정상 |
| 010 | 미등록 API 키 |
| 011 | 사용 중지 API 키 |
| 020 | 요청 허용 횟수 초과 |
| 100 | 필드 누락 |
| 800 | 시스템 점검 중 |

## 실용 팁
- 속도 제한: 공식 제한 없음. `time.sleep(0.05)` 권장 (초당 20건)
- corp_code는 data/corp_codes.json에 캐시하여 매번 다운로드 방지
- 비금융업 기준: fs_div=CFS (연결) 우선, 없으면 fs_div=OFS (별도)
- 결산월 3월 기업은 bsns_year가 1년 차이날 수 있음
