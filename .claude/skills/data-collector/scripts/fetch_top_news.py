"""
fetch_top_news.py
=================
국내·해외 주요기사 Top 10 수집 스크립트.

- 국내  : 네이버 뉴스 경제 섹션 인기기사 Top 10 (BeautifulSoup 스크래핑)
          실패 시 한경·매경·연합뉴스·이데일리·뉴시스 RSS 폴백
- 해외  : Bloomberg Markets RSS  Top 10 (requests + feedparser, 원문 요약)
         NYTimes Business RSS   Top 10 (기사 본문 스크래핑으로 5~6줄 요약)
- 번역  : deep_translator.GoogleTranslator (없으면 원문 유지)
- 출력  : output/step1_top_news.json
"""

import json
import os
import sys
import datetime
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

# ─── 경로 설정 ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WARN_LOG = OUTPUT_DIR / "pipeline_warn.log"

# ─── 번역 헬퍼 ──────────────────────────────────────────────────────────────────
try:
    from deep_translator import GoogleTranslator as _GT

    def translate_ko(text: str) -> tuple[str, bool]:
        """(번역문, 번역성공여부) 반환. 원문이 비어있으면 그대로 반환."""
        if not text or not text.strip():
            return text, False
        try:
            result = _GT(source="auto", target="ko").translate(text[:4900])
            return result or text, True
        except Exception as e:
            _warn(f"[번역 실패] {e}")
            return text, False

    _TRANSLATOR_AVAILABLE = True
except ImportError:
    def translate_ko(text: str) -> tuple[str, bool]:
        return text, False
    _TRANSLATOR_AVAILABLE = False


