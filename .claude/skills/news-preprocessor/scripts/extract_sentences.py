"""
STEP 2 - 핵심 문장 추출
TF 기반 점수화로 각 기사에서 상위 4문장을 추출한다.
KoNLPy 미설치 시 공백 분리 폴백.
step1_news_raw.json의 각 기사에 key_sentences 필드를 추가한다.
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))

STOP_WORDS = {
    "이", "가", "은", "는", "을", "를", "의", "에", "와", "과",
    "도", "로", "으로", "에서", "이다", "있다", "하다", "한다",
    "합니다", "했다", "됩니다", "됐다", "것", "수", "등", "및",
    "또", "이번", "지난", "이후", "통해", "위해", "대한",
}


def tokenize(text: str) -> list[str]:
    try:
        from konlpy.tag import Okt
        okt = Okt()
        return [w for w in okt.nouns(text) if len(w) > 1 and w not in STOP_WORDS]
    except Exception:
        tokens = re.split(r"[\s\.,!?;：:]+", text)
        return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]


def split_sentences(text: str) -> list[str]:
    # 한국어 문장 분리: 마침표/느낌표/물음표 뒤 또는 줄바꿈
    sentences = re.split(r"(?<=[.!?。])\s+|\n+", text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def score_sentences(sentences: list[str], doc_tokens: list[str]) -> list[tuple[float, str]]:
    freq = Counter(doc_tokens)
    scored = []
    for sent in sentences:
        sent_tokens = tokenize(sent)
        score = sum(freq.get(t, 0) for t in sent_tokens)
        scored.append((score, sent))
    return sorted(scored, reverse=True)


def extract_key_sentences(article: dict, top_n: int = 4) -> list[str]:
    raw_content = article.get("content", "")
    content = BeautifulSoup(raw_content, "lxml").get_text(" ", strip=True) if raw_content else ""
    if not content or len(content) < 20:
        return [article.get("title", "")]

    sentences = split_sentences(content)
    if not sentences:
        return [article.get("title", "")]

    doc_tokens = tokenize(content)
    scored = score_sentences(sentences, doc_tokens)

    top = [s for _, s in scored[:top_n]]

    # 첫 문장은 항상 포함 (문맥 유지)
    if sentences[0] not in top:
        top = [sentences[0]] + top[:top_n - 1]

    return top


def main():
    path = OUTPUT_DIR / "step1_news_raw.json"
    if not path.exists():
        print("ERROR: step1_news_raw.json 없음", file=sys.stderr)
        sys.exit(1)

    articles = json.loads(path.read_text(encoding="utf-8"))
    skipped = 0
    for a in articles:
        try:
            a["key_sentences"] = extract_key_sentences(a)
        except Exception as e:
            a["key_sentences"] = [a.get("title", "")]
            skipped += 1

    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[extract_sentences] 처리 완료: {len(articles)}건 (스킵: {skipped}건)")


if __name__ == "__main__":
    main()
