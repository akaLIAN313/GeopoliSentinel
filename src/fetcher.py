"""
Phase 1 — Delta Data Pipeline
Fetches the past 24 h of geopolitically relevant articles from RSS feeds and
NewsAPI, filters by stream-specific keywords, deduplicates, caps at 20 articles,
and serialises the result to data/articles.json.

This module has no AI dependency — it is pure data collection.
Run standalone:  python src/fetcher.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

# Make sure sibling modules resolve when run directly or imported from src/
sys.path.insert(0, str(Path(__file__).parent))
from models import Article

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — sources and keywords per stream
# ---------------------------------------------------------------------------

RSS_SOURCES: dict[str, list[dict]] = {
    # Stream A: Israeli internal political crisis
    "A": [
        {"url": "https://www.timesofisrael.com/feed/", "name": "Times of Israel"},
        {"url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx", "name": "Jerusalem Post"},
        {"url": "https://www.haaretz.com/cmlink/1.628765", "name": "Haaretz"},
    ],
    # Stream B: Iranian internal crisis
    "B": [
        {"url": "https://www.al-monitor.com/rss", "name": "Al-Monitor"},
        {"url": "https://www.rferl.org/api/epiqr_content/rl/rferl/-/2143/2143", "name": "RFE/RL"},
    ],
    # Stream C: Military / proxy moves (reference baseline)
    "C": [
        {"url": "https://feeds.reuters.com/reuters/topNews", "name": "Reuters"},
        {"url": "https://www.aljazeera.com/xml/rss/all.xml", "name": "Al Jazeera"},
    ],
}

KEYWORDS: dict[str, list[str]] = {
    "A": [
        "netanyahu", "netanyahu trial", "ben-gvir", "smotrich",
        "coalition collapse", "judicial reform", "protest", "strike",
        "knesset", "far-right",
    ],
    "B": [
        "rial", "toman", "iran economy", "inflation", "irgc",
        "khamenei", "succession", "protest", "sanctions", "nuclear",
        "revolutionary guard",
    ],
    "C": [
        "air strike", "airstrike", "missile", "centcom", "hezbollah",
        "hamas", "houthi", "idf", "military operation", "attack",
        "proxy",
    ],
}

# NewsAPI — only streams that benefit from it; stream A is well covered by RSS
NEWSAPI_QUERIES: dict[str, str] = {
    "B": "Iran economy OR Rial OR IRGC OR inflation OR Khamenei",
    "C": "CENTCOM OR Hezbollah missile OR Iran attack OR IDF strike",
}

DATA_DIR = Path(__file__).parents[1] / "data"
ARTICLES_PATH = DATA_DIR / "articles.json"


# ---------------------------------------------------------------------------
# ID / deduplication helpers
# ---------------------------------------------------------------------------

def _article_id(title: str, published_at: datetime) -> str:
    """
    Stable 12-char ID = SHA256( normalised_title + hour_bucket ).
    Two articles with the same title published within the same UTC hour
    are treated as the same event across different sources.
    """
    hour_bucket = published_at.replace(minute=0, second=0, microsecond=0).isoformat()
    raw = f"{title.lower().strip()}|{hour_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in keywords)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_rss_stream(stream: str, cutoff: datetime) -> list[Article]:
    """Fetch and keyword-filter RSS articles for one stream."""
    articles: list[Article] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ResonanceBot/1.0; +github.com)"}

    for source in RSS_SOURCES[stream]:
        try:
            feed = feedparser.parse(source["url"], request_headers=headers)
        except Exception as exc:
            logger.warning("RSS fetch failed — %s: %s", source["name"], exc)
            continue

        for entry in feed.entries:
            # --- parse publish time ---
            if getattr(entry, "published_parsed", None):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            else:
                published = datetime.now(timezone.utc)

            if published < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "")[:500].strip()

            if not title:
                continue

            if not _matches_keywords(f"{title} {summary}", KEYWORDS[stream]):
                continue

            articles.append(Article(
                id=_article_id(title, published),
                title=title,
                summary=summary,
                source=source["name"],
                stream=stream,
                published_at=published,
                url=entry.get("link", ""),
            ))

    logger.info("Stream %s RSS: %d articles", stream, len(articles))
    return articles


def fetch_newsapi_stream(stream: str, cutoff: datetime) -> list[Article]:
    """Fetch articles from NewsAPI for the given stream (if configured)."""
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key or stream not in NEWSAPI_QUERIES:
        return []

    articles: list[Article] = []
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": NEWSAPI_QUERIES[stream],
                "from": cutoff.strftime("%Y-%m-%dT%H:%M:%S"),
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": 20,
                "apiKey": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()

        for item in resp.json().get("articles", []):
            title = (item.get("title") or "").strip()
            description = (item.get("description") or "")[:500].strip()

            if not title or not _matches_keywords(f"{title} {description}", KEYWORDS[stream]):
                continue

            raw_date = item.get("publishedAt", "")
            try:
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                published = datetime.now(timezone.utc)

            articles.append(Article(
                id=_article_id(title, published),
                title=title,
                summary=description,
                source=item.get("source", {}).get("name", "NewsAPI"),
                stream=stream,
                published_at=published,
                url=item.get("url", ""),
            ))

    except Exception as exc:
        logger.warning("NewsAPI failed for stream %s: %s", stream, exc)

    logger.info("Stream %s NewsAPI: %d articles", stream, len(articles))
    return articles


# ---------------------------------------------------------------------------
# Dedup + cap
# ---------------------------------------------------------------------------

def deduplicate(articles: list[Article]) -> list[Article]:
    """Keep the first occurrence of each id, sorted newest-first."""
    seen: set[str] = set()
    unique: list[Article] = []
    for article in sorted(articles, key=lambda a: a.published_at, reverse=True):
        if article.id not in seen:
            seen.add(article.id)
            unique.append(article)
    return unique


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_pipeline() -> list[Article]:
    """
    Fetch delta articles from all streams for the past 24 h.
    Returns a deduplicated, capped list (max 20) ready for the AI analysis step.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    raw: list[Article] = []

    for stream in ("A", "B", "C"):
        raw.extend(fetch_rss_stream(stream, cutoff))
        raw.extend(fetch_newsapi_stream(stream, cutoff))

    unique = deduplicate(raw)
    capped = unique[:20]  # hard cap — context budget protection

    counts = {s: sum(1 for a in capped if a.stream == s) for s in "ABC"}
    logger.info(
        "Pipeline done: %d unique → %d sent to AI  (A=%d B=%d C=%d)",
        len(unique), len(capped), counts["A"], counts["B"], counts["C"],
    )
    return capped


def save_articles(articles: list[Article], path: Path = ARTICLES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            [a.model_dump(mode="json") for a in articles],
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )


def load_articles(path: Path = ARTICLES_PATH) -> list[Article]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [Article(**item) for item in data]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    articles = run_pipeline()
    save_articles(articles)
    print(f"Saved {len(articles)} articles → {ARTICLES_PATH}")
