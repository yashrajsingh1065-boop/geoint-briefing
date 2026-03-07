from __future__ import annotations

import os
import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH, NARRATIVE_MAX_CHARS

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _secure_db_permissions() -> None:
    """Set restrictive file permissions on the database and WAL files."""
    for suffix in ("", "-shm", "-wal"):
        path = Path(str(DB_PATH) + suffix)
        if path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


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

            CREATE TABLE IF NOT EXISTS stories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                narrative       TEXT NOT NULL DEFAULT '',
                urgency         INTEGER DEFAULT 3,
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      TEXT NOT NULL,
                last_event_date TEXT,
                closed_at       TEXT
            );

            CREATE TABLE IF NOT EXISTS story_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                story_id     INTEGER NOT NULL REFERENCES stories(id),
                event_id     INTEGER REFERENCES events(id),
                event_date   TEXT NOT NULL,
                headline     TEXT NOT NULL DEFAULT '',
                summary_line TEXT NOT NULL DEFAULT '',
                entry_type   TEXT NOT NULL DEFAULT 'live',
                added_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS story_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                story_id        INTEGER NOT NULL REFERENCES stories(id),
                action_type     TEXT NOT NULL,
                merge_target_id INTEGER REFERENCES stories(id),
                reason          TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_story_events_story ON story_events(story_id);
            CREATE INDEX IF NOT EXISTS idx_story_events_event ON story_events(event_id);
            CREATE INDEX IF NOT EXISTS idx_story_actions_story ON story_actions(story_id);
            CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL UNIQUE,
                indices_json TEXT NOT NULL,
                sectors_json TEXT NOT NULL DEFAULT '[]',
                summary      TEXT,
                created_at   TEXT NOT NULL
            );
        """)
        # Idempotent migration: add coverage_tier column
        try:
            conn.execute("ALTER TABLE stories ADD COLUMN coverage_tier TEXT NOT NULL DEFAULT 'full'")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent migration: add source_type column to articles
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN source_type TEXT NOT NULL DEFAULT 'rss'")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stories_tier ON stories(coverage_tier)")
    _secure_db_permissions()
    logger.info("Database initialized")


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
                   (briefing_id, source_name, url, title, body, published_at, fetched_at, source_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    briefing_id,
                    a.get("source_name", ""),
                    a.get("url", ""),
                    a.get("title", ""),
                    a.get("body", ""),
                    a.get("published_at"),
                    a.get("fetched_at", _now()),
                    a.get("source_type", "rss"),
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


def save_market_snapshot(date_str: str, indices: list[dict], summary: str, sectors: list[dict] | None = None) -> None:
    """Insert or replace a market snapshot for the given date."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO market_snapshots
               (date, indices_json, sectors_json, summary, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (date_str, json.dumps(indices), json.dumps(sectors or []), summary, _now()),
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
    all_sectors = json.loads(result.get("sectors_json") or "[]")
    result["gainers"] = [s for s in all_sectors if s.get("pct_change", 0) >= 0][:5]
    result["losers"] = [s for s in all_sectors if s.get("pct_change", 0) < 0][-5:][::-1]  # worst first
    # If all positive, take bottom 5 as "losers" (least gain)
    if not result["losers"] and len(all_sectors) > 5:
        result["losers"] = all_sectors[-5:][::-1]
    return result


# ── Story CRUD ────────────────────────────────────────────────────────────────


def get_active_stories() -> list[dict]:
    """Return all active stories with basic info."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM stories WHERE status = 'active' ORDER BY urgency DESC, last_event_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_story(title: str, narrative: str, urgency: int, event_date: str, coverage_tier: str = "full") -> int:
    """Create a new live story and return its id."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO stories (title, narrative, urgency, status, created_at, last_event_date, coverage_tier)
               VALUES (?, ?, ?, 'active', ?, ?, ?)""",
            (title, narrative, max(1, min(5, urgency)), _now(), event_date, coverage_tier),
        )
        return cur.lastrowid


def link_event_to_story(story_id: int, event_id: int, event_date: str, summary_line: str, headline: str = "") -> None:
    """Link an event to a story with a summary line for the timeline."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO story_events (story_id, event_id, event_date, headline, summary_line, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (story_id, event_id, event_date, headline or summary_line, summary_line, _now()),
        )


def add_historical_timeline_entry(story_id: int, event_date: str, headline: str, summary_line: str, entry_type: str = "arc") -> None:
    """Add a historical timeline entry (no real event_id) for backfill. Skips duplicates."""
    with _connect() as conn:
        existing = conn.execute(
            """SELECT id FROM story_events
               WHERE story_id = ? AND event_date = ? AND headline = ?""",
            (story_id, event_date, headline),
        ).fetchone()
        if existing:
            return
        conn.execute(
            """INSERT INTO story_events (story_id, event_id, event_date, headline, summary_line, entry_type, added_at)
               VALUES (?, NULL, ?, ?, ?, ?, ?)""",
            (story_id, event_date, headline, summary_line, entry_type, _now()),
        )


