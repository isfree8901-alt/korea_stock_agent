"""
STEP 5 - 재무지표 계산
TOP20 종목에 대해 PER/ROE/PBR/EPS성장률/매출성장률/부채비율을 계산한다.
출력: output/step5_indicators.json
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
        f.write(f"[{ts}] [calc_indicators] {msg}\n")


def safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    try:
        return float(a) / float(b)
    except (TypeError, ZeroDivisionError):
        return None


def compute_per(close: float | None, eps: float | None) -> float | None:
    if not eps or eps <= 0:
        return None
    return safe_div(close, eps)


def compute_pbr(close: float | None, equity: float | None,
                shares: float | None) -> float | None:
    bvps = safe_div(equity, shares)
    if not bvps or bvps <= 0:
        return None
    return safe_div(close, bvps)


def compute_roe(net_income: float | None, equity: float | None) -> float | None:
    ratio = safe_div(net_income, equity)
    return ratio * 100 if ratio is not None else None


def compute_growth(current: float | None, prev: float | None) -> float | None:
    if current is None or prev is None or prev == 0:
        return None
    return (current - prev) / abs(prev) * 100


def compute_debt_ratio(total_debt: float | None, equity: float | None) -> float | None:
    ratio = safe_div(total_debt, equity)
    return ratio * 100 if ratio is not None else None


def compute_all_indicators(tickers: list[str], market_data: dict,
                            financial_data: dict) -> dict:
    result = {}
    for ticker in tickers:
        mkt = market_data.get(ticker, {})
        fin = financial_data.get(ticker, {})

        close = mkt.get("close")
        shares = mkt.get("shares")  # fetch_krx에서 수집한 상장주식수
        equity = fin.get("total_equity")
        net_income = fin.get("net_income")
        revenue = fin.get("revenue")
        total_debt = fin.get("total_debt")
        eps = fin.get("eps") or fin.get("eps_raw")
        disclosure_warning = fin.get("disclosure_warning", False)

        per = fin.get("per") or compute_per(close, eps)
        roe = fin.get("roe") or compute_roe(net_income, equity)
        pbr = fin.get("pbr") or compute_pbr(close, equity, shares)
        debt_ratio = fin.get("debt_ratio") or compute_debt_ratio(total_debt, equity)

        # 성장률: 전분기 데이터가 없으면 None
        eps_growth = compute_growth(eps, fin.get("eps_prev"))
        revenue_growth = compute_growth(revenue, fin.get("revenue_prev"))

        if not any([per, roe, pbr, eps_growth, revenue_growth, debt_ratio]):
            disclosure_warning = True
            log_warn(f"{ticker}: 모든 재무지표 계산 불가")

        result[ticker] = {
            "name": mkt.get("name", fin.get("corp_name", "")),
            "sector": mkt.get("sector"),
            "close": close,
            "per": per,
            "roe": roe,
            "pbr": pbr,
            "eps_growth": eps_growth,
            "revenue_growth": revenue_growth,
            "debt_ratio": debt_ratio,
            "disclosure_warning": disclosure_warning,
        }

    return result


def main():
    top20_path = OUTPUT_DIR / "top20_tickers.json"
    market_path = OUTPUT_DIR / "step1_market_data.json"
    financial_path = OUTPUT_DIR / "step1_financial_data.json"

    for p in [top20_path, market_path]:
        if not p.exists():
            print(f"ERROR: {p} 없음", file=sys.stderr)
            sys.exit(1)

    top20 = json.loads(top20_path.read_text(encoding="utf-8"))
    market_data = json.loads(market_path.read_text(encoding="utf-8"))
    financial_data = {}
    if financial_path.exists():
        financial_data = json.loads(financial_path.read_text(encoding="utf-8"))

    # 포트폴리오 종목도 포함
    signals_path = OUTPUT_DIR / "step4_portfolio_signals.json"
    portfolio_tickers: set[str] = set()
    if signals_path.exists():
        signals = json.loads(signals_path.read_text(encoding="utf-8"))
        for entries in signals.values():
            for e in entries:
                portfolio_tickers.add(e["ticker"])

    all_tickers = list(set(top20) | portfolio_tickers)
    indicators = compute_all_indicators(all_tickers, market_data, financial_data)

    out_path = OUTPUT_DIR / "step5_indicators.json"
    out_path.write_text(json.dumps(indicators, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calc_indicators] {len(indicators)}개 종목 지표 계산 완료 → {out_path}")


if __name__ == "__main__":
    main()
