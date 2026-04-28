"""
퀀트 소형주 스크리너 — 강환국 영상 기준
필터: 시총 하위 20%
팩터: 저PER(0.30) + 저PBR(0.20) + 고EPS성장(0.25) + 고매출성장(0.25)
출력: factor_score 내림차순 Top50
"""
from __future__ import annotations

import numpy as np


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
