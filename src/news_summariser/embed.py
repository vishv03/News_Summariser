"""Turn articles into vectors for clustering, reusing ones already cached (ADR 0006)."""

import sqlite3
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from news_summariser import store
from news_summariser.models import Article

# Changing this needs an ADR: it decides which articles count as the same event. Stored vectors
# carry the model name, so a change recomputes them instead of mixing old and new.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Encoder(Protocol):
    def encode(self, inputs: Sequence[str], /, **kwargs: Any) -> Any: ...


def article_text(article: Article) -> str:
    """Return the text an article is embedded from."""
    # Title first, so the model's 128-token cut-off only ever drops the end of the summary.
    return "\n".join(part for part in (article.title, article.summary) if part)


def _load_model() -> Encoder:
    # Imported here, not at the top: it pulls in PyTorch, and runs with nothing new to embed
    # shouldn't pay for that.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, device="cpu")


def embed_articles(
    conn: sqlite3.Connection, articles: list[Article], model: Encoder | None = None
) -> dict[str, np.ndarray]:
    """Return a unit-length vector for every article, embedding only those not already cached."""
    vectors = store.get_embeddings(conn, (a.url_hash for a in articles), MODEL_NAME)
    missing = [a for a in articles if a.url_hash not in vectors]
    if missing:
        model = model or _load_model()
        # Unit length makes cosine similarity a plain dot product for clustering.
        encoded = np.asarray(
            model.encode([article_text(a) for a in missing], normalize_embeddings=True),
            dtype=np.float32,
        )
        new = {a.url_hash: v for a, v in zip(missing, encoded, strict=True)}
        store.save_embeddings(conn, MODEL_NAME, new)
        vectors.update(new)
    return vectors
