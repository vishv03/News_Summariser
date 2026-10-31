import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from anthropic import Anthropic
from anthropic.types import Message
from conftest import MakeArticle

from news_summariser import store, summarize
from news_summariser.config import Settings, Source, load_settings, load_sources
from news_summariser.models import ArticleJudgment, JudgedStory, Story, StoryWriteup
from news_summariser.selection import side_of

NOW = datetime(2026, 10, 15, 6, tzinfo=UTC)

WRITEUP = {
    "headline": "Parliament passes the budget",
    "left": "Left outlets focus on cuts.",
    "right": "Right outlets focus on tax.",
    "international": "International outlets focus on the government's win.",
    "differs": "Left stresses who loses; right stresses who pays.",
}


def _message(text: str, stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 1_000, "output_tokens": 200},
        }
    )


class FakeMessages:
    def __init__(self, replies: list[Message]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        return self.replies.pop(0)


class FakeClient:
    def __init__(self, replies: list[Message]) -> None:
        self.messages = FakeMessages(replies)


@pytest.fixture
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def sources() -> dict[str, Source]:
    return {s.id: s for s in load_sources(include_disabled=True)}


@pytest.fixture
def state() -> sqlite3.Connection:
    conn, _ = store.open_db(Path("unused"), "state", dry_run=True)
    return conn


def _first_on(side: str | None, sources: dict[str, Source]) -> str:
    return next(s.id for s in sources.values() if side_of(s) == side)


def _judged(story: Story, *, judged_hashes: list[str] | None = None) -> JudgedStory:
    hashes = judged_hashes if judged_hashes is not None else [a.url_hash for a in story.articles]
    judgment = ArticleJudgment(
        article_id="a1",
        lean="insufficient",
        framing_tags=["economic"],
        loaded_words=[],
        take="Frames it as a fight over money.",
    )
    return JudgedStory(
        story_id=story.story_id,
        facts="Parliament passed the budget.",
        judgments={h: judgment for h in hashes},
        not_judged=frozenset(a.url_hash for a in story.articles) - set(hashes),
    )


def _run(
    replies: list[Message],
    story: Story,
    judged: JudgedStory,
    state: sqlite3.Connection,
    settings: Settings,
    sources: dict[str, Source],
) -> tuple[StoryWriteup | None, FakeClient]:
    client = FakeClient(replies)
    writeup = summarize.write_story(
        cast(Anthropic, client), state, settings, "prompt", story, judged, sources, NOW
    )
    return writeup, client


@pytest.fixture
def story(make_article: MakeArticle, sources: dict[str, Source]) -> Story:
    outlets = [_first_on(side, sources) for side in ("left", "right", "international", None)]
    return Story(articles=tuple(make_article(f"h{i}", source_id=s) for i, s in enumerate(outlets)))


def test_good_reply_is_returned_and_logged(
    story: Story, state: sqlite3.Connection, settings: Settings, sources: dict[str, Source]
) -> None:
    writeup, client = _run(
        [_message(json.dumps(WRITEUP))], story, _judged(story), state, settings, sources
    )
    assert writeup == StoryWriteup.model_validate(WRITEUP)
    assert client.messages.calls[0]["model"] == settings.models.summary.id
    # Sonnet 5.5: 1,000 x $2/M + 200 x $10/M.
    assert store.spend_since(state, NOW) == pytest.approx(0.004)


def test_sonnet_sees_judge_output_only(
    story: Story, state: sqlite3.Connection, settings: Settings, sources: dict[str, Source]
) -> None:
    _, client = _run(
        [_message(json.dumps(WRITEUP))], story, _judged(story), state, settings, sources
    )
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert sent["facts"] == "Parliament passed the budget."
    assert [a["side"] for a in sent["articles"]] == ["left", "right", "international", "centre"]
    # No article text: the made-up articles' title and summary must not reach Sonnet.
    raw = client.messages.calls[0]["messages"][0]["content"]
    assert "Title" not in raw and "Summary" not in raw


def test_not_judged_articles_are_left_out_and_their_side_dropped(
    story: Story, state: sqlite3.Connection, settings: Settings, sources: dict[str, Source]
) -> None:
    judged = _judged(story, judged_hashes=["h0", "h2", "h3"])  # the right outlet failed
    writeup, client = _run([_message(json.dumps(WRITEUP))], story, judged, state, settings, sources)
    sent = json.loads(client.messages.calls[0]["messages"][0]["content"])
    assert "right" not in [a["side"] for a in sent["articles"]]
    assert writeup is not None and writeup.right is None  # an invented line is removed
    assert writeup.left is not None


def test_retry_then_fall_back_to_judge(
    story: Story, state: sqlite3.Connection, settings: Settings, sources: dict[str, Source]
) -> None:
    replies = [_message("not json"), _message("{}", "max_tokens")]
    writeup, client = _run(replies, story, _judged(story), state, settings, sources)
    assert writeup is None
    assert len(client.messages.calls) == 2
    assert store.spend_since(state, NOW) == pytest.approx(0.008)  # both were billed


def test_nothing_judged_means_no_call(
    story: Story, state: sqlite3.Connection, settings: Settings, sources: dict[str, Source]
) -> None:
    writeup, client = _run([], story, _judged(story, judged_hashes=[]), state, settings, sources)
    assert writeup is None
    assert not client.messages.calls


def test_canned_writeup_and_prompt(story: Story) -> None:
    assert summarize.canned_writeup(_judged(story)).headline.startswith("Dry run")
    assert summarize.load_prompt().startswith("You write the comparison part")
