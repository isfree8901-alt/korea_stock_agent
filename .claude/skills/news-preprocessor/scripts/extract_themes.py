"""
STEP 2 - 뉴스 테마 추출 (LLM 미사용)
섹터별 뉴스에서 핵심 키워드 빈도를 분석하고
향후 6개월 주목 섹터를 알고리즘으로 선정한다.

선정 기준:
  forward_score  = 전망 키워드 언급 비율 × 감성점수
  momentum_score = (avg_change_rate 정규화) × advancing_ratio 정규화
  composite_6m   = sentiment(30%) + forward(40%) + momentum(30%)

출력: output/step2_themes.json
"""
import json
import os
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))

# 미래 전망 키워드 — 6개월 이상 forward-looking 기사 감지용
FORWARD_KEYWORDS = [
    "전망", "예상", "목표", "계획", "성장", "기대", "전환", "확대", "수혜", "수주",
    "신규", "발표", "출시", "개발", "투자", "확장", "협약", "계약", "증가", "향후",
    "장기", "중장기", "2025년", "2026년", "하반기", "상반기", "내년", "올해",
]

# 리스크 키워드 — 부정적 미래 신호
RISK_KEYWORDS = [
    "우려", "리스크", "위기", "불안", "불확실", "하락", "감소", "축소", "철수",
    "규제", "제재", "분쟁", "손실", "적자", "부진", "침체", "구조조정",
]

# 섹터별 핵심 테마 키워드 (뉴스 내 언급 빈도로 테마 강도 측정)
THEME_KEYWORDS: dict[str, list[str]] = {
    "반도체":   ["HBM", "AI반도체", "파운드리", "DRAM", "엔비디아", "TSMC", "메모리", "시스템반도체"],
    "2차전지":  ["배터리", "전기차", "리튬", "양극재", "음극재", "충전", "ESS", "CATL"],
    "바이오":   ["임상", "신약", "FDA", "항체", "바이오시밀러", "유전자", "치료제", "허가"],
    "자동차":   ["전기차", "자율주행", "수소차", "SDV", "플랫폼", "수출", "현대차", "기아"],
    "금융":     ["금리", "대출", "예금", "인수합병", "보험", "펀드", "IPO", "리츠"],
    "에너지":   ["원유", "LNG", "재생에너지", "태양광", "풍력", "수소", "탄소중립", "전력"],
    "통신":     ["5G", "6G", "AI", "클라우드", "데이터센터", "OTT", "미디어"],
    "철강":     ["철강", "포스코", "원자재", "수요", "중국", "슬래브", "열연"],
    "화학":     ["정제", "석유화학", "스프레드", "NCC", "폴리머", "플라스틱", "친환경"],
    "IT서비스": ["AI", "클라우드", "SaaS", "디지털전환", "플랫폼", "빅데이터", "자동화"],
    "건설":     ["수주", "분양", "재개발", "인프라", "해외건설", "SOC", "PF"],
    "유통":     ["소비", "물가", "온라인", "오프라인", "이커머스", "편의점", "할인점"],
}


def extract_text_corpus(articles: list[dict]) -> str:
    """기사 목록에서 텍스트 코퍼스 생성."""
    parts = []
    for art in articles:
        parts.append(art.get("title", ""))
        sentences = art.get("key_sentences", "")
        if isinstance(sentences, list):
            parts.extend(sentences)
        elif isinstance(sentences, str):
            parts.append(sentences)
    return " ".join(parts)


def count_keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(text.count(kw) for kw in keywords)


def extract_top_keywords(text: str, sector_keywords: list[str], top_n: int = 5) -> list[str]:
    """섹터 대표 키워드 중 빈도 상위 N개 반환."""
    counts = {kw: text.count(kw) for kw in sector_keywords if text.count(kw) > 0}
    return [kw for kw, _ in sorted(counts.items(), key=lambda x: -x[1])[:top_n]]


def normalize(values: list[float | None]) -> list[float]:
    """0~1 min-max 정규화. None → 중립값 0.5 대입."""
    filtered = [v for v in values if v is not None]
    if not filtered:
        return [0.5] * len(values)
    mn, mx = min(filtered), max(filtered)
    rng = mx - mn
    result = []
    for v in values:
        if v is None:
            result.append(0.5)
        elif rng == 0:
            result.append(0.5)
        else:
            result.append((v - mn) / rng)
    return result


