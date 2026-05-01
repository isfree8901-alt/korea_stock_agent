"""
STEP 2 - LLM 입력 텍스트 구성
keyword_dict.json으로 기사를 섹터에 태깅하고
섹터별 요약 텍스트(최대 500자)를 조합하여
output/step2_llm_input.json 으로 저장한다.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
KEYWORD_DICT_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "keyword_dict.json"
)
MAX_SUMMARY_CHARS = 500


def load_keyword_dict() -> dict:
    return json.loads(KEYWORD_DICT_PATH.read_text(encoding="utf-8"))


def tag_article_sectors(article: dict, keyword_dict: dict) -> list[str]:
    text = article.get("title", "") + " " + " ".join(article.get("key_sentences", []))
    matched = []
    for sector, keywords in keyword_dict.items():
        if any(kw in text for kw in keywords):
            matched.append(sector)
    return matched


def build_sector_summary(articles: list[dict], max_chars: int = MAX_SUMMARY_CHARS) -> str:
    sentences = []
    for a in articles:
        sentences.extend(a.get("key_sentences", [a.get("title", "")]))
    combined = " ".join(sentences)
    return combined[:max_chars]


def build_output(articles: list[dict], keyword_dict: dict) -> dict:
    sector_map: dict[str, list[dict]] = {s: [] for s in keyword_dict}

    for article in articles:
        sectors = tag_article_sectors(article, keyword_dict)
        for sector in sectors:
            sector_map[sector].append({
                "title": article.get("title", ""),
                "key_sentences": article.get("key_sentences", []),
                "url": article.get("url", ""),
            })

    result = {}
    for sector, arts in sector_map.items():
        if not arts:
            continue
        keywords_found = list({
            kw
            for kw in keyword_dict[sector]
            for a in arts
            if kw in (a.get("title", "") + " ".join(a.get("key_sentences", [])))
        })
        result[sector] = {
            "article_count": len(arts),
            "summary": build_sector_summary(arts),
            "keywords": keywords_found,
            "articles": [{"title": a["title"], "key_sentences": a.get("key_sentences", []), "url": a.get("url", "")} for a in arts],
        }

    return result


def main():
    raw_path = OUTPUT_DIR / "step1_news_raw.json"
    if not raw_path.exists():
        print("ERROR: step1_news_raw.json 없음", file=sys.stderr)
        sys.exit(1)

    articles = json.loads(raw_path.read_text(encoding="utf-8"))
    keyword_dict = load_keyword_dict()
    output = build_output(articles, keyword_dict)

    # 섹터 스코어카드 병합 (build_sector_scorecard.py 결과)
    scores_path = OUTPUT_DIR / "step2_sector_scores.json"
    if scores_path.exists():
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
        for sector, v in output.items():
            sc = scores.get(sector, {})
            v["sentiment_score"]        = sc.get("sentiment_score")
            v["sentiment_label"]        = sc.get("sentiment_label")
            v["avg_change_rate"]        = sc.get("avg_change_rate")
            v["advancing_ratio"]        = sc.get("advancing_ratio")
            v["avg_revenue_growth"]     = sc.get("avg_revenue_growth")
            v["profit_improving_ratio"] = sc.get("profit_improving_ratio")
            v["composite_score"]        = sc.get("composite_score")

    out_path = OUTPUT_DIR / "step2_llm_input.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # 대시보드 "섹터별 상세분석 & 뉴스" 섹션이 읽는 파일도 동시 갱신
    pre_path = OUTPUT_DIR / "step2_news_preprocessed.json"
    pre_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    tagged_sectors = len(output)
    tagged_articles = sum(len(v["articles"]) for v in output.values())
    print(f"[build_llm_input] {tagged_sectors}개 섹터, {tagged_articles}건 태깅 완료 → {out_path}, {pre_path.name}")


if __name__ == "__main__":
    main()
