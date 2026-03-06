import re
import logging
from html.parser import HTMLParser

import bleach

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

from config import MIN_ARTICLE_BODY_CHARS

logger = logging.getLogger(__name__)

# Tags to completely remove (including their content)
_STRIP_CONTENT_TAGS = {"script", "style", "noscript", "iframe", "object", "embed"}


class _HTMLStripper(HTMLParser):
    """HTML stripper that removes script/style content and extracts text."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in _STRIP_CONTENT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in _STRIP_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(text: str) -> str:
    """Strip HTML tags and dangerous content, returning plain text.

    Uses trafilatura for semantic article extraction when available,
    falling back to the bleach + HTMLParser triple-pass approach.
    """
    if not text:
        return ""

    # Primary: trafilatura extracts main article text from HTML
    if _HAS_TRAFILATURA:
        try:
            extracted = trafilatura.extract(text, include_comments=False, favor_precision=True)
            if extracted and len(extracted) >= MIN_ARTICLE_BODY_CHARS:
                return extracted
        except Exception:
            pass  # fall through to legacy approach

    # Fallback: triple-pass cleaning
    try:
        # First pass: remove dangerous tags AND their content via regex
        for tag in ("script", "style", "noscript", "iframe", "object", "embed"):
            text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        # Second pass: use bleach to strip remaining tags
        cleaned = bleach.clean(text, tags=set(), strip=True)
        # Third pass: use HTMLParser for any remaining entities
        stripper = _HTMLStripper()
        stripper.feed(cleaned)
        return stripper.get_text()
    except Exception:
        # Last resort: crude tag removal via regex
        for tag in ("script", "style", "noscript", "iframe"):
            text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", text, flags=re.DOTALL | re.IGNORECASE)
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
