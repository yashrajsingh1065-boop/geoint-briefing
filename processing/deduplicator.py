import logging
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import DEDUP_SIMILARITY_THRESHOLD, DEDUP_SEMANTIC_THRESHOLD
from processing.embeddings import is_available as embeddings_available, encode, cosine_sim_matrix

logger = logging.getLogger(__name__)


def _build_corpus(articles: list[dict]) -> list[str]:
    return [a["title"] + " " + a["body"][:500] for a in articles]


def _dedup_greedy(sim_matrix: np.ndarray, threshold: float, n: int) -> set[int]:
    """Greedy dedup pass: return set of indices to drop."""
    dropped = set()
    for i in range(n):
        if i in dropped:
            continue
        for j in range(i + 1, n):
            if j not in dropped and sim_matrix[i, j] >= threshold:
                dropped.add(j)
    return dropped


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Remove near-duplicate articles using semantic similarity (sentence-transformers),
    falling back to TF-IDF cosine similarity if unavailable.
    """
    if len(articles) <= 1:
        return articles

    corpus = _build_corpus(articles)

    # Try semantic embeddings first
    if embeddings_available():
        try:
            embs = encode(corpus)
            sim_matrix = cosine_sim_matrix(embs)
            dropped = _dedup_greedy(sim_matrix, DEDUP_SEMANTIC_THRESHOLD, len(articles))
            result = [a for i, a in enumerate(articles) if i not in dropped]
            logger.info(
                "Deduplication (semantic): %d → %d articles (removed %d duplicates)",
                len(articles), len(result), len(dropped),
            )
            return result
        except Exception as exc:
            logger.warning("Semantic dedup failed (%s); falling back to TF-IDF", type(exc).__name__)

    # Fallback: TF-IDF
    try:
        vectorizer = TfidfVectorizer(max_features=10_000, sublinear_tf=True, stop_words="english")
        X = vectorizer.fit_transform(corpus)
    except Exception as exc:
        logger.warning("TF-IDF vectorization failed (%s); skipping dedup", type(exc).__name__)
        return articles

    sim_matrix = cosine_similarity(X)
    dropped = _dedup_greedy(sim_matrix, DEDUP_SIMILARITY_THRESHOLD, len(articles))

    result = [a for i, a in enumerate(articles) if i not in dropped]
    logger.info(
        "Deduplication (TF-IDF): %d → %d articles (removed %d duplicates)",
        len(articles), len(result), len(dropped),
    )
    return result
