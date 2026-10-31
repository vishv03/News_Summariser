import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from news_summariser.cluster import SIMILARITY_THRESHOLD, cluster
from news_summariser.models import Article

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _article(url_hash: str, source_id: str = "bbc", hours_ago: int = 0) -> Article:
    at = NOW - timedelta(hours=hours_ago)
    return Article(
        url_hash=url_hash,
        source_id=source_id,
        url=f"https://example.com/{url_hash}",
        title="Made-up headline",
        summary="",
        language="en",
        published_at=at,
        fetched_at=at,
    )


def _at_similarity(similarity: float) -> np.ndarray:
    """A unit vector whose cosine similarity with [1, 0, 0] is `similarity`."""
    return np.array([similarity, math.sqrt(1 - similarity**2), 0.0])


def _ids(stories: list) -> list[set[str]]:
    return sorted(({a.url_hash for a in s.articles} for s in stories), key=sorted)


def test_empty_and_single_article() -> None:
    assert cluster([], {}) == []
    stories = cluster([_article("a")], {"a": np.array([1.0, 0.0, 0.0])})
    assert _ids(stories) == [{"a"}]


def test_same_story_across_languages_groups_and_others_stay_apart() -> None:
    # Stand-ins for "quake in Chile" reported in English, French and Spanish, plus an unrelated
    # rate rise in English and German, shaped like the real model's scores (0.9+ within a story,
    # under 0.1 between them).
    vectors = {
        "quake-en": np.array([1.0, 0.0, 0.0]),
        "quake-fr": np.array([0.95, 0.31, 0.0]),
        "quake-es": np.array([0.93, 0.0, 0.37]),
        "rates-en": np.array([0.0, 1.0, 0.0]),
        "rates-de": np.array([0.0, 0.94, -0.34]),
    }
    vectors = {k: v / np.linalg.norm(v) for k, v in vectors.items()}
    articles = [_article(h, source_id=h.split("-")[1]) for h in vectors]

    stories = cluster(articles, vectors)

    assert _ids(stories) == [{"quake-en", "quake-es", "quake-fr"}, {"rates-de", "rates-en"}]


@pytest.mark.parametrize(
    ("similarity", "merged"),
    [(SIMILARITY_THRESHOLD + 0.01, True), (SIMILARITY_THRESHOLD - 0.01, False)],
)
def test_threshold_boundary(similarity: float, merged: bool) -> None:
    vectors = {"a": np.array([1.0, 0.0, 0.0]), "b": _at_similarity(similarity)}

    stories = cluster([_article("a"), _article("b", "fox")], vectors)

    assert len(stories) == (1 if merged else 2)


def test_every_article_in_exactly_one_story_and_order_is_stable() -> None:
    rng = np.random.default_rng(0)
    vectors = {f"h{i}": v / np.linalg.norm(v) for i, v in enumerate(rng.normal(size=(40, 8)))}
    articles = [_article(h, hours_ago=i) for i, h in enumerate(vectors)]

    first = cluster(articles, vectors)
    again = cluster(list(reversed(articles)), vectors)

    hashes = [a.url_hash for s in first for a in s.articles]
    assert sorted(hashes) == sorted(vectors)
    assert [s.story_id for s in first] == [s.story_id for s in again]
