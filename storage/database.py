from __future__ import annotations

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS briefings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS events (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                briefing_id        INTEGER NOT NULL REFERENCES briefings(id),
                title              TEXT NOT NULL,
                summary            TEXT,
                consequence        TEXT,
                historical_context TEXT,
                regions            TEXT,
                actors             TEXT,
                urgency            INTEGER DEFAULT 3,
                article_count      INTEGER DEFAULT 0,
                created_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id     INTEGER REFERENCES events(id),
                briefing_id  INTEGER REFERENCES briefings(id),
                source_name  TEXT NOT NULL,
                url          TEXT NOT NULL UNIQUE,
                title        TEXT NOT NULL,
                body         TEXT,
                published_at TEXT,
                fetched_at   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_briefing ON events(briefing_id);
            CREATE INDEX IF NOT EXISTS idx_articles_briefing ON articles(briefing_id);
            CREATE INDEX IF NOT EXISTS idx_articles_event ON articles(event_id);

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL UNIQUE,
                indices_json TEXT NOT NULL,
                summary      TEXT,
                created_at   TEXT NOT NULL
            );
        """)
    logger.info("Database initialized at %s", DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_briefing(date: str) -> int:
    """Insert a new briefing row and return its id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO briefings (date, created_at, status) VALUES (?, ?, 'pending')",
            (date, _now()),
        )
        return cur.lastrowid


def save_articles(briefing_id: int, articles: list[dict]) -> list[int]:
    """
    Bulk-insert cleaned articles. Uses INSERT OR IGNORE on url.
    Returns list of inserted row ids (0 for ignored duplicates).
    """
    ids = []
    with _connect() as conn:
        for a in articles:
            cur = conn.execute(
                """INSERT OR IGNORE INTO articles
                   (briefing_id, source_name, url, title, body, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    briefing_id,
                    a.get("source_name", ""),
                    a.get("url", ""),
                    a.get("title", ""),
                    a.get("body", ""),
                    a.get("published_at"),
                    a.get("fetched_at", _now()),
                ),
            )
            ids.append(cur.lastrowid)
    return ids


def get_article_ids_for_briefing(briefing_id: int) -> dict[str, int]:
    """Return {url: id} mapping for all articles in this briefing."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, url FROM articles WHERE briefing_id = ?", (briefing_id,)
        ).fetchall()
    return {row["url"]: row["id"] for row in rows}


def save_event(briefing_id: int, analysis: dict, article_count: int = 0) -> int:
    """Insert one event row and return its id."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (briefing_id, title, summary, consequence, historical_context,
                regions, actors, urgency, article_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                briefing_id,
                analysis.get("title", "Untitled Event"),
                analysis.get("summary", ""),
                analysis.get("consequence", ""),
                analysis.get("historical_context", ""),
                json.dumps(analysis.get("regions", [])),
                json.dumps(analysis.get("actors", [])),
                int(analysis.get("urgency", 3)),
                article_count,
                _now(),
            ),
        )
        return cur.lastrowid


def link_articles_to_event(event_id: int, article_ids: list[int]) -> None:
    """Set event_id on a list of article rows."""
    if not article_ids:
        return
    placeholders = ",".join("?" * len(article_ids))
    with _connect() as conn:
        conn.execute(
            f"UPDATE articles SET event_id = ? WHERE id IN ({placeholders})",
            [event_id] + article_ids,
        )


def mark_briefing_complete(briefing_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE briefings SET status = 'complete' WHERE id = ?", (briefing_id,)
        )


def mark_briefing_error(briefing_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE briefings SET status = 'error' WHERE id = ?", (briefing_id,)
        )


def get_briefing_by_date(date: str) -> dict | None:
    """Return briefing row (as dict) or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM briefings WHERE date = ?", (date,)
        ).fetchone()
    return dict(row) if row else None


def get_events_for_briefing(briefing_id: int) -> list[dict]:
    """Return all events for a briefing, sorted by urgency desc. Includes top source names."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM events WHERE briefing_id = ?
               ORDER BY urgency DESC, article_count DESC""",
            (briefing_id,),
        ).fetchall()
        events = []
        for row in rows:
            e = dict(row)
            e["regions"] = json.loads(e["regions"] or "[]")
            e["actors"]  = json.loads(e["actors"]  or "[]")
            # Fetch distinct source names for this event (up to 3)
            src_rows = conn.execute(
                """SELECT DISTINCT source_name FROM articles
                   WHERE event_id = ? LIMIT 3""",
                (e["id"],),
            ).fetchall()
            e["sources"] = [r["source_name"] for r in src_rows]
            events.append(e)
    return events


def get_event_with_articles(event_id: int) -> dict | None:
    """Return event dict with nested 'articles' list, or None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        event = dict(row)
        event["regions"] = json.loads(event["regions"] or "[]")
        event["actors"]  = json.loads(event["actors"]  or "[]")
        articles = conn.execute(
            """SELECT source_name, url, title, published_at
               FROM articles WHERE event_id = ?
               ORDER BY published_at DESC""",
            (event_id,),
        ).fetchall()
        event["articles"] = [dict(a) for a in articles]
    return event


def save_market_snapshot(date_str: str, indices: list[dict], summary: str) -> None:
    """Insert or replace a market snapshot for the given date."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO market_snapshots
               (date, indices_json, summary, created_at)
               VALUES (?, ?, ?, ?)""",
            (date_str, json.dumps(indices), summary, _now()),
        )


def get_market_snapshot(date_str: str) -> dict | None:
    """Return market snapshot dict with parsed indices, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM market_snapshots WHERE date = ?", (date_str,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["indices"] = json.loads(result.get("indices_json") or "[]")
    return result


def list_briefing_dates() -> list[dict]:
    """Return list of {date, status, event_count} dicts, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT b.date, b.status,
                      COUNT(e.id) as event_count
               FROM briefings b
               LEFT JOIN events e ON e.briefing_id = b.id
               GROUP BY b.id
               ORDER BY b.date DESC""",
        ).fetchall()
    return [dict(r) for r in rows]
