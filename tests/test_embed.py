from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from news_summariser import store
from news_summariser.embed import MODEL_NAME, article_text, embed_articles
from news_summariser.models import Article
from news_summariser.store import open_db

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


class FakeModel:
    """Stands in for the 471 MB model; records what it was asked to embed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, inputs: Sequence[str], /, **kwargs: Any) -> np.ndarray:
        assert kwargs.get("normalize_embeddings") is True
        self.calls.append(list(inputs))
        return np.array([[float(len(t)), 1.0, 0.0] for t in inputs])


def _article(url_hash: str, title: str = "Title", summary: str = "Summary") -> Article:
    return Article(
        url_hash=url_hash,
        source_id="bbc",
        url=f"https://example.com/{url_hash}",
        title=title,
        summary=summary,
        language="en",
        published_at=NOW,
        fetched_at=NOW,
    )


def test_article_text_puts_title_first_and_skips_empty_summary() -> None:
    assert article_text(_article("a", "Quake hits", "Details")) == "Quake hits\nDetails"
    assert article_text(_article("a", "Quake hits", "")) == "Quake hits"


def test_only_uncached_articles_are_embedded() -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    model = FakeModel()
    first = _article("a")

    embed_articles(conn, [first], model)
    vectors = embed_articles(conn, [first, _article("b", "Other")], model)

    assert model.calls == [["Title\nSummary"], ["Other\nSummary"]]
    assert set(vectors) == {"a", "b"}
    assert vectors["a"].dtype == np.float32


def test_nothing_new_means_no_model_needed() -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.save_embeddings(conn, MODEL_NAME, {"a": np.ones(3, dtype=np.float32)})

    # No model passed: if it tried to embed, it would load the real one.
    vectors = embed_articles(conn, [_article("a")])

    assert list(vectors) == ["a"]


def test_vectors_from_another_model_are_recomputed() -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.save_embeddings(conn, "old-model", {"a": np.ones(3, dtype=np.float32)})
    model = FakeModel()

    embed_articles(conn, [_article("a")], model)

    assert model.calls == [["Title\nSummary"]]
