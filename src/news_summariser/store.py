"""SQLite access for state.db (committed) and cache.db (article text, not committed)."""

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Literal

import numpy as np

from news_summariser.models import Article, FeedHealth, PostedStory, SpendRecord

STATE_PATH = Path("data/state.db")
CACHE_PATH = Path("data/cache.db")

# Dedupe needs to remember a URL for longer than any feed keeps listing it.
SEEN_URL_RETENTION = timedelta(days=30)
# The digest looks back 24 hours; 48 leaves room for a missed day.
ARTICLE_RETENTION = timedelta(hours=48)
# A story quiet this long that comes back is treated as new (ADR 0007).
POSTED_STORY_RETENTION = timedelta(days=30)
# Long enough for the 7-day estimate and to audit last month against the bill.
SPEND_RETENTION = timedelta(days=30)

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
    # Off by default in SQLite and set per connection; pruning posted stories relies on the
    # cascade to delete their articles (ADR 0007).
    conn.execute("PRAGMA foreign_keys = ON")
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


def posted_stories(conn: sqlite3.Connection) -> list[PostedStory]:
    """Return every posted story still on record, oldest first."""
    stories = {
        row[0]: (row[1], row[2], set[str](), set[str]())
        for row in conn.execute("SELECT id, posted_at, kind FROM posted_stories ORDER BY id")
    }
    for story_id, url_hash, source_id, role in conn.execute(
        "SELECT posted_story_id, url_hash, source_id, role FROM posted_articles"
    ):
        _, _, outlets, hashes = stories[story_id]
        hashes.add(url_hash)
        if role == "posted":
            outlets.add(source_id)
    return [
        PostedStory(
            id=story_id,
            posted_at=datetime.fromisoformat(posted_at),
            kind=kind,
            posted_outlets=frozenset(outlets),
            url_hashes=frozenset(hashes),
        )
        for story_id, (posted_at, kind, outlets, hashes) in stories.items()
    ]


def add_spend(conn: sqlite3.Connection, record: SpendRecord) -> None:
    """Record one paid API call."""
    u = record.usage
    conn.execute(
        "INSERT INTO spend (at, model, purpose, dev, input_tokens, output_tokens,"
        " cache_write_tokens, cache_read_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _iso(record.at),
            record.model,
            record.purpose,
            int(record.dev),
            u.input_tokens,
            u.output_tokens,
            u.cache_write_tokens,
            u.cache_read_tokens,
            record.cost_usd,
        ),
    )


def spend_since(conn: sqlite3.Connection, since: datetime) -> float:
    """Return total USD spent at or after `since`."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE at >= ?", (_iso(since),))
    return row.fetchone()[0]


def daily_digest_spend(
    conn: sqlite3.Connection, since: datetime, until: datetime
) -> dict[date, float]:
    """Return scheduled digest spend per UTC day in [since, until); days with none are left out."""
    # Stored times are all UTC (see _iso), so the first 10 characters are the UTC date.
    rows = conn.execute(
        "SELECT substr(at, 1, 10), SUM(cost_usd) FROM spend"
        " WHERE at >= ? AND at < ? AND dev = 0 AND purpose IN ('judge', 'summary') GROUP BY 1",
        (_iso(since), _iso(until)),
    )
    return {date.fromisoformat(day): total for day, total in rows}


def prune(state: sqlite3.Connection, cache: sqlite3.Connection, now: datetime) -> None:
    """Delete old seen URLs, quiet posted stories and old spend rows, and cached articles."""
    state.execute(
        "DELETE FROM seen_urls WHERE first_seen_at < ?", (_iso(now - SEEN_URL_RETENTION),)
    )
    # Counted from the last chained article, not the post date, so a story still running keeps
    # its chain (ADR 0007). Its articles go with it through ON DELETE CASCADE.
    state.execute(
        "DELETE FROM posted_stories WHERE last_linked_at < ?",
        (_iso(now - POSTED_STORY_RETENTION),),
    )
    # Never before the start of this UTC month: the cap is a monthly sum, so pruning on the 31st
    # mustn't drop the 1st (ADR 0008).
    month_start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    state.execute(
        "DELETE FROM spend WHERE at < ?", (_iso(min(month_start, now - SPEND_RETENTION)),)
    )
    cache.execute("DELETE FROM articles WHERE published_at < ?", (_iso(now - ARTICLE_RETENTION),))
    # Keyed to articles rather than by age, so a vector never outlives the text it came from.
    cache.execute("DELETE FROM embeddings WHERE url_hash NOT IN (SELECT url_hash FROM articles)")
