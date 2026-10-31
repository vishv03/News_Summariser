"""Build the daily digest and post it; a re-run sends only what an earlier attempt missed."""

import asyncio
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, closing
from datetime import UTC, date, datetime, timedelta, timezone

from anthropic import Anthropic
from pydantic import BaseModel, ConfigDict
from telegram import Bot
from telegram.error import TelegramError

from news_summariser import budget, embed, judge, store, summarize
from news_summariser.cluster import cluster
from news_summariser.config import Settings, Source, load_settings, load_sources
from news_summariser.ingest import run_ingest
from news_summariser.models import DigestDay, FeedHealth, Pick, RenderedMessage, Selection
from news_summariser.render import render_header, render_story
from news_summariser.selection import (
    NON_WESTERN_SLOTS,
    ONE_SIDED_SLOTS,
    TOP_COUNT,
    TOPIC_DESCRIPTIONS,
    select,
)
from news_summariser.send import send_all

log = logging.getLogger(__name__)

# Readers get the digest at 07:00 IST, so a digest "day" is an IST date. IST has no daylight
# saving, so a fixed offset is exact and needs no time-zone database.
IST = timezone(timedelta(hours=5, minutes=30))

# Names only; the values live in .env locally and in GitHub Secrets (CLAUDE.md).
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_VAR = "TELEGRAM_CHAT_ID"
TOPIC_VAR = "TELEGRAM_DIGEST_TOPIC_ID"


class Slot(BaseModel):
    """One message of the digest; pick is None for the header."""

    model_config = ConfigDict(frozen=True)

    pick: Pick | None
    message: RenderedMessage


class TelegramTarget(BaseModel):
    """Where the digest goes, read from the environment."""

    model_config = ConfigDict(frozen=True)

    token: str
    chat_id: int
    topic_id: int


def digest_day_for(now: datetime) -> date:
    """The IST date a run at `now` posts for."""
    return now.astimezone(IST).date()


def telegram_target() -> TelegramTarget:
    """Read the bot token, chat id and topic id; raises KeyError or ValueError if missing."""
    # Never logged or printed: Actions logs are public (CLAUDE.md).
    return TelegramTarget(
        token=os.environ[TOKEN_VAR],
        chat_id=int(os.environ[CHAT_VAR]),
        topic_id=int(os.environ[TOPIC_VAR]),
    )


def build(
    state: sqlite3.Connection,
    cache: sqlite3.Connection,
    sources: Mapping[str, Source],
    settings: Settings,
    progress: DigestDay,
    now: datetime,
    *,
    client: Anthropic | None,
    spend_conn: sqlite3.Connection,
    dev: bool = False,
    model: embed.Encoder | None = None,
) -> tuple[list[Slot], Selection]:
    """Select, judge, write and render the slots still missing; client=None uses canned AI."""
    health: dict[str, FeedHealth] = store.get_health(state)
    articles = store.recent_articles(cache, now - store.ARTICLE_RETENTION)
    model = model or embed.load_model()
    vectors = embed.embed_articles(cache, articles, model)
    cache.commit()
    selection = select(
        cluster(articles, vectors),
        vectors,
        embed.embed_texts(TOPIC_DESCRIPTIONS, model),
        sources,
        health,
        store.posted_stories(state),
        now,
    )
    # Stories sent by an earlier attempt are repeats to selection now, so it fills the day with
    # fresh picks; only as many as are still missing are sent (ADR 0009).
    blindspot_slots = ONE_SIDED_SLOTS + NON_WESTERN_SLOTS
    picks = [
        *selection.top[: max(0, TOP_COUNT - progress.top_sent)],
        *selection.blindspots[: max(0, blindspot_slots - progress.blindspots_sent)],
    ]

    # Checked once, before the first paid call: a whole headline-only digest, never half of one.
    headline_only = client is not None and not budget.can_afford_run(spend_conn, settings, now)
    source_dict = dict(sources)
    slots: list[Slot] = []
    if not progress.header_sent:
        header = render_header(
            progress.day,
            len(selection.top),
            len(selection.blindspots),
            headline_only=headline_only,
            sources=sources,
            health=health,
        )
        slots.append(Slot(pick=None, message=header))
    judge_prompt = judge.load_prompt()
    summary_prompt = summarize.load_prompt()
    for pick in picks:
        if headline_only:
            judged, writeup = None, None
        elif client is None:
            judged = judge.canned_judgment(pick.story)
            writeup = summarize.canned_writeup(judged)
        else:
            judged = judge.judge_story(
                client, spend_conn, settings, judge_prompt, pick.story, source_dict, now, dev=dev
            )
            writeup = summarize.write_story(
                client,
                spend_conn,
                settings,
                summary_prompt,
                pick.story,
                judged,
                source_dict,
                now,
                dev=dev,
            )
        slots.append(Slot(pick=pick, message=render_story(pick, judged, writeup, sources, health)))
    return slots, selection


