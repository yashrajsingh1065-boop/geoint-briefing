import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "briefings.db"

# ── Claude API ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS  = 1500
CLAUDE_TEMPERATURE = 0.3

# ── Scheduler ──────────────────────────────────────────────────────────────────
SCHEDULE_HOUR   = 6
SCHEDULE_MINUTE = 0

# ── Processing thresholds ──────────────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD = 0.72   # lower = more aggressive dedup
CLUSTER_DISTANCE_THRESHOLD = 0.60   # higher = merge more related stories into one event
MIN_ARTICLE_BODY_CHARS     = 150
MAX_ARTICLES_PER_FEED      = 30
MAX_CLUSTERS_PER_BRIEFING  = 20
FETCH_TIMEOUT_SECONDS      = 15

# ── Regions ────────────────────────────────────────────────────────────────────
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
