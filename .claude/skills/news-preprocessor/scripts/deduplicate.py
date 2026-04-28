"""
STEP 2 - 뉴스 중복 제거
제목 유사도(difflib)를 기준으로 중복 기사를 제거한다.
step1_news_raw.json을 읽어 중복 제거된 리스트를 동일 파일에 덮어쓴다.
"""
import json
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
THRESHOLD = 0.85


def title_similarity(t1: str, t2: str) -> float:
    return SequenceMatcher(None, t1, t2).ratio()


def deduplicate_articles(articles: list[dict],
                          threshold: float = THRESHOLD) -> list[dict]:
    kept: list[dict] = []
    for article in articles:
        title = article.get("title", "")
        is_dup = any(
            title_similarity(title, k.get("title", "")) >= threshold
            for k in kept
        )
        if not is_dup:
            kept.append(article)
    removed = len(articles) - len(kept)
    print(f"[deduplicate] {len(articles)}건 → {len(kept)}건 (제거: {removed}건)")
    return kept


def main():
    path = OUTPUT_DIR / "step1_news_raw.json"
    if not path.exists():
        print("ERROR: step1_news_raw.json 없음", file=sys.stderr)
        sys.exit(1)

    articles = json.loads(path.read_text(encoding="utf-8"))
    deduped = deduplicate_articles(articles)
    path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[deduplicate] 저장 완료 → {path}")


if __name__ == "__main__":
    main()
