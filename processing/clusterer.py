import logging
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity

from config import CLUSTER_DISTANCE_THRESHOLD, MAX_CLUSTERS_PER_BRIEFING

logger = logging.getLogger(__name__)


def _build_combined_text(cluster_articles: list[dict]) -> str:
    """
    Concatenate article texts for a Claude prompt.
    Lead article gets full title; all bodies truncated to 300 chars.
    Total target: ~2000 chars.
    """
    parts = []
    for i, a in enumerate(cluster_articles):
        snippet = f"[{a['source_name']}] {a['title']}\n{a['body'][:300]}"
        parts.append(snippet)
        if len("\n\n".join(parts)) > 2000:
            break
    return "\n\n".join(parts)


def cluster_into_events(articles: list[dict]) -> list[dict]:
    """
    Group articles about the same event using agglomerative clustering on TF-IDF vectors.
    Returns list of EventCluster dicts, sorted by size descending,
    truncated to MAX_CLUSTERS_PER_BRIEFING.

    Each EventCluster = {
        "articles":      list[dict],
        "lead_article":  dict,   # highest word-count article in cluster
        "combined_text": str,
    }
    """
    if not articles:
        return []

    if len(articles) == 1:
        return [_make_cluster(articles)]

    corpus = [a["title"] + " " + a["body"][:500] for a in articles]

    try:
        vectorizer = TfidfVectorizer(
            max_features=10_000,
            sublinear_tf=True,
            stop_words="english",
        )
        X = vectorizer.fit_transform(corpus)
    except Exception as exc:
        logger.warning("Clustering vectorization failed (%s); returning ungrouped", exc)
        return [_make_cluster([a]) for a in articles[:MAX_CLUSTERS_PER_BRIEFING]]

    # Cosine distance matrix (1 - similarity)
    sim = cosine_similarity(X)
    dist = np.clip(1.0 - sim, 0.0, 2.0)

    try:
        clustering = AgglomerativeClustering(
            metric="precomputed",
            linkage="average",
            distance_threshold=CLUSTER_DISTANCE_THRESHOLD,
            n_clusters=None,
        )
        labels = clustering.fit_predict(dist)
    except Exception as exc:
        logger.warning("Agglomerative clustering failed (%s); returning ungrouped", exc)
        return [_make_cluster([a]) for a in articles[:MAX_CLUSTERS_PER_BRIEFING]]

    # Group by cluster label
    groups: dict[int, list[dict]] = {}
    for article, label in zip(articles, labels):
        groups.setdefault(int(label), []).append(article)

    # Sort clusters by size descending
    sorted_groups = sorted(groups.values(), key=len, reverse=True)

    clusters = [_make_cluster(group) for group in sorted_groups]
    clusters = clusters[:MAX_CLUSTERS_PER_BRIEFING]

    logger.info(
        "Clustering: %d articles → %d event clusters (top %d kept)",
        len(articles), len(sorted_groups), len(clusters),
    )
    return clusters


def _make_cluster(cluster_articles: list[dict]) -> dict:
    """Build an EventCluster dict from a list of articles."""
    # Lead article = the one with the most body text
    lead = max(cluster_articles, key=lambda a: len(a.get("body", "")))
    return {
        "articles":      cluster_articles,
        "lead_article":  lead,
        "combined_text": _build_combined_text(cluster_articles),
    }
