"""
STEP 4 - 전일 포트폴리오 대비 변경사항 계산
portfolio_prev.json(전일)과 step4_top10_filtered.json(금일)을 비교하여
BUY / SELL / HOLD 시그널 초안을 생성한다.
출력: output/step4_portfolio_diff.json
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))


def load_prev_portfolio() -> dict:
    path = OUTPUT_DIR / "portfolio_prev.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def compute_diff(today_top10: dict, prev_portfolio: dict) -> dict:
    """
    today_top10:  {sector_name: [{ticker, name, ...}]}
    prev_portfolio: 동일 구조 (비어있으면 첫 실행)
    반환: {sector_name: [{ticker, name, signal, is_new, ...}]}
    """
    result = {}
    all_sectors = set(today_top10.keys()) | set(prev_portfolio.keys())

    for sector in all_sectors:
        today_tickers = {t["ticker"]: t for t in today_top10.get(sector, [])}
        prev_tickers = {t["ticker"] for t in prev_portfolio.get(sector, [])}

        sector_result = []

        # 금일 포트폴리오 종목: BUY 또는 HOLD
        first_run = not prev_portfolio
        for ticker, info in today_tickers.items():
            signal = "BUY" if (first_run or ticker not in prev_tickers) else "HOLD"
            sector_result.append({
                **info,
                "signal": signal,
                "is_new": (signal == "BUY"),
                "disclosure_warning": False,
            })

        # 전일 포트폴리오에 있었지만 금일 제외된 종목: SELL
        for t in prev_portfolio.get(sector, []):
            if t["ticker"] not in today_tickers:
                sector_result.append({
                    **t,
                    "signal": "SELL",
                    "is_new": False,
                    "disclosure_warning": False,
                })

        result[sector] = sector_result

    return result


def main():
    top10_path = OUTPUT_DIR / "step4_top10_filtered.json"
    if not top10_path.exists():
        print("ERROR: step4_top10_filtered.json 없음", file=sys.stderr)
        sys.exit(1)

    today_top10 = json.loads(top10_path.read_text(encoding="utf-8"))
    prev_portfolio = load_prev_portfolio()
    is_first = not prev_portfolio
    if is_first:
        print("[diff_portfolio] 첫 실행 — 전체 종목 BUY 처리")

    diff = compute_diff(today_top10, prev_portfolio)

    out_path = OUTPUT_DIR / "step4_portfolio_diff.json"
    out_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    buys = sum(1 for s in diff.values() for t in s if t["signal"] == "BUY")
    sells = sum(1 for s in diff.values() for t in s if t["signal"] == "SELL")
    print(f"[diff_portfolio] BUY: {buys}건, SELL: {sells}건 → {out_path}")


if __name__ == "__main__":
    main()
