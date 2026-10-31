from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from news_summariser.models import Article, Story

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _article(url_hash: str, source_id: str, published_at: datetime) -> Article:
    return Article(
        url_hash=url_hash,
        source_id=source_id,
        url=f"https://example.com/{url_hash}",
        title="Title",
        summary="Summary",
        language="en",
        published_at=published_at,
        fetched_at=published_at,
    )


def test_story_id_is_earliest_article_whatever_the_order() -> None:
    early = _article("early", "bbc", NOW - timedelta(hours=2))
    late = _article("late", "fox", NOW)

    assert Story(articles=[late, early]).story_id == "early"
    assert Story(articles=[early, late]).story_id == "early"


def test_story_id_tie_breaks_on_url_hash() -> None:
    a = _article("aaa", "bbc", NOW)
    b = _article("bbb", "fox", NOW)

    assert Story(articles=[b, a]).story_id == "aaa"


def test_story_derived_properties() -> None:
    story = Story(
        articles=[
            _article("1", "bbc", NOW - timedelta(hours=3)),
            _article("2", "bbc", NOW - timedelta(hours=1)),
            _article("3", "lemonde", NOW),
        ]
    )

    assert story.source_ids == {"bbc", "lemonde"}
    assert story.first_published == NOW - timedelta(hours=3)
    assert story.last_published == NOW


def test_empty_story_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Story(articles=[])
