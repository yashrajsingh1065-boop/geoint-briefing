# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env  # then add ANTHROPIC_API_KEY

# Run the application (scheduler + web server on port 8000)
python main.py

# Manually trigger a briefing (without starting the scheduler)
# POST to the API after server is running:
curl -X POST http://localhost:8000/api/trigger

# Check briefing status
curl http://localhost:8000/api/status

# Run health check (queries /api/status and logs result)
python check_briefing.py
```

No test suite or linter configuration exists in this project.

## Architecture

This is an automated geopolitical intelligence briefing system. It runs a daily pipeline at 6:00 AM that fetches news, clusters it into events, and analyzes each event with Claude AI. Results are served via a FastAPI web dashboard.

### Pipeline Flow (`scheduler/jobs.py`)

```
RSS Feeds (20 sources, parallel) → ingestion/fetcher.py
  → HTML cleaning              → ingestion/cleaner.py
  → SQLite storage             → storage/database.py
  → TF-IDF deduplication       → processing/deduplicator.py  (threshold: 0.72)
  → Agglomerative clustering   → processing/clusterer.py     (distance: 0.60)
  → Claude AI analysis         → ai/analyst.py               (sequential, 0.5s delay)
  → Persist events + articles  → storage/database.py
```

### Module Responsibilities

- **`main.py`** — Wires everything: initializes DB, registers APScheduler cron job, checks for existing today's briefing, starts FastAPI via uvicorn.
- **`config.py`** — Single source of truth for all constants: RSS feed URLs, Claude model/thresholds, processing thresholds, scheduler time.
- **`ai/analyst.py`** — Calls Claude API with a geopolitical analyst system prompt. Returns structured JSON (title, summary, consequence, historical_context, regions, actors, urgency 1–5). Has fallback degraded analysis if Claude fails.
- **`storage/database.py`** — All SQLite operations. Database uses WAL mode. Tables: `briefings` (status: pending/complete/error), `events`, `articles` (unique on URL).
- **`web/app.py`** — FastAPI routes: `/briefing/today`, `/briefing/{date}`, `/event/{event_id}`, `/history`, `/api/status`, `/api/trigger`. Templates use Jinja2. Dashboard auto-polls every 15s while status is "pending".

### Key Configuration (`config.py`)

- `CLAUDE_MODEL = "claude-sonnet-4-6"` — update here to change model
- `DEDUP_SIMILARITY_THRESHOLD = 0.72` — higher = fewer duplicates removed
- `CLUSTER_DISTANCE_THRESHOLD = 0.60` — lower = more clusters (more events)
- `MAX_CLUSTERS_PER_BRIEFING = 20` — caps Claude API calls
- `SCHEDULE_HOUR = 6` — daily pipeline trigger time

### Important Patterns

- **Lazy imports** in `scheduler/jobs.py` to avoid circular imports (each pipeline step imports its module at call time).
- **Graceful degradation** everywhere: Claude failures, feed timeouts, and DB errors don't crash the pipeline — they produce partial/degraded output.
- **Background threads** for the scheduler, manual trigger endpoint, and browser auto-open (all daemonized).
- **`INSERT OR IGNORE`** on article URL prevents cross-day duplicates in storage.
