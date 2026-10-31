"""Turn articles into vectors for clustering, reusing ones already cached (ADR 0006)."""

import sqlite3
from collections.abc import Hashable, Mapping, Sequence
from typing import Any, Protocol

import numpy as np

from news_summariser import store
from news_summariser.models import Article

# Changing this needs an ADR: it decides which articles count as the same event. Stored vectors
# carry the model name, so a change recomputes them instead of mixing old and new.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# The Hugging Face commit the 0.55 threshold was tested on (2026-10-31). Pinned so new weights
# pushed under the same name can't arrive unnoticed and be compared with cached vectors.
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
# What stored vectors are tagged with: a change to either part marks them stale.
MODEL_KEY = f"{MODEL_NAME}@{MODEL_REVISION}"


class Encoder(Protocol):
    def encode(self, inputs: Sequence[str], /, **kwargs: Any) -> Any: ...


def article_text(article: Article) -> str:
    """Return the text an article is embedded from."""
    # Title first, so the model's 128-token cut-off only ever drops the end of the summary.
    return "\n".join(part for part in (article.title, article.summary) if part)


def load_model() -> Encoder:
    """Load the embedding model, pinned to the tested revision, on CPU."""
    # Imported here, not at the top: it pulls in PyTorch, and runs with nothing new to embed
    # shouldn't pay for that.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device="cpu")


def embed_texts[K: Hashable](texts: Mapping[K, str], model: Encoder) -> dict[K, np.ndarray]:
    """Return a unit-length vector for each text, keyed like the input; nothing is cached."""
    # Unit length is required by selection's topic match, which ranks by dot product.
    encoded = np.asarray(
        model.encode(list(texts.values()), normalize_embeddings=True), dtype=np.float32
    )
    return dict(zip(texts, encoded, strict=True))


def embed_articles(
    conn: sqlite3.Connection, articles: list[Article], model: Encoder | None = None
) -> dict[str, np.ndarray]:
    """Return a unit-length vector for every article, embedding only those not already cached."""
    vectors = store.get_embeddings(conn, (a.url_hash for a in articles), MODEL_KEY)
    missing = [a for a in articles if a.url_hash not in vectors]
    if missing:
        model = model or load_model()
        # Unit length makes cosine similarity a plain dot product for clustering.
        encoded = np.asarray(
            model.encode([article_text(a) for a in missing], normalize_embeddings=True),
            dtype=np.float32,
        )
        new = {a.url_hash: v for a, v in zip(missing, encoded, strict=True)}
        store.save_embeddings(conn, MODEL_KEY, new)
        vectors.update(new)
    return vectors
