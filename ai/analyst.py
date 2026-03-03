from __future__ import annotations

import json
import logging
import time

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS,
    CLAUDE_TEMPERATURE,
    REGIONS,
)

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a senior geopolitical analyst producing an intelligence briefing. "
    "You write with clarity, precision, and strategic depth. "
    "You always respond with valid JSON only — no markdown fences, no extra text."
)

_USER_PROMPT_TEMPLATE = """Below are news articles covering a single world event (from multiple sources):

---
{combined_text}
---

Respond with ONLY a JSON object using these exact keys:

{{
  "title": "...",               (20-25 words — a full informative headline: who did what, where, with what outcome. Like a front-page newspaper headline.)
  "summary": "...",             (4-5 sentences: full detail — what happened, who is involved, key facts, numbers, latest developments)
  "consequence": "...",         (3-4 sentences: strategic implications — what does this mean for the region, key players, and the world? What happens next?)
  "historical_context": "...",  (1-2 sentences of relevant historical background, or "" if not applicable)
  "regions": [...],             (array — use ONLY values from this list: {regions})
  "actors": [...],              (array of named people, governments, organizations directly involved)
  "urgency": N                  (integer 1-5: 1=routine development, 3=significant, 5=major crisis or breaking)
}}"""


def _build_prompt(cluster: dict) -> str:
    return _USER_PROMPT_TEMPLATE.format(
        combined_text=cluster["combined_text"],
        regions=json.dumps(REGIONS),
    )


# ── Core API call ─────────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> str:
    client = _get_client()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        temperature=CLAUDE_TEMPERATURE,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _parse_response(raw: str) -> dict:
    """Parse Claude's JSON response and validate/coerce fields."""
    # Strip any accidental markdown fences
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)

    # Coerce urgency to int 1-5
    urgency = int(data.get("urgency", 3))
    data["urgency"] = max(1, min(5, urgency))

    # Filter regions to only recognized values
    raw_regions = data.get("regions", [])
    if isinstance(raw_regions, list):
        data["regions"] = [r for r in raw_regions if r in REGIONS]
    else:
        data["regions"] = []

    # Ensure actors is a list
    if not isinstance(data.get("actors"), list):
        data["actors"] = []

    # Ensure required string fields
    for key in ("title", "summary", "consequence", "historical_context"):
        if not isinstance(data.get(key), str):
            data[key] = ""

    return data


def _fallback_result(cluster: dict) -> dict:
    """Return a degraded result when Claude analysis fails."""
    lead = cluster.get("lead_article", {})
    return {
        "title":              lead.get("title", "Untitled Event")[:100],
        "summary":            "Analysis unavailable for this event.",
        "consequence":        "",
        "historical_context": "",
        "regions":            [],
        "actors":             [],
        "urgency":            2,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_event(cluster: dict) -> dict:
    """
    Analyze one event cluster with Claude.
    Never raises — returns a fallback result on any error.
    """
    try:
        prompt = _build_prompt(cluster)
        raw = _call_claude(prompt)
        result = _parse_response(raw)
        logger.debug("Analyzed: %s (urgency %s)", result.get("title"), result.get("urgency"))
        return result
    except Exception as exc:
        lead_title = cluster.get("lead_article", {}).get("title", "?")
        logger.warning("Claude analysis failed for '%s': %s", lead_title, exc)
        return _fallback_result(cluster)


def analyze_all_events(clusters: list[dict]) -> list[dict]:
    """
    Analyze all event clusters sequentially.
    Adds a short sleep between calls to respect rate limits.
    Returns list of AnalysisResult dicts in the same order as input.
    """
    results = []
    total = len(clusters)
    for i, cluster in enumerate(clusters, 1):
        logger.info("Analyzing event %d/%d ...", i, total)
        result = analyze_event(cluster)
        results.append(result)
        if i < total:
            time.sleep(0.5)
    return results
