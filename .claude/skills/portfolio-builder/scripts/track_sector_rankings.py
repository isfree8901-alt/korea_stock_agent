"""
STEP 2 - 섹터별 시총 Top 10 추적
각 KRX 섹터에서 시총 상위 10개 종목을 선별하고
1일/7일/15일/30일 전 스냅샷과 비교하여 편입/제외/순위변동을 감지한다.
출력:
  data/portfolio_history/YYYYMMDD_sector_rankings.json  (스냅샷)
  output/step2_sector_rankings.json                     (변동 포함 최종)
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
HISTORY_DIR = BASE_DIR / "data" / "portfolio_history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

COMPARE_DAYS = [1, 7, 15, 30]


def load_snapshot(target_date: date) -> dict[str, list]:
    """날짜별 스냅샷 로드. 없으면 {} 반환."""
    path = HISTORY_DIR / f"{target_date.strftime('%Y%m%d')}_sector_rankings.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def find_nearest_snapshot(days_ago: int) -> tuple[date | None, dict]:
    """days_ago 전 가장 가까운 스냅샷 탐색 (최대 ±4일)."""
    base = date.today() - timedelta(days=days_ago)
    for delta in range(0, 5):
        for d in [base - timedelta(days=delta), base + timedelta(days=delta)]:
            snap = load_snapshot(d)
            if snap:
                return d, snap
    return None, {}


def build_sector_top10(market_data: dict) -> dict[str, list]:
    """KRX 섹터별 시총 Top 10 추출."""
    sector_tickers: dict[str, list] = {}
    for ticker, info in market_data.items():
        sector = info.get("sector")
        mkt_cap = info.get("market_cap")
        if sector and mkt_cap:
            sector_tickers.setdefault(sector, []).append({
                "ticker": ticker,
                "name": info.get("name", ""),
                "market_cap": mkt_cap,
                "close": info.get("close"),
                "change_rate": info.get("change_rate"),
            })

    result = {}
    for sector, tickers in sector_tickers.items():
        top10 = sorted(tickers, key=lambda x: x["market_cap"], reverse=True)[:10]
        for i, item in enumerate(top10):
            item["rank"] = i + 1
        result[sector] = top10
    return result


def compare_rankings(
    current: list, snap: list
) -> tuple[list[str], list[str], dict[str, int]]:
    """
    현재 vs 과거 스냅샷 비교.
    Returns: (신규편입 티커 목록, 제외된 티커 목록, 티커→순위변동 dict)
    순위변동 양수 = 상승(이전순위가 더 낮았음), 음수 = 하락
    """
    cur_map = {item["ticker"]: item["rank"] for item in current}
    snap_map = {item["ticker"]: item["rank"] for item in snap}

    cur_tickers = set(cur_map.keys())
    snap_tickers = set(snap_map.keys())

    new_entries = list(cur_tickers - snap_tickers)
    removed = list(snap_tickers - cur_tickers)

    rank_delta: dict[str, int] = {}
    for ticker in cur_tickers & snap_tickers:
        rank_delta[ticker] = snap_map[ticker] - cur_map[ticker]  # 양수=상승

    return new_entries, removed, rank_delta


def status_label(ticker: str, new_entries: list, removed_prev: list, rank_delta: dict) -> str:
    if ticker in new_entries:
        return "🆕 신규편입"
    delta = rank_delta.get(ticker, 0)
    if delta > 0:
        return f"🔺 +{delta}"
    if delta < 0:
        return f"🔻 {delta}"
    return "➖ 유지"


def main():
    market_path = OUTPUT_DIR / "step1_market_data.json"
    if not market_path.exists():
        print("ERROR: step1_market_data.json 없음", file=sys.stderr)
        sys.exit(1)

    market_data = json.loads(market_path.read_text(encoding="utf-8"))
    today = date.today()
    today_str = today.strftime("%Y%m%d")

    current_top10 = build_sector_top10(market_data)

    # 오늘 스냅샷 저장 (rank 포함)
    snap_path = HISTORY_DIR / f"{today_str}_sector_rankings.json"
    snap_path.write_text(
        json.dumps(current_top10, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 히스토리 스냅샷 로드
    historical: dict[int, tuple[date | None, dict]] = {}
    for days in COMPARE_DAYS:
        snap_date, snap = find_nearest_snapshot(days)
        historical[days] = (snap_date, snap)

    # 섹터별 비교 결과 생성
    result = {}
    for sector, cur_items in sorted(current_top10.items()):
        sector_result: dict = {"as_of": today_str, "top10": [], "changes": {}}

        for days in COMPARE_DAYS:
            snap_date, snap = historical[days]
            past_items = snap.get(sector, [])
            new_entries, removed, rank_delta = compare_rankings(cur_items, past_items)

            key = f"{days}d"
            snap_map = {item["ticker"]: item.get("rank", 99) for item in past_items}

            sector_result["changes"][key] = {
                "snap_date": snap_date.strftime("%Y%m%d") if snap_date else None,
                "new_entries": new_entries,
                "removed": removed,
            }

            # Top10 각 종목에 기간별 순위 변동 추가
            if days == COMPARE_DAYS[0]:
                for item in cur_items:
                    t = item["ticker"]
                    row = {
                        "rank": item["rank"],
                        "ticker": t,
                        "name": item["name"],
                        "market_cap_억": round(item["market_cap"] / 1e8),
                        "close": item["close"],
                        "change_rate": item["change_rate"],
                    }
                    sector_result["top10"].append(row)

            for row in sector_result["top10"]:
                t = row["ticker"]
                if days == 1:
                    new_e, _, rd = compare_rankings(cur_items, past_items)
                    row[f"변동_{key}"] = status_label(t, new_entries, removed, rank_delta)
                else:
                    row[f"변동_{key}"] = status_label(t, new_entries, removed, rank_delta)

        result[sector] = sector_result

    out_path = OUTPUT_DIR / "step2_sector_rankings.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    total_sectors = len(result)
    total_stocks = sum(len(v["top10"]) for v in result.values())
    print(f"[track_sector_rankings] {total_sectors}개 섹터 × Top10 = {total_stocks}개 종목 → {out_path}")
    print(f"[track_sector_rankings] 스냅샷 저장 → {snap_path}")

    # 오늘 기준 변동 요약 출력
    print("\n▼ 1일 전 대비 주요 변동 (신규편입/제외)")
    for sector, v in sorted(result.items()):
        ch = v["changes"].get("1d", {})
        if ch.get("new_entries") or ch.get("removed"):
            print(f"  [{sector}] 편입: {ch.get('new_entries',[])} | 제외: {ch.get('removed',[])}")


if __name__ == "__main__":
    main()
