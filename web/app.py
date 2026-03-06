import logging
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)


def _format_date(value: str) -> str:
    """Convert YYYY-MM-DD to DD-Mon'YY (e.g., 06-Mar'26)."""
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(str(value)[:10], "%Y-%m-%d")
        return d.strftime("%d-%b'%y")
    except Exception:
        return value


def create_app() -> FastAPI:
    app = FastAPI(title="Geoint Briefing", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["fmtdate"] = _format_date

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
        )
        today = date.today().isoformat()
        briefing = get_briefing_by_date(today)
        market = get_market_snapshot(today)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": today,
                 "status": "none", "market": market, "stories": [], "actions": []},
            )

        events = []
        stories = []
        actions = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])
            stories = get_active_stories_with_timelines()
            actions = get_pending_actions()
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
                "actions":  actions,
            },
        )

    # ── Historical briefing ────────────────────────────────────────────────────

    @app.get("/briefing/{date_str}", response_class=HTMLResponse)
    async def briefing_by_date(request: Request, date_str: str):
        from storage.database import (
            get_briefing_by_date, get_events_for_briefing, get_market_snapshot,
            get_active_stories_with_timelines, get_pending_actions, get_events_linked_to_stories,
        )
        briefing = get_briefing_by_date(date_str)
        market = get_market_snapshot(date_str)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": date_str,
                 "status": "none", "market": market, "stories": [], "actions": []},
            )

        events = []
        stories = []
        actions = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])
            stories = get_active_stories_with_timelines()
            actions = get_pending_actions()
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
                "actions":  actions,
            },
        )

    # ── Event detail ───────────────────────────────────────────────────────────

    @app.get("/event/{event_id}", response_class=HTMLResponse)
    async def event_detail(request: Request, event_id: int):
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

    # ── API: manual trigger ────────────────────────────────────────────────────

    @app.post("/api/trigger")
    async def trigger_pipeline():
        from scheduler.jobs import run_daily_pipeline
        t = threading.Thread(target=run_daily_pipeline, daemon=True, name="manual-pipeline")
        t.start()
        return JSONResponse({"status": "started"})

    # ── API: story actions ──────────────────────────────────────────────────────

    @app.post("/api/story/{story_id}/close")
    async def approve_close_story(story_id: int):
        from storage.database import close_story, resolve_story_action
        close_story(story_id)
        # Resolve any pending close actions for this story
        from storage.database import get_pending_actions
        for action in get_pending_actions():
            if action["story_id"] == story_id and action["action_type"] == "close":
                resolve_story_action(action["id"], "approved")
        return JSONResponse({"status": "closed", "story_id": story_id})

    @app.post("/api/story/action/{action_id}/dismiss")
    async def dismiss_action(action_id: int):
        from storage.database import resolve_story_action
        resolve_story_action(action_id, "dismissed")
        return JSONResponse({"status": "dismissed", "action_id": action_id})

    @app.post("/api/story/action/{action_id}/approve-merge")
    async def approve_merge(action_id: int):
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

    # ── API: resync stories + sectors on existing briefing ──────────────────────

    @app.post("/api/resync")
    async def resync():
        def _run():
            from storage.database import get_briefing_by_date
            from datetime import date as _date
            date_str = _date.today().isoformat()
            briefing = get_briefing_by_date(date_str)
            if not briefing or briefing["status"] != "complete":
                return
            # Story linking (builds on top, never alters existing links)
            try:
                from processing.story_linker import run_story_linking
                run_story_linking(briefing["id"], date_str)
            except Exception:
                pass
            # Sector + market refresh
            try:
                from market.fetcher import fetch_market_data, fetch_sector_data
                from storage.database import save_market_snapshot, get_market_snapshot
                existing = get_market_snapshot(date_str)
                indices = existing["indices"] if existing else fetch_market_data()
                sectors = fetch_sector_data()
                save_market_snapshot(date_str, indices, "", sectors)
            except Exception:
                pass
        t = threading.Thread(target=_run, daemon=True, name="resync")
        t.start()
        return JSONResponse({"status": "resync_started"})

    # ── API: market snapshot debug ─────────────────────────────────────────────

    @app.get("/api/market")
    async def market_snapshot():
        from storage.database import get_market_snapshot
        today = date.today().isoformat()
        market = get_market_snapshot(today)
        if not market:
            return JSONResponse({"date": today, "snapshot": None})
        return JSONResponse({"date": today, "snapshot": market})

    return app
