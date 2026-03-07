import os
import ipaddress
import logging
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "briefings.db"

# ── Claude API ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS  = 1500
CLAUDE_TEMPERATURE = 0.3

# ── Authentication ─────────────────────────────────────────────────────────────
# Required for all POST endpoints. Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# ── Scheduler ──────────────────────────────────────────────────────────────────
SCHEDULE_HOUR     = 6
SCHEDULE_MINUTE   = 30
SCHEDULE_TIMEZONE = os.environ.get("SCHEDULE_TIMEZONE", "Asia/Kolkata")

# ── Processing thresholds ──────────────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD = 0.55   # TF-IDF fallback: lower = more aggressive dedup
CLUSTER_DISTANCE_THRESHOLD = 0.70   # TF-IDF fallback: higher = merge more related stories
MIN_ARTICLE_BODY_CHARS     = 150
MAX_ARTICLES_PER_FEED      = 30
MAX_CLUSTERS_PER_BRIEFING  = 20
FETCH_TIMEOUT_SECONDS      = 15
MAX_FEED_SIZE_BYTES        = 10 * 1024 * 1024  # 10 MB max per feed download

# ── Semantic Embeddings (sentence-transformers) ──────────────────────────────
EMBEDDING_MODEL_NAME           = "all-MiniLM-L6-v2"
DEDUP_SEMANTIC_THRESHOLD       = 0.72   # semantic: higher = stricter (0.72 ≈ TF-IDF 0.55)
CLUSTER_SEMANTIC_DISTANCE      = 0.65   # semantic distance threshold (1 - sim)
STORY_SEMANTIC_THRESHOLD       = 0.35   # semantic: story matching candidate threshold (Claude confirms)

# ── Live Stories ─────────────────────────────────────────────────────────────
STORY_MATCH_SIMILARITY_THRESHOLD = 0.30  # TF-IDF fallback candidate threshold
STORY_DORMANT_DAYS               = 15    # auto-close after N days with no new events
MIN_ARTICLES_FOR_STORY           = 3     # minimum articles in an event to consider it for a new story
NARRATIVE_MAX_CHARS              = 100_000  # cap story narrative growth

# ── Less Coverage Stories ────────────────────────────────────────────────
MIN_ARTICLES_FOR_LOW_COVERAGE_STORY = 1
LOW_COVERAGE_DORMANT_DAYS           = 30
LOW_COVERAGE_MAX_NEW_PER_RUN        = 5
LOW_COVERAGE_PROMOTE_THRESHOLD      = 3   # promote to 'full' if 3+ events match in one run

# ── News API Sources ──────────────────────────────────────────────────────
GDELT_ENABLED = os.environ.get("GDELT_ENABLED", "true").lower() == "true"
GDELT_QUERIES = [
    "(theme:MILITARY OR theme:ARMED_CONFLICT)",
    "(theme:DIPLOMACY OR theme:FOREIGN_POLICY)",
    "(theme:PROTEST OR theme:POLITICAL_TURMOIL)",
]
GDELT_MAX_ARTICLES = 75      # per query
GDELT_MAX_TOTAL = 100        # cap after all queries
GDELT_FETCH_WORKERS = 5      # concurrent trafilatura fetches

NEWSDATA_ENABLED = os.environ.get("NEWSDATA_ENABLED", "false").lower() == "true"
NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY", "")
NEWSDATA_CATEGORIES = "politics,world"
NEWSDATA_MAX_ARTICLES = 50

WORLDNEWS_ENABLED = os.environ.get("WORLDNEWS_ENABLED", "false").lower() == "true"
WORLDNEWS_API_KEY = os.environ.get("WORLDNEWS_API_KEY", "")
WORLDNEWS_MAX_ARTICLES = 50

# ── Security ─────────────────────────────────────────────────────────────────
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "auto")  # "auto", "true", "false"
LOG_LEVEL    = os.environ.get("LOG_LEVEL", "INFO").upper()


def validate_feed_url(url: str) -> bool:
    """Validate that a feed URL is safe (not pointing to internal services)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Block localhost variants
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return False
        # Block cloud metadata endpoints
        if hostname in ("169.254.169.254", "metadata.google.internal"):
            return False
        # Block private IP ranges
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # hostname is a domain name, not an IP — OK
        return True
    except Exception:
        return False

# ── Regions ────────────────────────────────────────────────────────────────────
# Top-20 GDP countries (used for story classification)
G20_COUNTRIES = {
    "United States", "China", "Germany", "Japan", "India",
    "United Kingdom", "France", "Italy", "Brazil", "Canada",
    "Russia", "Australia", "South Korea", "Mexico", "Indonesia",
    "Turkey", "Saudi Arabia", "Netherlands", "Switzerland", "Spain",
    # Common aliases
    "USA", "US", "UK",
}

REGIONS = [
    "India", "United States", "China", "Russia", "European Union",
    "Middle East", "Africa", "Southeast Asia", "Latin America",
    "United Kingdom", "Japan", "South Korea", "Pakistan", "Iran",
    "Israel", "Ukraine", "NATO", "United Nations", "Global",
]

# ── RSS Feeds — tier-1 credible, fact-checked global sources ───────────────────
RSS_FEEDS = [
    {"name": "Reuters",                  "url": "https://feeds.reuters.com/reuters/worldNews"},
    {"name": "BBC World",                "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Associated Press",         "url": "https://rsshub.app/apnews/topics/ap-top-news"},
    {"name": "Al Jazeera",               "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "The Guardian",             "url": "https://www.theguardian.com/world/rss"},
    {"name": "Financial Times",          "url": "https://www.ft.com/world?format=rss"},
    {"name": "The Economist",            "url": "https://www.economist.com/international/rss.xml"},
    {"name": "Foreign Policy",           "url": "https://foreignpolicy.com/feed/"},
    {"name": "NPR World",                "url": "https://feeds.npr.org/1004/rss.xml"},
    {"name": "France 24",                "url": "https://www.france24.com/en/rss"},
    {"name": "DW World",                 "url": "https://rss.dw.com/rdf/rss-en-world"},
    {"name": "Der Spiegel International","url": "https://www.spiegel.de/international/index.rss"},
    {"name": "The Hindu",                "url": "https://www.thehindu.com/news/international/feeder/default.rss"},
    {"name": "Nikkei Asia",              "url": "https://asia.nikkei.com/rss/feed/nar"},
    {"name": "Middle East Eye",          "url": "https://www.middleeasteye.net/rss"},
    {"name": "Dawn",                     "url": "https://www.dawn.com/feeds/home"},
    {"name": "The Straits Times",        "url": "https://www.straitstimes.com/news/world/rss.xml"},
    {"name": "Euronews",                 "url": "https://www.euronews.com/rss?level=theme&name=news"},
    {"name": "VOA News",                 "url": "https://feeds.voanews.com/voaspecialenglish/world"},
    {"name": "PBS NewsHour",             "url": "https://www.pbs.org/newshour/feeds/rss/world"},
]
