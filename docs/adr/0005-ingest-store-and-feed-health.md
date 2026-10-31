# 0005. Ingest, store and feed health

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
Ingest fetches 38 RSS/Atom feeds every 20 minutes on GitHub Actions and stores only new articles,
deduped by a cleaned-up URL. It also has to notice feeds that break quietly, without anyone
checking. The constraints: no paid services, a public repo (so logs and state can't hold secrets),
every package needs the owner's approval, and state lives in one SQLite file committed back by the
workflow, so it has to stay small and diff cleanly.

## Options considered
### Fetching and parsing
#### httpx + feedparser (chosen)
- ✅ Fetches all feeds at the same time with strict timeouts.
- ✅ Shows status codes and redirects, which feed health needs.
- ✅ feedparser copes with messy real-world RSS and Atom.
- ❌ Two packages.

#### requests + feedparser
- ✅ The best-known HTTP library.
- ❌ No async, so parallel fetching needs a thread pool.

#### feedparser alone
- ✅ One package.
- ❌ Weak control over timeouts and redirects, and status codes are hidden.

### Config loading
#### PyYAML + pydantic (chosen)
- ✅ A bad lean value or a duplicate id stops the run with a clear error.
- ✅ pydantic is needed anyway for the AI output (ADR 0002).
- ❌ Two packages.

#### ruamel.yaml + pydantic
- ✅ Keeps comments when writing YAML back.
- ❌ We never write `sources.yaml` automatically, so that's no benefit here.

#### PyYAML + hand-written checks
- ✅ One package.
- ❌ Hand-written validation, against the CLAUDE.md rule to use pydantic between modules.

### Database access
#### stdlib sqlite3 with plain SQL (chosen)
- ✅ No package, and the schema is readable SQL files.
- ❌ Rows are mapped to pydantic models by hand.

#### SQLModel or SQLAlchemy Core
- ✅ Typed models or a query builder that's well known in industry.
- ❌ A large dependency tree for about 5 tables with one writer.

### Schema changes
#### Numbered SQL migrations with `PRAGMA user_version` (chosen)
- ✅ No package, and every change is a reviewable file in a PR.
- ❌ We write the `ALTER` statements ourselves.

#### One `schema.sql`, rebuilt on change
- ✅ Simplest.
- ❌ Wipes posted-today records and feed health, which breaks idempotent posting (ADR 0004) and
  the 3-strike counter.

#### Alembic
- ✅ The industry standard.
- ❌ Needs SQLAlchemy.

### Full article text
#### Later, only for articles being judged (chosen)
- ✅ ~60–80 fetches a day instead of hundreds per run, so less robots.txt and paywall risk.
- ❌ Clustering works on headline + RSS summary only.

#### Now, for every article
- ✅ More text for clustering.
- ❌ Hundreds of requests per run, robots.txt checks and an extraction package right away.

## Decision
- Ingest uses **httpx** (async, strict timeouts) and **feedparser**. Every request sends an honest
  User-Agent: `news-summariser/<version> (+<repo url>)`. Several sites blocked a browser-like one.
- `config/sources.yaml` is loaded with **PyYAML** and validated with **pydantic**.
- State uses stdlib **sqlite3** with plain SQL. Schema changes are numbered files in
  `migrations/`. `PRAGMA user_version` records the last one applied, and missing ones run at startup.
- Ingest stores the RSS title and summary only. Full text is fetched in step 5, only for articles
  being judged.
- Everything is keyed by source `id`, which is never renamed.
- Article URLs are cleaned up before dedupe: tracking parameters removed, host lowercased,
  fragment dropped.
- Each run records per-source health in `state.db`. A run is **unhealthy** if there's an error,
  0 items, or nothing new in 48 hours. After 3 unhealthy runs in a row, the workflow opens a GitHub
  Issue for that feed and closes it when the feed recovers. Redirects are followed and flagged in
  the issue. `sources.yaml` is never edited automatically.

## Consequences
- Four new runtime packages: httpx, feedparser, PyYAML, pydantic.
- Every query maps rows to pydantic models by hand, which means more boilerplate than an ORM.
- Feeds that give only a headline may cluster less well until full text arrives in step 5.
- `state.db` is binary, so PR diffs aren't readable, and two runs at once would conflict. We rely
  on the Actions `concurrency` group (ADR 0004).
- Opening and closing issues needs `issues: write` on the workflow token.
- The digest says "view unavailable (N feeds offline)" instead of reporting a false blindspot.

## Revisit if
- `state.db` grows past 50 MB, even with 30-day pruning.
- An ingest run takes longer than 3 minutes.
- The same story from one outlet appears twice in the digest within a week, so URL cleanup alone
  isn't enough (add title hashing).
- More than 5 feeds' summaries are too thin to cluster, which would bring full-text fetching forward.
