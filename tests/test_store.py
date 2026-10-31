import sqlite3
from pathlib import Path

import pytest

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
