"""
STEP 5 - 섹터 평균 및 Z-score 계산
step5_indicators.json의 지표에 섹터 평균과 Z-score를 추가하여
output/step5_indicators_with_context.json 으로 저장한다.
이 파일이 qwen_infer.py --task financial_eval 의 입력이 된다.
"""
import json
import math
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))

METRIC_KEYS = ["per", "roe", "pbr", "eps_growth", "revenue_growth", "debt_ratio"]


def group_by_sector(indicators: dict) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for ticker, data in indicators.items():
        sector = data.get("sector") or "기타"
        groups.setdefault(sector, []).append({"ticker": ticker, **data})
    return groups


def mean_and_std(values: list[float]) -> tuple[float | None, float | None]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    n = len(vals)
    m = sum(vals) / n
    variance = sum((v - m) ** 2 for v in vals) / n
    return m, math.sqrt(variance) if variance > 0 else None


def compute_sector_stats(group: list[dict]) -> dict[str, dict]:
    """metric → {mean, std}"""
    stats = {}
    for key in METRIC_KEYS:
        values = [item.get(key) for item in group]
        m, s = mean_and_std(values)
        stats[key] = {"mean": m, "std": s}
    return stats


def z_score(value: float | None, mean: float | None,
            std: float | None) -> float | None:
    if value is None or mean is None or std is None or std == 0:
        return None
    return round((value - mean) / std, 3)


def enrich_with_context(indicators: dict) -> dict:
    groups = group_by_sector(indicators)
    sector_stats = {s: compute_sector_stats(g) for s, g in groups.items()}

    enriched = {}
    for ticker, data in indicators.items():
        sector = data.get("sector") or "기타"
        stats = sector_stats.get(sector, {})

        sector_avg = {k: stats[k]["mean"] for k in METRIC_KEYS if k in stats}
        z_scores = {
            k: z_score(data.get(k), stats[k]["mean"], stats[k]["std"])
            for k in METRIC_KEYS
            if k in stats
        }

        enriched[ticker] = {
            **data,
            "sector_avg": sector_avg,
            "z_scores": z_scores,
        }

    return enriched


def main():
    ind_path = OUTPUT_DIR / "step5_indicators.json"
    if not ind_path.exists():
        print("ERROR: step5_indicators.json 없음", file=sys.stderr)
        sys.exit(1)

    indicators = json.loads(ind_path.read_text(encoding="utf-8"))
    enriched = enrich_with_context(indicators)

    out_path = OUTPUT_DIR / "step5_indicators_with_context.json"
    out_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calc_sector_avg] 섹터 평균/Z-score 추가 완료 → {out_path}")


if __name__ == "__main__":
    main()
