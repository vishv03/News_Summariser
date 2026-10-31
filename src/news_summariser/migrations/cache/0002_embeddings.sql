-- Article embeddings for clustering (ADR 0006). Derived from outlets' text, so they live in the
-- uncommitted cache.db. `model` names the embedding model; vectors from any other model are stale
-- and get recomputed rather than compared with new ones.

CREATE TABLE embeddings (
    url_hash TEXT PRIMARY KEY,
    model    TEXT NOT NULL,
    vector   BLOB NOT NULL  -- float32 values, little-endian
);
