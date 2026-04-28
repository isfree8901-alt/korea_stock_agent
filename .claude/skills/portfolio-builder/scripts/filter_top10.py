"""
STEP 4 - 섹터별 시가총액 상위 10종목 필터링
step3_sector_selection.json의 선정 섹터별로
step1_market_data.json에서 market_cap 기준 상위 10종목을 추출한다.
출력: output/step4_top10_filtered.json
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
WARN_LOG = OUTPUT_DIR / "pipeline_warn.log"


def log_warn(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().isoformat()
    with open(WARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] [filter_top10] {msg}\n")
    print(f"WARN: {msg}", file=sys.stderr)


def filter_top10(market_data: dict, sector_name: str, top_n: int = 10) -> list[dict]:
    tickers_in_sector = [
        (ticker, info)
        for ticker, info in market_data.items()
        if info.get("sector") == sector_name
    ]
    sorted_tickers = sorted(
        tickers_in_sector,
        key=lambda x: x[1].get("market_cap") or 0,
        reverse=True,
    )
    if len(sorted_tickers) < top_n:
        log_warn(f"'{sector_name}' 섹터 종목 수 {len(sorted_tickers)}개 (10개 미만)")

    return [
        {
            "ticker": ticker,
            "name": info.get("name", ""),
            "market_cap": info.get("market_cap"),
            "close": info.get("close"),
            "volume": info.get("volume"),
            "sector": sector_name,
        }
        for ticker, info in sorted_tickers[:top_n]
    ]


def main():
    sector_path = OUTPUT_DIR / "step3_sector_selection.json"
    market_path = OUTPUT_DIR / "step1_market_data.json"

    for p in [sector_path, market_path]:
        if not p.exists():
            print(f"ERROR: {p} 없음", file=sys.stderr)
            sys.exit(1)

    sector_data = json.loads(sector_path.read_text(encoding="utf-8"))
    market_data = json.loads(market_path.read_text(encoding="utf-8"))

    selected_sectors = sector_data.get("selected_sectors", [])
    result = {}

    for sector_info in selected_sectors:
        sector_name = sector_info["sector_name"]
        top10 = filter_top10(market_data, sector_name)
        result[sector_name] = top10
        print(f"[filter_top10] '{sector_name}': {len(top10)}종목")

    out_path = OUTPUT_DIR / "step4_top10_filtered.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[filter_top10] 저장 완료 → {out_path}")


if __name__ == "__main__":
    main()
