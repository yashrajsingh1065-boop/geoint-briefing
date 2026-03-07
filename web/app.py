import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from config import ADMIN_TOKEN

TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)

# ── Simple in-memory rate limiter ──────────────────────────────────────────────

_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMITS = {
    "trigger": (1, 3600),     # 1 request per hour
    "resync":  (5, 60),       # 5 requests per minute
    "action":  (20, 60),      # 20 requests per minute
}
# Pipeline lock to prevent concurrent runs
_pipeline_lock = threading.Lock()


def _check_rate_limit(client_ip: str, action: str) -> None:
    max_requests, window_seconds = _RATE_LIMITS.get(action, (60, 60))
    now = time.time()
    key = f"{client_ip}:{action}"
    # Prune old entries
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window_seconds]
    if len(_rate_limit_store[key]) >= max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    _rate_limit_store[key].append(now)


# ── Authentication ─────────────────────────────────────────────────────────────

def _verify_admin(request: Request) -> None:
    """Verify admin token from Authorization header or X-Admin-Token."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin access is disabled (ADMIN_TOKEN not configured).")
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.headers.get("x-admin-token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required. Provide Bearer token or X-Admin-Token header.")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")


# ── Date validation ────────────────────────────────────────────────────────────

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date_str: str) -> str:
    """Validate and return a safe date string, or raise 400."""
    if not _DATE_PATTERN.match(date_str):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    # Verify it's a real date
    try:
        from datetime import datetime as _dt
        _dt.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date value.")
    return date_str


def _format_date(value: str) -> str:
    """Convert YYYY-MM-DD to DD-Mon'YY (e.g., 06-Mar'26)."""
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(str(value)[:10], "%Y-%m-%d")
        return d.strftime("%d-%b'%y")
    except Exception:
        return value


def _sanitize_url(url: str) -> str:
    """Ensure a URL uses http/https scheme. Return '#' for unsafe URLs."""
    if not url:
        return "#"
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    return "#"


def create_app() -> FastAPI:
    app = FastAPI(title="Geoint Briefing", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["fmtdate"] = _format_date
    templates.env.filters["safe_url"] = _sanitize_url

    # ── Security headers middleware ────────────────────────────────────────────

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # ── Redirect root ──────────────────────────────────────────────────────────

    @app.get("/", response_class=RedirectResponse, include_in_schema=False)
    async def root():
        return RedirectResponse(url="/briefing/today")

    # ── Today's briefing ───────────────────────────────────────────────────────

    @app.get("/briefing/today", response_class=HTMLResponse)
    async def today_briefing(request: Request):
        from storage.database import (
            get_briefing_by_date, get_events_for_briefing, get_market_snapshot,
            get_active_stories_with_timelines, get_pending_actions, get_events_linked_to_stories,
            get_story_actors_and_regions,
        )
        from config import G20_COUNTRIES
        today = date.today().isoformat()
        briefing = get_briefing_by_date(today)
        market = get_market_snapshot(today)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": today,
                 "status": "none", "market": market, "stories": [], "low_coverage_stories": [], "actions": []},
            )

        events = []
        stories = []
        low_coverage_stories = []
        actions = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])
            all_stories = get_active_stories_with_timelines()
            actions = get_pending_actions()
            # Classify stories: G20 actors/regions → stories, rest → low_coverage_stories
            for story in all_stories:
                data = get_story_actors_and_regions(story["id"])
                is_g20 = bool(data["actors"] & G20_COUNTRIES or data["regions"] & G20_COUNTRIES)
                # Fallback: check title + narrative for G20 country names
                if not is_g20:
                    text = (story.get("title", "") + " " + story.get("narrative", "")).lower()
                    is_g20 = any(c.lower() in text for c in G20_COUNTRIES)
                if is_g20:
                    stories.append(story)
                else:
                    low_coverage_stories.append(story)
            # Filter out events that are part of stories (they show in the story timeline)
            story_event_ids = get_events_linked_to_stories(briefing["id"])
            events = [e for e in events if e["id"] not in story_event_ids]

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request":  request,
                "briefing": briefing,
                "events":   events,
                "date":     today,
                "status":   briefing["status"],
                "market":   market,
                "stories":  stories,
                "low_coverage_stories": low_coverage_stories,
                "actions":  actions,
            },
        )

    # ── Historical briefing ────────────────────────────────────────────────────

    @app.get("/briefing/{date_str}", response_class=HTMLResponse)
    async def briefing_by_date(request: Request, date_str: str):
        date_str = _validate_date(date_str)
        from storage.database import (
            get_briefing_by_date, get_events_for_briefing, get_market_snapshot,
            get_active_stories_with_timelines, get_pending_actions, get_events_linked_to_stories,
            get_story_actors_and_regions,
        )
        from config import G20_COUNTRIES
        briefing = get_briefing_by_date(date_str)
        market = get_market_snapshot(date_str)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": date_str,
                 "status": "none", "market": market, "stories": [], "low_coverage_stories": [], "actions": []},
            )

        events = []
        stories = []
        low_coverage_stories = []
        actions = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])
            all_stories = get_active_stories_with_timelines()
            actions = get_pending_actions()
            for story in all_stories:
                data = get_story_actors_and_regions(story["id"])
                is_g20 = bool(data["actors"] & G20_COUNTRIES or data["regions"] & G20_COUNTRIES)
                if not is_g20:
                    text = (story.get("title", "") + " " + story.get("narrative", "")).lower()
                    is_g20 = any(c.lower() in text for c in G20_COUNTRIES)
                if is_g20:
                    stories.append(story)
                else:
                    low_coverage_stories.append(story)
            story_event_ids = get_events_linked_to_stories(briefing["id"])
            events = [e for e in events if e["id"] not in story_event_ids]

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request":  request,
                "briefing": briefing,
                "events":   events,
                "date":     date_str,
                "status":   briefing["status"],
                "market":   market,
                "stories":  stories,
                "low_coverage_stories": low_coverage_stories,
                "actions":  actions,
            },
        )

    # ── Event detail ───────────────────────────────────────────────────────────

    @app.get("/event/{event_id}", response_class=HTMLResponse)
    async def event_detail(request: Request, event_id: int):
        if event_id < 1:
            raise HTTPException(status_code=400, detail="Invalid event ID.")
        from storage.database import get_event_with_articles
        event = get_event_with_articles(event_id)
        if event is None:
            return HTMLResponse("<h1>Event not found</h1>", status_code=404)

        return templates.TemplateResponse(
            "event_detail.html",
            {"request": request, "event": event},
        )

    # ── History ────────────────────────────────────────────────────────────────

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request):
        from storage.database import list_briefing_dates, get_closed_stories
        entries = list_briefing_dates()
        closed_stories = get_closed_stories()
        return templates.TemplateResponse(
            "history.html",
            {"request": request, "entries": entries, "closed_stories": closed_stories},
        )

    # ── API: status ────────────────────────────────────────────────────────────

    @app.get("/api/status")
    async def pipeline_status():
        from storage.database import get_briefing_by_date, get_events_for_briefing
        today = date.today().isoformat()
        briefing = get_briefing_by_date(today)
        if briefing is None:
            return JSONResponse({"date": today, "status": "none", "event_count": 0})

        event_count = 0
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])
            event_count = len(events)

        return JSONResponse({
            "date":        today,
            "status":      briefing["status"],
            "event_count": event_count,
        })

    # ── API: manual trigger (auth + rate limited) ─────────────────────────────

    @app.post("/api/trigger")
    async def trigger_pipeline(request: Request):
        _verify_admin(request)
        _check_rate_limit(request.client.host, "trigger")

        if not _pipeline_lock.acquire(blocking=False):
            return JSONResponse({"status": "already_running"}, status_code=409)

        def _run_with_lock():
            try:
                from scheduler.jobs import run_daily_pipeline
                run_daily_pipeline()
            finally:
                _pipeline_lock.release()

        t = threading.Thread(target=_run_with_lock, daemon=True, name="manual-pipeline")
        t.start()
        return JSONResponse({"status": "started"})

    # ── API: story actions (auth + rate limited) ──────────────────────────────

    @app.post("/api/story/{story_id}/close")
    async def approve_close_story(request: Request, story_id: int):
        _verify_admin(request)
        _check_rate_limit(request.client.host, "action")
        if story_id < 1:
            raise HTTPException(status_code=400, detail="Invalid story ID.")
        from storage.database import close_story, resolve_story_action
        close_story(story_id)
        # Resolve any pending close actions for this story
        from storage.database import get_pending_actions
        for action in get_pending_actions():
            if action["story_id"] == story_id and action["action_type"] == "close":
                resolve_story_action(action["id"], "approved")
        return JSONResponse({"status": "closed", "story_id": story_id})

    @app.post("/api/story/action/{action_id}/dismiss")
    async def dismiss_action(request: Request, action_id: int):
        _verify_admin(request)
        _check_rate_limit(request.client.host, "action")
        if action_id < 1:
            raise HTTPException(status_code=400, detail="Invalid action ID.")
        from storage.database import resolve_story_action
        resolve_story_action(action_id, "dismissed")
        return JSONResponse({"status": "dismissed", "action_id": action_id})

    @app.post("/api/story/action/{action_id}/approve-merge")
    async def approve_merge(request: Request, action_id: int):
        _verify_admin(request)
        _check_rate_limit(request.client.host, "action")
        if action_id < 1:
            raise HTTPException(status_code=400, detail="Invalid action ID.")
        from storage.database import resolve_story_action, merge_stories
        from storage.database import _connect
        with _connect() as conn:
            row = conn.execute("SELECT * FROM story_actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            return JSONResponse({"error": "action not found"}, status_code=404)
        action = dict(row)
        if action["action_type"] != "merge" or not action.get("merge_target_id"):
            return JSONResponse({"error": "not a merge action"}, status_code=400)
        merge_stories(action["story_id"], action["merge_target_id"])
        resolve_story_action(action_id, "approved")
        return JSONResponse({"status": "merged", "source": action["story_id"], "target": action["merge_target_id"]})

    # ── API: resync stories + sectors (auth + rate limited) ───────────────────

    @app.post("/api/resync")
    async def resync(request: Request):
        _verify_admin(request)
        _check_rate_limit(request.client.host, "resync")

        def _run():
            from storage.database import get_briefing_by_date
            from datetime import date as _date
            date_str = _date.today().isoformat()
            briefing = get_briefing_by_date(date_str)
            if not briefing or briefing["status"] != "complete":
                return
            # Story linking
            try:
                from processing.story_linker import run_story_linking
                run_story_linking(briefing["id"], date_str)
            except Exception:
                logger.warning("Resync story linking failed", exc_info=True)
            # Sector + market refresh
            try:
                from market.fetcher import fetch_all_market_data
                from storage.database import save_market_snapshot
                indices, sectors = fetch_all_market_data()
                if indices:
                    save_market_snapshot(date_str, indices, "", sectors)
            except Exception:
                logger.warning("Resync market fetch failed", exc_info=True)

        t = threading.Thread(target=_run, daemon=True, name="resync")
        t.start()
        return JSONResponse({"status": "resync_started"})

    # ── API: market snapshot ─────────────────────────────────────────────────

    @app.get("/api/market")
    async def market_snapshot():
        from storage.database import get_market_snapshot
        today = date.today().isoformat()
        market = get_market_snapshot(today)
        if not market:
            return JSONResponse({"date": today, "snapshot": None})
        return JSONResponse({"date": today, "snapshot": market})

    return app
