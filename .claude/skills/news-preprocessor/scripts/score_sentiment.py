"""
STEP 2 - 뉴스 감성 분석 (Option A)
step1_news_raw.json의 각 기사를 긍/부정 키워드 기반으로 점수화하고
섹터별로 집계하여 step2_sentiment_scores.json에 저장한다.
"""
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
KEYWORD_DICT_PATH = Path(__file__).resolve().parent.parent / "references" / "keyword_dict.json"

POSITIVE_KEYWORDS = [
    "상승", "성장", "증가", "호조", "개선", "확대", "신고가", "최고가", "최고치",
    "수혜", "강세", "호실적", "급등", "돌파", "기대", "유망", "반등", "흑자",
    "최대", "사상최대", "상향", "낙관", "호황", "급증", "호전", "회복",
    "수주", "투자확대", "시장확대", "규제완화", "지원", "수출증가",
    "신제품", "기술혁신", "수익증가", "이익개선", "매출증가",
]

NEGATIVE_KEYWORDS = [
    "하락", "감소", "부진", "악화", "위기", "위험", "경고", "우려", "손실",
    "적자", "약세", "급락", "침체", "불안", "리스크", "저하", "후퇴",
    "급감", "불확실", "하향", "비관", "구조조정", "파산", "부도",
    "적자전환", "규제강화", "제재", "관세", "공급과잉", "재고증가",
    "수익악화", "매출감소", "이익감소", "비용증가", "고점",
]


def score_text(text: str) -> tuple[int, int]:
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    return pos, neg


def tag_sectors(text: str, keyword_dict: dict) -> list[str]:
    return [
        sector for sector, keywords in keyword_dict.items()
        if any(kw in text for kw in keywords)
    ]


def main():
    news_path = OUTPUT_DIR / "step1_news_raw.json"
    if not news_path.exists():
        print("ERROR: step1_news_raw.json 없음")
        return

    articles = json.loads(news_path.read_text(encoding="utf-8"))
    keyword_dict = json.loads(KEYWORD_DICT_PATH.read_text(encoding="utf-8"))

    # 섹터별 감성 집계
    sector_agg: dict[str, dict] = {
        s: {"pos": 0, "neg": 0, "count": 0} for s in keyword_dict
    }

    for article in articles:
        text = (
            article.get("title", "") + " "
            + " ".join(article.get("key_sentences", []))
        )
        pos, neg = score_text(text)
        sectors = tag_sectors(text, keyword_dict)
        for sector in sectors:
            sector_agg[sector]["pos"] += pos
            sector_agg[sector]["neg"] += neg
            sector_agg[sector]["count"] += 1

    result = {}
    for sector, agg in sector_agg.items():
        count = agg["count"]
        if count == 0:
            continue
        pos, neg = agg["pos"], agg["neg"]
        denom = max(pos + neg, 1)
        score = round((pos - neg) / denom, 3)
        result[sector] = {
            "article_count": count,
            "positive_signals": pos,
            "negative_signals": neg,
            "sentiment_score": score,           # -1.0 ~ +1.0
            "sentiment_label": (
                "긍정" if score > 0.15 else ("부정" if score < -0.15 else "중립")
            ),
        }

    out_path = OUTPUT_DIR / "step2_sentiment_scores.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[score_sentiment] {len(result)}개 섹터 감성 분석 완료 → {out_path}")
    for s, v in sorted(result.items(), key=lambda x: -x[1]["article_count"]):
        print(f"  {s:10s}: {v['article_count']:3d}건 | "
              f"긍정 {v['positive_signals']:2d} 부정 {v['negative_signals']:2d} | "
              f"감성 {v['sentiment_score']:+.2f} [{v['sentiment_label']}]")


if __name__ == "__main__":
    main()
