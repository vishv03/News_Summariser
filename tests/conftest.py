import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anthropic
import pytest
from anthropic import Anthropic
from anthropic.types import Message

from news_summariser import store
from news_summariser.config import Settings, Source, load_settings, load_sources
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


def make_message(
    text: str, stop_reason: str = "end_turn", *, input_tokens: int, output_tokens: int
) -> Message:
    """Build an API reply as the SDK returns it."""
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


class FakeAPIError(anthropic.APIError):
    """An API failure the SDK has given up retrying, without needing an HTTP request object."""

    def __init__(self) -> None:
        Exception.__init__(self, "connection failed")


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
    """Stands in for anthropic.Anthropic: returns canned replies and records each request."""

    def __init__(self, replies: list[Message | Exception]) -> None:
        self.messages = FakeMessages(replies)

    def as_client(self) -> Anthropic:
        return cast(Anthropic, self)


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
