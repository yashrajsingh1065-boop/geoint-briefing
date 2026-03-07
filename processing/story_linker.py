"""
Post-pipeline step: link today's events to existing stories or create new ones.
Runs after events are created but before briefing is marked complete.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

from config import (
    STORY_MATCH_SIMILARITY_THRESHOLD,
    STORY_SEMANTIC_THRESHOLD,
    STORY_DORMANT_DAYS,
    MIN_ARTICLES_FOR_STORY,
    MIN_ARTICLES_FOR_LOW_COVERAGE_STORY,
    LOW_COVERAGE_DORMANT_DAYS,
    LOW_COVERAGE_MAX_NEW_PER_RUN,
    LOW_COVERAGE_PROMOTE_THRESHOLD,
)
from processing.embeddings import is_available as embeddings_available, encode, cosine_sim_query

logger = logging.getLogger(__name__)


def _find_candidates(event_text: str, stories: list[dict]) -> list[dict]:
    """Find candidate story matches using semantic embeddings, falling back to TF-IDF."""
    if not stories:
        return []

    story_texts = [s["title"] + " " + (s["narrative"] or "")[:500] for s in stories]

    # Try semantic embeddings first
    if embeddings_available():
        try:
            all_embs = encode([event_text] + story_texts)
            sims = cosine_sim_query(all_embs[0], all_embs[1:])
            candidates = []
            for i, sim in enumerate(sims):
                if sim >= STORY_SEMANTIC_THRESHOLD:
                    candidates.append({**stories[i], "_similarity": float(sim)})
            candidates.sort(key=lambda x: x["_similarity"], reverse=True)
            return candidates[:5]
        except Exception as exc:
            logger.warning("Semantic candidate matching failed (%s); falling back to TF-IDF", type(exc).__name__)

    # Fallback: TF-IDF
    return _tfidf_candidates(event_text, stories, STORY_MATCH_SIMILARITY_THRESHOLD)


def _tfidf_candidates(event_text: str, stories: list[dict], threshold: float) -> list[dict]:
    """Use TF-IDF to find candidate story matches for an event."""
    if not stories:
        return []

    story_texts = [s["title"] + " " + (s["narrative"] or "")[:500] for s in stories]
    all_texts = [event_text] + story_texts

    try:
        vectorizer = TfidfVectorizer(max_features=5000, stop_words="english", sublinear_tf=True)
        tfidf = vectorizer.fit_transform(all_texts)
        sims = cosine_similarity(tfidf[0:1], tfidf[1:])[0]

        candidates = []
        for i, sim in enumerate(sims):
            if sim >= threshold:
                candidates.append({**stories[i], "_similarity": float(sim)})

        candidates.sort(key=lambda x: x["_similarity"], reverse=True)
        return candidates[:5]
    except Exception as exc:
        logger.warning("TF-IDF candidate matching failed: %s", type(exc).__name__)
        return []


def run_story_linking(briefing_id: int, date_str: str) -> None:
    """
    Main story linking orchestrator. Called after events are saved.
    1. Match new events to existing stories (TF-IDF + Claude)
    2. Evaluate unmatched events for new stories
    3. Check dormant stories for closure
    4. Check for merge opportunities
    """
    from storage import database as db
    from ai.analyst import (
        match_event_to_stories,
        generate_story_update,
        evaluate_new_story,
        evaluate_low_coverage_story,
        check_story_closure,
        check_story_merges,
    )

    logger.info("=== Story linking started for %s ===", date_str)

    active_stories = db.get_active_stories()
    events = db.get_events_for_briefing(briefing_id)

    if not events:
        logger.info("No events to link — skipping.")
        return

    # Skip events already linked to stories (never alter existing links)
    already_linked = db.get_events_linked_to_stories(briefing_id)
    events = [e for e in events if e["id"] not in already_linked]

    if not events:
        logger.info("All events already linked — skipping matching.")
        # Still check dormant/merge below
        events = []

    linked_event_ids = set()
    story_match_counts: dict[int, int] = {}  # track matches per story for auto-promotion

    # Step 1: Match events to existing stories
    if active_stories:
        logger.info("Matching %d events against %d active stories...", len(events), len(active_stories))
        for event in events:
            event_text = event["title"] + " " + (event.get("summary") or "")
            candidates = _find_candidates(event_text, active_stories)

            if not candidates:
                continue

            # Claude confirmation
            match_result = match_event_to_stories(
                event["title"],
                event.get("summary", ""),
                candidates,
            )
            time.sleep(0.5)

            if match_result.get("match") and match_result.get("story_id"):
                story_id = match_result["story_id"]
                # Verify story_id is valid
                valid_ids = {s["id"] for s in active_stories}
                if story_id not in valid_ids:
                    logger.warning("Claude returned invalid story_id %s, skipping", story_id)
                    continue

                # Get the matched story
                matched_story = next(s for s in active_stories if s["id"] == story_id)

                # Generate narrative update
                update = generate_story_update(
                    matched_story["title"],
                    matched_story.get("narrative", ""),
                    event["title"],
                    event.get("summary", ""),
                )
                time.sleep(0.5)

                # Persist
                db.link_event_to_story(story_id, event["id"], date_str, update.get("summary_line", event["title"][:100]))
                db.update_story(story_id, update.get("narrative_addition", ""), update.get("urgency", 3), date_str)
                linked_event_ids.add(event["id"])
                story_match_counts[story_id] = story_match_counts.get(story_id, 0) + 1
                logger.info("Linked event '%s' to story '%s'", event["title"][:50], matched_story["title"][:50])

    # Step 1b: Auto-promote low-coverage stories with enough matches (per-run)
    for story_id, count in story_match_counts.items():
        if count >= LOW_COVERAGE_PROMOTE_THRESHOLD:
            matched_story = next((s for s in active_stories if s["id"] == story_id), None)
            if matched_story and matched_story.get("coverage_tier") == "low":
                db.promote_story(story_id)
                logger.info("Promoted low-coverage story '%s' to full (got %d matches this run)", matched_story["title"][:50], count)

    # Step 1c: Auto-promote low-coverage stories with enough cumulative events
    for story in active_stories:
        if story.get("coverage_tier") != "low":
            continue
        total_events = db.count_story_events(story["id"])
        if total_events >= LOW_COVERAGE_PROMOTE_THRESHOLD:
            db.promote_story(story["id"])
            logger.info("Promoted low-coverage story '%s' to full (%d cumulative events)", story["title"][:50], total_events)

    # Step 2: Evaluate unmatched events for new stories
    unmatched = [e for e in events if e["id"] not in linked_event_ids]
    logger.info("Evaluating %d unmatched events for new stories...", len(unmatched))

    for event in unmatched:
        article_count = event.get("article_count", 0)
        if article_count < MIN_ARTICLES_FOR_STORY:
            continue

        result = evaluate_new_story(
            event["title"],
            event.get("summary", ""),
            article_count,
        )
        time.sleep(0.5)

        if result.get("create_story"):
            story_title = result.get("story_title", event["title"][:100])
            story_narrative = result.get("narrative", "")
            story_id = db.create_story(
                story_title,
                story_narrative,
                result.get("urgency", 3),
                date_str,
            )
            db.link_event_to_story(story_id, event["id"], date_str, event["title"][:100], headline=event["title"][:200])
            linked_event_ids.add(event["id"])
            logger.info("Created new story: '%s'", story_title)

            # Backfill historical timeline
            try:
                from ai.analyst import generate_historical_timeline
                history = generate_historical_timeline(story_title, story_narrative)
                for entry in history:
                    db.add_historical_timeline_entry(
                        story_id,
                        entry["date"],
                        entry["headline"],
                        entry.get("summary", ""),
                        entry_type=entry.get("type", "arc"),
                    )
                logger.info("Backfilled %d timeline entries for '%s'", len(history), story_title)
                time.sleep(0.5)
            except Exception as exc:
                logger.warning("Historical backfill failed for '%s': %s", story_title[:50], type(exc).__name__)

    # Step 2b: Evaluate remaining unmatched events for low-coverage stories
    still_unmatched = [e for e in events if e["id"] not in linked_event_ids]
    low_coverage_created = 0
    # Only consider events that weren't already evaluated in Step 2 (those with < MIN_ARTICLES_FOR_STORY)
    low_cov_candidates = [
        e for e in still_unmatched
        if e.get("article_count", 0) >= MIN_ARTICLES_FOR_LOW_COVERAGE_STORY
        and e.get("article_count", 0) < MIN_ARTICLES_FOR_STORY
    ]
    logger.info("Evaluating %d low-coverage candidates for new stories...", len(low_cov_candidates))

    for event in low_cov_candidates:
        if low_coverage_created >= LOW_COVERAGE_MAX_NEW_PER_RUN:
            break

        result = evaluate_low_coverage_story(
            event["title"],
            event.get("summary", ""),
            event.get("article_count", 0),
        )
        time.sleep(0.5)

        if result.get("create_story"):
            story_title = result.get("story_title", event["title"][:100])
            story_narrative = result.get("narrative", "")
            story_id = db.create_story(
                story_title,
                story_narrative,
                result.get("urgency", 3),
                date_str,
                coverage_tier="low",
            )
            db.link_event_to_story(story_id, event["id"], date_str, event["title"][:100], headline=event["title"][:200])
            linked_event_ids.add(event["id"])
            low_coverage_created += 1
            logger.info("Created low-coverage story: '%s'", story_title)

            # Backfill historical timeline (reuse existing logic)
            try:
                from ai.analyst import generate_historical_timeline
                history = generate_historical_timeline(story_title, story_narrative)
                for entry in history:
                    db.add_historical_timeline_entry(
                        story_id,
                        entry["date"],
                        entry["headline"],
                        entry.get("summary", ""),
                        entry_type=entry.get("type", "arc"),
                    )
                logger.info("Backfilled %d timeline entries for low-cov '%s'", len(history), story_title)
                time.sleep(0.5)
            except Exception as exc:
                logger.warning("Historical backfill failed for low-cov '%s': %s", story_title[:50], type(exc).__name__)

    # Step 3: Check dormant stories for closure
    active_stories = db.get_active_stories()  # refresh
    today = date.fromisoformat(date_str)

    for story in active_stories:
        last_date_str = story.get("last_event_date")
        if not last_date_str:
            continue

        try:
            last_date = date.fromisoformat(last_date_str)
        except ValueError:
            continue

        days_dormant = (today - last_date).days

        # Tier-aware dormancy thresholds
        is_low_tier = story.get("coverage_tier") == "low"
        dormant_threshold = LOW_COVERAGE_DORMANT_DAYS if is_low_tier else STORY_DORMANT_DAYS
        closure_check_threshold = 15 if is_low_tier else 5

        if days_dormant >= dormant_threshold:
            # Auto-close
            db.close_story(story["id"])
            logger.info("Auto-closed dormant story: '%s' (%d days, tier=%s)", story["title"][:50], days_dormant, story.get("coverage_tier", "full"))
        elif days_dormant >= closure_check_threshold:
            # Ask Claude if it should be closed
            closure = check_story_closure(
                story["title"],
                story.get("narrative", ""),
                last_date_str,
                days_dormant,
            )
            time.sleep(0.5)

            if closure.get("should_close"):
                db.create_story_action(
                    story["id"],
                    "close",
                    closure.get("reason", "Story appears to have concluded."),
                )
                logger.info("Suggested closing story: '%s' — %s", story["title"][:50], closure.get("reason", ""))

    # Step 4: Check for merge opportunities
    active_stories = db.get_active_stories()  # refresh again
    if len(active_stories) >= 2:
        merges = check_story_merges(active_stories)
        time.sleep(0.5)

        for merge in merges:
            source_id = merge.get("source_id")
            target_id = merge.get("target_id")
            reason = merge.get("reason", "")
            if source_id and target_id and source_id != target_id:
                valid_ids = {s["id"] for s in active_stories}
                if source_id in valid_ids and target_id in valid_ids:
                    db.create_story_action(
                        source_id,
                        "merge",
                        reason,
                        merge_target_id=target_id,
                    )
                    logger.info("Suggested merging story %d into %d: %s", source_id, target_id, reason)

    logger.info("=== Story linking complete ===")
