-- cache.db is gitignored and kept between runs by actions/cache (ADR 0005). It holds outlets'
-- titles and RSS summaries for 48 hours. Times are ISO 8601 UTC strings.

CREATE TABLE articles (
    url_hash     TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    language     TEXT NOT NULL,
    published_at TEXT NOT NULL,  -- feed date, or first-seen time if the feed gives none
    fetched_at   TEXT NOT NULL
);
CREATE INDEX articles_published_at ON articles (published_at);
