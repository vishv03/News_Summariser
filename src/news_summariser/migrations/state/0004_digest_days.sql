-- One row per digest day, so a re-run of a failed job sends only the missing slots (ADR 0009).
-- Counters only, no text: ADR 0004 keeps state.db to hashes and ids.

CREATE TABLE digest_days (
    -- The IST date the digest is for (YYYY-MM-DD); readers get it at 07:00 IST.
    day              TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    -- The header is slot 0, so a retry never sends it twice.
    header_sent      INTEGER NOT NULL DEFAULT 0 CHECK (header_sent IN (0, 1)),
    top_sent         INTEGER NOT NULL DEFAULT 0,
    one_sided_sent   INTEGER NOT NULL DEFAULT 0,
    non_western_sent INTEGER NOT NULL DEFAULT 0,
    -- Set once every slot was sent and the chain links written; a done day is skipped.
    done_at          TEXT
);
