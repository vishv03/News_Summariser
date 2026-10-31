"""Group articles about the same story, across languages (ADR 0006)."""

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from news_summariser.models import Article, Story

# A story means the same broader story: an event plus its follow-ups and angles, so left and right
# coverage of different angles still lands together. 0.55 cosine similarity was chosen on
# 2026-10-31: on 60 hand-labelled cross-outlet pairs it got 4 wrong (0.49-0.55 tied best), and on
# 1,870 real articles it kept the Israel election (48 articles, 13 outlets) as one story, while 0.60
# split it and 0.50 merged unrelated Korea news.
SIMILARITY_THRESHOLD = 0.55


def cluster(articles: list[Article], vectors: dict[str, np.ndarray]) -> list[Story]:
    """Group articles into stories; every article ends up in exactly one story."""
    if not articles:
        return []
    if len(articles) == 1:
        # The clusterer needs at least two samples.
        return [Story(articles=tuple(articles))]

    matrix = np.stack([vectors[a.url_hash] for a in articles])
    labels = AgglomerativeClustering(
        # scikit-learn's hints say int, but its docs require None when distance_threshold is set.
        n_clusters=None,  # pyright: ignore[reportArgumentType]
        metric="cosine",
        # Average linkage compares whole groups, so one borderline article can't chain two
        # unrelated stories together the way single linkage would.
        linkage="average",
        distance_threshold=1 - SIMILARITY_THRESHOLD,
    ).fit_predict(matrix)

    groups: dict[int, list[Article]] = {}
    for article, label in zip(articles, labels, strict=True):
        groups.setdefault(int(label), []).append(article)
    # Sorted so the same input always gives the same output order; select.py does the ranking.
    return sorted((Story(articles=tuple(g)) for g in groups.values()), key=lambda s: s.story_id)
