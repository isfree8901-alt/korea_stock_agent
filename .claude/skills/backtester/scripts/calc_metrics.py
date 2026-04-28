"""
STEP 6 - 백테스트 성과 지표 계산
step6_simulation.json의 일별 포트폴리오 가치로부터
총수익률/승률/MDD/리밸런싱횟수/초과수익을 계산한다.
출력: output/step6_backtest_result.json
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))


def compute_total_return(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return round((values[-1] - values[0]) / values[0] * 100, 2)


def compute_win_rate(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    daily_returns = [values[i] - values[i - 1] for i in range(1, len(values))]
    positive = sum(1 for r in daily_returns if r > 0)
    return round(positive / len(daily_returns) * 100, 2)


def compute_mdd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    max_dd = 0.0
    peak = values[0]
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def count_rebalancing(signals_path: Path) -> int:
    if not signals_path.exists():
        return 0
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    return sum(
        1
        for entries in signals.values()
        for e in entries
        if e.get("signal") in ("BUY", "SELL")
    )


def main():
    sim_path = OUTPUT_DIR / "step6_simulation.json"
    if not sim_path.exists():
        print("WARN: step6_simulation.json 없음 — 백테스트 결과 없음", file=sys.stderr)
        sys.exit(0)

    sim = json.loads(sim_path.read_text(encoding="utf-8"))

    if "error" in sim:
        result = {
            "period": f"{sim.get('start_date', '')} ~ {sim.get('end_date', '')}",
            "error": sim["error"],
            "total_return": None,
            "win_rate": None,
            "mdd": None,
            "rebalancing_count": None,
            "benchmark_return": None,
            "excess_return": None,
        }
    else:
        portfolio_vals = list(sim.get("portfolio", {}).values())
        benchmark_vals = list(sim.get("benchmark", {}).values())

        total_return = compute_total_return(portfolio_vals)
        win_rate = compute_win_rate(portfolio_vals)
        mdd = compute_mdd(portfolio_vals)
        rebalancing_count = count_rebalancing(OUTPUT_DIR / "step4_portfolio_signals.json")
        benchmark_return = compute_total_return(benchmark_vals) if benchmark_vals else None
        excess = (
            round(total_return - benchmark_return, 2)
            if benchmark_return is not None
            else None
        )

        result = {
            "period": f"{sim.get('start_date', '')} ~ {sim.get('end_date', '')}",
            "tickers": sim.get("tickers", []),
            "total_return": total_return,
            "win_rate": win_rate,
            "mdd": mdd,
            "rebalancing_count": rebalancing_count,
            "benchmark_return": benchmark_return,
            "excess_return": excess,
        }

    out_path = OUTPUT_DIR / "step6_backtest_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calc_metrics] 저장 완료 → {out_path}")
    if "error" not in result:
        print(f"  총수익률: {result['total_return']}%")
        print(f"  승률: {result['win_rate']}%")
        print(f"  MDD: {result['mdd']}%")
        print(f"  초과수익: {result['excess_return']}%")


if __name__ == "__main__":
    main()
