from datetime import timedelta

import pytest
from conftest import NOW, MakeArticle
from pydantic import ValidationError

from news_summariser.models import Story


def test_story_id_is_earliest_article_whatever_the_order(make_article: MakeArticle) -> None:
    early = make_article("early", published_at=NOW - timedelta(hours=2))
    late = make_article("late", source_id="fox")

    assert Story(articles=(late, early)).story_id == "early"
    assert Story(articles=(early, late)).story_id == "early"


def test_story_id_tie_breaks_on_url_hash(make_article: MakeArticle) -> None:
    a = make_article("aaa")
    b = make_article("bbb", source_id="fox")

    assert Story(articles=(b, a)).story_id == "aaa"


def test_story_derived_properties(make_article: MakeArticle) -> None:
    story = Story(
        articles=(
            make_article("1", published_at=NOW - timedelta(hours=3)),
            make_article("2", published_at=NOW - timedelta(hours=1)),
            make_article("3", source_id="lemonde"),
        )
    )

    assert story.source_ids == {"bbc", "lemonde"}
    assert story.first_published == NOW - timedelta(hours=3)
    assert story.last_published == NOW


def test_empty_story_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Story(articles=())


def test_story_cannot_be_changed_after_it_is_built(make_article: MakeArticle) -> None:
    story = Story(articles=(make_article("a"),))

    # Emptying a story would make story_id fail; code that narrows one must build a new Story.
    with pytest.raises(ValidationError):
        story.articles = ()
    assert not hasattr(story.articles, "remove")
