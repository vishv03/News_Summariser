-- One row per paid API call, so the $4/month cap can be enforced in code (ADR 0008).
-- Numbers only: no keys, prompts or article text, because state.db is public.

CREATE TABLE spend (
    id                 INTEGER PRIMARY KEY,
    at                 TEXT NOT NULL,     -- ISO 8601 UTC; the month and day are UTC too
    model              TEXT NOT NULL,
    purpose            TEXT NOT NULL CHECK (purpose IN ('judge', 'summary', 'breaking')),
    input_tokens       INTEGER NOT NULL,
    output_tokens      INTEGER NOT NULL,  -- includes thinking tokens, which are billed as output
    cache_write_tokens INTEGER NOT NULL,
    cache_read_tokens  INTEGER NOT NULL,
    -- Stored, not recomputed, so a later price change doesn't rewrite past spend.
    cost_usd           REAL NOT NULL
);
CREATE INDEX spend_at ON spend (at);
