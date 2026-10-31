-- state.db is committed to a public repo (ADR 0004), so it holds no article text: only URL
-- hashes for dedupe and each feed's current health. Times are ISO 8601 UTC strings.

CREATE TABLE seen_urls (
    url_hash      TEXT PRIMARY KEY,  -- sha256 of the cleaned URL (urls.py)
    source_id     TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX seen_urls_first_seen_at ON seen_urls (first_seen_at);

CREATE TABLE feed_health (
    source_id        TEXT PRIMARY KEY,
    last_checked_at  TEXT NOT NULL,
    last_success_at  TEXT,
    newest_item_at   TEXT,
    last_status      INTEGER,
    problem          TEXT,              -- NULL when healthy
    strikes          INTEGER NOT NULL DEFAULT 0,
    unhealthy_since  TEXT,
    redirected_to    TEXT,
    etag             TEXT,
    last_modified    TEXT
);
