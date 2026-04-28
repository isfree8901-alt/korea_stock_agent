"""
STEP 3 - 재무비율 계산
step1_financial_data.json(로우 DART 데이터) + step1_market_data.json(주가/주식수)에서
PER, ROE, PBR, EPS, EPS성장률, 매출성장률, 부채비율을 계산한다.

계산식:
  EPS       = eps_raw (DART 직접 제공) or net_income / shares
  PER       = close / EPS          (EPS > 0 일 때만)
  BPS       = total_equity / shares
  PBR       = close / BPS          (BPS > 0 일 때만)
  ROE       = net_income / total_equity × 100
  EPS성장률 = (EPS - EPS_prev) / |EPS_prev| × 100
  매출성장률 = (revenue - revenue_prev) / |revenue_prev| × 100
  부채비율  = total_debt / total_equity × 100

출력: output/step2_financial_ratios.json
  { "ticker": {per, roe, pbr, eps, eps_growth, revenue_growth, debt_ratio,
               period, corp_name, disclosure_warning, missing_fields: [...]} }
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
    with open(WARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] [calc_ratios] {msg}\n")


def safe_div(a, b) -> float | None:
    if a is None or b is None:
        return None
    try:
        b = float(b)
        return float(a) / b if b != 0 else None
    except (TypeError, ValueError):
        return None


def pct_growth(current, prev) -> float | None:
    if current is None or prev is None or prev == 0:
        return None
    return round((float(current) - float(prev)) / abs(float(prev)) * 100, 2)


def calc_ticker(fin: dict, close: float | None, shares: float | None) -> dict:
    """단일 종목 재무비율 계산. missing_fields에 계산 불가 항목 기록."""
    missing = []

    # ── EPS ───────────────────────────────────────────────
    eps_raw = fin.get("eps_raw") or fin.get("eps")
    eps_prev_raw = fin.get("eps_raw_prev") or fin.get("eps_prev")

    if eps_raw is not None:
        eps = eps_raw
    elif fin.get("net_income") is not None and shares:
        eps = safe_div(fin["net_income"], shares)
    else:
        eps = None
        missing.append("eps(순이익/발행주식수 모두 없음)")

    if eps_prev_raw is not None:
        eps_prev = eps_prev_raw
    elif fin.get("net_income_prev") is not None and shares:
        eps_prev = safe_div(fin["net_income_prev"], shares)
    else:
        eps_prev = None

    # ── PER ──────────────────────────────────────────────
    if close and eps and eps > 0:
        per = round(close / eps, 2)
    else:
        per = None
        if eps is None:
            pass  # already in missing
        elif eps <= 0:
            missing.append("per(EPS 음수—적자기업)")

    # ── BPS / PBR ─────────────────────────────────────────
    equity = fin.get("total_equity")
    if equity and shares:
        bps = safe_div(equity, shares)
        pbr = round(safe_div(close, bps), 2) if (bps and bps > 0 and close) else None
    else:
        bps = None
        pbr = None
        if not shares:
            missing.append("pbr(상장주식수 없음)")
        if not equity:
            missing.append("pbr(자본총계 없음)")

    # ── ROE ──────────────────────────────────────────────
    net_income = fin.get("net_income")
    if net_income is not None and equity and equity != 0:
        raw_roe = net_income / equity * 100
        if abs(raw_roe) > 500:
            # 자본잠식 수준의 극단값 — 계산 자체는 맞지만 섹터 비교에 의미 없음
            roe = None
            missing.append(f"roe(자본잠식 수준 극단값 {raw_roe:.0f}%—제외)")
        else:
            roe = round(raw_roe, 2)
    else:
        roe = None
        if net_income is None:
            missing.append("roe(순이익 없음)")

    # ── EPS 성장률 ────────────────────────────────────────
    eps_growth = pct_growth(eps, eps_prev) if (eps is not None and eps_prev is not None) else None

    # ── 매출 성장률 ───────────────────────────────────────
    revenue = fin.get("revenue")
    revenue_prev = fin.get("revenue_prev")
    rev_growth = pct_growth(revenue, revenue_prev)
    if rev_growth is None and (revenue is None or revenue_prev is None):
        missing.append("매출성장률(전기 매출 없음)")

    # ── 부채비율 ──────────────────────────────────────────
    total_debt = fin.get("total_debt")
    if total_debt is not None and equity and equity != 0:
        raw_dr = total_debt / equity * 100
        if raw_dr < 0:
            # 자본잠식: 자본총계가 음수 → 부채비율 음수는 무의미
            debt_ratio = None
            missing.append(f"부채비율(자본잠식—자본총계 음수, 제외)")
        elif raw_dr > 5000:
            # 완전자본잠식 직전 극단값
            debt_ratio = None
            missing.append(f"부채비율(극단값 {raw_dr:.0f}%—제외)")
        else:
            debt_ratio = round(raw_dr, 2)
    else:
        debt_ratio = None
        if total_debt is None:
            missing.append("부채비율(부채총계 없음)")

    return {
        "eps": round(eps, 2) if eps is not None else None,
        "per": per,
        "pbr": pbr,
        "roe": roe,
        "eps_growth": eps_growth,
        "revenue_growth": rev_growth,
        "debt_ratio": debt_ratio,
        "period": fin.get("period"),
        "corp_name": fin.get("corp_name", ""),
        "disclosure_warning": fin.get("disclosure_warning", False),
        "missing_fields": missing,
    }


def decode_period(period_str: str | None) -> str:
    """'2025_11011' → '2025년 사업보고서(연간)' 형식으로 변환."""
    if not period_str:
        return "-"
    labels = {"11011": "사업보고서(연간)", "11012": "반기보고서",
               "11013": "1분기보고서", "11014": "3분기보고서"}
    parts = period_str.split("_")
    if len(parts) == 2:
        return f"{parts[0]}년 {labels.get(parts[1], parts[1])}"
    return period_str


def main():
    fin_path = OUTPUT_DIR / "step1_financial_data.json"
    mkt_path = OUTPUT_DIR / "step1_market_data.json"

    if not mkt_path.exists():
        print("ERROR: step1_market_data.json 없음", file=sys.stderr)
        sys.exit(1)

    market_data: dict = {}
    if mkt_path.exists():
        market_data = json.loads(mkt_path.read_text(encoding="utf-8"))

    financial_data: dict = {}
    if fin_path.exists():
        financial_data = json.loads(fin_path.read_text(encoding="utf-8"))

    # 모든 KRX 종목 대상 (시장 데이터 기준)
    result: dict = {}
    no_fin_count = 0

    for ticker, mkt in market_data.items():
        fin = financial_data.get(ticker, {})
        close = mkt.get("close")
        shares = mkt.get("shares")

        if not fin or fin.get("disclosure_warning"):
            no_fin_count += 1
            result[ticker] = {
                "eps": None, "per": None, "pbr": None, "roe": None,
                "eps_growth": None, "revenue_growth": None, "debt_ratio": None,
                "period": fin.get("period") if fin else None,
                "corp_name": fin.get("corp_name", mkt.get("name", "")) if fin else mkt.get("name", ""),
                "disclosure_warning": True,
                "missing_fields": ["DART 공시 데이터 없음"],
            }
            continue

        ratios = calc_ticker(fin, close, shares)
        result[ticker] = ratios

    out_path = OUTPUT_DIR / "step2_financial_ratios.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    total = len(result)
    per_ok = sum(1 for v in result.values() if v.get("per") is not None)
    roe_ok = sum(1 for v in result.values() if v.get("roe") is not None)
    pbr_ok = sum(1 for v in result.values() if v.get("pbr") is not None)
    warn = sum(1 for v in result.values() if v.get("disclosure_warning"))

    # DART 기준 기간
    sample_period = next(
        (v.get("period") for v in result.values() if v.get("period")), None
    )
    print(f"[calc_ratios] {total}개 종목 재무비율 계산 완료 → {out_path}")
    print(f"  기준 기간: {decode_period(sample_period)}")
    print(f"  PER 계산 가능: {per_ok}개 ({per_ok/total*100:.1f}%)")
    print(f"  ROE 계산 가능: {roe_ok}개 ({roe_ok/total*100:.1f}%)")
    print(f"  PBR 계산 가능: {pbr_ok}개 ({pbr_ok/total*100:.1f}%)")
    print(f"  공시 경고(데이터 없음): {warn}개")


if __name__ == "__main__":
    main()
