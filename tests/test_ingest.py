import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from news_summariser.config import Source
from news_summariser.ingest import MAX_FEED_BYTES, USER_AGENT, assess, parse_items, run_ingest
from news_summariser.models import FeedHealth, FetchOutcome
from news_summariser.store import open_db

NOW = datetime(2026, 10, 31, 6, 0, tzinfo=UTC)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Fresh story</title><link>https://ex.com/a?utm_source=rss</link>
  <pubDate>Sat, 31 Oct 2026 05:00:00 GMT</pubDate>
  <description>&lt;p&gt;Some &lt;b&gt;text&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Old story</title><link>https://ex.com/b</link>
  <pubDate>Mon, 26 Oct 2026 05:00:00 GMT</pubDate></item>
<item><title>Undated story</title><link>https://ex.com/c</link></item>
</channel></rss>"""


def make_source(source_id: str) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url=f"https://{source_id}.test/feed",
        region="europe",
        country="GB",
        language="en",
        lean_axis="uk",
        baseline_lean=0,
        ownership="private",
        gov_aligned=False,
        note="test",
    )


@pytest.fixture
def dbs(tmp_path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    state, _ = open_db(tmp_path / "state.db", "state")
    cache, _ = open_db(tmp_path / "cache.db", "cache")
    return state, cache


def test_new_items_stored_and_deduped_across_runs(
    dbs: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    state, cache = dbs
    feed = httpx.MockTransport(lambda r: httpx.Response(200, content=RSS, headers={"ETag": '"v1"'}))

    [(outcome, health)] = run_ingest([make_source("one")], state, cache, now=NOW, transport=feed)
    assert (outcome.item_count, outcome.new_count) == (3, 3)
    assert health.healthy and health.etag == '"v1"'
    # The 5-day-old story is remembered for dedupe but not cached for clustering.
    titles = {t for (t,) in cache.execute("SELECT title FROM articles")}
    assert titles == {"Fresh story", "Undated story"}
    assert cache.execute("SELECT summary FROM articles WHERE title = 'Fresh story'").fetchone() == (
        "Some text",
    )

    [(outcome, _)] = run_ingest(
        [make_source("one")], state, cache, now=NOW + timedelta(minutes=20), transport=feed
    )
    assert outcome.new_count == 0


def test_conditional_headers_and_user_agent_sent(
    dbs: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    state, cache = dbs
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=RSS, headers={"ETag": '"v1"'})

    run_ingest([make_source("one")], state, cache, now=NOW, transport=httpx.MockTransport(handler))
    [(outcome, health)] = run_ingest(
        [make_source("one")], state, cache, now=NOW, transport=httpx.MockTransport(handler)
    )
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert outcome.not_modified and health.healthy

    # After a cache eviction the caller turns conditional off, so the cache refills.
    run_ingest(
        [make_source("one")],
        state,
        cache,
        conditional=False,
        now=NOW,
        transport=httpx.MockTransport(handler),
    )
    assert "If-None-Match" not in seen[-1].headers


@pytest.mark.parametrize(
    ("response", "problem"),
    [
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, content=b"<html><body>Moved</body></html>"), "feed has 0 items"),
    ],
)
def test_broken_feeds_are_unhealthy(
    dbs: tuple[sqlite3.Connection, sqlite3.Connection], response: httpx.Response, problem: str
) -> None:
    state, cache = dbs
    [(_, health)] = run_ingest(
        [make_source("one")],
        state,
        cache,
        now=NOW,
        transport=httpx.MockTransport(lambda r: response),
    )
    assert health.problem == problem and health.strikes == 1


def test_timeout_is_unhealthy(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    state, cache = dbs

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    [(_, health)] = run_ingest(
        [make_source("one")], state, cache, now=NOW, transport=httpx.MockTransport(handler)
    )
    assert health.problem is not None and health.problem.startswith("ConnectTimeout")


def test_redirect_is_flagged(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    state, cache = dbs

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed":
            return httpx.Response(301, headers={"Location": "https://one.test/new-feed"})
        return httpx.Response(200, content=RSS)

    [(_, health)] = run_ingest(
        [make_source("one")], state, cache, now=NOW, transport=httpx.MockTransport(handler)
    )
    assert health.healthy and health.redirected_to == "https://one.test/new-feed"


def unhealthy(source_id: str = "one") -> FetchOutcome:
    return FetchOutcome(source_id=source_id, error="HTTP 503", status=503)


def test_issue_needs_three_strikes_and_six_hours() -> None:
    health = None
    for minutes in (0, 20, 40):
        health = assess(health, unhealthy(), NOW + timedelta(minutes=minutes))
    assert health is not None and health.strikes == 3 and not health.needs_issue

    health = assess(health, unhealthy(), NOW + timedelta(hours=6))
    assert health.needs_issue


def test_recovery_resets_strikes() -> None:
    health = assess(None, unhealthy(), NOW)
    ok = FetchOutcome(source_id="one", status=200, item_count=5, new_count=2, newest_item_at=NOW)
    health = assess(health, ok, NOW + timedelta(minutes=20))
    assert health.healthy and health.strikes == 0 and health.unhealthy_since is None


def test_nothing_new_for_48_hours_is_unhealthy() -> None:
    prev = FeedHealth(source_id="one", last_checked_at=NOW, newest_item_at=NOW)
    same_items = FetchOutcome(source_id="one", status=200, item_count=5, newest_item_at=NOW)
    assert assess(prev, same_items, NOW + timedelta(hours=47)).healthy
    later = assess(prev, same_items, NOW + timedelta(hours=49))
    assert later.problem == "nothing new in 48 hours"


DEAD_FEED = b"""<rss version="2.0"><channel><title>Dead</title>
<item><title>Last story</title><link>https://dead.test/a</link>
  <pubDate>Mon, 01 Jun 2026 05:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_dead_feed_is_stale_on_first_run_and_after_pruning(
    dbs: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    # Its URL is new to us, but the item's own date is months old.
    state, cache = dbs
    feed = httpx.MockTransport(lambda r: httpx.Response(200, content=DEAD_FEED))
    [(outcome, health)] = run_ingest([make_source("one")], state, cache, now=NOW, transport=feed)
    assert outcome.new_count == 1
    assert health.problem == "nothing new in 48 hours"

    # 31 days on, seen_urls has been pruned and the URL counts as new again: still stale.
    later = NOW + timedelta(days=31)
    run_ingest([make_source("one")], state, cache, now=later, transport=feed)
    [(outcome, health)] = run_ingest(
        [make_source("one")], state, cache, now=later + timedelta(minutes=20), transport=feed
    )
    assert health.problem == "nothing new in 48 hours"


def test_plain_titles_keep_angle_brackets_and_html_titles_are_stripped() -> None:
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title type="text">Inflation &lt; 3% for first time</title><link href="https://x.test/a"/></entry>
<entry><title type="html">&lt;em&gt;Big&lt;/em&gt; news</title><link href="https://x.test/b"/></entry>
</feed>"""
    assert [i.title for i in parse_items(feed, NOW)] == [
        "Inflation < 3% for first time",
        "Big news",
    ]


def test_oversized_response_is_rejected(
    dbs: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    state, cache = dbs
    huge = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * (MAX_FEED_BYTES + 1)))
    [(_, health)] = run_ingest([make_source("one")], state, cache, now=NOW, transport=huge)
    assert health.problem == "response over 5 MB"
