# KRX 섹터 분류 참조표

## pykrx 섹터 분류 조회

```python
from pykrx import stock
# KOSPI 섹터
df_kospi = stock.get_market_sector_classifications("20250415", market="KOSPI")
# KOSDAQ 섹터
df_kosdaq = stock.get_market_sector_classifications("20250415", market="KOSDAQ")
```

## 주요 섹터명 (업종명 컬럼)

KRX는 한국표준산업분류(KSIC) 기준으로 업종을 구분합니다.
pykrx에서 반환되는 주요 업종명 예시:

| pykrx 업종명 | 에이전트 섹터명 |
|------------|--------------|
| 반도체 및 반도체장비 | 반도체 |
| 전자장비 및 기기 | 반도체 (일부 포함) |
| 화학 | 화학 |
| 자동차 | 자동차 |
| 은행 | 금융 |
| 보험 | 금융 |
| 증권 | 금융 |
| 에너지 | 에너지 |
| 통신서비스 | 통신 |
| 철강 | 철강 |
| 건설 | 건설 |
| 유통 | 유통 |

## keyword_dict.json 과의 연동

`news-preprocessor/references/keyword_dict.json`의 섹터명은
pykrx에서 반환되는 업종명과 **정확히 일치하지 않을 수 있습니다**.

실제 운영 시 fetch_krx.py 실행 후 다음 명령으로
실제 섹터명을 확인하여 keyword_dict.json을 조정하세요:

```python
import json
data = json.load(open("output/step1_market_data.json"))
sectors = set(v["sector"] for v in data.values() if v.get("sector"))
print(sorted(sectors))
```

## 섹터 매핑 커스터마이징

`keyword_dict.json`에 실제 pykrx 업종명을 키로 추가하거나,
`build_llm_input.py`의 `tag_article_sectors()` 함수에서
업종명 정규화 로직을 추가하면 정확도가 높아집니다.
