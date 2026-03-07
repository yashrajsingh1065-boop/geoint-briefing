from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import trafilatura

from config import (
    GDELT_ENABLED, GDELT_QUERIES, GDELT_MAX_ARTICLES, GDELT_MAX_TOTAL,
    GDELT_FETCH_WORKERS,
    NEWSDATA_ENABLED, NEWSDATA_API_KEY, NEWSDATA_CATEGORIES, NEWSDATA_MAX_ARTICLES,
    WORLDNEWS_ENABLED, WORLDNEWS_API_KEY, WORLDNEWS_MAX_ARTICLES,
    FETCH_TIMEOUT_SECONDS, validate_feed_url,
)

logger = logging.getLogger(__name__)


def fetch_all_apis() -> list[dict]:
    """Fetch from all enabled News APIs. Returns article dicts with source_type field."""
    articles = []

    if GDELT_ENABLED:
        try:
            articles.extend(_fetch_gdelt())
        except Exception as exc:
            logger.warning("GDELT fetch failed: %s", type(exc).__name__)

    if NEWSDATA_ENABLED and NEWSDATA_API_KEY:
        try:
            articles.extend(_fetch_newsdata())
        except Exception as exc:
            logger.warning("NewsData fetch failed: %s", type(exc).__name__)

    if WORLDNEWS_ENABLED and WORLDNEWS_API_KEY:
        try:
            articles.extend(_fetch_worldnews())
        except Exception as exc:
            logger.warning("WorldNews fetch failed: %s", type(exc).__name__)

    return articles


def _fetch_gdelt() -> list[dict]:
    """Query GDELT DOC 2.0 API and fetch article bodies via trafilatura."""
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    for query in GDELT_QUERIES:
        try:
            resp = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": query,
                    "mode": "artlist",
                    "format": "json",
                    "timespan": "24h",
                    "sort": "hybridrel",
                    "maxrecords": GDELT_MAX_ARTICLES,
                    "sourcelang": "eng",
                },
                timeout=FETCH_TIMEOUT_SECONDS,
                headers={"User-Agent": "GeointBriefing/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("GDELT query '%s' failed: %s", query[:40], type(exc).__name__)
            continue

        for item in data.get("articles", []):
            url = item.get("url", "").strip()
            title = item.get("title", "").strip()
            if not url or not title or url in seen_urls:
                continue
            if not validate_feed_url(url):
                continue
            seen_urls.add(url)
            domain = urlparse(url).netloc or "unknown"
            candidates.append({
                "source_name": f"GDELT: {domain}",
                "url": url,
                "title": title,
                "body": "",
                "published_at": item.get("seendate"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source_type": "gdelt",
            })

        if len(candidates) >= GDELT_MAX_TOTAL:
            break

    candidates = candidates[:GDELT_MAX_TOTAL]

    # Fetch article bodies concurrently
    if candidates:
        with ThreadPoolExecutor(max_workers=GDELT_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_article_body, a["url"]): i
                for i, a in enumerate(candidates)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    candidates[idx]["body"] = future.result()
                except Exception:
                    pass

    # Drop articles with no/tiny body (cleaner would catch these anyway)
    articles = [a for a in candidates if len(a.get("body", "")) >= 100]
    logger.info("GDELT: %d candidates → %d with body text", len(candidates), len(articles))
    return articles


def _fetch_article_body(url: str) -> str:
    """Fetch and extract article body text using trafilatura. Returns '' on failure."""
    if not validate_feed_url(url):
        return ""
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            verify=True,
            headers={"User-Agent": "GeointBriefing/1.0"},
        )
        resp.raise_for_status()
        text = trafilatura.extract(resp.text) or ""
        return text
    except Exception:
        return ""


def _fetch_newsdata() -> list[dict]:
    """Fetch latest articles from NewsData.io API."""
    try:
        resp = requests.get(
            "https://newsdata.io/api/1/latest",
            params={
                "apikey": NEWSDATA_API_KEY,
                "language": "en",
                "category": NEWSDATA_CATEGORIES,
            },
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "GeointBriefing/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("NewsData API failed: %s", type(exc).__name__)
        return []

    articles = []
    for item in data.get("results", [])[:NEWSDATA_MAX_ARTICLES]:
        url = (item.get("link") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue
        source = item.get("source_name") or item.get("source_id") or "unknown"
        articles.append({
            "source_name": f"NewsData: {source}",
            "url": url,
            "title": title,
            "body": item.get("description") or item.get("content") or "",
            "published_at": item.get("pubDate"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "newsdata",
        })

    logger.info("NewsData: %d articles fetched", len(articles))
    return articles


def _fetch_worldnews() -> list[dict]:
    """Fetch articles from WorldNewsAPI."""
    try:
        resp = requests.get(
            "https://api.worldnewsapi.com/search-news",
            params={
                "api-key": WORLDNEWS_API_KEY,
                "text": "geopolitics OR conflict OR diplomacy OR military",
                "language": "en",
                "number": WORLDNEWS_MAX_ARTICLES,
            },
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "GeointBriefing/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("WorldNews API failed: %s", type(exc).__name__)
        return []

    articles = []
    for item in data.get("news", []):
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue
        country = item.get("source_country") or "unknown"
        articles.append({
            "source_name": f"WorldNews: {country}",
            "url": url,
            "title": title,
            "body": item.get("text") or "",
            "published_at": item.get("publish_date"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "worldnews",
        })

    logger.info("WorldNews: %d articles fetched", len(articles))
    return articles
