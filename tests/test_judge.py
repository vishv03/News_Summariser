import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic import Anthropic
from anthropic.types import Message
from conftest import MakeArticle
from pydantic import ValidationError

from news_summariser import judge, store
from news_summariser.config import Settings, Source, load_settings, load_sources
from news_summariser.models import ArticleJudgment, Story

NOW = datetime(2026, 10, 15, 6, tzinfo=UTC)


def _judgment(article_id: str, **overrides: Any) -> dict[str, Any]:
    j = {
        "article_id": article_id,
        "lean": "-1",
        "reason": "Calls the measure 'cruel' in its own voice.",
        "confidence": "medium",
        "framing_tags": ["human_cost"],
        "loaded_words": [{"quote": "cruel", "neutral": "strict"}],
        "take": "Frames the policy through the families it affects.",
    }
    return {**j, **overrides}


def _message(text: str, stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 2_000, "output_tokens": 500},
        }
    )


class FakeMessages:
    def __init__(self, replies: list[Message | Exception]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies: list[Message | Exception]) -> None:
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


@pytest.fixture
def story(make_article: MakeArticle, sources: dict[str, Source]) -> Story:
    ids = list(sources)[:3]
    return Story(articles=tuple(make_article(f"h{i}", source_id=s) for i, s in enumerate(ids)))


def _run(
    replies: list[Message | Exception],
    state: sqlite3.Connection,
    settings: Settings,
    story: Story,
    sources: dict[str, Source],
) -> tuple[Any, FakeClient]:
    client = FakeClient(replies)
    result = judge.judge_story(
        cast(Anthropic, client), state, settings, "rubric", story, sources, NOW
    )
    return result, client


def _reply(*article_ids: str) -> Message:
    return _message(
        json.dumps(
            {"facts": "Something happened.", "judgments": [_judgment(a) for a in article_ids]}
        )
    )


def test_good_reply_judges_every_article_and_logs_spend(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([_reply("a1", "a2", "a3")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}
    assert not result.not_judged
    assert len(client.messages.calls) == 1
    # Haiku 5.5: 2,000 x $0.10/M + 500 x $0.50/M.
    assert store.spend_since(state, NOW) == pytest.approx(0.00045)


def test_retry_fills_missing_articles(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([_reply("a1"), _reply("a2", "a3")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 2
    assert store.spend_since(state, NOW) == pytest.approx(0.0009)  # both calls were billed


def test_still_missing_after_retry_marked_not_judged(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, _ = _run([_reply("a1"), _reply("a1", "a2")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1"}
    assert result.not_judged == {"h2"}


def test_bad_replies_are_logged_and_degrade_the_story(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    replies: list[Message | Exception] = [_message("not json"), _message("{}", "refusal")]
    result, client = _run(replies, state, settings, story, sources)
    assert not result.judgments
    assert result.not_judged == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 2
    assert store.spend_since(state, NOW) == pytest.approx(0.0009)


def test_invented_ids_are_ignored(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, _ = _run([_reply("a1", "a2", "a3", "a99")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}


def test_api_error_is_not_retried(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    result, client = _run([error], state, settings, story, sources)
    assert result.not_judged == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 1
    assert store.spend_since(state, NOW) == 0


def test_request_sends_rubric_cached_and_short_ids(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    _, client = _run([_reply("a1", "a2", "a3")], state, settings, story, sources)
    call = client.messages.calls[0]
    assert call["model"] == settings.models.judge.id
    assert call["output_config"]["effort"] == settings.models.judge.effort
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    sent = json.loads(call["messages"][0]["content"])["articles"]
    assert [a["article_id"] for a in sent] == ["a1", "a2", "a3"]
    assert "h0" not in call["messages"][0]["content"]  # url_hashes stay out of the prompt
    assert {"baseline_lean", "lean_axis", "state_media"} <= sent[0].keys()


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": None},  # a score needs a reason
        {"confidence": None},  # and a confidence
        {"lean": "insufficient"},  # insufficient has no confidence
        {"framing_tags": ["propaganda"]},  # not on the list
        {"framing_tags": []},
        {"framing_tags": ["conflict", "legal", "economic", "security"]},
    ],
)
def test_rubric_rules_enforced(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ArticleJudgment.model_validate(_judgment("a1", **overrides))


def test_insufficient_and_other_tag_accepted() -> None:
    j = ArticleJudgment.model_validate(
        _judgment(
            "a1", lean="insufficient", reason=None, confidence=None, framing_tags=["other:sport"]
        )
    )
    assert j.score is None
    assert ArticleJudgment.model_validate(_judgment("a1", lean="+2")).score == 2


def test_load_prompt_drops_maintainer_comment() -> None:
    prompt = judge.load_prompt()
    assert not prompt.startswith("<!--")
    assert prompt.startswith("You judge how news articles frame a story.")


def test_canned_judgment_makes_no_calls(story: Story) -> None:
    result = judge.canned_judgment(story)
    assert set(result.judgments) == {"h0", "h1", "h2"}
    assert all(j.lean == "insufficient" for j in result.judgments.values())
