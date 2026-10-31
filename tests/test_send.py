import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from telegram import Chat, Message
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from news_summariser.models import Button, RenderedMessage
from news_summariser.send import BotLike, send_all


class FakeBot:
    """Stands in for telegram.Bot: replies in order (an exception is raised) and records calls."""

    def __init__(self, replies: list[Exception | None]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> Message:
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        reply = self.replies.pop(0) if self.replies else None
        if reply is not None:
            raise reply
        return Message(
            message_id=len(self.calls),
            date=datetime.now(UTC),
            chat=Chat(id=cast(int, chat_id), type="supergroup"),
        )

    def as_bot(self) -> BotLike:
        return cast(BotLike, self)


async def _no_sleep(seconds: float) -> None:
    SLEPT.append(seconds)


SLEPT: list[float] = []

MESSAGES = [
    RenderedMessage(text="header"),
    RenderedMessage(text="story", buttons=(Button(label="🔵 A", url="https://a.example"),)),
    RenderedMessage(text="story 2"),
]


def _run(bot: FakeBot, messages: list[RenderedMessage] = MESSAGES) -> tuple[list[int], list[int]]:
    SLEPT.clear()
    sent: list[int] = []
    failed = asyncio.run(send_all(bot.as_bot(), -100, 7, messages, sent.append, _no_sleep))
    return sent, failed


def test_sends_all_in_order_to_the_topic_with_buttons() -> None:
    bot = FakeBot([])
    sent, failed = _run(bot)

    assert (sent, failed) == ([0, 1, 2], [])
    assert [c["text"] for c in bot.calls] == ["header", "story", "story 2"]
    assert all(c["message_thread_id"] == 7 and c["parse_mode"] == "HTML" for c in bot.calls)
    assert bot.calls[1]["reply_markup"].inline_keyboard[0][0].url == "https://a.example"
    # Spaced out to stay inside the group's per-minute limit.
    assert SLEPT == [1.0, 1.0]


def test_flood_control_is_waited_out() -> None:
    bot = FakeBot([RetryAfter(5)])
    sent, failed = _run(bot)

    assert (sent, failed) == ([0, 1, 2], [])
    assert 5 in SLEPT


def test_long_flood_wait_stops_the_run() -> None:
    bot = FakeBot([None, RetryAfter(600)])
    sent, failed = _run(bot)

    assert (sent, failed) == ([0], [1, 2])


def test_timeout_is_not_retried_and_the_run_carries_on() -> None:
    bot = FakeBot([None, TimedOut()])
    sent, failed = _run(bot)

    assert (sent, failed) == ([0, 2], [1])
    assert len(bot.calls) == 3


def test_bad_request_skips_one_message() -> None:
    bot = FakeBot([BadRequest("can't parse entities")])
    sent, failed = _run(bot)

    assert (sent, failed) == ([1, 2], [0])


def test_connection_errors_are_retried_then_stop() -> None:
    bot = FakeBot([NetworkError("reset"), None])
    assert _run(bot) == ([0, 1, 2], [])

    bot = FakeBot([NetworkError("down")] * 3)
    assert _run(bot) == ([], [0, 1, 2])


def test_forbidden_stops_the_run() -> None:
    bot = FakeBot([None, Forbidden("bot was kicked")])
    sent, failed = _run(bot)

    assert (sent, failed) == ([0], [1, 2])
    assert len(bot.calls) == 2
