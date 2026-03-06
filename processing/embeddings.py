"""
Shared semantic embedding module.

Loads a sentence-transformers model once and provides encode/similarity
functions used by deduplicator, clusterer, and story_linker.
Falls back gracefully if sentence-transformers is not installed.
"""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)

_model = None
_available: bool | None = None


def is_available() -> bool:
    """Check whether sentence-transformers can be used."""
    global _available
    if _available is None:
        try:
            import sentence_transformers  # noqa: F401
            _available = True
        except ImportError:
            _available = False
            logger.info("sentence-transformers not installed; using TF-IDF fallback")
    return _available


def _get_model():
    """Lazy-load the embedding model (singleton)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        from config import EMBEDDING_MODEL_NAME
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("Loaded embedding model: %s", EMBEDDING_MODEL_NAME)
    return _model


def encode(texts: list[str]) -> np.ndarray:
    """Encode texts into dense vectors. Returns (n, dim) numpy array."""
    model = _get_model()
    return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)


def cosine_sim_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity matrix from embeddings."""
    # Normalize rows
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normed = embeddings / norms
    return normed @ normed.T


def cosine_sim_query(query_emb: np.ndarray, corpus_emb: np.ndarray) -> np.ndarray:
    """Cosine similarity of one query vector against a corpus matrix. Returns 1-D array."""
    q = query_emb.reshape(1, -1)
    q_norm = q / max(np.linalg.norm(q), 1e-9)
    c_norms = np.linalg.norm(corpus_emb, axis=1, keepdims=True)
    c_norms = np.where(c_norms == 0, 1, c_norms)
    c_normed = corpus_emb / c_norms
    return (q_norm @ c_normed.T).flatten()
