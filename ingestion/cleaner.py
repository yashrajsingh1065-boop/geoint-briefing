import re
import logging
from html.parser import HTMLParser

from config import MIN_ARTICLE_BODY_CHARS

logger = logging.getLogger(__name__)


class _HTMLStripper(HTMLParser):
    """Minimal HTML stripper using only stdlib."""

    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(text: str) -> str:
    if not text:
        return ""
    try:
        stripper = _HTMLStripper()
        stripper.feed(text)
        return stripper.get_text()
    except Exception:
        # Fallback: crude tag removal via regex
        return re.sub(r"<[^>]+>", " ", text)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_articles(raw_articles: list[dict]) -> list[dict]:
    """
    Clean body text of each article.
    Drops articles whose cleaned body is shorter than MIN_ARTICLE_BODY_CHARS.
    Returns list of CleanArticle dicts (same shape as RawArticle, body is clean text).
    """
    cleaned = []
    dropped = 0
    for article in raw_articles:
        body = normalize_whitespace(strip_html(article.get("body", "")))
        title = normalize_whitespace(strip_html(article.get("title", "")))

        if len(body) < MIN_ARTICLE_BODY_CHARS:
            dropped += 1
            continue

        cleaned.append({**article, "title": title, "body": body})

    logger.info(
        "Cleaned %d articles, dropped %d (body < %d chars)",
        len(cleaned), dropped, MIN_ARTICLE_BODY_CHARS,
    )
    return cleaned
