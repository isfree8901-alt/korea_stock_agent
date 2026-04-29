"""
퀀트 소형주 스크리너 — 강환국 영상 기준
필터: 시총 하위 20%
팩터: 저PER(0.30) + 저PBR(0.20) + 고EPS성장(0.25) + 고매출성장(0.25)
출력: factor_score 내림차순 Top50
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd


_BASE_DIR = Path(__file__).resolve().parents[4]
_HIST_DIR = _BASE_DIR / "data" / "historical"

# pykrx 조회용 날짜 창 (영업일 기준 여유 있게 +15일)
_PYKRX_WINDOW = 55   # 30영업일 확보를 위해 55 캘린더일 조회


def _load_hist_csv(ticker: str) -> pd.DataFrame | None:
    """data/historical/{ticker}.csv 로드. 없으면 None."""
    path = _HIST_DIR / f"{ticker}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df
    except Exception:
        return None


def _pct_change_n_days(df: pd.DataFrame, n: int) -> float | None:
    """종가 기준 최근 n영업일 대비 등락률(%) 반환. 데이터 부족 시 None."""
    if df is None or len(df) < n + 1:
        return None
    last_close = df["Close"].iloc[-1]
    ref_close  = df["Close"].iloc[-(n + 1)]
    if ref_close == 0:
        return None
    return round((last_close / ref_close - 1) * 100, 2)


def _fetch_price_changes_pykrx(tickers: list[str]) -> dict[str, dict]:
    """
    pykrx로 최근 ~55일 OHLCV를 배치 조회해 1/7/15/30일 등락률 계산.
    히스토리컬 CSV가 없는 종목에 대한 fallback.
    실패해도 예외를 전파하지 않고 빈 dict 반환.
    """
    from datetime import datetime, timedelta
    result: dict[str, dict] = {}
    if not tickers:
        return result
    try:
        from pykrx import stock as _st
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=_PYKRX_WINDOW)
        end_str   = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")

        for ticker in tickers:
            try:
                df = _st.get_market_ohlcv_by_date(start_str, end_str, ticker)
                if df is None or df.empty:
                    continue
                df = df.reset_index()
                df.columns = [c if c != "날짜" else "Date" for c in df.columns]
                if "종가" in df.columns:
                    df = df.rename(columns={"종가": "Close"})
                elif "Close" not in df.columns:
                    continue
                df = df[df["Close"] > 0].sort_values("Date").reset_index(drop=True)
                result[ticker] = {
                    "chg_1d":  _pct_change_n_days(df, 1),
                    "chg_7d":  _pct_change_n_days(df, 7),
                    "chg_15d": _pct_change_n_days(df, 15),
                    "chg_30d": _pct_change_n_days(df, 30),
                }
            except Exception:
                continue
    except Exception:
        pass
    return result


def enrich_price_changes(results: list[dict], market_data: dict) -> list[dict]:
    """
    스크리너 결과에 전일/7일/15일/30일 등락률 필드 추가.
    우선순위: data/historical CSV → pykrx 온디맨드 → market_data.change_rate(전일만)
    """
    tickers_need_pykrx: list[str] = []

    for item in results:
        ticker = item["ticker"]
        df = _load_hist_csv(ticker)
        if df is not None and len(df) >= 32:
            item["chg_1d"]  = _pct_change_n_days(df, 1)
            item["chg_7d"]  = _pct_change_n_days(df, 7)
            item["chg_15d"] = _pct_change_n_days(df, 15)
            item["chg_30d"] = _pct_change_n_days(df, 30)
        else:
            # chg_1d: market_data.change_rate는 이미 % 단위로 저장됨 (1.17 = +1.17%)
            raw_chg = market_data.get(ticker, {}).get("change_rate", None)
            item["chg_1d"] = round(float(raw_chg), 2) if raw_chg is not None else None
            item["chg_7d"]  = None
            item["chg_15d"] = None
            item["chg_30d"] = None
            tickers_need_pykrx.append(ticker)

    # pykrx 배치 조회 (CSV 없는 종목들)
    if tickers_need_pykrx:
        pykrx_data = _fetch_price_changes_pykrx(tickers_need_pykrx)
        for item in results:
            ticker = item["ticker"]
            if ticker in pykrx_data:
                px = pykrx_data[ticker]
                # chg_1d는 market_data 값이 더 정확하므로 None일 때만 덮어쓰기
                if item["chg_1d"] is None:
                    item["chg_1d"] = px.get("chg_1d")
                item["chg_7d"]  = px.get("chg_7d")
                item["chg_15d"] = px.get("chg_15d")
                item["chg_30d"] = px.get("chg_30d")

    return results


WEIGHTS = {
    "per":            (0.30, "low"),   # 낮을수록 유리
    "pbr":            (0.20, "low"),
    "eps_growth":     (0.25, "high"),  # 높을수록 유리
    "revenue_growth": (0.25, "high"),
}

SMALL_CAP_PERCENTILE = 20   # 시총 하위 20%
TOP_N = 50


def _percentile_rank(values: list[float]) -> list[float]:
    """각 값의 백분위(0~1)를 반환. 동점은 평균 순위."""
    n = len(values)
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
            j += 1
        avg_rank = (i + j) / 2 / (n - 1) if n > 1 else 0.5
        for k in range(i, j + 1):
            ranks[sorted_vals[k][0]] = avg_rank
        i = j + 1
    return ranks


def calc_factor_scores(
    market_data: dict,
    ratios_data: dict,
) -> list[dict]:
    """
    소형주 팩터 스코어 계산 후 Top50 반환.
    market_data  : step1_market_data.json
    ratios_data  : step2_financial_ratios.json
    """
    # ── 시총 하위 20% 컷오프 ────────────────────────────────────────────────────
    all_caps = [
        info.get("market_cap", 0)
        for info in market_data.values()
        if info.get("market_cap", 0) > 0
    ]
    if not all_caps:
        return []
    cap_cutoff = float(np.percentile(all_caps, SMALL_CAP_PERCENTILE))

    # ── 소형주 후보 추출 ─────────────────────────────────────────────────────────
    candidates: list[dict] = []
    for ticker, mkt in market_data.items():
        cap = mkt.get("market_cap", 0)
        if cap <= 0 or cap > cap_cutoff:
            continue
        r = ratios_data.get(ticker, {})
        if r.get("disclosure_warning"):
            continue
        # 4개 팩터 중 최소 3개 이상 있어야 포함
        factor_vals = {
            "per":            r.get("per"),
            "pbr":            r.get("pbr"),
            "eps_growth":     r.get("eps_growth") if r.get("eps") is not None else None,
            "revenue_growth": r.get("revenue_growth"),
        }
        valid = sum(1 for v in factor_vals.values() if v is not None)
        if valid < 3:
            continue
        # PER 필터: 음수(적자) 제외
        if factor_vals["per"] is not None and factor_vals["per"] <= 0:
            continue

        candidates.append({
            "ticker":         ticker,
            "name":           mkt.get("name", ticker),
            "sector":         mkt.get("sector", "-"),
            "market_cap_억":  cap // 100_000_000,
            "close":          mkt.get("close", 0),
            "change_rate":    mkt.get("change_rate", 0),
            "per":            factor_vals["per"],
            "pbr":            factor_vals["pbr"],
            "eps_growth":     factor_vals["eps_growth"],
            "revenue_growth": factor_vals["revenue_growth"],
            "roe":            r.get("roe"),
        })

    if not candidates:
        return []

    # ── 팩터별 백분위 계산 ──────────────────────────────────────────────────────
    factor_keys = list(WEIGHTS.keys())

    for key, (weight, direction) in WEIGHTS.items():
        vals = [c[key] if c[key] is not None else float("nan") for c in candidates]
        # NaN 제외 후 유효 인덱스만 랭킹
        valid_idx = [i for i, v in enumerate(vals) if not (v != v)]  # NaN check
        if not valid_idx:
            for c in candidates:
                c[f"_rank_{key}"] = 0.5
            continue
        valid_vals = [vals[i] for i in valid_idx]
        ranks = _percentile_rank(valid_vals)
        rank_map = {valid_idx[i]: ranks[i] for i in range(len(valid_idx))}
        for i, c in enumerate(candidates):
            r = rank_map.get(i, 0.5)
            c[f"_rank_{key}"] = (1.0 - r) if direction == "low" else r

    # ── 복합 팩터 점수 계산 ─────────────────────────────────────────────────────
    for c in candidates:
        score = 0.0
        total_w = 0.0
        for key, (weight, _) in WEIGHTS.items():
            rank_key = f"_rank_{key}"
            if rank_key in c:
                score   += c[rank_key] * weight
                total_w += weight
        c["factor_score"] = round(score / total_w if total_w > 0 else 0.0, 4)

    # 임시 랭킹 컬럼 제거
    for c in candidates:
        for key in factor_keys:
            c.pop(f"_rank_{key}", None)

    # ── Top50 반환 ───────────────────────────────────────────────────────────────
    candidates.sort(key=lambda x: x["factor_score"], reverse=True)
    return candidates[:TOP_N]


if __name__ == "__main__":
    import json, os
    from pathlib import Path
    base = Path(__file__).resolve().parents[4]
    out  = Path(os.getenv("OUTPUT_DIR", base / "output"))
    mkt  = json.loads((out / "step1_market_data.json").read_text(encoding="utf-8"))
    rat  = json.loads((out / "step2_financial_ratios.json").read_text(encoding="utf-8"))
    results = calc_factor_scores(mkt, rat)
    print(f"소형주 퀀트 스크리너 Top{len(results)}")
    print(f"{'순위':>4} {'종목명':>12} {'시총(억)':>8} {'PER':>6} {'PBR':>5} {'EPS성장':>8} {'매출성장':>8} {'팩터점수':>8}")
    print("-" * 70)
    for i, r in enumerate(results, 1):
        def _s(v): return f"{v:.1f}" if v is not None else "N/A"
        print(f"{i:>4} {r['name']:>12} {r['market_cap_억']:>8,} "
              f"{_s(r['per']):>6} {_s(r['pbr']):>5} "
              f"{_s(r['eps_growth']):>8} {_s(r['revenue_growth']):>8} "
              f"{r['factor_score']:>8.4f}")
