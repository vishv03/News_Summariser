# 0004. Hosting: GitHub Actions with SQLite state; v2 on student cloud credits

- **Status:** Accepted
- **Date:** 2026-10-30

## Context
The bot needs to run a daily digest at 07:00 IST and check for breaking news regularly. It must
remember which articles and stories it has already seen and posted. Budget is $0–5/month, and that
goes to the AI. The repo is a public portfolio project, and the owner has GitHub Student Pro.

## Options considered
### GitHub Actions on a schedule, SQLite file committed back to the repo
- ✅ Free: public repos get unlimited Actions minutes.
- ✅ No server to look after; logs and manual re-runs are built in.
- ✅ The state commits keep the repo active, so GitHub doesn't pause scheduled jobs after 60 days.
- ❌ Not instant: breaking alerts can arrive up to ~20 minutes late, and scheduled starts can lag.
- ❌ No always-on process, so no interactive bot commands.

### Always-on free VM (Oracle Cloud free tier)
- ✅ Instant alerts and bot commands.
- ❌ Fiddly setup; idle free VMs can be reclaimed; we'd run the server ourselves.

### Own PC or Raspberry Pi
- ✅ Free and easy to debug.
- ❌ Stops when the machine is off or asleep.

## Decision
v1 runs on GitHub Actions in a public repo: `digest.yml` daily (scheduled about 15 minutes early to
absorb start delays) and `breaking.yml` every 20 minutes. State lives in `data/state.db` (article
URLs and hashes only), committed back by the workflow. (Article text lives in a separate,
uncommitted `data/cache.db`; see [ADR 0005](0005-ingest-store-and-feed-health.md).) Because Actions can't serve buttons that
call back to us, v1 uses URL buttons that open articles, with no bot commands.

v2 adds an always-on, Dockerised bot on a cloud server paid for by GitHub Student Pack credits
(Azure for Students or DigitalOcean, after checking the current offer), for `/more`, `/topic` and
`/sources`.

## Consequences
- Secrets live in GitHub Secrets; logs must never print them, because Actions logs are public.
- Two workflows writing `state.db` could clash, so they must not run at the same time (Actions `concurrency` group).
- Posting must be idempotent, because GitHub can re-run a job. Before sending anything, the digest
  commits a "started" record for the day, then marks each message as sent. A retry posts only what's
  missing, and skips completely if the day is marked "done".
- The state file grows; old rows need pruning, e.g. after 30 days.
- When student credits expire (usually after 12 months), v2 moves to the Oracle free VM or falls back to Actions-only.

## Revisit if
- Alerts arriving up to 20 minutes late becomes a real complaint from the group.
- Committing `state.db` causes merge conflicts or makes the repo noticeably bigger.
- The student credits run out, or v2 commands turn out to be wanted enough to justify a server.