def record_sent(state: sqlite3.Connection, day: date, slot: Slot) -> None:
    """Record one sent slot in state.db straight away."""
    if slot.pick is None:
        store.record_header_sent(state, day)
    else:
        store.record_story_sent(state, day, slot.pick, datetime.now(UTC))


async def _send(
    target: TelegramTarget, messages: Sequence[RenderedMessage], on_sent: Callable[[int], None]
) -> list[int]:
    async with Bot(target.token) as bot:
        return await send_all(bot, target.chat_id, target.topic_id, messages, on_sent)


def print_slots(slots: Sequence[Slot]) -> None:
    """Show the digest on stdout, for --dry-run."""
    for i, slot in enumerate(slots, start=1):
        print(f"──────── message {i} of {len(slots)} ────────")
        print(slot.message.text)
        for button in slot.message.buttons:
            print(f"  [{button.label}] {button.url}")
    print(f"\n{len(slots)} messages, {sum(len(s.message.text) for s in slots)} characters.")


def run_digest(*, dry_run: bool, live: bool = False, now: datetime | None = None) -> int:
    """Run the whole digest; return the process exit code."""
    now = now or datetime.now(UTC)
    day = digest_day_for(now)
    target = None
    if not dry_run:
        # Checked before any paid call, so a missing secret can't waste a day's AI spend.
        try:
            target = telegram_target()
        except (KeyError, ValueError) as e:
            log.error("Telegram settings missing or invalid (%s); nothing sent", type(e).__name__)
            return 1

    settings = load_settings()
    all_sources = load_sources(include_disabled=True)
    sources = {s.id: s for s in all_sources}
    with ExitStack() as stack:
        state, _ = store.open_db(store.STATE_PATH, "state", dry_run=dry_run)
        stack.enter_context(closing(state))
        cache, cache_is_new = store.open_db(store.CACHE_PATH, "cache", dry_run=dry_run)
        stack.enter_context(closing(cache))

        if dry_run:
            # A preview always shows a whole digest, whatever today's real progress is.
            progress = DigestDay(day=day)
        else:
            existing = store.digest_day(state, day)
            if existing is not None and existing.done:
                log.info("digest for %s already sent; nothing to do", day)
                return 0
            progress = store.start_digest_day(state, day, now)

        # After a cache eviction, a 304 would leave the empty cache unfilled, so fetch all.
        run_ingest(
            [s for s in all_sources if s.enabled],
            state,
            cache,
            conditional=not cache_is_new,
            now=now,
        )

        client = Anthropic() if (not dry_run or live) else None
        spend_conn = state
        if dry_run and live:
            # --dry-run --live: real calls whose spend is kept, marked dev; nothing else changes.
            spend_conn, _ = store.open_db(store.STATE_PATH, "state")
            stack.enter_context(closing(spend_conn))
        slots, selection = build(
            state,
            cache,
            sources,
            settings,
            progress,
            now,
            client=client,
            spend_conn=spend_conn,
            dev=dry_run,
        )

        if dry_run:
            print_slots(slots)
            print("Dry run: nothing posted, nothing written to data/.")
            return 0

        assert target is not None
        try:
            failed = asyncio.run(
                _send(
                    target, [s.message for s in slots], lambda i: record_sent(state, day, slots[i])
                )
            )
        except TelegramError as e:
            # Bot() checks the token on start-up; anything else is caught per message.
            log.error("could not start the Telegram bot: %s", type(e).__name__)
            return 1
        if failed:
            log.error(
                "%d of %d messages not sent; a re-run sends only those", len(failed), len(slots)
            )
            return 1
        store.finish_digest_day(state, day, selection.chain_links, datetime.now(UTC))
        log.info("digest for %s sent: %d messages", day, len(slots))
        return 0
