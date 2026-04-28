"""
STEP 1 - KRX 시장 데이터 수집
pykrx 배치 API로 전일 종가/시가총액/거래량/섹터를 수집하여
output/step1_market_data.json 으로 저장한다.
성공 기준: 종목 수 > 2000, 미달 시 exit(1) (파이프라인 전체 중단).
"""
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

from pykrx import stock
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ERROR_LOG = OUTPUT_DIR / "pipeline_error.log"


def log_error(msg: str) -> None:
    timestamp = datetime.now().isoformat()
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [fetch_krx] {msg}\n")
    print(f"ERROR: {msg}", file=sys.stderr)


def get_last_trading_date() -> str:
    """최근 거래일(T-1)을 YYYYMMDD 문자열로 반환한다.
    공휴일·주말에는 pykrx가 0으로 채운 DataFrame을 반환하므로
    종가 > 0 인 종목이 100개 이상인 날만 유효 거래일로 인정한다."""
    today = datetime.now()
    for delta in range(1, 14):
        candidate = today - timedelta(days=delta)
        date_str = candidate.strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_ticker(date_str, market="ALL")
            if df is not None and (df["종가"] > 0).sum() > 100:
                return date_str
        except Exception:
            continue
    raise RuntimeError("최근 14일 내 유효한 거래일을 찾지 못했습니다.")


def fetch_ohlcv(date: str):
    return stock.get_market_ohlcv_by_ticker(date, market="ALL")


def fetch_market_cap(date: str):
    return stock.get_market_cap_by_ticker(date, market="ALL")


def fetch_sector_classification(date: str):
    import pandas as pd
    kospi = stock.get_market_sector_classifications(date, market="KOSPI")
    kosdaq = stock.get_market_sector_classifications(date, market="KOSDAQ")
    return pd.concat([kospi, kosdaq])


def fetch_ticker_names(date: str) -> dict:
    tickers = stock.get_market_ticker_list(date, market="ALL")
    return {t: stock.get_market_ticker_name(t) for t in tickers}


def build_output(ohlcv, cap, sector, names: dict) -> dict:
    result = {}
    for ticker in ohlcv.index:
        try:
            close = int(ohlcv.at[ticker, "종가"]) if "종가" in ohlcv.columns else None
            volume = int(ohlcv.at[ticker, "거래량"]) if "거래량" in ohlcv.columns else None
            change_rate = float(ohlcv.at[ticker, "등락률"]) if "등락률" in ohlcv.columns else None
            trading_value = int(ohlcv.at[ticker, "거래대금"]) if "거래대금" in ohlcv.columns else None
        except (KeyError, ValueError):
            close, volume, change_rate, trading_value = None, None, None, None

        try:
            mkt_cap = int(cap.at[ticker, "시가총액"]) if "시가총액" in cap.columns else None
        except (KeyError, ValueError):
            mkt_cap = None

        try:
            shares = int(cap.at[ticker, "상장주식수"]) if "상장주식수" in cap.columns else None
        except (KeyError, ValueError):
            shares = None

        sector_name = None
        if ticker in sector.index:
            for col in ["업종명", "sector", "Sector"]:
                if col in sector.columns:
                    sector_name = str(sector.at[ticker, col])
                    break

        result[ticker] = {
            "name": names.get(ticker, ""),
            "close": close,
            "volume": volume,
            "trading_value": trading_value,
            "change_rate": change_rate,
            "market_cap": mkt_cap,
            "shares": shares,
            "sector": sector_name,
        }
    return result


def validate_and_save(data: dict, output_path: Path) -> None:
    if len(data) <= 2000:
        msg = f"수집된 종목 수 {len(data)}개 — 2000개 초과 필요. 파이프라인 중단."
        log_error(msg)
        sys.exit(1)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch_krx] {len(data)}개 종목 저장 완료 → {output_path}")


def main():
    try:
        date = get_last_trading_date()
        print(f"[fetch_krx] 수집 기준일: {date}")

        ohlcv = fetch_ohlcv(date)
        cap = fetch_market_cap(date)
        sector = fetch_sector_classification(date)
        names = fetch_ticker_names(date)

        data = build_output(ohlcv, cap, sector, names)
        validate_and_save(data, OUTPUT_DIR / "step1_market_data.json")

    except SystemExit:
        raise
    except Exception as e:
        log_error(f"예상치 못한 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
