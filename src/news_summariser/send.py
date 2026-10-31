"""Send rendered messages to the Telegram group's digest topic (ADR 0009)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Protocol

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TimedOut

from news_summariser.models import RenderedMessage

log = logging.getLogger(__name__)

# Telegram allows a bot about 20 messages a minute in a group; a digest is ~9, so one a second
# stays well inside it without making the post feel slow.
SPACING_SECONDS = 1.0
# Flood waits longer than this mean something is wrong; better to fail and let a re-run finish.
MAX_RETRY_AFTER_SECONDS = 60
RETRY_AFTER_ATTEMPTS = 3
# Connection errors mean nothing reached Telegram, so retrying them can't duplicate a post.
NETWORK_RETRIES = 2
NETWORK_BACKOFF_SECONDS = 2.0
# Buttons sit two to a row so outlet names stay readable on a phone.
BUTTONS_PER_ROW = 2


class MessageFailed(Exception):
    """One message could not be sent; the run carries on with the next one."""


class SendingStopped(Exception):
    """Telegram refused the bot itself (removed, no rights, bad token); nothing more can be sent."""


class BotLike(Protocol):
    """The part of telegram.Bot this module uses, so tests can pass a fake."""

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str,
        message_thread_id: int,
        reply_markup: InlineKeyboardMarkup | None,
        link_preview_options: LinkPreviewOptions,
    ) -> Message: ...


Sleep = Callable[[float], Awaitable[None]]


def _markup(message: RenderedMessage) -> InlineKeyboardMarkup | None:
    if not message.buttons:
        return None
    buttons = [InlineKeyboardButton(b.label, url=b.url) for b in message.buttons]
    rows = [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    return InlineKeyboardMarkup(rows)


def _seconds(retry_after: int | timedelta) -> float:
    # PTB returns an int today and will switch to timedelta in a future major version.
    return retry_after.total_seconds() if isinstance(retry_after, timedelta) else retry_after


async def send_one(
    bot: BotLike,
    chat_id: int,
    topic_id: int,
    message: RenderedMessage,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Send one message and return its Telegram message id."""
    flood_waits = 0
    network_retries = 0
    while True:
        try:
            sent = await bot.send_message(
                chat_id,
                message.text,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
                reply_markup=_markup(message),
                # Buttons already link the articles; a preview of one would favour that outlet.
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return sent.message_id
        except RetryAfter as e:
            wait = _seconds(e.retry_after)
            flood_waits += 1
            if flood_waits > RETRY_AFTER_ATTEMPTS or wait > MAX_RETRY_AFTER_SECONDS:
                raise SendingStopped(f"flood control: asked to wait {wait:.0f}s") from e
            log.warning("Telegram flood control: waiting %.0fs", wait)
            await sleep(wait)
        except TimedOut as e:
            # Telegram may have delivered it before the timeout; a retry could post it twice.
            raise MessageFailed("timed out (may have been delivered; not retried)") from e
        except BadRequest as e:
            # Usually our HTML or length, so it's this message's fault, not the run's.
            raise MessageFailed(f"rejected by Telegram: {e.message}") from e
        except NetworkError as e:
            # BadRequest and TimedOut are NetworkError subclasses, so this only sees the rest:
            # connection failures where nothing was sent.
            network_retries += 1
            if network_retries > NETWORK_RETRIES:
                raise SendingStopped(f"network error: {e.message}") from e
            log.warning("Telegram network error (%s); retrying", e.message)
            await sleep(NETWORK_BACKOFF_SECONDS * network_retries)
        except (Forbidden, InvalidToken) as e:
            raise SendingStopped(f"bot refused: {e.message}") from e


async def send_all(
    bot: BotLike,
    chat_id: int,
    topic_id: int,
    messages: Sequence[RenderedMessage],
    on_sent: Callable[[int], None],
    sleep: Sleep = asyncio.sleep,
) -> list[int]:
    """Send messages in order, calling on_sent(index) after each; return the failed indexes."""
    failed: list[int] = []
    for i, message in enumerate(messages):
        if i:
            await sleep(SPACING_SECONDS)
        try:
            await send_one(bot, chat_id, topic_id, message, sleep)
        except MessageFailed as e:
            log.error("message %d of %d not sent: %s", i + 1, len(messages), e)
            failed.append(i)
            continue
        except SendingStopped as e:
            log.error("sending stopped at message %d of %d: %s", i + 1, len(messages), e)
            return failed + list(range(i, len(messages)))
        # Recorded straight after its own send, so a crash later can't lose it (ADR 0009).
        on_sent(i)
    return failed