def update_story(story_id: int, narrative_addition: str, urgency: int, last_event_date: str) -> None:
    """Append to narrative, update urgency and last event date."""
    with _connect() as conn:
        row = conn.execute("SELECT narrative FROM stories WHERE id = ?", (story_id,)).fetchone()
        if row:
            new_narrative = (row["narrative"] + "\n\n" + narrative_addition).strip()
            # Cap narrative size to prevent unbounded growth
            if len(new_narrative) > NARRATIVE_MAX_CHARS:
                new_narrative = new_narrative[:NARRATIVE_MAX_CHARS]
            conn.execute(
                """UPDATE stories SET narrative = ?, urgency = ?, last_event_date = ?
                   WHERE id = ?""",
                (new_narrative, max(1, min(5, urgency)), last_event_date, story_id),
            )


def close_story(story_id: int) -> None:
    """Mark a story as closed."""
    with _connect() as conn:
        conn.execute(
            "UPDATE stories SET status = 'closed', closed_at = ? WHERE id = ?",
            (_now(), story_id),
        )


def get_story_with_timeline(story_id: int) -> dict | None:
    """Return a story with its timeline entries (newest first)."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        if not row:
            return None
        story = dict(row)
        timeline = conn.execute(
            """SELECT se.event_date, se.headline, se.summary_line, se.entry_type,
                      se.event_id, e.title as event_title, e.urgency
               FROM story_events se
               LEFT JOIN events e ON e.id = se.event_id
               WHERE se.story_id = ?
               ORDER BY se.event_date DESC, se.added_at DESC""",
            (story_id,),
        ).fetchall()
        story["timeline"] = [
            {
                "event_date": t["event_date"],
                "title": t["event_title"] or t["headline"],
                "summary_line": t["summary_line"],
                "event_id": t["event_id"],
                "entry_type": t["entry_type"] or "live",
                "urgency": t["urgency"] or 0,
            }
            for t in timeline
        ]
    return story


def get_active_stories_with_timelines() -> list[dict]:
    """Return all active stories with full timelines, sorted by urgency desc."""
    with _connect() as conn:
        stories = conn.execute(
            "SELECT * FROM stories WHERE status = 'active' ORDER BY urgency DESC, last_event_date DESC"
        ).fetchall()
        if not stories:
            return []
        story_ids = [s["id"] for s in stories]
        placeholders = ",".join("?" * len(story_ids))
        all_timeline = conn.execute(
            f"""SELECT se.story_id, se.event_date, se.headline, se.summary_line, se.entry_type,
                       se.event_id, e.title as event_title, e.urgency
                FROM story_events se
                LEFT JOIN events e ON e.id = se.event_id
                WHERE se.story_id IN ({placeholders})
                ORDER BY se.event_date DESC, se.added_at DESC""",
            story_ids,
        ).fetchall()
    from collections import defaultdict
    timeline_by_story: dict[int, list] = defaultdict(list)
    for t in all_timeline:
        timeline_by_story[t["story_id"]].append({
            "event_date": t["event_date"],
            "title": t["event_title"] or t["headline"],
            "summary_line": t["summary_line"],
            "event_id": t["event_id"],
            "entry_type": t["entry_type"] or "live",
            "urgency": t["urgency"] or 0,
        })
    result = []
    for s in stories:
        story = dict(s)
        story["timeline"] = timeline_by_story.get(s["id"], [])
        result.append(story)
    return result


def promote_story(story_id: int) -> None:
    """Promote a low-coverage story to full coverage tier."""
    with _connect() as conn:
        conn.execute(
            "UPDATE stories SET coverage_tier = 'full' WHERE id = ?",
            (story_id,),
        )


def get_story_actors_and_regions(story_id: int) -> dict:
    """Return aggregated actors and regions from all events linked to a story."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT e.actors, e.regions
               FROM story_events se
               JOIN events e ON e.id = se.event_id
               WHERE se.story_id = ? AND se.event_id IS NOT NULL""",
            (story_id,),
        ).fetchall()
    actors = set()
    regions = set()
    for r in rows:
        actors.update(json.loads(r["actors"] or "[]"))
        regions.update(json.loads(r["regions"] or "[]"))
    return {"actors": actors, "regions": regions}


def count_story_events(story_id: int) -> int:
    """Return total number of events linked to a story."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM story_events WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        return row[0] if row else 0


