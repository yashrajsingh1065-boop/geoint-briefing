import logging
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Geoint Briefing", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # ── Redirect root ──────────────────────────────────────────────────────────

    @app.get("/", response_class=RedirectResponse, include_in_schema=False)
    async def root():
        return RedirectResponse(url="/briefing/today")

    # ── Today's briefing ───────────────────────────────────────────────────────

    @app.get("/briefing/today", response_class=HTMLResponse)
    async def today_briefing(request: Request):
        from storage.database import get_briefing_by_date, get_events_for_briefing
        today = date.today().isoformat()
        briefing = get_briefing_by_date(today)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": today, "status": "none"},
            )

        events = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request":  request,
                "briefing": briefing,
                "events":   events,
                "date":     today,
                "status":   briefing["status"],
            },
        )

    # ── Historical briefing ────────────────────────────────────────────────────

    @app.get("/briefing/{date_str}", response_class=HTMLResponse)
    async def briefing_by_date(request: Request, date_str: str):
        from storage.database import get_briefing_by_date, get_events_for_briefing
        briefing = get_briefing_by_date(date_str)

        if briefing is None:
            return templates.TemplateResponse(
                "dashboard.html",
                {"request": request, "briefing": None, "events": [], "date": date_str, "status": "none"},
            )

        events = []
        if briefing["status"] == "complete":
            events = get_events_for_briefing(briefing["id"])

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request":  request,
                "briefing": briefing,
                "events":   events,
                "date":     date_str,
                "status":   briefing["status"],
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
        from storage.database import list_briefing_dates
        entries = list_briefing_dates()
        return templates.TemplateResponse(
            "history.html",
            {"request": request, "entries": entries},
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

    return app
