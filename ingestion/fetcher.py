from __future__ import annotations

import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser

from config import RSS_FEEDS, MAX_ARTICLES_PER_FEED, FETCH_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def fetch_all_feeds(feeds: list[dict] = None) -> list[dict]:
    """
    Concurrently fetch all RSS feeds.
    Returns a flat list of RawArticle dicts.
    """
    if feeds is None:
        feeds = RSS_FEEDS

    raw_articles = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_single_feed, f): f for f in feeds}
        for future in as_completed(futures):
            feed_config = futures[future]
            try:
                articles = future.result()
                raw_articles.extend(articles)
                logger.info("%-35s → %d articles", feed_config["name"], len(articles))
            except Exception as exc:
                logger.warning("Feed %s raised %s", feed_config["name"], exc)

    logger.info("Total raw articles fetched: %d", len(raw_articles))
    return raw_articles


def _fetch_single_feed(feed_config: dict) -> list[dict]:
    """
    Parse one RSS feed and return up to MAX_ARTICLES_PER_FEED RawArticle dicts.
    Never raises — returns [] on any error.
    """
    original_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(FETCH_TIMEOUT_SECONDS)
        parsed = feedparser.parse(feed_config["url"])
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", feed_config["name"], exc)
        return []
    finally:
        socket.setdefaulttimeout(original_timeout)

    articles = []
    for entry in parsed.entries[:MAX_ARTICLES_PER_FEED]:
        url = _get_url(entry)
        if not url:
            continue
        title = entry.get("title", "").strip()
        if not title:
            continue
        body = _get_body(entry)
        published_at = _get_published(entry)

        articles.append({
            "source_name": feed_config["name"],
            "url":         url,
            "title":       title,
            "body":        body,
            "published_at": published_at,
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
        })

    return articles


def _get_url(entry) -> str:
    """Extract canonical URL from a feed entry."""
    if hasattr(entry, "link") and entry.link:
        return entry.link.strip()
    if hasattr(entry, "id") and entry.id and entry.id.startswith("http"):
        return entry.id.strip()
    return ""


def _get_body(entry) -> str:
    """Extract the best available body text from a feed entry."""
    # Prefer full content over summary
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].get("value", "")
    if hasattr(entry, "summary") and entry.summary:
        return entry.summary
    if hasattr(entry, "description") and entry.description:
        return entry.description
    return ""


def _get_published(entry) -> str | None:
    """Extract published datetime as ISO string, or None."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return None