def create_story_action(story_id: int, action_type: str, reason: str, merge_target_id: int | None = None) -> int:
    """Create a pending action (close/merge suggestion)."""
    with _connect() as conn:
        # Don't create duplicate pending actions
        existing = conn.execute(
            """SELECT id FROM story_actions
               WHERE story_id = ? AND action_type = ? AND status = 'pending'""",
            (story_id, action_type),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO story_actions (story_id, action_type, merge_target_id, reason, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (story_id, action_type, merge_target_id, reason, _now()),
        )
        return cur.lastrowid


def get_pending_actions() -> list[dict]:
    """Return all pending story actions with story titles."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT sa.*, s.title as story_title,
                      s2.title as merge_target_title
               FROM story_actions sa
               JOIN stories s ON s.id = sa.story_id
               LEFT JOIN stories s2 ON s2.id = sa.merge_target_id
               WHERE sa.status = 'pending'
               ORDER BY sa.created_at DESC""",
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_story_action(action_id: int, status: str) -> None:
    """Resolve an action (approved/dismissed)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE story_actions SET status = ? WHERE id = ?",
            (status, action_id),
        )


def merge_stories(source_id: int, target_id: int) -> None:
    """Merge source story into target: move all events, append narrative, close source."""
    with _connect() as conn:
        source = conn.execute("SELECT * FROM stories WHERE id = ?", (source_id,)).fetchone()
        target = conn.execute("SELECT * FROM stories WHERE id = ?", (target_id,)).fetchone()
        if not source or not target:
            return
        # Move events
        conn.execute(
            "UPDATE story_events SET story_id = ? WHERE story_id = ?",
            (target_id, source_id),
        )
        # Append narrative
        merged_narrative = target["narrative"] + "\n\n[Merged from: " + source["title"] + "]\n" + source["narrative"]
        # Use higher urgency
        new_urgency = max(source["urgency"], target["urgency"])
        # Use latest event date
        new_last = max(source["last_event_date"] or "", target["last_event_date"] or "")
        conn.execute(
            """UPDATE stories SET narrative = ?, urgency = ?, last_event_date = ?
               WHERE id = ?""",
            (merged_narrative.strip(), new_urgency, new_last, target_id),
        )
        # Close source
        conn.execute(
            "UPDATE stories SET status = 'merged', closed_at = ? WHERE id = ?",
            (_now(), source_id),
        )


def get_closed_stories() -> list[dict]:
    """Return closed stories with timelines for the history page."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM stories WHERE status = 'closed'
               ORDER BY closed_at DESC""",
        ).fetchall()
        result = []
        for row in rows:
            story = dict(row)
            timeline = conn.execute(
                """SELECT se.event_date, se.headline, se.summary_line, se.entry_type,
                          se.event_id, e.title as event_title, e.urgency
                   FROM story_events se
                   LEFT JOIN events e ON e.id = se.event_id
                   WHERE se.story_id = ?
                   ORDER BY se.event_date DESC, se.added_at DESC""",
                (story["id"],),
            ).fetchall()
            story["timeline"] = [
                {
                    "event_date": t["event_date"],
                    "title": t["event_title"] or t["headline"],
                    "summary_line": t["summary_line"],
                    "event_id": t["event_id"],
                    "entry_type": t["entry_type"] or "live",
                    "urgency": t["urgency"] or 0,
                }
                for t in timeline
            ]
            result.append(story)
    return result


def get_events_linked_to_stories(briefing_id: int) -> set[int]:
    """Return set of event IDs that are linked to any story for this briefing."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT se.event_id FROM story_events se
               JOIN events e ON e.id = se.event_id
               WHERE e.briefing_id = ?""",
            (briefing_id,),
        ).fetchall()
    return {row["event_id"] for row in rows}


def get_all_story_actors_and_regions(story_ids: list[int]) -> dict[int, dict]:
    """Batch fetch actors and regions for multiple stories at once."""
    if not story_ids:
        return {}
    result: dict[int, dict] = {sid: {"actors": set(), "regions": set()} for sid in story_ids}
    placeholders = ",".join("?" * len(story_ids))
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT se.story_id, e.actors, e.regions
                FROM story_events se
                JOIN events e ON e.id = se.event_id
                WHERE se.story_id IN ({placeholders}) AND se.event_id IS NOT NULL""",
            story_ids,
        ).fetchall()
    for r in rows:
        sid = r["story_id"]
        result[sid]["actors"].update(json.loads(r["actors"] or "[]"))
        result[sid]["regions"].update(json.loads(r["regions"] or "[]"))
    return result


def get_story_action(action_id: int) -> dict | None:
    """Return a single story action by id, or None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM story_actions WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


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
