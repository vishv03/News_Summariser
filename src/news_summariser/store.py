"""SQLite access for state.db (committed) and cache.db (article text, not committed)."""

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Literal

import numpy as np

from news_summariser.models import Article, FeedHealth

STATE_PATH = Path("data/state.db")
CACHE_PATH = Path("data/cache.db")

# Dedupe needs to remember a URL for longer than any feed keeps listing it.
SEEN_URL_RETENTION = timedelta(days=30)
# The digest looks back 24 hours; 48 leaves room for a missed day.
ARTICLE_RETENTION = timedelta(hours=48)

Kind = Literal["state", "cache"]


def _iso(dt: datetime) -> str:
    # Times are compared as text in SQL, which is only correct if they all share one offset: an IST
    # "07:00+05:30" would otherwise sort after a UTC "06:00+00:00" that is 4.5 hours later.
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _migrations(kind: Kind) -> list[tuple[int, str]]:
    folder = resources.files("news_summariser") / "migrations" / kind
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    return [(int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8")) for f in files]


def migrate(conn: sqlite3.Connection, kind: Kind) -> int:
    """Apply any migrations newer than the database's user_version; return the old version."""
    old_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in _migrations(kind):
        if number > old_version:
            # executescript commits on its own, so `with conn` can't roll it back. An explicit
            # transaction makes each migration all-or-nothing: a failure partway would otherwise
            # leave a half-built schema that the next run trips over.
            try:
                conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
            except sqlite3.Error:
                conn.rollback()
                raise
    return old_version


def open_db(path: Path, kind: Kind, *, dry_run: bool = False) -> tuple[sqlite3.Connection, bool]:
    """Open and migrate a database; return it and whether it was newly created."""
    if dry_run:
        # Work on an in-memory copy so nothing on disk changes.
        conn = sqlite3.connect(":memory:")
        if path.exists():
            # closing(): sqlite3's own `with` only commits; it would leave the file open.
            with closing(sqlite3.connect(path)) as src:
                src.backup(conn)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    return conn, migrate(conn, kind) == 0


def first_seen(conn: sqlite3.Connection, hashes: Iterable[str]) -> dict[str, datetime]:
    """Return when each already-seen URL hash was first seen; unseen hashes are left out."""
    hashes = list(hashes)
    if not hashes:
        return {}
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT url_hash, first_seen_at FROM seen_urls WHERE url_hash IN ({placeholders})",
        hashes,
    )
    return {h: datetime.fromisoformat(t) for h, t in rows}


def mark_seen(
    conn: sqlite3.Connection, source_id: str, hashes: Iterable[str], now: datetime
) -> None:
    """Record URL hashes as seen."""
    conn.executemany(
        "INSERT OR IGNORE INTO seen_urls (url_hash, source_id, first_seen_at) VALUES (?, ?, ?)",
        [(h, source_id, _iso(now)) for h in hashes],
    )


def add_articles(conn: sqlite3.Connection, articles: Iterable[Article]) -> None:
    """Store articles in cache.db, skipping ones already there."""
    conn.executemany(
        "INSERT OR IGNORE INTO articles (url_hash, source_id, url, title, summary, language,"
        " published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a.url_hash,
                a.source_id,
                a.url,
                a.title,
                a.summary,
                a.language,
                _iso(a.published_at),
                _iso(a.fetched_at),
            )
            for a in articles
        ],
    )


_ARTICLE_COLUMNS = list(Article.model_fields)


def recent_articles(conn: sqlite3.Connection, since: datetime) -> list[Article]:
    """Return cached articles published at or after `since`, oldest first."""
    rows = conn.execute(
        f"SELECT {', '.join(_ARTICLE_COLUMNS)} FROM articles"
        " WHERE published_at >= ? ORDER BY published_at, url_hash",
        (_iso(since),),
    )
    return [Article.model_validate(dict(zip(_ARTICLE_COLUMNS, r, strict=True))) for r in rows]


# Fixed byte order, so a cache.db restored on another machine reads back the same numbers.
_VECTOR_DTYPE = np.dtype("<f4")


def get_embeddings(
    conn: sqlite3.Connection, hashes: Iterable[str], model: str
) -> dict[str, np.ndarray]:
    """Return stored vectors made by `model`; missing or stale ones are left out."""
    hashes = list(hashes)
    if not hashes:
        return {}
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT url_hash, vector FROM embeddings WHERE model = ? AND url_hash IN ({placeholders})",
        [model, *hashes],
    )
    return {h: np.frombuffer(blob, dtype=_VECTOR_DTYPE) for h, blob in rows}


def save_embeddings(conn: sqlite3.Connection, model: str, vectors: dict[str, np.ndarray]) -> None:
    """Insert or replace vectors; replacing is how a stale model's vector gets updated."""
    conn.executemany(
        "INSERT OR REPLACE INTO embeddings (url_hash, model, vector) VALUES (?, ?, ?)",
        [(h, model, v.astype(_VECTOR_DTYPE).tobytes()) for h, v in vectors.items()],
    )


_HEALTH_COLUMNS = list(FeedHealth.model_fields)


def get_health(conn: sqlite3.Connection) -> dict[str, FeedHealth]:
    """Return every source's stored health, keyed by source id."""
    rows = conn.execute(f"SELECT {', '.join(_HEALTH_COLUMNS)} FROM feed_health")
    return {
        row[0]: FeedHealth.model_validate(dict(zip(_HEALTH_COLUMNS, row, strict=True)))
        for row in rows
    }


def save_health(conn: sqlite3.Connection, health: FeedHealth) -> None:
    """Insert or replace one source's health row."""
    values = [_iso(v) if isinstance(v, datetime) else v for v in health.model_dump().values()]
    conn.execute(
        f"INSERT OR REPLACE INTO feed_health ({', '.join(_HEALTH_COLUMNS)})"
        f" VALUES ({', '.join('?' * len(_HEALTH_COLUMNS))})",
        values,
    )


def prune(state: sqlite3.Connection, cache: sqlite3.Connection, now: datetime) -> None:
    """Delete seen URLs older than 30 days, and cached articles and their vectors after 48 hours."""
    state.execute(
        "DELETE FROM seen_urls WHERE first_seen_at < ?", (_iso(now - SEEN_URL_RETENTION),)
    )
    cache.execute("DELETE FROM articles WHERE published_at < ?", (_iso(now - ARTICLE_RETENTION),))
    # Keyed to articles rather than by age, so a vector never outlives the text it came from.
    cache.execute("DELETE FROM embeddings WHERE url_hash NOT IN (SELECT url_hash FROM articles)")