def main():
    news_path = OUTPUT_DIR / "step2_news_preprocessed.json"
    scores_path = OUTPUT_DIR / "step2_sector_scores.json"

    if not news_path.exists():
        print("[extract_themes] step2_news_preprocessed.json 없음 — 스킵")
        return

    news_data: dict = json.loads(news_path.read_text(encoding="utf-8"))
    sector_scores: dict = {}
    if scores_path.exists():
        sector_scores = json.loads(scores_path.read_text(encoding="utf-8"))

    themes: dict = {}
    sectors = list(news_data.keys())

    # ── 1차 패스: 섹터별 텍스트 분석 ─────────────────────────────────────────
    raw_forward: dict[str, float] = {}
    raw_risk: dict[str, float] = {}

    for sector, data in news_data.items():
        articles = data.get("articles", [])
        if not articles:
            continue

        corpus = extract_text_corpus(articles)
        n = len(articles)

        forward_hits = count_keyword_hits(corpus, FORWARD_KEYWORDS)
        risk_hits = count_keyword_hits(corpus, RISK_KEYWORDS)
        forward_ratio = forward_hits / (forward_hits + risk_hits + 1)

        theme_kws = THEME_KEYWORDS.get(sector, [])
        top_kws = extract_top_keywords(corpus, theme_kws)

        sc = sector_scores.get(sector, {})
        sentiment = sc.get("sentiment_score", 0.0) or 0.0
        avg_change = sc.get("avg_change_rate")
        advancing = sc.get("advancing_ratio")
        composite = sc.get("composite_score", 0.5) or 0.5

        raw_forward[sector] = forward_ratio
        raw_risk[sector] = risk_hits / max(n, 1)

        themes[sector] = {
            "article_count": n,
            "top_keywords": top_kws,
            "forward_keyword_hits": forward_hits,
            "risk_keyword_hits": risk_hits,
            "forward_ratio": round(forward_ratio, 3),
            "sentiment_score": sentiment,
            "avg_change_rate": avg_change,
            "advancing_ratio": advancing,
            "composite_score": composite,
            # 아래는 정규화 후 채움
            "forward_score": None,
            "composite_6m": None,
            "highlight": False,
            "highlight_reason": "",
        }

    # ── 2차 패스: 정규화 & 6개월 종합 점수 ──────────────────────────────────
    if not themes:
        print("[extract_themes] 분석 가능한 섹터 없음")
        return

    sector_list = list(themes.keys())
    forward_vals = [themes[s]["forward_ratio"] for s in sector_list]
    sent_vals = [(themes[s]["sentiment_score"] + 1) / 2 for s in sector_list]  # -1~+1 → 0~1
    change_vals = [themes[s]["avg_change_rate"] for s in sector_list]
    adv_vals = [themes[s]["advancing_ratio"] for s in sector_list]

    norm_forward = normalize(forward_vals)
    norm_sent = normalize(sent_vals)
    norm_change = normalize(change_vals)
    norm_adv = normalize(adv_vals)

    for i, sector in enumerate(sector_list):
        momentum = (norm_change[i] + norm_adv[i]) / 2
        composite_6m = round(
            norm_sent[i] * 0.30
            + norm_forward[i] * 0.40
            + momentum * 0.30,
            3,
        )
        forward_score = round(norm_forward[i], 3)
        themes[sector]["forward_score"] = forward_score
        themes[sector]["composite_6m"] = composite_6m

    # ── 상위 섹터 하이라이트 ──────────────────────────────────────────────────
    sorted_sectors = sorted(
        sector_list, key=lambda s: themes[s]["composite_6m"] or 0, reverse=True
    )

    # 상위 3개 하이라이트 (단, 기사 수 3개 미만 제외)
    highlighted = 0
    for sector in sorted_sectors:
        if highlighted >= 3:
            break
        t = themes[sector]
        if t["article_count"] < 3:
            continue
        t["highlight"] = True
        kws = ", ".join(t["top_keywords"][:3]) if t["top_keywords"] else "-"
        adv_str = f"상승종목비율 {t['advancing_ratio']:.0f}%" if t["advancing_ratio"] else ""
        sent_str = "긍정" if t["sentiment_score"] > 0.3 else ("부정" if t["sentiment_score"] < -0.2 else "중립")
        t["highlight_reason"] = (
            f"뉴스 전망 언급 {t['forward_keyword_hits']}건 | 감성 {sent_str} | "
            f"핵심 이슈: {kws}" + (f" | {adv_str}" if adv_str else "")
        )
        highlighted += 1

    out_path = OUTPUT_DIR / "step2_themes.json"
    out_path.write_text(json.dumps(themes, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[extract_themes] {len(themes)}개 섹터 테마 분석 완료 → {out_path}")
    print("\n▼ 향후 6개월 주목 섹터 (composite_6m 기준)")
    print(f"{'섹터':10s} {'기사':>4s} {'전망비':>6s} {'감성':>6s} {'6m점수':>7s} {'하이라이트'}")
    print("-" * 60)
    for sector in sorted_sectors:
        t = themes[sector]
        badge = "⭐" if t["highlight"] else ""
        print(
            f"{sector:10s} {t['article_count']:4d}건 "
            f"{t['forward_ratio']:5.2f}  "
            f"{t['sentiment_score']:+5.2f}  "
            f"{t['composite_6m']:7.3f}  {badge}"
        )


if __name__ == "__main__":
    main()