# ─── 경고 로그 ──────────────────────────────────────────────────────────────────
def _warn(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [fetch_top_news] {msg}\n"
    print(line, end="", file=sys.stderr)
    try:
        with open(WARN_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


# ─── 네이버 경제 인기기사 스크래핑 ────────────────────────────────────────────────
NAVER_POPULAR_URL = (
    "https://news.naver.com/main/ranking/popularDay.naver"
    "?rankingType=popular&sectionId=101"
)


def _parse_pub_date(raw: str) -> str:
    """feedparser entry.published → 'YYYY-MM-DD HH:MM' 변환."""
    if not raw:
        return ""
    try:
        import email.utils
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(raw[:25], fmt[:len(raw[:25])])
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return raw[:16]


def fetch_domestic_naver() -> list[dict]:
    """네이버 뉴스 경제 인기기사 Top 10 스크래핑."""
    articles: list[dict] = []
    try:
        resp = requests.get(NAVER_POPULAR_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        ranking_items = soup.select("ul.rankingnews_list li.rankingnews_list_item")
        if not ranking_items:
            ranking_items = soup.select("ol.ranking_list li")
        if not ranking_items:
            ranking_items = soup.select("div.rankingnews_box ul li")

        rank = 1
        for item in ranking_items:
            if rank > 10:
                break
            a_tag = item.find("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            url = a_tag.get("href", "")
            if not url.startswith("http"):
                url = "https://news.naver.com" + url

            source_tag = item.select_one("span.rankingnews_name, em.media_name, span.name")
            source = source_tag.get_text(strip=True) if source_tag else "네이버뉴스"

            articles.append({
                "rank": rank,
                "title": title,
                "title_ko": title,
                "url": url,
                "source": source,
                "source_type": "domestic",
                "pub_date": "",
                "summary_ko": "",
                "translated": False,
            })
            rank += 1

        if articles:
            print(f"[fetch_top_news] 네이버 인기기사 {len(articles)}건 수집")
    except Exception as e:
        _warn(f"네이버 스크래핑 실패: {e}")
        articles = []

    return articles


# ─── 국내 RSS 폴백 ────────────────────────────────────────────────────────────
DOMESTIC_RSS_FEEDS = [
    ("한국경제",  "https://www.hankyung.com/feed/economy"),
    ("매일경제",  "https://www.mk.co.kr/rss/30100041/"),
    ("연합뉴스",  "https://www.yonhapnews.co.kr/rss/economy.xml"),
    ("이데일리",  "https://rss.edaily.co.kr/edaily_eco.xml"),
    ("뉴시스",   "https://www.newsis.com/RSS/economy.xml"),
]


def fetch_domestic_rss_fallback() -> list[dict]:
    """국내 주요 경제 RSS에서 최신기사 수집 (네이버 스크래핑 실패 시 폴백)."""
    articles: list[dict] = []
    for source_name, feed_url in DOMESTIC_RSS_FEEDS:
        if len(articles) >= 10:
            break
        try:
            raw = requests.get(feed_url, headers=HEADERS, timeout=10).text
            feed = feedparser.parse(raw)
            for entry in feed.entries[:max(1, 10 - len(articles))]:
                title = getattr(entry, "title", "")
                url = getattr(entry, "link", "")
                pub = _parse_pub_date(getattr(entry, "published", ""))
                if not title:
                    continue
                articles.append({
                    "rank": len(articles) + 1,
                    "title": title,
                    "title_ko": title,
                    "url": url,
                    "source": source_name,
                    "source_type": "domestic",
                    "pub_date": pub,
                    "summary_ko": "",
                    "translated": False,
                })
        except Exception as e:
            _warn(f"RSS 폴백 실패 [{source_name}]: {e}")

    for i, art in enumerate(articles[:10], start=1):
        art["rank"] = i

    print(f"[fetch_top_news] RSS 폴백 국내 기사 {len(articles[:10])}건 수집")
    return articles[:10]


def fetch_domestic() -> list[dict]:
    """네이버 스크래핑 우선, 실패 시 RSS 폴백."""
    articles = fetch_domestic_naver()
    if len(articles) < 3:
        _warn("네이버 스크래핑 결과 부족 — RSS 폴백 사용")
        articles = fetch_domestic_rss_fallback()
    return articles


# ─── 해외 RSS 수집 ────────────────────────────────────────────────────────────
BLOOMBERG_RSS = "https://feeds.bloomberg.com/markets/news.rss"
NYTIMES_RSS   = "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"


def _html_strip(text: str) -> str:
    """HTML 태그 제거 후 텍스트 반환."""
    if not text:
        return ""
    try:
        return BeautifulSoup(text, "lxml").get_text(separator=" ").strip()
    except Exception:
        return text


def _fetch_rss_with_requests(feed_url: str) -> feedparser.FeedParserDict:
    """requests로 RSS XML을 먼저 내려받은 뒤 feedparser로 파싱 (User-Agent 적용)."""
    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return feedparser.parse(resp.text)
    except Exception as e:
        _warn(f"RSS fetch 실패 [{feed_url}]: {e}")
        return feedparser.parse("")


def _fetch_nyt_article_body(url: str, max_chars: int = 1200) -> str:
    """NYT 기사 본문 앞부분 스크래핑 (5~6줄 분량). 실패 시 빈 문자열 반환."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # NYT 본문 p 태그 — class에 'story' 포함 또는 article[data-testid] 안 p
        paras = soup.select("article p") or soup.select("section[name='articleBody'] p") or soup.select("div.StoryBodyCompact p")
        if not paras:
            # 범용 폴백: 가장 긴 p 태그들
            paras = sorted(soup.find_all("p"), key=lambda p: len(p.get_text()), reverse=True)[:8]
        texts = []
        total = 0
        for p in paras:
            t = p.get_text(separator=" ").strip()
            if len(t) < 30:
                continue
            texts.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return " ".join(texts)
    except Exception:
        return ""


def fetch_bloomberg(top_n: int = 10) -> list[dict]:
    """Bloomberg Markets RSS Top N 수집 + 번역. RSS 요약문을 5~6줄 분량으로 표시."""
    articles: list[dict] = []
    feed = _fetch_rss_with_requests(BLOOMBERG_RSS)
    entries = feed.entries[:top_n]
    for rank, entry in enumerate(entries, start=1):
        title = getattr(entry, "title", "")
        url   = getattr(entry, "link", "")
        pub   = _parse_pub_date(getattr(entry, "published", ""))

        # Bloomberg RSS는 summary 필드에 원문 요약 포함
        summary_raw = ""
        if hasattr(entry, "summary"):
            summary_raw = _html_strip(entry.summary)
        elif hasattr(entry, "description"):
            summary_raw = _html_strip(entry.description)

        # content:encoded 시도
        if not summary_raw and hasattr(entry, "content"):
            for c in entry.get("content", []):
                if c.get("value"):
                    summary_raw = _html_strip(c["value"])
                    break

        title_ko, t_ok_title = translate_ko(title)
        # 요약문 전체 번역 (Bloomberg는 페이월로 원문 본문 접근 불가)
        summary_ko, t_ok_summ = translate_ko(summary_raw[:800] if summary_raw else "")

        articles.append({
            "rank": rank,
            "title": title,
            "title_ko": title_ko,
            "summary_en": summary_raw,
            "summary_ko": summary_ko,
            "url": url,
            "source": "Bloomberg",
            "source_type": "foreign",
            "pub_date": pub,
            "translated": t_ok_title or t_ok_summ,
        })

    print(f"[fetch_top_news] Bloomberg {len(articles)}건 수집 (번역={'가능' if _TRANSLATOR_AVAILABLE else '불가'})")
    return articles


def fetch_nytimes(top_n: int = 10) -> list[dict]:
    """NYTimes Business RSS Top N 수집 + 기사 본문 스크래핑으로 5~6줄 요약 생성."""
    articles: list[dict] = []
    feed = _fetch_rss_with_requests(NYTIMES_RSS)
    entries = feed.entries[:top_n]
    for rank, entry in enumerate(entries, start=1):
        title = getattr(entry, "title", "")
        url   = getattr(entry, "link", "")
        pub   = _parse_pub_date(getattr(entry, "published", ""))

        # RSS description/summary
        summary_raw = ""
        if hasattr(entry, "summary"):
            summary_raw = _html_strip(entry.summary)
        elif hasattr(entry, "description"):
            summary_raw = _html_strip(entry.description)

        # NYT 기사 본문 스크래핑으로 보강 (RSS 요약이 짧을 때)
        body_text = ""
        if url and "nytimes.com" in url:
            body_text = _fetch_nyt_article_body(url)

        # RSS 요약 + 본문 앞부분 합쳐서 최대 1000자 구성
        full_summary_en = summary_raw
        if body_text and body_text.strip() != summary_raw.strip():
            if summary_raw:
                full_summary_en = summary_raw + "\n\n" + body_text
            else:
                full_summary_en = body_text
        full_summary_en = full_summary_en[:1000]

        title_ko, t_ok_title = translate_ko(title)
        summary_ko, t_ok_summ = translate_ko(full_summary_en)

        articles.append({
            "rank": rank,
            "title": title,
            "title_ko": title_ko,
            "summary_en": full_summary_en,
            "summary_ko": summary_ko,
            "url": url,
            "source": "NYTimes",
            "source_type": "foreign",
            "pub_date": pub,
            "translated": t_ok_title or t_ok_summ,
        })

    print(f"[fetch_top_news] NYTimes {len(articles)}건 수집 (번역={'가능' if _TRANSLATOR_AVAILABLE else '불가'})")
    return articles


# ─── 메인 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    fetched_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[fetch_top_news] 시작 — {fetched_at}")

    domestic  = fetch_domestic()
    bloomberg = fetch_bloomberg(top_n=10)
    nytimes   = fetch_nytimes(top_n=10)

    result = {
        "fetched_at": fetched_at,
        "domestic":   domestic,
        "bloomberg":  bloomberg,
        "nytimes":    nytimes,
    }

    out_path = OUTPUT_DIR / "step1_top_news.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch_top_news] 저장 완료 → {out_path}")
    print(
        f"[fetch_top_news] 국내 {len(domestic)}건 / Bloomberg {len(bloomberg)}건 / NYTimes {len(nytimes)}건"
    )


if __name__ == "__main__":
    main()
