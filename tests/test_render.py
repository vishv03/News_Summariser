from datetime import date, timedelta

import pytest
from conftest import NOW, MakeArticle

from news_summariser.config import Source
from news_summariser.models import (
    ArticleJudgment,
    FeedHealth,
    JudgedStory,
    LoadedWord,
    Pick,
    Story,
    StoryWriteup,
)
from news_summariser.render import MAX_CHARS, render_header, render_story


def _judgment(lean: str, take: str = "A take.", words: list[LoadedWord] | None = None):
    scored = lean != "insufficient"
    return ArticleJudgment(
        article_id="a",
        lean=lean,  # type: ignore[arg-type]
        reason="Because." if scored else None,
        confidence="high" if scored else None,
        framing_tags=["conflict"],
        loaded_words=words or [],
        take=take,
    )


@pytest.fixture
def story(make_article: MakeArticle) -> Story:
    return Story(
        articles=(
            make_article("l1", source_id="nyt", published_at=NOW - timedelta(hours=3)),
            make_article("l2", source_id="huffpost", published_at=NOW - timedelta(hours=2)),
            make_article("r1", source_id="foxnews", title="Fox title", published_at=NOW),
            make_article("c1", source_id="bbc", published_at=NOW),
            make_article("i1", source_id="rt", published_at=NOW).model_copy(
                update={"language": "ar"}
            ),
        )
    )


def _pick(story: Story, kind: str = "top", update_of: int | None = None) -> Pick:
    return Pick(story=story, topic="economy", score=1.0, kind=kind, update_of=update_of)  # type: ignore[arg-type]


def _healthy(sources: dict[str, Source]) -> dict[str, FeedHealth]:
    return {s: FeedHealth(source_id=s, last_checked_at=NOW) for s in sources}


WRITEUP = StoryWriteup(
    headline="Neutral headline",
    left="Left framing.",
    right="Right framing.",
    international="International framing.",
    differs="They differ.",
)


def test_story_shows_framing_outlets_and_baseline(story: Story, sources: dict[str, Source]) -> None:
    judged = JudgedStory(
        story_id=story.story_id,
        facts="What happened.",
        judgments={"l1": _judgment("-1"), "l2": _judgment("insufficient"), "r1": _judgment("+2")},
        not_judged=frozenset({"c1", "i1"}),
    )
    msg = render_story(_pick(story), judged, WRITEUP, sources, _healthy(sources))

    assert "📰 <b>Neutral headline</b>" in msg.text
    assert "🔵 <b>Left:</b> Left framing." in msg.text
    # Insufficient and not judged both fall back to the outlet's baseline, labelled (ADR 0008).
    assert "HuffPost −2 (baseline)" in msg.text
    assert "The New York Times −1" in msg.text
    assert "🏛 RT +2 (baseline)" in msg.text
    assert "Also covering: BBC News 0 (baseline)" in msg.text
    assert "🔍 <b>What differs:</b> They differ." in msg.text


def test_buttons_open_each_sides_strongest_article(
    story: Story, sources: dict[str, Source]
) -> None:
    judged = JudgedStory(
        story_id=story.story_id,
        facts="What happened.",
        # NYT judged -2 beats HuffPost's -2 baseline on the tie-break: NYT is earlier.
        judgments={"l1": _judgment("-2"), "r1": _judgment("+1")},
        not_judged=frozenset({"l2", "c1", "i1"}),
    )
    msg = render_story(_pick(story), judged, WRITEUP, sources, _healthy(sources))

    labels = [b.label for b in msg.buttons]
    assert labels[:3] == ["🔵 The New York Times", "🔴 Fox News", "🌍 RT"]
    # RT's article is already a side button, so no separate local-language button.
    assert len(msg.buttons) == 3


def test_loaded_line_takes_one_flag_per_side(story: Story, sources: dict[str, Source]) -> None:
    left_words = [LoadedWord(quote="regime", neutral="government")]
    right_words = [
        LoadedWord(quote="invasion", neutral="arrival"),
        LoadedWord(quote="chaos", neutral="disorder"),
    ]
    judged = JudgedStory(
        story_id=story.story_id,
        facts="What happened.",
        judgments={
            "l1": _judgment("-1", words=left_words),
            "l2": _judgment("-1"),
            "r1": _judgment("+1", words=right_words),
            "i1": _judgment(
                "+1", words=[LoadedWord(quote="نظام", neutral="government", gloss="regime")]
            ),
        },
        not_judged=frozenset({"c1"}),
    )
    msg = render_story(_pick(story), judged, WRITEUP, sources, _healthy(sources))

    loaded = next(line for line in msg.text.splitlines() if line.startswith("⚠"))
    assert "“regime” → government (The New York Times)" in loaded
    assert "“invasion” → arrival (Fox News)" in loaded
    assert "chaos" not in loaded
    assert "“نظام” (“regime”) → government (RT)" in loaded


