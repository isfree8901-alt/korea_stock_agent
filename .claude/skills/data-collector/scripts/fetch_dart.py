"""
STEP 1 - DART OpenAPI 재무 데이터 수집
분기 재무제표에서 PER/ROE/PBR/EPS/매출/부채비율 계산용 원천값을 수집한다.
개별 종목 실패는 non-critical (스킵 + disclosure_warning 플래그 + 로그).
"""
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DART_BASE = "https://opendart.fss.or.kr/api"
WARN_LOG = OUTPUT_DIR / "pipeline_warn.log"


def log_warn(msg: str) -> None:
    timestamp = datetime.now().isoformat()
    with open(WARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [fetch_dart] {msg}\n")
    print(f"WARN: {msg}", file=sys.stderr)


def get_api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        raise ValueError("DART_API_KEY 환경변수가 설정되지 않았습니다.")
    return key


def get_corp_codes(api_key: str) -> dict:
    """stock_code → corp_code 매핑. 캐시 파일 존재 시 재사용."""
    cache_path = DATA_DIR / "corp_codes.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    url = f"{DART_BASE}/corpCode.xml"
    resp = requests.get(url, params={"crtfc_key": api_key}, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        xml_data = z.read(z.namelist()[0]).decode("utf-8")

    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_data)
    mapping = {}
    for corp in root.findall("list"):
        stock_code = (corp.findtext("stock_code") or "").strip()
        corp_code = (corp.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    cache_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    print(f"[fetch_dart] corp_code 매핑 {len(mapping)}개 캐시 저장")
    return mapping


def get_latest_report_code() -> tuple[str, str]:
    """현재 날짜 기준 가장 최근 분기를 반환: (연도, 분기코드)"""
    now = datetime.now()
    year = str(now.year)
    month = now.month
    if month >= 11:
        return year, "11014"   # 3분기보고서
    elif month >= 8:
        return year, "11013"   # 반기보고서 (1분기)
    elif month >= 5:
        return str(now.year - 1), "11011"  # 전년 사업보고서
    else:
        return str(now.year - 1), "11011"


def fetch_single_financial(corp_code: str, year: str,
                            reprt_code: str, api_key: str,
                            fs_div: str = "CFS") -> list:
    url = f"{DART_BASE}/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return []
    return data.get("list", [])


ACCOUNT_MAP = {
    # 매출
    "매출액": "revenue",
    "영업수익": "revenue",
    "수익(매출액)": "revenue",
    # 영업이익
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    # 순이익 (기업마다 계정명이 다양함)
    "당기순이익": "net_income",
    "당기순손익": "net_income",
    "당기순이익(손실)": "net_income",
    "당기순손실": "net_income",            # 적자기업 표기 (SK이노베이션·SKC·롯데케미칼 등)
    "연결당기순이익": "net_income",
    "연결당기순이익(손실)": "net_income",  # NC소프트 등
    "지배기업소유주귀속당기순이익": "net_income",
    "지배주주순이익": "net_income",
    # 자본·부채·자산
    "자본총계": "total_equity",
    "부채총계": "total_debt",
    "부채 총계": "total_debt",             # 공백 포함 표기 (LG화학·SKC 등)
    "자산총계": "total_assets",
    "자산 총계": "total_assets",           # 공백 포함 표기
    # EPS
    "주당순이익": "eps_raw",
    "기본주당이익(손실)": "eps_raw",
    "기본주당순이익": "eps_raw",
    "보통주기본주당손실": "eps_raw",        # 적자기업 EPS
    "보통주기본주당이익(손실)": "eps_raw",
}


def parse_amount(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(val.replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def parse_financials(raw_list: list) -> dict:
    extracted = {}
    for item in raw_list:
        acct = item.get("account_nm", "")
        key = ACCOUNT_MAP.get(acct)
        if key:
            if key not in extracted:
                amount = parse_amount(item.get("thstrm_amount", ""))
                if amount is not None:
                    extracted[key] = amount
            # 전기(전년도) 값도 수집 → 성장률 계산용
            prev_key = key + "_prev"
            if prev_key not in extracted:
                prev_amount = parse_amount(item.get("frmtrm_amount", ""))
                if prev_amount is not None:
                    extracted[prev_key] = prev_amount
    return extracted


def compute_ratios(financials: dict, close: float | None,
                   shares: float | None) -> dict:
    """close(원), shares(주수)를 받아 PER/ROE/PBR 파생 지표 계산."""
    result = dict(financials)
    equity = financials.get("total_equity")
    net_income = financials.get("net_income")
    revenue = financials.get("revenue")
    total_debt = financials.get("total_debt")

    eps = None
    if financials.get("eps_raw") is not None:
        eps = financials["eps_raw"]
    elif net_income and shares and shares > 0:
        eps = net_income / shares

    roe = (net_income / equity * 100) if (net_income and equity and equity != 0) else None
    bvps = (equity / shares) if (equity and shares and shares > 0) else None
    pbr = (close / bvps) if (close and bvps and bvps > 0) else None
    per = (close / eps) if (close and eps and eps > 0) else None
    debt_ratio = (total_debt / equity * 100) if (total_debt and equity and equity != 0) else None

    result.update({
        "eps": eps,
        "roe": roe,
        "pbr": pbr,
        "per": per,
        "debt_ratio": debt_ratio,
    })
    return result


def main():
    api_key = get_api_key()
    year, reprt_code = get_latest_report_code()

    market_path = OUTPUT_DIR / "step1_market_data.json"
    if not market_path.exists():
        log_warn("step1_market_data.json 없음 — fetch_krx.py를 먼저 실행하세요.")
        sys.exit(0)

    market_data = json.loads(market_path.read_text(encoding="utf-8"))
    corp_map = get_corp_codes(api_key)

    result = {}
    tickers = list(market_data.keys())
    print(f"[fetch_dart] {len(tickers)}개 종목 재무 수집 시작 (기준: {year} {reprt_code})")

    for i, ticker in enumerate(tickers):
        if i % 100 == 0:
            print(f"  진행: {i}/{len(tickers)}")

        corp_code = corp_map.get(ticker)
        if not corp_code:
            result[ticker] = {"disclosure_warning": True, "reason": "corp_code 없음"}
            continue

        try:
            raw = fetch_single_financial(corp_code, year, reprt_code, api_key, "CFS")
            if not raw:
                # CFS(연결) 없으면 IFS(개별) 폴백
                raw = fetch_single_financial(corp_code, year, reprt_code, api_key, "IFS")
            if not raw:
                result[ticker] = {"disclosure_warning": True, "reason": "재무 데이터 없음"}
                log_warn(f"{ticker}: 재무 데이터 응답 없음")
                continue

            financials = parse_financials(raw)
            # CFS에서 net_income 누락 시 IFS로 보완
            if "net_income" not in financials:
                raw_ifs = fetch_single_financial(corp_code, year, reprt_code, api_key, "IFS")
                if raw_ifs:
                    ifs_fin = parse_financials(raw_ifs)
                    for key in ("net_income", "net_income_prev", "operating_income",
                                "operating_income_prev", "eps_raw"):
                        if key not in financials and key in ifs_fin:
                            financials[key] = ifs_fin[key]
            close = market_data[ticker].get("close")
            # 상장주식수가 없으면 None (PBR/EPS 계산 불가)
            shares = None  # pykrx cap 데이터에서 가져오려면 별도 파일 필요
            ratios = compute_ratios(financials, close, shares)
            ratios["disclosure_warning"] = False
            ratios["period"] = f"{year}_{reprt_code}"
            ratios["corp_name"] = market_data[ticker].get("name", "")
            result[ticker] = ratios

            time.sleep(0.05)  # DART API 속도 제한 대응

        except Exception as e:
            log_warn(f"{ticker}: {e}")
            result[ticker] = {"disclosure_warning": True, "reason": str(e)}

    out_path = OUTPUT_DIR / "step1_financial_data.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    warned = sum(1 for v in result.values() if v.get("disclosure_warning"))
    print(f"[fetch_dart] 저장 완료 → {out_path} (경고 종목: {warned}개)")


if __name__ == "__main__":
    main()
