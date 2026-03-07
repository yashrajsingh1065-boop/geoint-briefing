import logging
import os
import threading
import webbrowser
from datetime import date

from config import OPEN_BROWSER

logger = logging.getLogger(__name__)

# Pipeline lock to prevent concurrent runs from scheduler + manual trigger
_pipeline_lock = threading.Lock()

# Import lazily inside the function to avoid circular imports at module load time


def _fetch_market_data(date_str: str) -> None:
    """Fetch market indices and save snapshot. Always runs independently."""
    try:
        from market.fetcher import fetch_all_market_data
        from storage.database import save_market_snapshot

        logger.info("Fetching market indices + sectors...")
        indices, sectors = fetch_all_market_data()
        if indices:
            save_market_snapshot(date_str, indices, "", sectors)
            logger.info("Market snapshot saved (%d indices, %d sectors)", len(indices), len(sectors))
    except Exception as exc:
        logger.warning("Market data fetch failed (non-fatal): %s", type(exc).__name__)


def run_daily_pipeline() -> None:
    """
    The main pipeline orchestrator. Runs all stages in sequence:
    fetch -> clean -> save -> deduplicate -> cluster -> analyze -> persist -> open browser.
    Safe to call multiple times per day — skips briefing if already complete,
    but always refreshes market data.
    """
    if not _pipeline_lock.acquire(blocking=False):
        logger.info("Pipeline already running — skipping concurrent invocation.")
        return

    try:
        _run_pipeline_locked()
    finally:
        _pipeline_lock.release()


def _run_pipeline_locked() -> None:
    """Internal pipeline logic, called while holding the lock."""
    from storage import database as db
    from ingestion.fetcher import fetch_all_feeds
    from ingestion.cleaner import clean_articles
    from processing.deduplicator import deduplicate
    from processing.clusterer import cluster_into_events
    from ai.analyst import analyze_all_events
    from config import RSS_FEEDS

    date_str = date.today().isoformat()
    logger.info("=== Daily pipeline starting for %s ===", date_str)

    # Always refresh market data regardless of briefing status
    _fetch_market_data(date_str)

    # Skip briefing pipeline if already done
    existing = db.get_briefing_by_date(date_str)
    if existing and existing["status"] == "complete":
        logger.info("Briefing for %s already complete — skipping.", date_str)
        return

    briefing_id = (
        existing["id"]
        if existing
        else db.create_briefing(date_str)
    )

    try:
        # 1a. Fetch RSS
        logger.info("Step 1/6: Fetching RSS feeds...")
        raw_articles = fetch_all_feeds(RSS_FEEDS)
        logger.info("Fetched %d RSS articles", len(raw_articles))

        # 1b. Fetch from News APIs
        from ingestion.api_fetcher import fetch_all_apis
        api_articles = fetch_all_apis()
        logger.info("Fetched %d API articles", len(api_articles))
        raw_articles.extend(api_articles)
        logger.info("Total raw articles (RSS + API): %d", len(raw_articles))

        # 2. Clean
        logger.info("Step 2/6: Cleaning articles...")
        clean = clean_articles(raw_articles)
        logger.info("%d articles after cleaning", len(clean))

        if not clean:
            logger.error("No articles survived cleaning — aborting.")
            db.mark_briefing_error(briefing_id)
            return

        # 3. Save raw articles to DB
        logger.info("Step 3/6: Saving articles to database...")
        db.save_articles(briefing_id, clean)
        url_to_id = db.get_article_ids_for_briefing(briefing_id)

        # 4. Deduplicate
        logger.info("Step 4/6: Deduplicating...")
        deduped = deduplicate(clean)
        logger.info("%d articles after deduplication", len(deduped))

        # 5. Cluster into events
        logger.info("Step 5/6: Clustering into events...")
        clusters = cluster_into_events(deduped)
        logger.info("%d event clusters identified", len(clusters))

        if not clusters:
            logger.error("No clusters formed — aborting.")
            db.mark_briefing_error(briefing_id)
            return

        # 6. Analyze with Claude
        logger.info("Step 6/6: Analyzing events with Claude AI...")
        analyses = analyze_all_events(clusters)

        # 7. Persist events and link articles
        for cluster, analysis in zip(clusters, analyses):
            article_count = len(cluster["articles"])
            event_id = db.save_event(briefing_id, analysis, article_count)

            # Map articles back to their DB ids
            article_ids = [
                url_to_id[a["url"]]
                for a in cluster["articles"]
                if a["url"] in url_to_id
            ]
            db.link_articles_to_event(event_id, article_ids)

        # 7b. Story linking — match events to ongoing stories
        logger.info("Step 7/7: Linking events to stories...")
        try:
            from processing.story_linker import run_story_linking
            run_story_linking(briefing_id, date_str)
        except Exception as exc:
            logger.warning("Story linking failed (non-fatal): %s", type(exc).__name__)

        db.mark_briefing_complete(briefing_id)
        logger.info("=== Briefing complete: %d events saved ===", len(analyses))

        # Open dashboard in browser (configurable)
        if _should_open_browser():
            _open_browser_delayed()

    except Exception as exc:
        logger.exception("Pipeline failed: %s", type(exc).__name__)
        db.mark_briefing_error(briefing_id)
        raise


def _should_open_browser() -> bool:
    """Check if browser should be opened based on configuration."""
    if OPEN_BROWSER == "false":
        return False
    if OPEN_BROWSER == "true":
        return True
    # "auto" — only on desktop environments
    return (
        os.environ.get("DISPLAY")
        or os.name == "nt"
        or __import__("sys").platform == "darwin"
    )


def _open_browser_delayed() -> None:
    """Open the dashboard in the default browser after a short delay."""
    def _open():
        import time
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8000/briefing/today")

    t = threading.Thread(target=_open, daemon=True)
    t.start()
