"""
STEP 1 - 뉴스 수집 (RSS + BeautifulSoup)
한국경제/매일경제/연합인포맥스 RSS에서 기사를 수집하여
output/step1_news_raw.json 으로 저장한다.
소스별 실패는 non-critical (스킵 + 로그).
"""
import json
import os
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WARN_LOG = OUTPUT_DIR / "pipeline_warn.log"

RSS_SOURCES = {
    "yonhap_info":  "https://news.einfomax.co.kr/rss/allArticle.xml",
    "investing_kr": "https://kr.investing.com/rss/news.rss",
    "newsis_eco":   "https://newsis.com/RSS/economy.xml",
    "hankyung":     "https://www.hankyung.com/feed/economy",
    "maeil_eco":    "https://www.mk.co.kr/rss/30100041/",
    "yonhap_eco":   "https://www.yna.co.kr/rss/economy.xml",
    "edaily":       "https://www.edaily.co.kr/rss/RssSection.xml?pt_nm=Economy",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

ARTICLE_SELECTORS = [
    "div.article-body",
    "div#articleBody",
    "div.news_body",
    "div.article_txt",
    "article",
    "div.article",
]


def log_warn(msg: str) -> None:
    timestamp = datetime.now().isoformat()
    with open(WARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] [fetch_news] {msg}\n")
    print(f"WARN: {msg}", file=sys.stderr)


def _normalize_date(raw: str) -> str:
    """RFC-2822 / ISO / 기타 형식을 ISO 8601(YYYY-MM-DD HH:MM:SS)로 통일."""
    if not raw:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return raw


def fetch_rss_feed(url: str, source_name: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        articles = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            content = entry.get("summary", entry.get("description", "")).strip()
            raw_date = entry.get("published", entry.get("updated", ""))
            link = entry.get("link", "")
            if title:
                articles.append({
                    "title": title,
                    "content": content,
                    "pub_date": _normalize_date(raw_date),
                    "source": source_name,
                    "url": link,
                })
        return articles
    except Exception as e:
        log_warn(f"{source_name} RSS 수집 실패: {e}")
        return []


def fetch_article_body(url: str) -> str:
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for sel in ARTICLE_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                return text[:2000]

        # 폴백: p 태그 전체 수집
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(strip=True) for p in paragraphs)
        return text[:2000]

    except Exception:
        return ""


def deduplicate_by_url(articles: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for a in articles:
        url = a.get("url", "")
        if url not in seen:
            seen.add(url)
            result.append(a)
    return result


def enrich_with_body(articles: list[dict], max_articles: int = 80) -> list[dict]:
    """본문이 짧은 기사에 한해 URL에서 본문 추가 수집."""
    enriched = 0
    for a in articles[:max_articles]:
        if len(a.get("content", "")) < 100 and a.get("url"):
            body = fetch_article_body(a["url"])
            if body:
                a["content"] = body
                enriched += 1
    print(f"[fetch_news] 본문 추가 수집: {enriched}건")
    return articles


def validate_and_save(articles: list[dict], path: Path) -> None:
    if len(articles) < 30:
        log_warn(f"수집 기사 {len(articles)}건 — 30건 미만 (파이프라인 계속 진행)")
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch_news] {len(articles)}건 저장 완료 → {path}")


def main():
    all_articles: list[dict] = []
    for name, url in RSS_SOURCES.items():
        articles = fetch_rss_feed(url, name)
        print(f"[fetch_news] {name}: {len(articles)}건")
        all_articles.extend(articles)

    all_articles = deduplicate_by_url(all_articles)
    all_articles = enrich_with_body(all_articles)

    validate_and_save(all_articles, OUTPUT_DIR / "step1_news_raw.json")


if __name__ == "__main__":
    main()
