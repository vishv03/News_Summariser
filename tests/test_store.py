import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from conftest import NOW, MakeArticle

from news_summariser import store
from news_summariser.store import open_db


def test_new_db_is_migrated(tmp_path: Path) -> None:
    conn, is_new = open_db(tmp_path / "state.db", "state")
    assert is_new
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
    conn.close()

    conn, is_new = open_db(tmp_path / "state.db", "state")
    assert not is_new
    conn.close()


def test_dry_run_leaves_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    conn, _ = open_db(path, "cache", dry_run=True)
    conn.close()
    assert not path.exists()


def test_failed_migration_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = [(1, "CREATE TABLE t (x INTEGER);\nALTER TABLE missing ADD COLUMN y;")]
    monkeypatch.setattr(store, "_migrations", lambda kind: broken)
    conn = sqlite3.connect(":memory:")

    with pytest.raises(sqlite3.OperationalError):
        store.migrate(conn, "state")

    # Nothing half-applied: the table is gone and the version is unchanged, so a fixed
    # migration can run cleanly next time.
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 't'").fetchone() is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_embeddings_round_trip_and_stale_model_is_skipped() -> None:
    conn, _ = open_db(Path("unused"), "cache", dry_run=True)
    vector = np.arange(384, dtype=np.float32) / 384
    store.save_embeddings(conn, "model-a", {"h1": vector})

    got = store.get_embeddings(conn, ["h1", "h2"], "model-a")
    assert list(got) == ["h1"]
    np.testing.assert_array_equal(got["h1"], vector)

    # A vector from another model must never be compared with new ones.
    assert store.get_embeddings(conn, ["h1"], "model-b") == {}

    # Re-saving under the new model replaces the stale row.
    store.save_embeddings(conn, "model-b", {"h1": vector})
    assert list(store.get_embeddings(conn, ["h1"], "model-b")) == ["h1"]


def test_prune_drops_vectors_whose_article_is_gone(make_article: MakeArticle) -> None:
    state, _ = open_db(Path("unused"), "state", dry_run=True)
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    old = make_article("old", published_at=NOW - timedelta(days=3))
    store.add_articles(cache, [old, make_article("new")])
    vector = np.zeros(384, dtype=np.float32)
    store.save_embeddings(cache, "m", {"old": vector, "new": vector})

    store.prune(state, cache, NOW)

    assert list(store.get_embeddings(cache, ["old", "new"], "m")) == ["new"]


def test_recent_articles_are_oldest_first_and_windowed(make_article: MakeArticle) -> None:
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.add_articles(
        cache,
        [
            make_article("b"),
            make_article("a", published_at=NOW - timedelta(hours=1)),
            make_article("too-old", published_at=NOW - timedelta(hours=30)),
        ],
    )

    got = store.recent_articles(cache, since=NOW - timedelta(hours=24))

    assert [a.url_hash for a in got] == ["a", "b"]
    assert got[0].published_at == NOW - timedelta(hours=1)


def test_recent_articles_window_is_right_whatever_the_callers_timezone(
    make_article: MakeArticle,
) -> None:
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.add_articles(cache, [make_article("a", published_at=NOW - timedelta(hours=2))])
    ist = timezone(timedelta(hours=5, minutes=30))

    # Three hours ago, given in IST ("14:30+05:30"). Compared as text with the stored UTC
    # "10:00+00:00" it would sort after the article and drop it.
    got = store.recent_articles(cache, since=(NOW - timedelta(hours=3)).astimezone(ist))

    assert [a.url_hash for a in got] == ["a"]


def _insert_post(
    state: sqlite3.Connection, post_id: int, last_linked_at: str, articles: list[tuple]
) -> None:
    state.execute(
        "INSERT INTO posted_stories (id, posted_at, kind, last_linked_at) VALUES (?, ?, 'top', ?)",
        (post_id, last_linked_at, last_linked_at),
    )
    state.executemany(
        "INSERT INTO posted_articles (url_hash, source_id, posted_story_id, role)"
        " VALUES (?, ?, ?, ?)",
        [(h, s, post_id, role) for h, s, role in articles],
    )


def test_posted_stories_keep_chained_outlets_out_of_posted_outlets() -> None:
    state, _ = open_db(Path("unused"), "state", dry_run=True)
    _insert_post(
        state,
        1,
        store._iso(NOW),
        [("a", "bbc", "posted"), ("b", "fox", "posted"), ("c", "cnn", "chained")],
    )

    (posted,) = store.posted_stories(state)

    assert posted.id == 1
    assert posted.posted_at == NOW
    assert posted.posted_outlets == {"bbc", "fox"}
    assert posted.url_hashes == {"a", "b", "c"}


def test_prune_keeps_posted_stories_still_being_chained() -> None:
    state, _ = open_db(Path("unused"), "state", dry_run=True)
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    _insert_post(state, 1, store._iso(NOW - timedelta(days=31)), [("old", "bbc", "posted")])
    _insert_post(state, 2, store._iso(NOW - timedelta(days=29)), [("live", "bbc", "posted")])

    store.prune(state, cache, NOW)

    assert [p.id for p in store.posted_stories(state)] == [2]
    # The cascade took the pruned story's articles with it.
    assert state.execute("SELECT url_hash FROM posted_articles").fetchall() == [("live",)]


def test_pruning_an_old_post_keeps_its_update() -> None:
    state, _ = open_db(Path("unused"), "state", dry_run=True)
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    _insert_post(state, 1, store._iso(NOW - timedelta(days=31)), [("a", "bbc", "posted")])
    _insert_post(state, 2, store._iso(NOW), [("a", "bbc", "posted"), ("b", "fox", "posted")])
    state.execute("UPDATE posted_stories SET update_of = 1 WHERE id = 2")

    store.prune(state, cache, NOW)

    assert state.execute("SELECT id, update_of FROM posted_stories").fetchall() == [(2, None)]
