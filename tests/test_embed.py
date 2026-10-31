from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import MakeArticle

from news_summariser import store
from news_summariser.embed import MODEL_KEY, MODEL_NAME, article_text, embed_articles
from news_summariser.store import open_db


class FakeModel:
    """Stands in for the 471 MB model; records what it was asked to embed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, inputs: Sequence[str], /, **kwargs: Any) -> np.ndarray:
        assert kwargs.get("normalize_embeddings") is True
        self.calls.append(list(inputs))
        return np.array([[float(len(t)), 1.0, 0.0] for t in inputs])


def test_article_text_puts_title_first_and_skips_empty_summary(make_article: MakeArticle) -> None:
    quake = make_article("a", title="Quake hits", summary="Details")
    assert article_text(quake) == "Quake hits\nDetails"
    assert article_text(make_article("a", title="Quake hits", summary="")) == "Quake hits"


def test_only_uncached_articles_are_embedded(make_article: MakeArticle) -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    model = FakeModel()
    first = make_article("a")

    embed_articles(conn, [first], model)
    vectors = embed_articles(conn, [first, make_article("b", title="Other")], model)

    assert model.calls == [["Title\nSummary"], ["Other\nSummary"]]
    assert set(vectors) == {"a", "b"}
    assert vectors["a"].dtype == np.float32


def test_nothing_new_means_no_model_needed(make_article: MakeArticle) -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.save_embeddings(conn, MODEL_KEY, {"a": np.ones(3, dtype=np.float32)})

    # No model passed: if it tried to embed, it would load the real one.
    vectors = embed_articles(conn, [make_article("a")])

    assert list(vectors) == ["a"]


@pytest.mark.parametrize(
    "stored_under",
    # Another model, and the same model at another Hugging Face commit.
    ["another-model", f"{MODEL_NAME}@0000000"],
)
def test_vectors_from_another_model_or_revision_are_recomputed(
    make_article: MakeArticle, stored_under: str
) -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.save_embeddings(conn, stored_under, {"a": np.ones(3, dtype=np.float32)})
    model = FakeModel()

    embed_articles(conn, [make_article("a")], model)

    assert model.calls == [["Title\nSummary"]]
