import sqlite3
from datetime import UTC, date, datetime, timedelta

from conftest import NOW, MakeArticle

from news_summariser import store
from news_summariser.digest import Slot, digest_day_for, record_sent
from news_summariser.models import ChainLink, Pick, RenderedMessage, Story

DAY = date(2026, 10, 31)


def _pick(make_article: MakeArticle, *hashes: str, kind: str = "top") -> Pick:
    story = Story(articles=tuple(make_article(h, source_id=f"s{h}") for h in hashes))
    return Pick(story=story, topic="economy", score=1.0, kind=kind)  # type: ignore[arg-type]


def test_digest_day_is_the_ist_date() -> None:
    # 19:00 UTC is already 00:30 the next day in IST.
    assert digest_day_for(datetime(2026, 10, 31, 19, tzinfo=UTC)) == date(2026, 11, 1)
    assert digest_day_for(datetime(2026, 10, 31, 1, 15, tzinfo=UTC)) == DAY


def test_start_is_idempotent(state: sqlite3.Connection) -> None:
    assert store.digest_day(state, DAY) is None
    first = store.start_digest_day(state, DAY, NOW)
    store.record_header_sent(state, DAY)
    again = store.start_digest_day(state, DAY, NOW + timedelta(hours=1))

    assert not first.header_sent
    # A retry keeps the earlier attempt's progress.
    assert again.header_sent and not again.done


def test_each_sent_story_is_recorded_and_counted(
    state: sqlite3.Connection, make_article: MakeArticle
) -> None:
    store.start_digest_day(state, DAY, NOW)
    record_sent(state, DAY, Slot(pick=None, message=RenderedMessage(text="h")))
    top = _pick(make_article, "a", "b")
    record_sent(state, DAY, Slot(pick=top, message=RenderedMessage(text="t")))
    record_sent(
        state,
        DAY,
        Slot(pick=_pick(make_article, "c", kind="one_sided"), message=RenderedMessage(text="o")),
    )

    progress = store.digest_day(state, DAY)
    assert progress is not None
    assert (progress.header_sent, progress.top_sent, progress.blindspots_sent) == (True, 1, 1)
    # Selection on a retry sees the sent story as a repeat (ADR 0009).
    posted = store.posted_stories(state)
    assert [p.url_hashes for p in posted] == [frozenset({"a", "b"}), frozenset({"c"})]
    assert posted[0].posted_outlets == frozenset({"sa", "sb"})


def test_finish_writes_chain_links_and_marks_done(
    state: sqlite3.Connection, make_article: MakeArticle
) -> None:
    store.start_digest_day(state, DAY, NOW)
    store.record_story_sent(state, DAY, _pick(make_article, "a"), NOW - timedelta(days=2))
    posted_id = store.posted_stories(state)[0].id
    links = [ChainLink(posted_story_id=posted_id, url_hash="z", source_id="sz")]

    store.finish_digest_day(state, DAY, links, NOW)

    progress = store.digest_day(state, DAY)
    assert progress is not None and progress.done
    posted = store.posted_stories(state)[0]
    assert posted.url_hashes == {"a", "z"}
    # Chained articles match follow-ups but aren't "already shown" outlets (ADR 0007).
    assert posted.posted_outlets == {"sa"}
    last_linked = state.execute("SELECT last_linked_at FROM posted_stories").fetchone()[0]
    assert datetime.fromisoformat(last_linked) == NOW


def test_old_digest_days_are_pruned(state: sqlite3.Connection) -> None:
    cache, _ = store.open_db(store.CACHE_PATH, "cache", dry_run=True)
    store.start_digest_day(state, DAY - timedelta(days=40), NOW)
    store.start_digest_day(state, DAY, NOW)

    store.prune(state, cache, NOW)

    assert store.digest_day(state, DAY - timedelta(days=40)) is None
    assert store.digest_day(state, DAY) is not None
