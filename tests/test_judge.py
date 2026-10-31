import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest
from anthropic.types import Message
from conftest import FakeAPIError, FakeClient, MakeArticle, make_message
from pydantic import ValidationError

from news_summariser import judge, store
from news_summariser.config import Settings, Source
from news_summariser.models import ArticleJudgment, JudgedStory, Story

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
    return make_message(text, stop_reason, input_tokens=2_000, output_tokens=500)


def _reply(*judgments: dict[str, Any]) -> Message:
    return _message(json.dumps({"facts": "Something happened.", "judgments": list(judgments)}))


def _good(*article_ids: str) -> Message:
    return _reply(*(_judgment(a) for a in article_ids))


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
) -> tuple[JudgedStory, FakeClient]:
    client = FakeClient(replies)
    result = judge.judge_story(client.as_client(), state, settings, "rubric", story, sources, NOW)
    return result, client


def test_good_reply_judges_every_article_and_logs_spend(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([_good("a1", "a2", "a3")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}
    assert result.facts == "Something happened."
    assert not result.not_judged
    assert len(client.messages.calls) == 1
    # Haiku 5.5: 2,000 x $0.10/M + 500 x $0.50/M.
    assert store.spend_since(state, NOW) == pytest.approx(0.00045)
    assert not state.in_transaction  # committed at once, so a later crash can't lose it


def test_retry_fills_missing_articles(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([_good("a1"), _good("a2", "a3")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 2
    assert store.spend_since(state, NOW) == pytest.approx(0.0009)  # both calls were billed


def test_still_missing_after_retry_marked_not_judged(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, _ = _run([_good("a1"), _good("a1", "a2")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1"}
    assert result.not_judged == {"h2"}


def test_one_broken_judgment_keeps_the_rest(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    broken = _judgment("a3", framing_tags=["conflict", "legal", "economic", "security"])
    first = _reply(_judgment("a1"), _judgment("a2"), broken)
    result, client = _run([first, _good("a3")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}  # the retry filled only a3
    assert len(client.messages.calls) == 2


def test_bad_replies_degrade_the_story(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    replies: list[Message | Exception] = [_message("not json"), _message("{}", "max_tokens")]
    result, client = _run(replies, state, settings, story, sources)
    assert not result.judgments
    assert result.facts is None
    assert result.not_judged == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 2
    assert store.spend_since(state, NOW) == pytest.approx(0.0009)


def test_refusal_is_logged_but_not_retried(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([_message("", "refusal")], state, settings, story, sources)
    assert result.not_judged == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 1
    assert store.spend_since(state, NOW) == pytest.approx(0.00045)


def test_invented_ids_are_ignored(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, _ = _run([_good("a1", "a2", "a3", "a99")], state, settings, story, sources)
    assert set(result.judgments) == {"h0", "h1", "h2"}


def test_api_error_is_not_retried(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    result, client = _run([FakeAPIError()], state, settings, story, sources)
    assert result.not_judged == {"h0", "h1", "h2"}
    assert len(client.messages.calls) == 1
    assert store.spend_since(state, NOW) == 0


def test_request_sends_rubric_cached_and_short_ids(
    state: sqlite3.Connection, settings: Settings, story: Story, sources: dict[str, Source]
) -> None:
    _, client = _run([_good("a1", "a2", "a3")], state, settings, story, sources)
    call = client.messages.calls[0]
    assert call["model"] == settings.models.judge.id
    assert call["output_config"]["effort"] == settings.models.judge.effort
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = call["messages"][0]["content"]
    sent = json.loads(content)["articles"]
    assert [a["article_id"] for a in sent] == ["a1", "a2", "a3"]
    assert "h0" not in content  # url_hashes stay out of the prompt
    assert "\n" not in content  # compact JSON: whitespace is billed
    assert {"baseline_lean", "lean_axis", "state_media"} <= sent[0].keys()


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": None},  # a score needs a reason
        {"confidence": None},  # and a confidence
        {"lean": "insufficient"},  # insufficient has no confidence
        {"framing_tags": ["propaganda"]},  # not on the list
        {"framing_tags": ["other:Sport"]},  # lowercase only
        {"framing_tags": ["other:sport\n"]},
        {"framing_tags": []},
        {"framing_tags": ["conflict", "legal", "economic", "security"]},
    ],
)
def test_rubric_rules_enforced(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ArticleJudgment.model_validate(_judgment("a1", **overrides))


@pytest.mark.parametrize("tag", ["other:sport", "other:human-rights", "other:public_health"])
def test_other_tags_accepted(tag: str) -> None:
    j = ArticleJudgment.model_validate(
        _judgment("a1", lean="insufficient", reason=None, confidence=None, framing_tags=[tag])
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
