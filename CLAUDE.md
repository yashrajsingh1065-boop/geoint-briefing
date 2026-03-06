# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env  # then add ANTHROPIC_API_KEY and ADMIN_TOKEN

# Run the application (scheduler + web server on port 8000)
python main.py

# Manually trigger a briefing (requires ADMIN_TOKEN):
curl -X POST http://localhost:8000/api/trigger -H "X-Admin-Token: YOUR_TOKEN"

# Check briefing status
curl http://localhost:8000/api/status

# Run health check (queries /api/status and logs result)
python check_briefing.py

# Run security test suite
python -m pytest tests/test_security.py -v

# Backup the database
python backup_db.py --verify

# Backup with custom retention
python backup_db.py --max-backups 7 --verify
```

## Architecture

This is an automated geopolitical intelligence briefing system. It runs a daily pipeline at 6:30 AM that fetches news, clusters it into events, and analyzes each event with Claude AI. Results are served via a FastAPI web dashboard.

### Pipeline Flow (`scheduler/jobs.py`)

```
RSS Feeds (20 sources, parallel) → ingestion/fetcher.py
  → URL validation + size-limited download (SSRF/DoS protection)
  → HTML cleaning (bleach + HTMLParser) → ingestion/cleaner.py
  → SQLite storage             → storage/database.py
  → TF-IDF deduplication       → processing/deduplicator.py  (threshold: 0.55)
  → Agglomerative clustering   → processing/clusterer.py     (distance: 0.70)
  → Claude AI analysis         → ai/analyst.py               (sequential, 0.5s delay, prompt injection safeguards)
  → Story linking              → processing/story_linker.py
  → Persist events + articles  → storage/database.py
```

### Module Responsibilities

- **`main.py`** — Wires everything: validates config, initializes DB, registers APScheduler cron job, checks for existing today's briefing, starts FastAPI via uvicorn.
- **`config.py`** — Single source of truth for all constants: RSS feed URLs, Claude model/thresholds, processing thresholds, scheduler time, security settings, `validate_feed_url()` for SSRF protection.
- **`ai/analyst.py`** — Calls Claude API with a geopolitical analyst system prompt. Uses prompt delimiters and `_sanitize_source_text()` to prevent prompt injection. Returns structured JSON. Has fallback degraded analysis if Claude fails.
- **`storage/database.py`** — All SQLite operations. Database uses WAL mode with busy_timeout. Enforces file permissions (0o600). Narrative size capped at 100K chars.
- **`web/app.py`** — FastAPI routes with security middleware (auth, rate limiting, security headers, input validation). All POST endpoints require `ADMIN_TOKEN`.
- **`backup_db.py`** — SQLite online backup utility with integrity verification and retention policy.

### Key Configuration (`config.py`)

- `CLAUDE_MODEL = "claude-sonnet-4-6"` — update here to change model
- `DEDUP_SIMILARITY_THRESHOLD = 0.55` — lower = more aggressive dedup
- `CLUSTER_DISTANCE_THRESHOLD = 0.70` — higher = merge more related stories
- `MAX_CLUSTERS_PER_BRIEFING = 20` — caps Claude API calls
- `SCHEDULE_HOUR = 6` — daily pipeline trigger time

### Environment Variables

- `ANTHROPIC_API_KEY` — required, app exits if missing
- `ADMIN_TOKEN` — required for POST endpoints (generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `LOG_LEVEL` — optional, defaults to `INFO`
- `OPEN_BROWSER` — `auto`/`true`/`false`, defaults to `auto`
- `SCHEDULE_TIMEZONE` — defaults to `Asia/Kolkata`

### Security Features

- **Authentication** — All POST endpoints require `X-Admin-Token` or `Bearer` token header
- **Rate limiting** — In-memory per-IP rate limiter (1/hr trigger, 5/min resync, 20/min actions)
- **Security headers** — X-Frame-Options, CSP, HSTS, X-Content-Type-Options, Referrer-Policy
- **SSRF protection** — `validate_feed_url()` blocks private IPs, localhost, cloud metadata
- **Prompt injection** — Delimiters + regex sanitizer on all Claude prompts
- **HTML sanitization** — Triple-layer: regex + bleach + HTMLParser
- **Input validation** — Date format, positive IDs, URL scheme validation
- **DB security** — WAL mode, busy_timeout, 0o600 permissions, narrative size cap
- **Feed size limits** — 10MB max per feed download with streaming enforcement

### Important Patterns

- **Lazy imports** in `scheduler/jobs.py` to avoid circular imports (each pipeline step imports its module at call time).
- **Graceful degradation** everywhere: Claude failures, feed timeouts, and DB errors don't crash the pipeline — they produce partial/degraded output.
- **Pipeline lock** prevents concurrent runs from scheduler + manual trigger.
- **Background threads** for the scheduler, manual trigger endpoint, and browser auto-open (all daemonized).
- **`INSERT OR IGNORE`** on article URL prevents cross-day duplicates in storage.
- **Safe error logging** — exception logs use `type(exc).__name__` to avoid leaking details.
