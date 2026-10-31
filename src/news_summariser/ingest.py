"""Fetch RSS/Atom feeds, store new articles and record each feed's health."""

import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from importlib.metadata import version
from typing import NamedTuple

import feedparser
import httpx

from news_summariser.config import Source
from news_summariser.models import Article, FeedHealth, FetchOutcome
from news_summariser.store import (
    ARTICLE_RETENTION,
    add_articles,
    first_seen,
    get_health,
    mark_seen,
    prune,
    save_health,
)
from news_summariser.urls import url_hash

# Several outlets returned 402/403 to a browser-like User-Agent but accepted an honest one.
USER_AGENT = (
    f"news-summariser/{version('news-summariser')} (+https://github.com/vishv03/News_Summariser)"
)
# One slow outlet must not hold up the whole run; 20 s is generous for a feed of a few hundred KB.
TIMEOUT = httpx.Timeout(20.0)
# Enough to finish 38 feeds quickly without hitting any one host with many parallel requests.
MAX_CONCURRENT = 10
STALE_AFTER = timedelta(hours=48)
# Clustering only needs the gist; long summaries just make cache.db bigger.
SUMMARY_MAX_CHARS = 1000
# The biggest feed is well under 1 MB. Anything far larger is a broken or hijacked URL, and
# reading it whole could exhaust the runner's memory.
MAX_FEED_BYTES = 5 * 1024 * 1024


class FeedItem(NamedTuple):
    url: str
    title: str
    summary: str
    published_at: datetime | None


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(text: str) -> str:
    """Return the text content of an HTML snippet with whitespace collapsed."""
    parser = _TextOnly()
    parser.feed(text)
    parser.close()
    return " ".join("".join(parser.parts).split())


def _entry_date(entry: feedparser.FeedParserDict, now: datetime) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not isinstance(parsed, time.struct_time):
        return None
    # Some feeds stamp items in the future; capping at now keeps them from outliving pruning.
    return min(datetime(*parsed[:6], tzinfo=UTC), now)


def parse_items(content: bytes, now: datetime) -> list[FeedItem]:
    """Parse feed bytes into items, skipping entries with no link or title."""
    items: list[FeedItem] = []
    for entry in feedparser.parse(content).entries:
        url = str(entry.get("link") or "").strip()
        title = str(entry.get("title") or "")
        # feedparser already decodes plain-text titles; stripping them again would eat
        # "Inflation < 3%" from the "<" on.
        if "html" in str((entry.get("title_detail") or {}).get("type", "")):
            title = strip_html(title)
        title = " ".join(title.split())
        if not url.startswith(("http://", "https://")) or not title:
            continue
        summary = strip_html(str(entry.get("summary") or ""))[:SUMMARY_MAX_CHARS]
        items.append(FeedItem(url, title, summary, _entry_date(entry, now)))
    return items


async def fetch(
    client: httpx.AsyncClient, source: Source, prev: FeedHealth | None, conditional: bool
) -> tuple[FetchOutcome, bytes | None]:
    """Fetch one feed; return what happened and the body if there is a new one."""
    headers: dict[str, str] = {}
    if conditional and prev is not None:
        if prev.etag:
            headers["If-None-Match"] = prev.etag
        if prev.last_modified:
            headers["If-Modified-Since"] = prev.last_modified
    try:
        async with client.stream("GET", source.url, headers=headers) as response:
            outcome = FetchOutcome(
                source_id=source.id,
                status=response.status_code,
                redirected_to=str(response.url) if response.history else None,
            )
            if response.status_code == 304:
                outcome.not_modified = True
                return outcome, None
            if response.status_code != 200:
                outcome.error = f"HTTP {response.status_code}"
                return outcome, None

            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    outcome.error = f"response over {MAX_FEED_BYTES // (1024 * 1024)} MB"
                    return outcome, None
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        return FetchOutcome(source_id=source.id, error=f"{type(e).__name__}: {e}"), None

    outcome.etag = response.headers.get("etag")
    outcome.last_modified = response.headers.get("last-modified")
    return outcome, bytes(body)