def test_without_writeup_falls_back_to_takes(story: Story, sources: dict[str, Source]) -> None:
    judged = JudgedStory(
        story_id=story.story_id,
        facts="What happened.",
        judgments={
            "l1": _judgment("-1", take="NYT take."),
            "r1": _judgment("+1", take="Fox take."),
        },
        not_judged=frozenset({"l2", "c1", "i1"}),
    )
    msg = render_story(_pick(story), judged, None, sources, _healthy(sources))

    # Headline is the earliest article's title, and there's no "what differs" line (ADR 0008).
    assert "📰 <b>Title</b>" in msg.text
    assert "🔵 <b>Left:</b> NYT take." in msg.text
    assert "🔴 <b>Right:</b> Fox take." in msg.text
    assert "What differs" not in msg.text


def test_headline_only_lists_outlet_titles(story: Story, sources: dict[str, Source]) -> None:
    msg = render_story(_pick(story), None, None, sources, _healthy(sources))

    assert '<a href="https://example.com/r1">Fox title</a> (Fox News)' in msg.text
    assert "Left:" not in msg.text


def test_blindspot_and_update_labels(story: Story, sources: dict[str, Source]) -> None:
    msg = render_story(_pick(story, "non_western", update_of=4), None, None, sources, {})

    assert msg.text.startswith("🌏 <b>Blindspot:</b> big outside the West\n🔄 <b>Update</b>")


def test_missing_side_says_offline_only_under_half_healthy(
    make_article: MakeArticle, sources: dict[str, Source]
) -> None:
    story = Story(
        articles=(
            make_article("l1", source_id="nyt"),
            make_article("l2", source_id="guardian"),
            make_article("c1", source_id="bbc"),
        )
    )
    judged = JudgedStory(story_id=story.story_id, facts="What happened.", judgments={})
    health = _healthy(sources)
    right = [s.id for s in sources.values() if s.enabled and s.lean_axis != "local"]
    right = [s for s in right if sources[s].baseline_lean > 0]

    msg = render_story(_pick(story), judged, None, sources, health)
    assert "view unavailable" not in msg.text

    for source_id in right[: len(right) // 2 + 1]:
        health[source_id] = FeedHealth(source_id=source_id, last_checked_at=NOW, problem="down")
    msg = render_story(_pick(story), judged, None, sources, health)
    assert f"🔴 <b>Right:</b> view unavailable ({len(right) // 2 + 1} feeds offline)" in msg.text


def test_text_is_escaped(make_article: MakeArticle, sources: dict[str, Source]) -> None:
    story = Story(articles=(make_article("x", source_id="nyt", title="A <b> & B"),))
    msg = render_story(_pick(story), None, None, sources, {})

    assert "A &lt;b&gt; &amp; B" in msg.text


def test_long_story_fits_one_message(story: Story, sources: dict[str, Source]) -> None:
    long = "word " * 2000
    writeup = StoryWriteup(headline="H", left=long, right=long, international=long, differs=long)
    judged = JudgedStory(story_id=story.story_id, facts=long, judgments={})

    msg = render_story(_pick(story), judged, writeup, sources, _healthy(sources))

    assert len(msg.text) <= MAX_CHARS


def test_header_has_date_counts_legend_and_notices(sources: dict[str, Source]) -> None:
    health = {"nyt": FeedHealth(source_id="nyt", last_checked_at=NOW, problem="down")}
    msg = render_header(
        date(2026, 10, 31), 5, 3, headline_only=True, sources=sources, health=health
    )

    assert "Daily digest · Sat 31 Oct" in msg.text
    assert "5 stories + 3 blindspots" in msg.text
    assert "AI budget is used up" in msg.text
    assert "1 of" in msg.text and "feeds are offline today" in msg.text
    assert "🏛 State media" in msg.text
