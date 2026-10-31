# 0007. Posted stories: a record of what was posted, in state.db

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
The owner's selection rule says a story that was already posted is skipped, unless it has gained
3+ new outlets since, in which case it comes back as an "update". `story_id` can't do this on its
own: it is the earliest article's hash, so it drifts when that article ages out of the 48-hour cache
(ADR 0006). A plain overlap check has a gap too: if day 1's articles are pruned and day 2's were
never recorded, the story looks new on day 3. So skipped repeats must be **chained**: their
articles join the posted record, and the overlap carries forward.

That needs a durable record of every digest story (top 5 and blindspots): which articles and which
outlets it had. `state.db` is committed to a public repo, so it holds hashes and ids only, never
text (ADR 0004), and must stay small. Schema changes go through the numbered, atomic migrations of
ADR 0005. `--dry-run` must be able to run selection without writing anything.

## Options considered
### Scope
#### Posted stories only (chosen)
- ✅ Covers exactly what selection needs now.
- ✅ Idempotent posting (a "started" record, each message marked sent) depends on what Telegram
  returns, which step 6 will know. Designing it now would mean guessing in a committed database.
- ❌ Step 6 adds another migration and probably another ADR.

#### Posted stories and posting state together
- ✅ The posting schema is designed once.
- ❌ Fixes step 6 details before Telegram sending exists.

### Storage
#### Two tables: `posted_stories` + `posted_articles` (chosen)
- ✅ The overlap check is one indexed lookup on `url_hash`.
- ✅ A `role` column (`posted` or `chained`) separates the outlets in the posted version from
  articles added later by chaining, so "3+ new outlets" is a simple query.
- ❌ The most code, and two tables to keep consistent.

#### One table with JSON lists of hashes and outlets
- ✅ Simplest schema; at about 8 stories a day, loading everything is fast.
- ❌ No index, and chaining rewrites JSON blobs.

#### New columns on `seen_urls`
- ✅ Reuses rows that already exist.
- ❌ Mixes dedupe with posting history, and migrates a table ingest depends on.

## Decision
- **`state.db` gets migration `0002` with two tables:**
  - `posted_stories`: one row per digest story, with when it was posted, its kind (top story,
    one-sided blindspot, non-Western blindspot), an optional link to the story it updates, and when
    an article was last chained to it.
  - `posted_articles`: `url_hash`, `source_id`, the posted story it belongs to, and `role`
    (`posted` or `chained`), indexed on `url_hash`. Deleting a posted story deletes its articles
    (a foreign key with `ON DELETE CASCADE`).
- **`select.py` only reads this record.** It returns its picks plus its chain links (the articles of
  skipped repeats).
- **Rows are written only after a successful send, by step 6's code.** If selection wrote them when
  picking and the send then failed, the story would be blocked as a repeat although nobody saw it.
  Until step 6 exists, nothing writes to these tables, and `--dry-run` never does.
- **Pruning:** a posted story is deleted once nothing has been chained to it for 30 days, not 30
  days after it was posted, so a story that runs for months keeps its chain.

## Consequences
- Repeat checks survive `story_id` drift and the 48-hour cache window.
- Step 6 must write both picks and chain links after sending, in one transaction.
- `state.db` grows with each digest: about 8 stories a day with roughly 10 articles each, kept while
  the story is active plus 30 days.
- A story that goes quiet for over 30 days and then returns is treated as new, which is intended.
- Idempotent posting still needs its own tables in step 6.

## Revisit if
- `state.db` passes 5 MB.
- A story is re-posted without the "update" label more than once a month: article overlap is
  missing repeats, and matching may need embedding similarity.
- One story is posted as an "update" 3+ days running more than once a month: the 3-new-outlets
  rule is too loose. That rule is the owner's to retune in `select.py` without a new ADR.
