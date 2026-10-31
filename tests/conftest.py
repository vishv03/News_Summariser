from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from news_summariser.models import Article

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)

MakeArticle = Callable[..., Article]


@pytest.fixture
def make_article() -> MakeArticle:
    """Build a made-up Article; only the fields a test cares about need passing."""

    def make(
        url_hash: str,
        *,
        source_id: str = "bbc",
        title: str = "Title",
        summary: str = "Summary",
        published_at: datetime = NOW,
    ) -> Article:
        return Article(
            url_hash=url_hash,
            source_id=source_id,
            url=f"https://example.com/{url_hash}",
            title=title,
            summary=summary,
            language="en",
            published_at=published_at,
            fetched_at=published_at,
        )

    return make
