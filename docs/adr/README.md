# Architecture Decision Records

Each file records one hard-to-reverse decision: the options considered, what was chosen, and why.
New ADRs start from [template.md](template.md) and take the next number.

| # | Decision | Status |
|---|---|---|
| [0001](0001-news-sources-rss-and-gdelt.md) | News sources: curated RSS + GDELT | Accepted |
| [0002](0002-lean-judging-ai-with-outlet-baseline.md) | Lean judging: AI per article against an outlet baseline | Accepted |
| [0003](0003-ai-models-haiku-and-sonnet.md) | AI models: Haiku 5.5 for bulk work, Sonnet 5.5 for the daily write-up | Accepted |
| [0004](0004-hosting-github-actions-and-sqlite.md) | Hosting: GitHub Actions with SQLite state; v2 on student credits | Accepted |
| [0005](0005-ingest-store-and-feed-health.md) | Ingest, store and feed health: httpx + feedparser, pydantic config, sqlite3 with numbered migrations | Accepted |
| [0006](0006-clustering-embeddings-and-stories.md) | Clustering: multilingual embeddings (CPU-only PyTorch), agglomerative clustering, embeddings in cache.db, Story model | Accepted |
| [0007](0007-posted-stories-in-state-db.md) | Posted stories: two tables in state.db, written after a successful send, read by selection | Accepted |
| [0008](0008-judge-summary-and-budget.md) | Judge, summary and budget: anthropic SDK, rubric structure, spend log in state.db | Accepted |
