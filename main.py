import logging
import os
import sys
import threading
from datetime import date

from zoneinfo import ZoneInfo
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from config import SCHEDULE_HOUR, SCHEDULE_MINUTE, SCHEDULE_TIMEZONE, ANTHROPIC_API_KEY, ADMIN_TOKEN, LOG_LEVEL
from storage.database import init_db, get_briefing_by_date

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _start_pipeline_in_background() -> None:
    from scheduler.jobs import run_daily_pipeline
    t = threading.Thread(target=run_daily_pipeline, daemon=True, name="pipeline")
    t.start()


def main() -> None:
    # 0. Validate critical configuration
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set — pipeline will not work.")
        sys.exit(1)
    if not ADMIN_TOKEN:
        logger.warning(
            "ADMIN_TOKEN is not set — POST endpoints will reject all requests. "
            "Set ADMIN_TOKEN in .env to enable admin actions."
        )

    # 1. Initialize database
    init_db()

    # 2. Register daily cron job
    tz = ZoneInfo(SCHEDULE_TIMEZONE)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _start_pipeline_in_background,
        trigger="cron",
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        id="daily_briefing",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — daily briefing will run at %02d:%02d local time",
        SCHEDULE_HOUR, SCHEDULE_MINUTE,
    )

    # 3. Run pipeline immediately if no briefing for today
    today = date.today().isoformat()
    existing = get_briefing_by_date(today)
    if existing is None or existing["status"] in ("pending", "error"):
        logger.info("No complete briefing for today — running pipeline now...")
        _start_pipeline_in_background()
    else:
        logger.info("Briefing for %s already exists (status: %s)", today, existing["status"])

    # 4. Start FastAPI server (blocks until Ctrl-C)
    from web.app import create_app
    app = create_app()
    port = int(os.environ.get("PORT", 8000))
    logger.info("Dashboard → http://0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
