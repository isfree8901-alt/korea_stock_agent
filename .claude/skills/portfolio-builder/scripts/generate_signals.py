"""
STEP 4 - 포트폴리오 시그널 최종 생성
step4_portfolio_diff.json에 disclosure_warning 정보를 부착하고
step4_portfolio_signals.json 으로 저장한다.
또한 portfolio_prev.json을 금일 포트폴리오(BUY+HOLD 종목)로 갱신한다.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))


def attach_disclosure_warnings(portfolio: dict, financial_data: dict) -> dict:
    for sector_entries in portfolio.values():
        for entry in sector_entries:
            ticker = entry["ticker"]
            fin = financial_data.get(ticker, {})
            if fin.get("disclosure_warning"):
                entry["disclosure_warning"] = True
    return portfolio


def extract_active_portfolio(signals: dict) -> dict:
    """BUY/HOLD 종목만 추출하여 다음날 portfolio_prev.json 용도로 반환."""
    result = {}
    for sector, entries in signals.items():
        active = [e for e in entries if e["signal"] in ("BUY", "HOLD")]
        if active:
            result[sector] = active
    return result


def main():
    diff_path = OUTPUT_DIR / "step4_portfolio_diff.json"
    financial_path = OUTPUT_DIR / "step1_financial_data.json"

    if not diff_path.exists():
        print("ERROR: step4_portfolio_diff.json 없음", file=sys.stderr)
        sys.exit(1)

    portfolio = json.loads(diff_path.read_text(encoding="utf-8"))

    financial_data = {}
    if financial_path.exists():
        financial_data = json.loads(financial_path.read_text(encoding="utf-8"))

    portfolio = attach_disclosure_warnings(portfolio, financial_data)

    out_path = OUTPUT_DIR / "step4_portfolio_signals.json"
    out_path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8")

    # 전일 포트폴리오 업데이트 (다음 실행에서 비교용)
    prev_path = OUTPUT_DIR / "portfolio_prev.json"
    active = extract_active_portfolio(portfolio)
    prev_path.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")

    # 일별 스냅샷 저장 (대시보드 비교용)
    history_dir = BASE_DIR / "data" / "portfolio_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().strftime("%Y%m%d")
    snap_path = history_dir / f"{today_str}_signals.json"
    snap_path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8")

    # 시총 Top50 스냅샷 (순위 추이 비교용)
    market_path = OUTPUT_DIR / "step1_market_data.json"
    if market_path.exists():
        mkt = json.loads(market_path.read_text(encoding="utf-8"))
        top50 = sorted(
            [(t, d.get("name", ""), d.get("market_cap") or 0, d.get("close"))
             for t, d in mkt.items() if d.get("market_cap")],
            key=lambda x: x[2], reverse=True
        )[:50]
        cap_snap = [{"rank": i + 1, "ticker": t, "name": n, "market_cap": c, "close": p}
                    for i, (t, n, c, p) in enumerate(top50)]
        (history_dir / f"{today_str}_market_cap.json").write_text(
            json.dumps(cap_snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    total = sum(len(v) for v in portfolio.values())
    warned = sum(1 for v in portfolio.values() for e in v if e.get("disclosure_warning"))
    print(f"[generate_signals] {total}개 시그널 저장 (공시 경고: {warned}건) → {out_path}")
    print(f"[generate_signals] 스냅샷 저장 → {snap_path}")


if __name__ == "__main__":
    main()