async def fetch_all(
    sources: list[Source],
    health: dict[str, FeedHealth],
    conditional: bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[tuple[Source, FetchOutcome, bytes | None]]:
    """Fetch every feed concurrently."""
    limit = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    ) as client:

        async def one(source: Source) -> tuple[Source, FetchOutcome, bytes | None]:
            async with limit:
                outcome, content = await fetch(client, source, health.get(source.id), conditional)
            return source, outcome, content

        return await asyncio.gather(*(one(s) for s in sources))


def store_items(
    state: sqlite3.Connection,
    cache: sqlite3.Connection,
    source: Source,
    items: list[FeedItem],
    now: datetime,
) -> tuple[int, datetime | None]:
    """Mark items as seen, cache the recent ones; return the new count and newest item date."""
    by_hash = {url_hash(item.url): item for item in items}
    seen = first_seen(state, by_hash)
    new_hashes = [h for h in by_hash if h not in seen]
    mark_seen(state, source.id, new_hashes, now)

    articles = []
    newest: datetime | None = None
    for h, item in by_hash.items():
        # With no feed date, first-seen time stands in, so an undated item ages out like the rest
        # instead of looking fresh every time it is re-fetched.
        published_at = item.published_at or seen.get(h, now)
        newest = max(newest, published_at) if newest else published_at
        if now - published_at > ARTICLE_RETENTION:
            continue
        articles.append(
            Article(
                url_hash=h,
                source_id=source.id,
                url=item.url,
                title=item.title,
                summary=item.summary,
                language=source.language,
                published_at=published_at,
                fetched_at=now,
            )
        )
    add_articles(cache, articles)
    return len(new_hashes), newest


def assess(prev: FeedHealth | None, outcome: FetchOutcome, now: datetime) -> FeedHealth:
    """Work out a feed's health after this run from its previous health and this fetch."""
    responded = outcome.error is None
    # Freshness comes from the items' own dates, not from whether their URLs are new to us:
    # a dead feed's old items would otherwise look new on the first run, and again every time
    # their seen_urls rows are pruned after 30 days.
    dates = [d for d in (prev.newest_item_at if prev else None, outcome.newest_item_at) if d]
    newest = max(dates, default=None)

    problem: str | None = None
    if not responded:
        problem = outcome.error
    elif not outcome.not_modified and outcome.item_count == 0:
        # Catches a URL that now serves a web page instead of a feed.
        problem = "feed has 0 items"
    elif newest is None or now - newest > STALE_AFTER:
        problem = "nothing new in 48 hours"

    # Keep validators only from a response that had items, so a broken page's ETag can't turn
    # every later run into a 304 that hides the problem.
    got_feed = responded and not outcome.not_modified and outcome.item_count > 0
    return FeedHealth(
        source_id=outcome.source_id,
        last_checked_at=now,
        last_success_at=now if responded else (prev.last_success_at if prev else None),
        newest_item_at=newest,
        last_status=outcome.status,
        problem=problem,
        strikes=0 if problem is None else (prev.strikes if prev else 0) + 1,
        unhealthy_since=None
        if problem is None
        else (prev.unhealthy_since if prev and prev.unhealthy_since else now),
        redirected_to=outcome.redirected_to
        if outcome.status is not None
        else (prev.redirected_to if prev else None),
        etag=outcome.etag if got_feed else (prev.etag if prev else None),
        last_modified=outcome.last_modified if got_feed else (prev.last_modified if prev else None),
    )


def run_ingest(
    sources: list[Source],
    state: sqlite3.Connection,
    cache: sqlite3.Connection,
    *,
    conditional: bool = True,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[tuple[FetchOutcome, FeedHealth]]:
    """Fetch all feeds, store new articles, update feed health and prune old rows."""
    now = now or datetime.now(UTC)
    health = get_health(state)
    fetched = asyncio.run(fetch_all(sources, health, conditional, transport))

    results: list[tuple[FetchOutcome, FeedHealth]] = []
    with state, cache:
        for source, outcome, content in fetched:
            if content is not None:
                items = parse_items(content, now)
                outcome.item_count = len(items)
                outcome.new_count, outcome.newest_item_at = store_items(
                    state, cache, source, items, now
                )
            feed_health = assess(health.get(source.id), outcome, now)
            save_health(state, feed_health)
            results.append((outcome, feed_health))
        prune(state, cache, now)
    return results
