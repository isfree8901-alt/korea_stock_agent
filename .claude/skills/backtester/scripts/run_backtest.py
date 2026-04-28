"""
STEP 6 - 백테스트 시뮬레이션
step4_portfolio_signals.json의 현재 포트폴리오 종목들에 대해
FinanceDataReader로 과거 시계열을 로드하고 동일가중 포트폴리오 시뮬레이션을 실행한다.
출력: output/step6_simulation.json (일별 포트폴리오 가치)
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
HIST_DIR = Path(os.getenv("HISTORICAL_DATA_DIR", BASE_DIR / "data" / "historical"))
HIST_DIR.mkdir(parents=True, exist_ok=True)

BACKTEST_START = os.getenv("BACKTEST_START_DATE", "2022-01-01")
WARN_LOG = OUTPUT_DIR / "pipeline_warn.log"


def log_warn(msg: str) -> None:
    ts = datetime.now().isoformat()
    with open(WARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] [run_backtest] {msg}\n")
    print(f"WARN: {msg}", file=sys.stderr)


def load_ticker_data(ticker: str, start: str, end: str) -> pd.Series | None:
    cache = HIST_DIR / f"{ticker}.csv"
    try:
        import FinanceDataReader as fdr
        if cache.exists():
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
        else:
            df = fdr.DataReader(ticker, start, end)
            df.to_csv(cache)

        if df.empty:
            return None

        close_col = next((c for c in ["Close", "종가"] if c in df.columns), None)
        if close_col is None:
            return None

        series = df[close_col].dropna()
        series.index = pd.to_datetime(series.index)
        return series.loc[start:end]

    except Exception as e:
        log_warn(f"{ticker} 데이터 로드 실패: {e}")
        cache.unlink(missing_ok=True)
        return None


def load_benchmark(start: str, end: str) -> pd.Series | None:
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KS11", start, end)
        col = next((c for c in ["Close", "종가"] if c in df.columns), None)
        if col is None:
            return None
        s = df[col].dropna()
        s.index = pd.to_datetime(s.index)
        return s
    except Exception as e:
        log_warn(f"KOSPI 벤치마크 로드 실패: {e}")
        return None


def simulate_equal_weight(price_data: dict[str, pd.Series]) -> pd.Series:
    """동일 가중치 포트폴리오 일별 수익률 시뮬레이션."""
    if not price_data:
        return pd.Series(dtype=float)

    normalized = {}
    for ticker, series in price_data.items():
        if series.empty:
            continue
        normalized[ticker] = series / series.iloc[0]

    df = pd.DataFrame(normalized).fillna(method="ffill")
    portfolio = df.mean(axis=1)
    return portfolio * 100  # 초기 100 기준


def main():
    signals_path = OUTPUT_DIR / "step4_portfolio_signals.json"
    if not signals_path.exists():
        log_warn("step4_portfolio_signals.json 없음 — 백테스트 스킵")
        sys.exit(0)

    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    tickers = list({
        e["ticker"]
        for entries in signals.values()
        for e in entries
        if e["signal"] in ("BUY", "HOLD")
    })

    if not tickers:
        log_warn("활성 종목 없음 — 백테스트 스킵")
        sys.exit(0)

    end_date = datetime.now().strftime("%Y-%m-%d")
    print(f"[run_backtest] {len(tickers)}개 종목, 기간: {BACKTEST_START} ~ {end_date}")

    price_data = {}
    for ticker in tickers:
        series = load_ticker_data(ticker, BACKTEST_START, end_date)
        if series is not None and len(series) > 10:
            price_data[ticker] = series
        else:
            log_warn(f"{ticker}: 과거 데이터 불충분, 제외")

    if not price_data:
        log_warn("유효한 시계열 데이터 없음 — 백테스트 스킵")
        (OUTPUT_DIR / "step6_simulation.json").write_text(
            json.dumps({"error": "데이터 부족"}, ensure_ascii=False), encoding="utf-8"
        )
        sys.exit(0)

    portfolio_series = simulate_equal_weight(price_data)
    benchmark_series = load_benchmark(BACKTEST_START, end_date)

    simulation = {
        "start_date": BACKTEST_START,
        "end_date": end_date,
        "tickers": list(price_data.keys()),
        "portfolio": {str(d.date()): round(v, 4) for d, v in portfolio_series.items()},
    }
    if benchmark_series is not None:
        norm_bm = benchmark_series / benchmark_series.iloc[0] * 100
        simulation["benchmark"] = {
            str(d.date()): round(v, 4) for d, v in norm_bm.items()
        }

    out_path = OUTPUT_DIR / "step6_simulation.json"
    out_path.write_text(json.dumps(simulation, ensure_ascii=False), encoding="utf-8")
    print(f"[run_backtest] 시뮬레이션 저장 완료 → {out_path}")


if __name__ == "__main__":
    main()
