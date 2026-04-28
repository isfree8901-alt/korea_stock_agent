"""
STEP 5 - 거래량 TOP20 종목 추출
step1_market_data.json에서 거래량 기준 상위 20종목을 추출한다.
출력: output/top20_tickers.json
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))


def get_top20_by_volume(market_data: dict, top_n: int = 20) -> list[str]:
    tickers_with_vol = [
        (ticker, info.get("volume") or 0)
        for ticker, info in market_data.items()
    ]
    sorted_tickers = sorted(tickers_with_vol, key=lambda x: x[1], reverse=True)
    return [t for t, _ in sorted_tickers[:top_n]]


def main():
    market_path = OUTPUT_DIR / "step1_market_data.json"
    if not market_path.exists():
        print("ERROR: step1_market_data.json 없음", file=sys.stderr)
        sys.exit(1)

    market_data = json.loads(market_path.read_text(encoding="utf-8"))
    top20 = get_top20_by_volume(market_data)

    out_path = OUTPUT_DIR / "top20_tickers.json"
    out_path.write_text(json.dumps(top20, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[top20_volume] TOP20 저장 완료 → {out_path}")
    for i, t in enumerate(top20, 1):
        name = market_data[t].get("name", "")
        vol = market_data[t].get("volume", 0)
        print(f"  {i:2d}. {t} {name} ({vol:,})")


if __name__ == "__main__":
    main()
