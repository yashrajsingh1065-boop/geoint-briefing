import logging
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity

from config import CLUSTER_DISTANCE_THRESHOLD, CLUSTER_SEMANTIC_DISTANCE, MAX_CLUSTERS_PER_BRIEFING
from processing.embeddings import is_available as embeddings_available, encode, cosine_sim_matrix

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


def _run_agglomerative(dist: np.ndarray, threshold: float, n_articles: int) -> np.ndarray:
    """Run agglomerative clustering on a precomputed distance matrix."""
    clustering = AgglomerativeClustering(
        metric="precomputed",
        linkage="average",
        distance_threshold=threshold,
        n_clusters=None,
    )
    return clustering.fit_predict(dist)


def cluster_into_events(articles: list[dict]) -> list[dict]:
    """
    Group articles about the same event using semantic embeddings
    (sentence-transformers), falling back to TF-IDF.
    """
    if not articles:
        return []

    if len(articles) == 1:
        return [_make_cluster(articles)]

    corpus = [a["title"] + " " + a["body"][:500] for a in articles]

    # Try semantic embeddings first
    if embeddings_available():
        try:
            embs = encode(corpus)
            sim = cosine_sim_matrix(embs)
            dist = np.clip(1.0 - sim, 0.0, 2.0)
            labels = _run_agglomerative(dist, CLUSTER_SEMANTIC_DISTANCE, len(articles))
            return _group_and_sort(articles, labels)
        except Exception as exc:
            logger.warning("Semantic clustering failed (%s); falling back to TF-IDF", type(exc).__name__)

    # Fallback: TF-IDF
    try:
        vectorizer = TfidfVectorizer(max_features=10_000, sublinear_tf=True, stop_words="english")
        X = vectorizer.fit_transform(corpus)
    except Exception as exc:
        logger.warning("Clustering vectorization failed (%s); returning ungrouped", type(exc).__name__)
        return [_make_cluster([a]) for a in articles[:MAX_CLUSTERS_PER_BRIEFING]]

    sim = cosine_similarity(X)
    dist = np.clip(1.0 - sim, 0.0, 2.0)

    try:
        labels = _run_agglomerative(dist, CLUSTER_DISTANCE_THRESHOLD, len(articles))
    except Exception as exc:
        logger.warning("Agglomerative clustering failed (%s); returning ungrouped", type(exc).__name__)
        return [_make_cluster([a]) for a in articles[:MAX_CLUSTERS_PER_BRIEFING]]

    return _group_and_sort(articles, labels)


def _group_and_sort(articles: list[dict], labels: np.ndarray) -> list[dict]:
    """Group articles by cluster label, sort by size, cap at MAX_CLUSTERS."""
    groups: dict[int, list[dict]] = {}
    for article, label in zip(articles, labels):
        groups.setdefault(int(label), []).append(article)

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
    lead = max(cluster_articles, key=lambda a: len(a.get("body", "")))
    return {
        "articles":      cluster_articles,
        "lead_article":  lead,
        "combined_text": _build_combined_text(cluster_articles),
    }
