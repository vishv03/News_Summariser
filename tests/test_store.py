import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from news_summariser import store
from news_summariser.models import Article
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


NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _article(url_hash: str, published_at: datetime) -> Article:
    return Article(
        url_hash=url_hash,
        source_id="bbc",
        url=f"https://example.com/{url_hash}",
        title="Title",
        summary="Summary",
        language="en",
        published_at=published_at,
        fetched_at=published_at,
    )


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


def test_prune_drops_vectors_whose_article_is_gone() -> None:
    state, _ = open_db(Path("unused"), "state", dry_run=True)
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.add_articles(cache, [_article("old", NOW - timedelta(days=3)), _article("new", NOW)])
    vector = np.zeros(384, dtype=np.float32)
    store.save_embeddings(cache, "m", {"old": vector, "new": vector})

    store.prune(state, cache, NOW)

    assert list(store.get_embeddings(cache, ["old", "new"], "m")) == ["new"]


def test_recent_articles_are_oldest_first_and_windowed() -> None:
    cache, _ = open_db(Path("unused"), "cache", dry_run=True)
    store.add_articles(
        cache,
        [
            _article("b", NOW),
            _article("a", NOW - timedelta(hours=1)),
            _article("too-old", NOW - timedelta(hours=30)),
        ],
    )

    got = store.recent_articles(cache, since=NOW - timedelta(hours=24))

    assert [a.url_hash for a in got] == ["a", "b"]
    assert got[0].published_at == NOW - timedelta(hours=1)
