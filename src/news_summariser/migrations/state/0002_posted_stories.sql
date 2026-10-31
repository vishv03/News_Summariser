-- What each digest story was, so selection can skip repeats even after story ids drift (ADR 0007).
-- Written only after a successful send; selection only reads it. Hashes and ids only, no text.

CREATE TABLE posted_stories (
    id             INTEGER PRIMARY KEY,
    posted_at      TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('top', 'one_sided', 'non_western')),
    -- The earlier post this one updates. SET NULL: pruning the old post mustn't drop the update.
    update_of      INTEGER REFERENCES posted_stories (id) ON DELETE SET NULL,
    -- Pruning counts 30 days from here, so a long-running story keeps its chain.
    last_linked_at TEXT NOT NULL
);
CREATE INDEX posted_stories_last_linked_at ON posted_stories (last_linked_at);

CREATE TABLE posted_articles (
    url_hash        TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    posted_story_id INTEGER NOT NULL REFERENCES posted_stories (id) ON DELETE CASCADE,
    -- posted: in the story when it was sent. chained: added later from a skipped repeat, so it
    -- matches follow-ups but doesn't count as "already shown" for the new-outlets rule.
    role            TEXT NOT NULL CHECK (role IN ('posted', 'chained')),
    PRIMARY KEY (posted_story_id, url_hash)
);
CREATE INDEX posted_articles_url_hash ON posted_articles (url_hash);
