"""
STEP 2 - 섹터 종합 점수 산출
세 가지 객관적 신호를 결합하여 섹터별 스코어카드를 생성한다.
  A. 뉴스 감성 점수  (step2_sentiment_scores.json)
  B. 주가 모멘텀     (step1_market_data.json  — change_rate, advancing_ratio)
  C. 재무 개선 추세  (step1_financial_data.json — revenue_growth, profit_improvement)
출력: step2_sector_scores.json
"""
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
KEYWORD_DICT_PATH = Path(__file__).resolve().parent.parent / "references" / "keyword_dict.json"

# 뉴스 카테고리명 → KRX 시장 섹터명 매핑
# 뉴스 분류와 KRX 업종명이 다를 때 주가/재무 데이터를 올바르게 연결하기 위한 별칭표
NEWS_SECTOR_TO_KRX: dict[str, list[str]] = {
    "IT서비스":  ["IT 서비스"],
    "반도체":    ["전기·전자"],
    "바이오":    ["제약", "의료·정밀기기"],
    "에너지":    ["전기·가스", "전기·가스·수도"],
    "자동차":    ["운송장비·부품"],
    "철강":      ["금속"],
    "화학":      ["화학"],
    "금융":      ["금융", "은행", "보험", "증권", "기타금융"],
    "건설":      ["건설"],
    "유통":      ["유통"],
    "통신":      ["통신"],
}


def _get_krx_sectors(news_sector: str) -> list[str]:
    return NEWS_SECTOR_TO_KRX.get(news_sector, [news_sector])


# ── B. 주가 모멘텀 ────────────────────────────────────────────────────────────

def calc_price_momentum(market_data: dict) -> dict[str, dict]:
    sector_rates: dict[str, list[float]] = {}
    for info in market_data.values():
        sector = info.get("sector")
        rate = info.get("change_rate")
        if sector and rate is not None:
            sector_rates.setdefault(sector, []).append(rate)

    result = {}
    for sector, rates in sector_rates.items():
        n = len(rates)
        avg = round(sum(rates) / n, 2)
        advancing = sum(1 for r in rates if r > 0)
        result[sector] = {
            "avg_change_rate": avg,
            "advancing_ratio": round(advancing / n * 100, 1),
            "ticker_count": n,
        }
    return result


# ── C. 재무 개선 추세 ─────────────────────────────────────────────────────────

def calc_financial_improvement(market_data: dict, financial_data: dict) -> dict[str, dict]:
    sector_tickers: dict[str, list[str]] = {}
    for ticker, info in market_data.items():
        sector = info.get("sector")
        if sector:
            sector_tickers.setdefault(sector, []).append(ticker)

    result = {}
    for sector, tickers in sector_tickers.items():
        rev_growths: list[float] = []
        profit_flags: list[bool] = []

        for ticker in tickers:
            fin = financial_data.get(ticker, {})
            if not isinstance(fin, dict) or fin.get("disclosure_warning"):
                continue

            rev = fin.get("revenue")
            rev_prev = fin.get("revenue_prev")
            if rev and rev_prev and rev_prev != 0:
                rev_growths.append((rev - rev_prev) / abs(rev_prev) * 100)

            net = fin.get("net_income")
            net_prev = fin.get("net_income_prev")
            if net is not None and net_prev is not None:
                profit_flags.append(net > net_prev)

        if not rev_growths and not profit_flags:
            continue

        result[sector] = {
            "avg_revenue_growth": round(sum(rev_growths) / len(rev_growths), 1) if rev_growths else None,
            "profit_improving_ratio": round(sum(profit_flags) / len(profit_flags) * 100, 1) if profit_flags else None,
            "data_count": len(rev_growths),
        }
    return result


# ── 종합 스코어 계산 ──────────────────────────────────────────────────────────

def compute_composite_score(sent: dict, price: dict, fin: dict) -> float:
    """
    각 신호를 0~1로 정규화 후 가중 평균.
    감성 40% + 주가모멘텀 35% + 재무개선 25%
    """
    score = 0.0
    weight_total = 0.0

    # A. 감성: -1~+1 → 0~1
    if sent:
        s = (sent.get("sentiment_score", 0) + 1) / 2
        score += s * 0.40
        weight_total += 0.40

    # B. 주가모멘텀: advancing_ratio 0~100 → 0~1
    if price:
        adv = price.get("advancing_ratio", 50) / 100
        score += adv * 0.35
        weight_total += 0.35

    # C. 재무: revenue_growth -50~+50 → 0~1 (클램프)
    if fin and fin.get("avg_revenue_growth") is not None:
        raw = max(-50, min(50, fin["avg_revenue_growth"]))
        f = (raw + 50) / 100
        score += f * 0.25
        weight_total += 0.25

    return round(score / weight_total, 3) if weight_total > 0 else 0.0


