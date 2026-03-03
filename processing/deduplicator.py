import logging
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import DEDUP_SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


def _build_corpus(articles: list[dict]) -> list[str]:
    return [a["title"] + " " + a["body"][:500] for a in articles]


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Remove near-duplicate articles using TF-IDF cosine similarity.
    Greedy pass: keeps the first occurrence, drops subsequent articles
    that are >= DEDUP_SIMILARITY_THRESHOLD similar to any kept article.

    Returns deduplicated list.
    """
    if len(articles) <= 1:
        return articles

    corpus = _build_corpus(articles)

    try:
        vectorizer = TfidfVectorizer(
            max_features=10_000,
            sublinear_tf=True,
            stop_words="english",
        )
        X = vectorizer.fit_transform(corpus)
    except Exception as exc:
        logger.warning("TF-IDF vectorization failed (%s); skipping dedup", exc)
        return articles

    # Compute pairwise cosine similarity
    sim_matrix = cosine_similarity(X)

    kept_indices = []
    dropped = set()

    for i in range(len(articles)):
        if i in dropped:
            continue
        kept_indices.append(i)
        # Mark all subsequent articles that are too similar to this one
        for j in range(i + 1, len(articles)):
            if j not in dropped and sim_matrix[i, j] >= DEDUP_SIMILARITY_THRESHOLD:
                dropped.add(j)

    result = [articles[i] for i in kept_indices]
    logger.info(
        "Deduplication: %d → %d articles (removed %d duplicates)",
        len(articles), len(result), len(dropped),
    )
    return result