def _aggregate_price(news_sector: str, price_signals: dict) -> dict:
    """뉴스 섹터 → 복수 KRX 섹터 가중 평균 모멘텀."""
    total_count = 0
    weighted_rate = 0.0
    advancing_values: list[float] = []
    for krx in _get_krx_sectors(news_sector):
        p = price_signals.get(krx, {})
        if p.get("avg_change_rate") is not None:
            cnt = p.get("ticker_count", 1)
            weighted_rate += p["avg_change_rate"] * cnt
            total_count += cnt
        if p.get("advancing_ratio") is not None:
            advancing_values.append(p["advancing_ratio"])
    if not total_count:
        return {}
    return {
        "avg_change_rate": round(weighted_rate / total_count, 2),
        "advancing_ratio": round(sum(advancing_values) / len(advancing_values), 1) if advancing_values else None,
        "ticker_count": total_count,
    }


def _aggregate_fin(news_sector: str, fin_signals: dict) -> dict:
    """뉴스 섹터 → 복수 KRX 섹터 평균 재무 개선."""
    rev_growths: list[float] = []
    profit_ratios: list[float] = []
    for krx in _get_krx_sectors(news_sector):
        f = fin_signals.get(krx, {})
        if f.get("avg_revenue_growth") is not None:
            rev_growths.append(f["avg_revenue_growth"])
        if f.get("profit_improving_ratio") is not None:
            profit_ratios.append(f["profit_improving_ratio"])
    return {
        "avg_revenue_growth": round(sum(rev_growths) / len(rev_growths), 1) if rev_growths else None,
        "profit_improving_ratio": round(sum(profit_ratios) / len(profit_ratios), 1) if profit_ratios else None,
    }


def main():
    keyword_dict = json.loads(KEYWORD_DICT_PATH.read_text(encoding="utf-8"))

    market_data = {}
    financial_data = {}
    sentiment_data = {}

    market_path = OUTPUT_DIR / "step1_market_data.json"
    if market_path.exists():
        market_data = json.loads(market_path.read_text(encoding="utf-8"))

    fin_path = OUTPUT_DIR / "step1_financial_data.json"
    if fin_path.exists():
        financial_data = json.loads(fin_path.read_text(encoding="utf-8"))

    sent_path = OUTPUT_DIR / "step2_sentiment_scores.json"
    if sent_path.exists():
        sentiment_data = json.loads(sent_path.read_text(encoding="utf-8"))

    price_signals = calc_price_momentum(market_data)
    fin_signals = calc_financial_improvement(market_data, financial_data)

    all_sectors = set(keyword_dict) & (
        set(sentiment_data) | set(price_signals) | set(fin_signals)
    )

    result = {}
    for sector in all_sectors:
        sent = sentiment_data.get(sector, {})
        price = _aggregate_price(sector, price_signals)
        fin = _aggregate_fin(sector, fin_signals)

        if not sent.get("article_count"):
            continue

        composite = compute_composite_score(sent, price, fin)

        result[sector] = {
            # A. 뉴스 감성
            "article_count": sent.get("article_count", 0),
            "sentiment_score": sent.get("sentiment_score", 0),
            "sentiment_label": sent.get("sentiment_label", "중립"),
            "positive_signals": sent.get("positive_signals", 0),
            "negative_signals": sent.get("negative_signals", 0),
            # B. 주가 모멘텀
            "avg_change_rate": price.get("avg_change_rate"),
            "advancing_ratio": price.get("advancing_ratio"),
            # C. 재무 개선
            "avg_revenue_growth": fin.get("avg_revenue_growth"),
            "profit_improving_ratio": fin.get("profit_improving_ratio"),
            # 종합
            "composite_score": composite,
        }

    out_path = OUTPUT_DIR / "step2_sector_scores.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build_sector_scorecard] {len(result)}개 섹터 종합 점수 산출 → {out_path}")
    print()
    print(f"{'섹터':10s} {'기사':>4s} {'감성':>6s} {'등락률':>6s} {'상승비':>6s} {'매출성장':>8s} {'종합점수':>8s}")
    print("-" * 60)
    for s, v in sorted(result.items(), key=lambda x: -x[1]["composite_score"]):
        print(
            f"{s:10s} "
            f"{v['article_count']:4d}건 "
            f"{v['sentiment_score']:+6.2f} "
            f"{(str(v['avg_change_rate'])+'%') if v['avg_change_rate'] is not None else 'N/A':>6s} "
            f"{(str(v['advancing_ratio'])+'%') if v['advancing_ratio'] is not None else 'N/A':>6s} "
            f"{(str(v['avg_revenue_growth'])+'%') if v['avg_revenue_growth'] is not None else 'N/A':>8s} "
            f"{v['composite_score']:8.3f}"
        )


if __name__ == "__main__":
    main()
